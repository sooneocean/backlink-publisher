from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(tmp_path):
    old = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
    try:
        import webui_store
        from webui_app import create_app

        webui_store._refresh_paths()
        app = create_app(start_scheduler=False)
        app.config["TESTING"] = True
        app.config["CSRF_ENABLED"] = False
        app.config["WTF_CSRF_ENABLED"] = False
        yield app
    finally:
        if old is None:
            os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
        else:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old
        import webui_store

        webui_store._refresh_paths()


@pytest.fixture
def client(app):
    return app.test_client()


def _profile() -> dict:
    return {
        "ok": True,
        "official_url": "https://example.com/",
        "target_url": "https://example.com/",
        "main_url": "https://example.com",
        "category_url": None,
        "work_url": None,
        "main_domain": "https://example.com",
        "language": "zh-CN",
        "title": "Example",
    }


def _eligibility() -> list[dict]:
    return [
        {
            "slug": "blogger",
            "display_name": "Blogger",
            "eligible": True,
            "selected": True,
            "reason": "eligible",
            "dofollow": True,
            "auth_type": "oauth",
        },
        {
            "slug": "devto",
            "display_name": "Dev.to",
            "eligible": False,
            "selected": False,
            "reason": "nofollow_only",
            "dofollow": False,
            "auth_type": "token",
        },
    ]


def test_get_official_url_page_returns_200(client):
    resp = client.get("/official-url")

    assert resp.status_code == 200
    assert b"Official URL Intake" in resp.data
    assert b"Draft-first queue" in resp.data
    assert b"Not live dofollow proof" in resp.data


def test_post_official_url_preview_renders_profile(client, monkeypatch):
    import webui_app.routes.official_url as route

    monkeypatch.setattr(route, "build_target_profile", lambda raw_url: _profile())
    monkeypatch.setattr(route, "resolve_channel_eligibility", lambda: _eligibility())

    resp = client.post(
        "/official-url",
        data={"action": "preview", "official_url": "https://example.com/"},
    )

    assert resp.status_code == 200
    assert b"Example" in resp.data
    assert b"Blocked channels" in resp.data
    assert b"nofollow_only" in resp.data


def test_post_official_url_enqueue_adds_queue_item(client, monkeypatch):
    import webui_app.routes.official_url as route
    from webui_store import queue_store

    monkeypatch.setattr(route, "build_target_profile", lambda raw_url: _profile())
    monkeypatch.setattr(route, "resolve_channel_eligibility", lambda: _eligibility())

    resp = client.post(
        "/official-url",
        data={
            "action": "enqueue",
            "official_url": "https://example.com/",
            "channels": ["blogger"],
        },
    )

    assert resp.status_code == 200
    assert b"Queued draft tasks" in resp.data
    tasks = queue_store.load()
    assert len(tasks) == 1
    assert tasks[0]["config"]["publish_mode"] == "draft"
    assert tasks[0]["config"]["source"] == "official_url_intake"


def test_post_official_url_requires_csrf_when_enabled(tmp_path):
    old = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
    try:
        import webui_store
        from webui_app import create_app

        webui_store._refresh_paths()
        app = create_app(start_scheduler=False)
        app.config["TESTING"] = True
        with app.test_client() as client:
            resp = client.post(
                "/official-url",
                data={"action": "preview", "official_url": "https://example.com/"},
            )
            assert resp.status_code == 403
    finally:
        if old is None:
            os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
        else:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old
        import webui_store

        webui_store._refresh_paths()
