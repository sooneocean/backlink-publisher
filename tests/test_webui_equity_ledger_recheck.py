"""Unit 6 — equity-ledger on-demand recheck POST.

One canonical target backed by two history rows: recheck must iterate both,
write each back via update_item, and recompute the target's row.
"""

import json

import pytest

from backlink_publisher.events import EventStore

T = "https://site.com/p"


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cache = tmp_path / "cache"
    cfg.mkdir()
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


def _seed_two_rows():
    store = EventStore()
    store.add_article({"target_urls_json": json.dumps([T]), "live_url": "https://medium.com/l1"})
    store.add_article({"target_urls_json": json.dumps([T]), "live_url": "https://blog.ex/l2"})
    from webui_store import history_store
    history_store.save([
        {"id": "h1", "platform": "medium", "target_url": T,
         "article_urls": ["https://medium.com/l1"], "status": "published_unverified",
         "title": "t"},
        {"id": "h2", "platform": "blogger", "target_url": T,
         "article_urls": ["https://blog.ex/l2"], "status": "published_unverified",
         "title": "t"},
    ])


def _post(client, target=T):
    return client.post(
        "/ce:equity-ledger/recheck",
        data=json.dumps({"target_url": target}),
        content_type="application/json",
    )


def test_recheck_both_rows_confirmed(client, monkeypatch):
    _seed_two_rows()
    # Default verify_fn routes through the shared probe_liveness engine
    # (Plan 2026-05-29-004 U2) — patch the underlying inspect_target_anchor.
    monkeypatch.setattr(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        lambda *a, **k: {
            "page_readable": True, "target_anchor_found": True,
            "target_is_nofollow": False, "target_rel": None,
            "target_anchor_text": None, "reason": None, "marker_present": None,
        },
    )
    resp = _post(client)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["summary"] == "2 confirmed, 0 failed, 0 skipped"
    # Both rows written back via update_item (status upgraded, verified_at set).
    from webui_store import history_store
    assert history_store.get_item("h1")["status"] == "published"
    assert history_store.get_item("h2")["status"] == "published"
    assert history_store.get_item("h1")["verified_at"]


def test_recheck_downgrade_reports_failed(client, monkeypatch):
    _seed_two_rows()
    monkeypatch.setattr(
        "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
        lambda *a, **k: {
            "page_readable": False, "target_anchor_found": False,
            "target_is_nofollow": False, "target_rel": None,
            "target_anchor_text": None, "reason": "http_404", "marker_present": None,
        },
    )
    resp = _post(client)
    body = resp.get_json()
    assert "2 failed" in body["summary"]
    from webui_store import history_store
    assert history_store.get_item("h1")["status"] == "failed"
    # Refreshed row reflects worst-status liveness.
    assert body["row"]["liveness"] == "failed"


def test_missing_target_400(client):
    resp = client.post("/ce:equity-ledger/recheck",
                       data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 400


def test_unknown_target_404(client):
    _seed_two_rows()
    resp = _post(client, target="https://nope.example/x")
    assert resp.status_code == 404
