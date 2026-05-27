"""Unit 7b — enforce reconciliation precondition (R19b). Enforce refuses to
publish until the dedup store covers the back-catalogue (missing keys → exit 3;
quarantine tail → exit 3 unless acknowledged). Read-only check via
--check-enforce-readiness. Observe mode is unaffected.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U7).
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

import backlink_publisher.publishing.adapters  # noqa: F401
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.events import EventStore
from backlink_publisher.idempotency.backfill import run_backfill
from backlink_publisher.idempotency.reconcile import check_enforce_readiness
from backlink_publisher.linkcheck.verify import VerificationResult

_ENFORCE = "BACKLINK_PUBLISHER_DEDUP_ENFORCE"
_ACK = "BACKLINK_PUBLISHER_DEDUP_ENFORCE_ACK_QUARANTINE"


@pytest.fixture(autouse=True)
def _fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv(_ENFORCE, raising=False)
    monkeypatch.delenv(_ACK, raising=False)


@pytest.fixture(autouse=True)
def _verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _payload(target="https://example.com/new", item_id="r1", platform="medium"):
    return {
        "id": item_id, "platform": platform, "language": "en",
        "publish_mode": "draft", "target_url": target,
        "main_domain": "https://example.com", "url_mode": "A",
        "title": "T", "slug": "t", "excerpt": "e", "tags": ["a", "b"],
        "content_markdown": f"About {target} and https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "E", "kind": "main_domain", "required": True},
            {"url": target, "anchor": "A", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "W", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "M", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "S", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "G", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "S", "description": "d", "canonical_url": target},
    }


def _event(target, adapter="blogger-api", kind="publish.confirmed", live="https://b/p"):
    EventStore().append(
        kind, {"live_url": live, "target_url": target, "platform": adapter},
        target_url=target,
    )


def _run(rows, argv, enforce=False, ack=False):
    if enforce:
        os.environ[_ENFORCE] = "1"
    if ack:
        os.environ[_ACK] = "1"
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO("\n".join(json.dumps(r) for r in rows))
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old
        os.environ.pop(_ENFORCE, None)
        os.environ.pop(_ACK, None)


# --------------------------------------------------------------------------- #
# Precondition gates publishing
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_blocked_when_backcatalog_missing(mock_pub, _mv):
    _event("https://example.com/live-a")  # a live post never backfilled
    _out, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 3
    assert "enforce blocked" in stderr
    assert "backfill-dedup" in stderr
    mock_pub.assert_not_called()  # refused before any publish/lease


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_proceeds_after_backfill(mock_pub, _mv):
    _event("https://example.com/live-a")
    run_backfill()  # now the back-catalogue is covered
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/new",
    )
    _out, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 0, stderr
    mock_pub.assert_called_once()  # new key dispatches; back-catalogue covered


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_blocked_by_quarantine_until_acked(mock_pub, _mv):
    _event("https://example.com/old", adapter="http-form-post")  # unregistered -> quarantine
    _out, stderr, code = _run([_payload()], ["--platform", "medium"], enforce=True)
    assert code == 3
    assert "unmappable/retired" in stderr
    mock_pub.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_enforce_proceeds_with_quarantine_acknowledged(mock_pub, _mv):
    _event("https://example.com/old", adapter="http-form-post")
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/new",
    )
    _out, stderr, code = _run(
        [_payload()], ["--platform", "medium"], enforce=True, ack=True
    )
    assert code == 0, stderr
    mock_pub.assert_called_once()


# --------------------------------------------------------------------------- #
# Observe mode is unaffected by the precondition
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_observe_ignores_reconcile_precondition(mock_pub, _mv):
    _event("https://example.com/live-a")  # would block enforce, but observe ignores
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/new",
    )
    _out, _err, code = _run([_payload()], ["--platform", "medium"], enforce=False)
    assert code == 0
    mock_pub.assert_called_once()


# --------------------------------------------------------------------------- #
# Read verbs are NOT blocked by the precondition (operator needs them to recover)
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_preview_manifest_not_blocked_by_precondition(mock_pub, _mv):
    _event("https://example.com/live-a")
    _out, _err, code = _run(
        [_payload()], ["--preview-manifest"], enforce=True
    )
    assert code == 0  # read-only preview bypasses the precondition


def test_backfill_verb_not_blocked_by_precondition():
    _event("https://example.com/live-a")
    _out, _err, code = _run([], ["--backfill-dedup"], enforce=True)
    assert code == 0  # the recovery verb itself must run under enforce


# --------------------------------------------------------------------------- #
# --check-enforce-readiness verb
# --------------------------------------------------------------------------- #
def test_check_readiness_not_ready_exit_3_counts_only():
    _event("https://example.com/live-secret-campaign")
    _out, stderr, code = _run([], ["--check-enforce-readiness"])
    assert code == 3  # DependencyError: operator action required (matches precondition)
    assert "NOT READY" in stderr
    assert "missing=1" in stderr
    assert "secret-campaign" not in stderr  # no URL leak (digest only)


def test_check_readiness_ready_exit_0():
    _event("https://example.com/live-a")
    run_backfill()
    _out, stderr, code = _run([], ["--check-enforce-readiness"])
    assert code == 0
    assert "READY" in stderr


def test_check_readiness_empty_is_ready():
    _out, _stderr, code = _run([], ["--check-enforce-readiness"])
    assert code == 0  # nothing published yet → trivially ready


# --------------------------------------------------------------------------- #
# Resume seam honors the precondition
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli._publish_helpers._do_sleep")
@patch("backlink_publisher.cli._resume.verify_adapter_setup")
@patch("backlink_publisher.cli._resume.adapter_publish")
def test_resume_blocked_when_backcatalog_missing(mock_pub, _mv, _ms, mock_cache, tmp_path):
    from backlink_publisher.checkpoint import create_checkpoint

    mock_cache.return_value = tmp_path / "cache"
    rows = [_payload(item_id="r0", platform="blogger")]
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    _event("https://example.com/live-a")  # uncovered back-catalogue
    _out, stderr, code = _run([], ["--resume", run_id], enforce=True)
    assert code == 3
    assert "enforce blocked" in stderr
    mock_pub.assert_not_called()
