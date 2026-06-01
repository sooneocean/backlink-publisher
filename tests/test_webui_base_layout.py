"""base.html layout + static lib/ + tokens.css — Plan 2026-06-01-007 Unit 1.

Validates the foundation scaffolding before any page extends it: the shared lib
assets and token source are served, and a child-of-base renders exactly one head
with one CSRF meta and a CLASSIC (non-defer/non-module) Bootstrap script.
"""

from __future__ import annotations

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


# ── shared lib + token assets are served ─────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/static/js/lib/api.js",
    "/static/js/lib/dom.js",
    "/static/js/lib/profiles.js",
])
def test_lib_module_served(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    assert "javascript" in resp.content_type


def test_tokens_css_served(client):
    resp = client.get("/static/css/tokens.css")
    assert resp.status_code == 200
    assert "text/css" in resp.content_type


def test_tokens_define_brand_primary(client):
    """tokens.css is the single source for the duplicated brand vars."""
    body = client.get("/static/css/tokens.css").data.decode()
    assert "--primary:" in body
    assert "--gradient:" in body


# ── a child-of-base renders one well-formed head ─────────────────────────────

_CHILD = (
    "{% extends 'base.html' %}"
    "{% block content %}<p id='probe'>hi</p>{% endblock %}"
    "{% block page_module %}"
    "<script type='module' src='{{ url_for(\"static\", filename=\"js/lib/api.js\") }}'></script>"
    "{% endblock %}"
)


def _render_child(app):
    with app.test_request_context("/"):
        return app.jinja_env.from_string(_CHILD).render()


def test_child_of_base_has_single_head_and_one_csrf_meta(app):
    html = _render_child(app)
    assert html.count("<head>") == 1
    assert html.count('name="csrf-token"') == 1
    assert "bootstrap@5.3.0" in html  # Bootstrap CDN present from base
    assert 'id=\'probe\'' in html or 'id="probe"' in html


def test_base_bootstrap_script_is_classic_not_deferred(app):
    """The Bootstrap bundle must stay a classic, non-defer, non-module script so
    window.bootstrap is defined before any deferred page module runs."""
    html = _render_child(app)
    # locate the bootstrap bundle <script> tag
    marker = "bootstrap.bundle.min.js"
    assert marker in html
    start = html.rindex("<script", 0, html.index(marker))
    tag = html[start:html.index(">", html.index(marker))]
    assert "defer" not in tag
    assert "type=" not in tag  # not type="module"


def test_child_emits_single_module_entry(app):
    html = _render_child(app)
    assert html.count('type=\'module\'') + html.count('type="module"') == 1


def test_static_refs_carry_cache_bust_version(app):
    """base.html stamps ?v=<asset_version> on its static refs."""
    html = _render_child(app)
    assert "tokens.css?v=" in html
