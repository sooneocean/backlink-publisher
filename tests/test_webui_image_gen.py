"""Tests for WebUI image_gen route + status helper — Plan 2026-05-20-001 Unit 6."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    from webui_app import create_app
    a = create_app(start_scheduler=False)
    a.config['CSRF_ENABLED'] = False
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_image_gen_config(config_dir):
    (config_dir / "config.toml").write_text(
        '[image_gen]\n'
        'base_url = "https://gateway.example.com/v1"\n'
        'model = "banner-m"\n'
        'banner_size = "1200x630"\n'
    )


def _seed_token(config_dir):
    from backlink_publisher._util.secrets import write_frw_token
    write_frw_token("sk_webui_test")


# ── /settings page renders image_gen card ───────────────────────────────────


def test_settings_page_shows_image_gen_card_when_configured(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed_image_gen_config(tmp_path)

    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI Banner 图生成" in body
    assert "gateway.example.com" in body
    assert "1200x630" in body
    assert "frw-token.json" in body


def test_settings_page_shows_unconfigured_message(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    # NO config.toml → image_gen section absent

    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "AI Banner 图生成" in body
    assert "[image_gen]</code> section 未配置" in body


# ── /settings/test-image-gen route ──────────────────────────────────────────


def test_test_image_gen_no_section(client, tmp_path, monkeypatch):
    """No [image_gen] section → JSON error, no crash."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    resp = client.post("/settings/test-image-gen")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "no_image_gen_section" in data["error"]


def test_test_image_gen_no_token(client, tmp_path, monkeypatch):
    """Section configured but frw-token.json missing → operator-actionable error."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed_image_gen_config(tmp_path)

    resp = client.post("/settings/test-image-gen")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "no_token" in data["error"] or "FRW token not found" in data["error"]


def test_test_image_gen_success(client, tmp_path, monkeypatch):
    """200 from /models → ok with model_count."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed_image_gen_config(tmp_path)
    _seed_token(tmp_path)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "data": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
    }
    fake_resp.text = "ok"

    with patch("webui_app.routes.image_gen.requests.get", return_value=fake_resp):
        resp = client.post("/settings/test-image-gen")

    data = resp.get_json()
    assert data["ok"] is True
    assert data["model_count"] == 3
    assert data["configured_model"] == "banner-m"


def test_test_image_gen_auth_failed(client, tmp_path, monkeypatch):
    """401 → auth_failed mentions frw-login (rotate path)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed_image_gen_config(tmp_path)
    _seed_token(tmp_path)

    fake_resp = MagicMock()
    fake_resp.status_code = 401
    fake_resp.text = "invalid_api_key"
    fake_resp.json.return_value = {"error": "bad"}

    with patch("webui_app.routes.image_gen.requests.get", return_value=fake_resp):
        resp = client.post("/settings/test-image-gen")

    data = resp.get_json()
    assert data["ok"] is False
    assert "frw-login" in data["error"]


def test_test_image_gen_network_error(client, tmp_path, monkeypatch):
    """Network exception → ok=False with error, no 500."""
    import requests

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed_image_gen_config(tmp_path)
    _seed_token(tmp_path)

    with patch(
        "webui_app.routes.image_gen.requests.get",
        side_effect=requests.ConnectionError("dns fail"),
    ):
        resp = client.post("/settings/test-image-gen")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is False
    assert "dns fail" in data["error"] or "network" in data["error"]


# ── _image_gen_status helper ─────────────────────────────────────────────────


def test_image_gen_status_helper_reports_token_presence(tmp_path, monkeypatch):
    from webui_app.helpers import _image_gen_status
    from backlink_publisher.config import load_config

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    cfg = load_config()
    status = _image_gen_status(cfg)
    assert status["configured"] is False
    assert status["token_present"] is False

    from backlink_publisher._util.secrets import write_frw_token
    write_frw_token("sk_x")

    status = _image_gen_status(cfg)
    assert status["token_present"] is True
    assert status["token_mtime"]  # non-empty timestamp string
