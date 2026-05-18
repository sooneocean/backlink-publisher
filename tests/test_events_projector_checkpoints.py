"""Tests for the checkpoint reducer in ``events.projector``.

Test-first per plan §U4 Execution note. Each test seeds a checkpoint
JSON file under ``tmp_path``, calls ``flush_for(path)``, then asserts
event + article rows landed as documented in the design notes.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from backlink_publisher.events import EventStore, flush_for


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _write_checkpoint(
    path: Path,
    *,
    run_id: str = "20260518T120000-abcd1234",
    started_at: str = "2026-05-18T12:00:00+00:00",
    items: list[dict[str, Any]] | None = None,
) -> Path:
    payload = {
        "run_id": run_id,
        "started_at": started_at,
        "platform": "blogger",
        "mode": "publish",
        "status": None,
        "items": items or [],
        "flags": {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_pending(item_id: str, target_url: str, **overrides) -> dict[str, Any]:
    base = {
        "id": item_id,
        "status": "pending",
        "title": f"title-{item_id}",
        "platform": "blogger",
        "adapter": None,
        "published_url": None,
        "error": None,
        "error_class": None,
        "completed_at": None,
        "payload": {"target_url": target_url, "id": item_id},
    }
    base.update(overrides)
    return base


def _query_events(store: EventStore) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            "SELECT id, kind, run_id, target_url, host, article_id, "
            "payload_json, ts_raw, ts_utc FROM events ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def _query_articles(store: EventStore) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            "SELECT article_id, body, anchors_json, target_urls_json, lang, "
            "host, live_url, published_at_raw, published_at_utc, run_id "
            "FROM articles ORDER BY article_id"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Scenario 1: new run with 3 pending → 3 publish.intent ─────────


def test_checkpoint_happy_path_emits_publish_intent_for_each_pending(tmp_path):
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json",
        items=[
            _make_pending("a", "https://example.com/a"),
            _make_pending("b", "https://example.com/b"),
            _make_pending("c", "https://example.com/c"),
        ],
    )

    result = flush_for(ckpt)

    assert result.events_inserted == 3
    assert result.articles_inserted == 0
    assert result.cursor_updated is True

    events = _query_events(EventStore())
    assert [e["kind"] for e in events] == [
        "publish.intent", "publish.intent", "publish.intent",
    ]
    assert {e["target_url"] for e in events} == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }
    assert {e["host"] for e in events} == {"example.com"}
    assert all(e["run_id"] == "20260518T120000-abcd1234" for e in events)
    assert all(e["ts_raw"] == "2026-05-18T12:00:00+00:00" for e in events)


# ── Scenario 2: pending → succeeded → publish.confirmed + article ──


def test_pending_to_succeeded_emits_confirmed_and_article(tmp_path):
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json",
        items=[
            _make_pending(
                "a", "https://example.com/a",
                payload={
                    "target_url": "https://example.com/a",
                    "content_markdown": "Hello [anchor](https://example.com/a).",
                    "lang": "en",
                    "links": [
                        {"kind": "main_domain", "anchor": "Example"},
                        {"kind": "target", "anchor": "anchor"},
                        {"kind": "filler", "anchor": "ignore-me"},
                    ],
                },
            ),
        ],
    )
    flush_for(ckpt)  # intent
    items = json.loads(ckpt.read_text())["items"]
    items[0].update(
        status="succeeded",
        published_url="https://blog.example.org/post-1",
        completed_at="2026-05-18T12:05:00+00:00",
    )
    ckpt.write_text(json.dumps({
        "run_id": "20260518T120000-abcd1234",
        "started_at": "2026-05-18T12:00:00+00:00",
        "platform": "blogger", "mode": "publish", "status": None,
        "items": items, "flags": {},
    }))

    result = flush_for(ckpt)

    assert result.articles_inserted == 1
    assert result.events_inserted == 1
    events = _query_events(EventStore())
    confirmed = [e for e in events if e["kind"] == "publish.confirmed"]
    assert len(confirmed) == 1
    payload = json.loads(confirmed[0]["payload_json"])
    assert payload["live_url"] == "https://blog.example.org/post-1"
    assert payload["live_url_canonical"] == "https://blog.example.org/post-1"

    articles = _query_articles(EventStore())
    assert len(articles) == 1
    art = articles[0]
    assert art["body"] == "Hello [anchor](https://example.com/a)."
    assert art["lang"] == "en"
    assert art["host"] == "blog.example.org"
    assert art["live_url"] == "https://blog.example.org/post-1"
    anchors = json.loads(art["anchors_json"])
    assert [a["kind"] for a in anchors] == ["main_domain", "target"]
    targets = json.loads(art["target_urls_json"])
    assert targets == ["https://example.com/a"]


# ── Scenario 3: pending → failed → publish.failed with scrub ──────


def test_pending_to_failed_emits_scrubbed_publish_failed(tmp_path):
    leaky_message = (
        "Auth failed: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 "
        "and AIzaSyABCDEF0123456789ABCDEF0123456789AB rejected"
    )
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json",
        items=[_make_pending("a", "https://example.com/a")],
    )
    flush_for(ckpt)  # intent

    items = json.loads(ckpt.read_text())["items"]
    items[0].update(
        status="failed",
        error=leaky_message,
        error_class="AuthError",
        completed_at="2026-05-18T12:05:00+00:00",
    )
    ckpt.write_text(json.dumps({
        "run_id": "20260518T120000-abcd1234",
        "started_at": "2026-05-18T12:00:00+00:00",
        "platform": "blogger", "mode": "publish", "status": None,
        "items": items, "flags": {},
    }))

    flush_for(ckpt)

    failed = [e for e in _query_events(EventStore()) if e["kind"] == "publish.failed"]
    assert len(failed) == 1
    payload = json.loads(failed[0]["payload_json"])
    assert payload["error_class"] == "AuthError"
    # secret shapes are redacted
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in payload["error_message_clean"]
    assert "AIzaSyABCDEF" not in payload["error_message_clean"]
    assert "<REDACTED>" in payload["error_message_clean"]
    # scrub_hits captured
    assert payload["scrub_hits"]


# ── Scenario 4: empty published_url → article.live_url NULL ───────


def test_succeeded_with_empty_published_url_normalises_to_null_live_url(tmp_path):
    item = _make_pending("a", "https://example.com/a")
    item.update(
        status="succeeded",
        published_url="",  # empty string
        completed_at="2026-05-18T12:05:00+00:00",
    )
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json", items=[item]
    )

    flush_for(ckpt)

    articles = _query_articles(EventStore())
    assert len(articles) == 1
    assert articles[0]["live_url"] is None


# ── Scenario 5: payload.links empty → anchors_json '[]' ───────────


def test_succeeded_with_no_links_yields_empty_anchors_json(tmp_path):
    item = _make_pending(
        "a", "https://example.com/a",
        payload={"target_url": "https://example.com/a", "links": []},
    )
    item.update(
        status="succeeded",
        published_url="https://blog.example.org/p",
        completed_at="2026-05-18T12:05:00+00:00",
    )
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json", items=[item]
    )

    flush_for(ckpt)

    articles = _query_articles(EventStore())
    assert articles[0]["anchors_json"] == "[]"


# ── Scenario 6: started_at timestamp lands on both columns ────────


def test_ts_raw_preserves_offset_and_ts_utc_normalises_to_utc(tmp_path):
    ckpt = _write_checkpoint(
        tmp_path / "20260518T120000-abcd1234.json",
        started_at="2026-05-18T20:00:00+08:00",
        items=[_make_pending("a", "https://example.com/a")],
    )

    flush_for(ckpt)

    events = _query_events(EventStore())
    assert len(events) == 1
    assert events[0]["ts_raw"] == "2026-05-18T20:00:00+08:00"
    # 20:00 +08:00 == 12:00 UTC
    assert events[0]["ts_utc"] == "2026-05-18T12:00:00+00:00"
