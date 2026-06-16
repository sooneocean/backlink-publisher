"""Characterization of the /ce:plan input-assembly + validation contract.

Plan 2026-06-01-001 Unit 2 (test-first). Pins the CURRENT accept/reject and
config-assembly behavior of ``routes/pipeline.py::ce_plan`` BEFORE the logic is
extracted into ``services/pipeline_service.py``. The extracted service MUST
reproduce these outcomes exactly (behavior-preserving refactor).

Locked contract:
- empty main_url            -> error "请输入主网域", no session config written
- non-https main_url        -> error contains "必须 https"
- valid https main_url      -> session['config'] assembled with detected
                               language/platform, url_mode='C', publish_mode='publish'

Language accept/reject is enforced downstream by the engine's
``validate_input_payload`` (SUPPORTED_LANGUAGES = {zh-CN, ru, en, ko}); this is a
PRE-EXISTING contract (ja/zh-TW/es/de/fr already rejected today) and is pinned
separately at the schema layer, not introduced by Unit 2.
"""

from __future__ import annotations

import pytest

from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    app = create_app()
    app.config["TESTING"] = True
    app.config.update(CSRF_ENABLED=False)  # isolate route logic from the global CSRF guard
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_empty_main_url_rejected(client):
    """Empty main domain -> friendly error, never reaches network/session."""
    resp = client.post("/ce:plan", data={})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "请输入主网域" in body
    with client.session_transaction() as sess:
        assert "config" not in sess


def test_non_https_main_url_rejected(client):
    """http:// main domain -> https field error, no config persisted."""
    resp = client.post("/ce:plan", data={"main_url": "http://example.com"})
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "必须 https" in body
    with client.session_transaction() as sess:
        assert "config" not in sess


def test_valid_https_assembles_config(client, monkeypatch):
    """Valid https main_url -> config assembled with detected language/platform.

    Network-touching helpers are stubbed so the test isolates the
    input-assembly contract (the part Unit 2 relocates into the service).
    """
    from webui_app.routes import pipeline as pipeline_mod

    # SSRF/url-verify gate: pass through clean.
    monkeypatch.setattr(
        pipeline_mod, "_verify_urls_or_error", lambda urls, label: (urls, None)
    )
    # Parallel metadata + TDK fetch: deterministic success stubs.
    monkeypatch.setattr(
        pipeline_mod, "fetch_url_metadata",
        lambda url: {"status": "success", "url": url, "title": "T"},
    )
    monkeypatch.setattr(
        pipeline_mod, "fetch_full_tdk",
        lambda url: {"status": "success", "suggested_anchors": ["a1"]},
    )

    resp = client.post("/ce:plan", data={"main_url": "https://example.cn/work"})
    assert resp.status_code == 200

    with client.session_transaction() as sess:
        config = sess.get("config")
        assert config is not None, "valid plan must persist config to session"
        assert config["target_url"] == "https://example.cn/work"
        assert config["main_domain"] == "https://example.cn"
        # .cn -> zh-CN per detect_language (a SUPPORTED language)
        assert config["target_language"] == "zh-CN"
        assert config["url_mode"] == "C"
        assert config["publish_mode"] == "publish"
        assert config["suggested_anchors"] == ["a1"]
