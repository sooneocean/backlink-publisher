"""Tests for pr_outreach store, scorer, and CLI."""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

import pytest

from backlink_publisher.pr_outreach.scorer import (
    build_topic_tokens,
    score_opportunity,
)


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

def test_score_zero_without_topics():
    opp = {"headline": "looking for AI expert", "summary": "need comment on AI trends"}
    assert score_opportunity(opp, set()) == 0.0


def test_score_headline_match():
    tokens = {"ai", "expert"}
    opp = {"headline": "looking for AI expert", "summary": "generic request"}
    score = score_opportunity(opp, tokens)
    assert score >= 10


def test_score_summary_match():
    tokens = {"manga"}
    opp = {"headline": "generic headline", "summary": "article about manga culture"}
    score = score_opportunity(opp, tokens)
    assert score >= 5


def test_score_cap_at_100():
    tokens = {str(i) for i in range(20)}
    opp = {
        "headline": " ".join(str(i) for i in range(10)),
        "summary": " ".join(str(i) for i in range(10)),
    }
    assert score_opportunity(opp, tokens) <= 100.0


def test_build_topic_tokens_extracts_from_pools():
    config_targets = {
        "site1": {
            "branded_pool": ["MyBrand", "My Brand"],
            "exact_pool": ["exact term"],
        }
    }
    tokens = build_topic_tokens(config_targets)
    assert "mybrand" in tokens or "my" in tokens
    assert "exact" in tokens or "term" in tokens


def test_build_topic_tokens_skips_non_dicts():
    config_targets = {"bad": "not a dict", "ok": {"branded_pool": ["hello"]}}
    tokens = build_topic_tokens(config_targets)
    assert "hello" in tokens


# ---------------------------------------------------------------------------
# Store (via tmp_path config dir)
# ---------------------------------------------------------------------------

def _with_config_dir(tmp_path, fn):
    """Run fn with BACKLINK_PUBLISHER_CONFIG_DIR pointing to tmp_path."""
    import os
    old = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
    try:
        return fn()
    finally:
        if old is None:
            os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
        else:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old


def test_store_round_trip(tmp_path):
    from backlink_publisher.pr_outreach.store import load_opportunities, upsert_opportunity

    def _run():
        entry = {"id": "opp-1", "headline": "test", "status": "pending"}
        saved = upsert_opportunity(entry)
        assert saved["id"] == "opp-1"
        rows = load_opportunities()
        assert len(rows) == 1
        assert rows[0]["id"] == "opp-1"

    _with_config_dir(tmp_path, _run)


def test_store_upsert_updates_existing(tmp_path):
    from backlink_publisher.pr_outreach.store import load_opportunities, upsert_opportunity

    def _run():
        upsert_opportunity({"id": "opp-1", "headline": "v1", "status": "pending"})
        upsert_opportunity({"id": "opp-1", "headline": "v2"})
        rows = load_opportunities()
        assert len(rows) == 1
        assert rows[0]["headline"] == "v2"

    _with_config_dir(tmp_path, _run)


def test_store_update_status(tmp_path):
    from backlink_publisher.pr_outreach.store import update_status, upsert_opportunity

    def _run():
        upsert_opportunity({"id": "opp-1", "headline": "test", "status": "pending"})
        saved = update_status("opp-1", "won")
        assert saved["status"] == "won"

    _with_config_dir(tmp_path, _run)


def test_store_missing_id_raises(tmp_path):
    from backlink_publisher.pr_outreach.store import upsert_opportunity

    def _run():
        with pytest.raises(ValueError, match="id"):
            upsert_opportunity({"headline": "no id here"})

    _with_config_dir(tmp_path, _run)


def test_store_invalid_status_raises(tmp_path):
    from backlink_publisher.pr_outreach.store import update_status, upsert_opportunity

    def _run():
        upsert_opportunity({"id": "opp-1", "headline": "test", "status": "pending"})
        with pytest.raises(ValueError, match="status"):
            update_status("opp-1", "invented-status")

    _with_config_dir(tmp_path, _run)


# ---------------------------------------------------------------------------
# CLI ingest / list
# ---------------------------------------------------------------------------

import backlink_publisher.cli.pr_opportunities as cli_mod


def _run_ingest(tmp_path, jsonl_input: str, extra_argv=None):
    import os
    argv = ["ingest"] + list(extra_argv or [])
    old = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
    try:
        with (
            patch.object(sys, "stdin", io.StringIO(jsonl_input)),
            patch.object(sys, "stderr", io.StringIO()) as err,
            patch.object(cli_mod, "_load_config_targets", return_value={}),
        ):
            cli_mod.main(argv)
        return err.getvalue()
    finally:
        if old is None:
            os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
        else:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old


def test_cli_ingest_one_row(tmp_path):
    row = {"id": "opp-1", "headline": "test request", "source": "sos"}
    err = _run_ingest(tmp_path, json.dumps(row) + "\n")
    assert "1 ingested" in err


def test_cli_ingest_skips_missing_id(tmp_path):
    row = {"headline": "no id here"}
    err = _run_ingest(tmp_path, json.dumps(row) + "\n")
    assert "1 skipped" in err


def test_cli_ingest_min_score_filter(tmp_path):
    row = {"id": "opp-1", "headline": "irrelevant topic X", "source": "sos"}
    # relevance_score will be 0 (no matching tokens), min-score=50 → skipped
    err = _run_ingest(tmp_path, json.dumps(row) + "\n", ["--min-score", "50"])
    assert "1 skipped" in err
