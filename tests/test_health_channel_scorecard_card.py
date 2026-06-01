"""/ce:health per-channel value scorecard card (Plan 2026-06-01-005, Unit 8 MVP).

GET-only, fail-open, advisory. The card pairs declared registry signals with
measured liveness as a signal vector; the GA4/GSC/AI axes render inert.
"""

from __future__ import annotations

import json

import pytest

from backlink_publisher.events import EventStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    from webui_app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_card_renders_on_get_with_inert_axes(client):
    # GET needs no CSRF token (read-only dashboard).
    resp = client.get("/ce:health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Per-channel value scorecard" in body
    # Deferred axes are shown as inert, never a misleading zero.
    assert "inert (not landed)" in body
    # No composite — the card footer states it is a signal vector.
    assert "no composite score" in body.lower()


def test_seeded_channel_appears_with_declared_signal(client):
    EventStore().add_article({
        "target_urls_json": json.dumps(["https://site.com/p"]),
        "live_url": "https://medium.com/post1",
    })
    from webui_store import history_store
    history_store.save([{
        "id": "h1", "platform": "medium", "target_url": "https://site.com/p",
        "article_urls": ["https://medium.com/post1"], "status": "published",
    }])
    body = client.get("/ce:health").get_data(as_text=True)
    assert "medium" in body
    assert "dofollow" in body  # medium's declared dofollow status renders


def test_card_fails_open_when_engine_raises(client, monkeypatch):
    # Fail-open: if the scorecard engine raises, the dashboard must not 500.
    import backlink_publisher.scorecard as sc

    def _boom(*a, **k):
        raise RuntimeError("scorecard boom")

    monkeypatch.setattr(sc, "build_channel_scorecard", _boom)
    resp = client.get("/ce:health")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # The page still renders; the card degrades to its empty state.
    assert "Per-channel value scorecard" in body
    assert "No channels to score yet" in body
