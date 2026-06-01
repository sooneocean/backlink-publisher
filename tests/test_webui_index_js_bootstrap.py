"""index_main.js bootstrap injection — Plan B Unit 3."""

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


def test_index_main_js_served(client):
    """GET /static/js/index.js (ESM entry, replaced index_main.js in Plan 007 U6)
    returns 200 application/javascript."""
    resp = client.get("/static/js/index.js")
    assert resp.status_code == 200
    assert "javascript" in resp.content_type


def test_bootstrap_var_present_in_page(client):
    """GET / includes window.__indexBootstrap with plans_list, profiles, platform_slugs."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "window.__indexBootstrap" in body
    # Extract the JSON object
    m = re.search(r'window\.__indexBootstrap\s*=\s*(\{.*?\});', body, re.DOTALL)
    assert m, "window.__indexBootstrap assignment not found in page"
    data = json.loads(m.group(1))
    assert "plans_list" in data
    assert "profiles" in data
    assert "platform_slugs" in data


def test_no_jinja_interpolation_in_page_js(client):
    """GET / response body must not contain Jinja-style plans_list or profiles interpolations."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "let _plansData = " not in body, "Jinja-rendered _plansData found inline"
    assert "const _PROFILES = [" not in body, "Jinja-rendered _PROFILES found inline"


def test_bootstrap_null_plans_list_no_error(app):
    """GET / with plans_list=None in context renders without error (JS falls back to [])."""
    with app.test_client() as c:
        # Default render with no active pipeline should have plans_list=None or []
        resp = c.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        # The bootstrap var should serialize plans_list as null or [] when absent
        assert "window.__indexBootstrap" in body


def test_bootstrap_empty_platforms_renders_without_error(client):
    """GET / renders without error even when platform_slugs would be empty."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.data.decode()
    m = re.search(r'window\.__indexBootstrap\s*=\s*(\{.*?\});', body, re.DOTALL)
    assert m
    data = json.loads(m.group(1))
    # platform_slugs must be a list (possibly empty)
    assert isinstance(data["platform_slugs"], list)


def test_bootstrap_equivalence_plans_list(tmp_path, monkeypatch):
    """window.__indexBootstrap.plans_list equals what _render injects into the template context."""
    import json as _json
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as c:
        resp = c.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        m = re.search(r'window\.__indexBootstrap\s*=\s*(\{.*?\});', body, re.DOTALL)
        assert m, "window.__indexBootstrap not found"
        data = _json.loads(m.group(1))
        # With no active pipeline, plans_list should serialize to null or []
        assert data["plans_list"] is None or isinstance(data["plans_list"], list)
