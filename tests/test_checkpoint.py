"""Tests for backlink_publisher.checkpoint module."""

from __future__ import annotations

import json
import os
import re
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher.checkpoint import (
    create_checkpoint,
    delete,
    delete_complete,
    generate_run_id,
    list_incomplete,
    load_checkpoint,
    mark_complete,
    update_item,
)

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")


@pytest.fixture()
def ckpt_cache(tmp_path):
    """Patch _cache_dir to use tmp_path for all checkpoint tests."""
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        yield tmp_path / "cache"


def _rows(n=2):
    return [
        {"id": f"r{i}", "title": f"Title {i}", "platform": "blogger", "target_url": "https://example.com"}
        for i in range(n)
    ]


# ── run_id format ──────────────────────────────────────────────────────────────

def test_run_id_format():
    run_id = generate_run_id()
    assert _RUN_ID_RE.match(run_id), f"run_id {run_id!r} does not match expected format"


# ── create_checkpoint ──────────────────────────────────────────────────────────

def test_create_checkpoint_writes_pending_items(ckpt_cache):
    rows = _rows(2)
    run_id, path = create_checkpoint(rows, platform="blogger", mode="draft")

    assert _RUN_ID_RE.match(run_id)
    assert path.exists()

    data = json.loads(path.read_text())
    assert data["run_id"] == run_id
    assert data["status"] is None
    assert data["platform"] == "blogger"
    assert data["mode"] == "draft"
    assert len(data["items"]) == 2
    for item, row in zip(data["items"], rows):
        assert item["status"] == "pending"
        assert item["id"] == row["id"]
        assert item["payload"] == row


def test_create_checkpoint_sets_file_permissions_0600(ckpt_cache):
    run_id, path = create_checkpoint(_rows(1), platform=None, mode="draft")
    file_mode = stat.S_IMODE(path.stat().st_mode)
    # On non-Windows systems, permissions should be 0o600
    if os.name != "nt":
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"


