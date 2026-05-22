"""settings.html template split — Plan B2 (CSS, JS, card partials)."""

from __future__ import annotations

import json
import re

import pytest

from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


# ── Unit 1: CSS ──────────────────────────────────────────────────────────────

def test_settings_css_served(client):
    """GET /static/css/settings.css returns 200 text/css."""
    resp = client.get("/static/css/settings.css")
    assert resp.status_code == 200
    assert "text/css" in resp.content_type


def test_settings_page_links_to_css(client):
    """GET /settings includes <link> pointing to css/settings.css."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "css/settings.css" in resp.data.decode()


def test_settings_page_has_no_inline_style(client):
    """GET /settings response has no inline <style> element."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "<style>" not in resp.data.decode()


# ── Unit 2: JS ───────────────────────────────────────────────────────────────

def test_settings_main_js_served(client):
    """GET /static/js/settings_main.js returns 200 application/javascript."""
    resp = client.get("/static/js/settings_main.js")
    assert resp.status_code == 200
    assert "javascript" in resp.content_type


def test_settings_bootstrap_var_present(client):
    """GET /settings includes window.__settingsBootstrap with plans_list and profiles."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "window.__settingsBootstrap" in body
    m = re.search(r'window\.__settingsBootstrap\s*=\s*(\{.*?\});', body, re.DOTALL)
    assert m, "window.__settingsBootstrap assignment not found"
    data = json.loads(m.group(1))
    assert "plans_list" in data
    assert "profiles" in data


def test_settings_no_jinja_interpolation_in_page(client):
    """GET /settings must not contain inline Jinja-rendered _plansData or _PROFILES."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "let _plansData = " not in body
    assert "const _PROFILES = [" not in body


# ── Unit 3: Card partials ────────────────────────────────────────────────────

def test_settings_renders_llm_integration_section(client):
    """GET /settings renders LLM integration card from partial."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "进阶 LLM 整合" in body


def test_settings_renders_diagnostics_section(client):
    """GET /settings renders diagnostics console card from partial."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "生成诊断控制台" in body


def test_settings_renders_banner_section(client):
    """GET /settings renders AI banner card from partial."""
    resp = client.get("/settings")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "AI Banner" in body


def test_settings_html_final_size():
    """settings.html must be ≤400 lines after all splits (R3)."""
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[1]
        / "webui_app" / "templates" / "settings.html"
    ).read_text(encoding="utf-8")
    lines = len(src.splitlines())
    assert lines <= 400, f"settings.html is {lines} lines, expected ≤400"
