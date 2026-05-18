"""Idempotency / error-path tests for ``events.projector``.

Plan §U4 maps three dedup layers — in-tx ``seen``, cursor state diff,
and ``articles.live_url UNIQUE``. The tests here exercise each layer in
isolation plus the cross-source dedup that ties them together.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import (
    EventStore,
    ProjectionError,
    ProjectionResult,
    flush_for,
)


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _count(store: EventStore, table: str) -> int:
    with store.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _make_checkpoint(
    tmp_path: Path,
    items: list[dict[str, Any]],
    *,
    run_id: str = "20260518T120000-abcd1234",
) -> Path:
    path = tmp_path / f"{run_id}.json"
    path.write_text(json.dumps({
        "run_id": run_id,
        "started_at": "2026-05-18T12:00:00+00:00",
        "platform": "blogger", "mode": "publish", "status": None,
        "items": items, "flags": {},
    }))
    return path


# ── Layer 2: cursor diff stops a no-op re-flush ───────────────────


def test_double_flush_on_same_state_is_a_noop(tmp_path):
    ckpt = _make_checkpoint(tmp_path, [
        {
            "id": "a", "status": "pending", "title": "t",
            "platform": "blogger", "adapter": None,
            "published_url": None, "error": None, "error_class": None,
            "completed_at": None,
            "payload": {"target_url": "https://example.com/a"},
        },
    ])
    flush_for(ckpt)
    first_events = _count(EventStore(), "events")
    first_articles = _count(EventStore(), "articles")
    first_cursor = _query_cursor(EventStore(), str(ckpt))

    flush_for(ckpt)

    assert _count(EventStore(), "events") == first_events
    assert _count(EventStore(), "articles") == first_articles
    assert _query_cursor(EventStore(), str(ckpt)) == first_cursor


def _query_cursor(store: EventStore, source: str) -> str | None:
    with store.connect() as conn:
        row = conn.execute(
            "SELECT last_seen_state_json FROM projection_cursor WHERE source = ?",
            (source,),
        ).fetchone()
    return row[0] if row else None


# ── Layer 3: articles.live_url UNIQUE catches reset-to-None replay ─


def test_succeeded_to_reset_to_succeeded_only_emits_one_publish_confirmed(tmp_path):
    """The checkpoint ``update_item`` writer resets optional fields to None on
    every status change, so succeeded → succeeded (same URL) shows up as two
    distinct cursor-level transitions. The DB UNIQUE on ``articles.live_url``
    must collapse them into one ``publish.confirmed`` + one article row.
    """
    base_item = {
        "id": "a", "status": "succeeded", "title": "t",
        "platform": "blogger", "adapter": None,
        "published_url": "https://blog.example.org/post-a",
        "error": None, "error_class": None,
        "completed_at": "2026-05-18T12:05:00+00:00",
        "payload": {"target_url": "https://example.com/a"},
    }
    ckpt = _make_checkpoint(tmp_path, [base_item])
    flush_for(ckpt)

    # Simulate the bug: status reset to pending (clears published_url) and
    # then forwarded back to succeeded with the same live URL.
    reset_item = dict(base_item, status="pending", published_url=None, completed_at=None)
    ckpt.write_text(json.dumps({
        "run_id": "20260518T120000-abcd1234",
        "started_at": "2026-05-18T12:00:00+00:00",
        "platform": "blogger", "mode": "publish", "status": None,
        "items": [reset_item], "flags": {},
    }))
    # The reset wipes the cursor's published_url snapshot; a fresh
    # "succeeded" therefore looks like a transition to succeeded again.
    flush_for(ckpt)
    ckpt.write_text(json.dumps({
        "run_id": "20260518T120000-abcd1234",
        "started_at": "2026-05-18T12:00:00+00:00",
        "platform": "blogger", "mode": "publish", "status": None,
        "items": [base_item], "flags": {},
    }))
    result = flush_for(ckpt)

    # Article UNIQUE caught the second insert.
    assert result.articles_inserted == 0
    assert result.skipped_due_to_dedup >= 1
    assert _count(EventStore(), "articles") == 1
    confirmed = _count_kind(EventStore(), "publish.confirmed")
    assert confirmed == 1


def _count_kind(store: EventStore, kind: str) -> int:
    with store.connect() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM events WHERE kind = ?", (kind,)
        ).fetchone()[0]


# ── Cross-source dedup: checkpoint wins, history skips article ────


def test_history_dedup_vs_checkpoint_same_live_url(tmp_path):
    live_url = "https://blog.example.org/cross-source"
    ckpt = _make_checkpoint(tmp_path, [{
        "id": "a", "status": "succeeded", "title": "t",
        "platform": "blogger", "adapter": None,
        "published_url": live_url,
        "error": None, "error_class": None,
        "completed_at": "2026-05-18T12:05:00+00:00",
        "payload": {"target_url": "https://example.com/a"},
    }])
    flush_for(ckpt)
    assert _count(EventStore(), "articles") == 1

    history = tmp_path / "publish-history.json"
    history.write_text(json.dumps([{
        "id": "h1",
        "target_url": "https://example.com/a",
        "platform": "blogger",
        "language": "en",
        "status": "published",
        "created_at": "2026-05-18 12:30",
        "article_urls": [live_url],
    }]))
    result = flush_for(history)

    assert result.articles_inserted == 0
    assert result.skipped_due_to_dedup == 1
    # No second article row landed.
    assert _count(EventStore(), "articles") == 1


# ── Layer 1: in-tx seen set guards against duplicates inside one flush ─


def test_duplicate_target_url_in_one_checkpoint_only_emits_one_intent(tmp_path):
    ckpt = _make_checkpoint(tmp_path, [
        {
            "id": "a", "status": "pending", "title": "t-a",
            "platform": "blogger", "adapter": None,
            "published_url": None, "error": None, "error_class": None,
            "completed_at": None,
            "payload": {"target_url": "https://example.com/dup"},
        },
        {
            "id": "b", "status": "pending", "title": "t-b",
            "platform": "blogger", "adapter": None,
            "published_url": None, "error": None, "error_class": None,
            "completed_at": None,
            "payload": {"target_url": "https://example.com/dup"},
        },
    ])

    flush_for(ckpt)

    assert _count_kind(EventStore(), "publish.intent") == 1


# ── Unknown source path → ProjectionError ─────────────────────────


def test_unknown_source_path_raises(tmp_path):
    bogus = tmp_path / "not-a-known-source.json"
    bogus.write_text("[]")
    with pytest.raises(ProjectionError):
        flush_for(bogus)


# ── Corrupted cursor state → ProjectionError, transaction rolls back ─


def test_corrupted_cursor_raises_projection_error_and_rolls_back(tmp_path):
    ckpt = _make_checkpoint(tmp_path, [{
        "id": "a", "status": "pending", "title": "t",
        "platform": "blogger", "adapter": None,
        "published_url": None, "error": None, "error_class": None,
        "completed_at": None,
        "payload": {"target_url": "https://example.com/a"},
    }])
    store = EventStore()
    # Seed a corrupted cursor row.
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO projection_cursor (source, last_seen_state_json) "
            "VALUES (?, ?)",
            (str(ckpt), "not-json"),
        )

    before_events = _count(store, "events")
    with pytest.raises(ProjectionError):
        flush_for(ckpt, store=store)

    # Transaction rolled back: events did not move.
    assert _count(store, "events") == before_events


def test_projection_result_is_immutable_dataclass():
    r = ProjectionResult(events_inserted=2, articles_inserted=1)
    with pytest.raises(Exception):
        r.events_inserted = 99  # type: ignore[misc]