def test_create_checkpoint_dir_permissions_0700(ckpt_cache):
    create_checkpoint(_rows(1), platform=None, mode="draft")
    ckpt_dir = ckpt_cache / "checkpoints"
    if os.name != "nt":
        dir_mode = stat.S_IMODE(ckpt_dir.stat().st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"


# ── load_checkpoint ────────────────────────────────────────────────────────────

def test_load_checkpoint_returns_data(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(2), platform="medium", mode="publish")
    data = load_checkpoint(run_id)
    assert data["run_id"] == run_id
    assert len(data["items"]) == 2


def test_load_checkpoint_raises_on_missing(ckpt_cache):
    with pytest.raises(FileNotFoundError, match="checkpoint not found: 20260101T000000-deadbeef"):
        load_checkpoint("20260101T000000-deadbeef")


def test_load_checkpoint_raises_on_invalid_run_id(ckpt_cache):
    with pytest.raises(ValueError, match="invalid run_id"):
        load_checkpoint("../etc/passwd")


# ── update_item ────────────────────────────────────────────────────────────────

def test_update_item_done(ckpt_cache):
    rows = _rows(3)
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")

    update_item(run_id, "r0", "done", published_url="https://blog.example.com/p/123", completed_at="2026-01-01T00:00:00+00:00")

    data = load_checkpoint(run_id)
    item0 = next(i for i in data["items"] if i["id"] == "r0")
    assert item0["status"] == "done"
    assert item0["published_url"] == "https://blog.example.com/p/123"
    assert item0["completed_at"] == "2026-01-01T00:00:00+00:00"
    # other items unchanged
    assert data["items"][1]["status"] == "pending"
    assert data["items"][2]["status"] == "pending"


def test_update_item_failed(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")

    update_item(run_id, "r0", "failed", error="svc error", error_class="http_5xx")

    data = load_checkpoint(run_id)
    item0 = next(i for i in data["items"] if i["id"] == "r0")
    assert item0["status"] == "failed"
    assert item0["error"] == "svc error"
    assert item0["error_class"] == "http_5xx"


def test_update_item_round_trip_integrity(ckpt_cache):
    """Second update overwrites first; no stale state."""
    run_id, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")

    update_item(run_id, "r0", "failed", error="first error", error_class="transient")
    update_item(run_id, "r0", "done", published_url="https://example.com/x", completed_at="t")

    data = load_checkpoint(run_id)
    item0 = next(i for i in data["items"] if i["id"] == "r0")
    assert item0["status"] == "done"
    assert item0["published_url"] == "https://example.com/x"
    # first update's error should be gone (overwritten)
    assert item0.get("error") is None or item0.get("error") != "first error"


def test_update_item_preserves_payload(ckpt_cache):
    rows = _rows(2)
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="u")
    data = load_checkpoint(run_id)
    item0 = next(i for i in data["items"] if i["id"] == "r0")
    assert item0["payload"] == rows[0]


def test_update_item_invalid_run_id(ckpt_cache):
    with pytest.raises(ValueError):
        update_item("bad-id", "r0", "done")


# ── mark_complete ──────────────────────────────────────────────────────────────

def test_mark_complete(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(1), platform="blogger", mode="draft")
    mark_complete(run_id)
    data = load_checkpoint(run_id)
    assert data["status"] == "complete"


def test_mark_complete_invalid_run_id(ckpt_cache):
    with pytest.raises(ValueError):
        mark_complete("not-valid")


# ── list_incomplete ────────────────────────────────────────────────────────────

def test_list_incomplete_returns_only_incomplete(ckpt_cache):
    r1, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")
    r2, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")
    mark_complete(r1)

    incomplete = list_incomplete()
    run_ids = [d["run_id"] for d in incomplete]
    assert r2 in run_ids
    assert r1 not in run_ids


def test_list_incomplete_empty_dir(ckpt_cache):
    assert list_incomplete() == []


def test_list_incomplete_skips_tmp_files(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(1), platform="blogger", mode="draft")
    # Place a stale .tmp orphan
    ckpt_dir = ckpt_cache / "checkpoints"
    (ckpt_dir / "stale.tmp").write_text("{}", encoding="utf-8")
    # list_incomplete only globs *.json, so the .tmp file should not cause issues
    incomplete = list_incomplete()
    assert any(d["run_id"] == run_id for d in incomplete)


def test_list_incomplete_complete_run_excluded(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="u")
    update_item(run_id, "r1", "done", published_url="v")
    mark_complete(run_id)

    assert list_incomplete() == []


# ── delete / delete_complete ───────────────────────────────────────────────────

def test_delete_removes_checkpoint(ckpt_cache):
    run_id, path = create_checkpoint(_rows(1), platform="blogger", mode="draft")
    delete(run_id)
    assert not path.exists()


def test_delete_raises_on_missing(ckpt_cache):
    with pytest.raises(FileNotFoundError):
        delete("20260101T000000-deadbeef")


def test_delete_invalid_run_id(ckpt_cache):
    with pytest.raises(ValueError):
        delete("../etc/bad")


def test_delete_complete_removes_only_complete(ckpt_cache):
    r1, p1 = create_checkpoint(_rows(1), platform="blogger", mode="draft")
    r2, p2 = create_checkpoint(_rows(1), platform="blogger", mode="draft")
    mark_complete(r1)

    count = delete_complete()

    assert count == 1
    assert not p1.exists()
    assert p2.exists()


def test_delete_complete_empty_no_error(ckpt_cache):
    assert delete_complete() == 0


# ── integration ────────────────────────────────────────────────────────────────

def test_create_update_load_integration(ckpt_cache):
    rows = _rows(3)
    run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")

    update_item(run_id, "r0", "done", published_url="https://x.com/a", completed_at="t1")
    update_item(run_id, "r1", "failed", error="oops", error_class="transient")
    # r2 stays pending

    data = load_checkpoint(run_id)
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id["r0"]["status"] == "done"
    assert by_id["r1"]["status"] == "failed"
    assert by_id["r2"]["status"] == "pending"


def test_complete_run_not_in_list_incomplete(ckpt_cache):
    run_id, _ = create_checkpoint(_rows(2), platform="blogger", mode="draft")
    update_item(run_id, "r0", "done", published_url="u")
    update_item(run_id, "r1", "done", published_url="v")
    mark_complete(run_id)

    assert list_incomplete() == []
