"""Tests for the draft-queue reducer in ``events.projector``.

State machine (plan §U4 Design notes):
    NEW + drafted        → draft.created
    drafted → scheduled  → draft.scheduled
    (any) → published    → publish.confirmed + article  (no fresh draft.*)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import EventStore, flush_for


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _write_drafts(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _query_events(store: EventStore) -> list[dict[str, Any]]:
    import sqlite3
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT kind, target_url, host, article_id, payload_json "
            "FROM events ORDER BY id"
        )]


def test_drafts_state_machine_drafted_then_scheduled_then_published(tmp_path):
    path = tmp_path / "draft-queue.json"
    _write_drafts(path, [
        {
            "id": "d1",
            "status": "drafted",
            "target_url": "https://example.com/d1",
            "language": "en",
        }
    ])
    flush_for(path)

    events = _query_events(EventStore())
    assert [e["kind"] for e in events] == ["draft.created"]
    assert json.loads(events[0]["payload_json"])["draft_id"] == "d1"

    # drafted → scheduled
    _write_drafts(path, [
        {
            "id": "d1",
            "status": "scheduled",
            "target_url": "https://example.com/d1",
            "language": "en",
        }
    ])
    flush_for(path)
    events = _query_events(EventStore())
    assert [e["kind"] for e in events] == ["draft.created", "draft.scheduled"]

    # scheduled → published with article URL
    _write_drafts(path, [
        {
            "id": "d1",
            "status": "published",
            "target_url": "https://example.com/d1",
            "language": "en",
            "article_urls": ["https://blog.example.org/post-d1"],
            "published_at": "2026-05-18 13:00",
        }
    ])
    flush_for(path)

    events = _query_events(EventStore())
    kinds = [e["kind"] for e in events]
    assert kinds == ["draft.created", "draft.scheduled", "publish.confirmed"]
    confirmed = events[-1]
    payload = json.loads(confirmed["payload_json"])
    assert payload["live_url"] == "https://blog.example.org/post-d1"
    assert payload["draft_id"] == "d1"
    assert confirmed["host"] == "blog.example.org"


def test_drafts_replay_emits_each_transition_only_once(tmp_path):
    """Idempotency: flushing twice on the same drafts state is a no-op."""
    path = tmp_path / "draft-queue.json"
    _write_drafts(path, [
        {
            "id": "d1",
            "status": "scheduled",
            "target_url": "https://example.com/d1",
            "language": "en",
        }
    ])
    flush_for(path)
    flush_for(path)

    events = _query_events(EventStore())
    # NEW + scheduled jumps straight to draft.scheduled (no spurious draft.created).
    assert [e["kind"] for e in events] == ["draft.scheduled"]
