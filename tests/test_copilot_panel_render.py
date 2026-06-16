"""Server-side render tests for the Copilot side panel (Plan U4).

The panel is a self-contained partial ({% include '_copilot_panel.html' %})
mounted on five top-level pages. JS behaviour (toggle, fetch, dynamic rows) is
deferred to a lightweight DOM smoke / E2E; these assertions lock the
server-rendered contract the panel JS depends on:
  * the panel + launcher mount on every page,
  * each page carries exactly one csrf-token meta (base.html / standalone /
    the sites.html addition) — no duplicate injected by the partial,
  * the copilot.css + copilot.js (ES module) asset tags are wired per page,
  * the interaction-state containers + locked Q&A markup are present,
  * no inline on* handler sneaks in via the included markup.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from webui_app import create_app

# url -> the template that backs it. All five must mount the panel.
PAGE_ROUTES = {
    "/": "index.html",
    "/settings": "settings.html",
    "/ce:equity-ledger": "equity_ledger.html",
    "/ce:health": "health.html",
    "/sites": "sites.html",
}

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "webui_app" / "templates"


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


def _body(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp.data.decode("utf-8")


# ── mount + assets on every page ────────────────────────────────────────────

@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_panel_mounts_on_every_page(client, url):
    body = _body(client, url)
    assert 'id="copilotPanel"' in body, f"panel missing on {url}"
    assert 'id="copilotToggle"' in body, f"launcher missing on {url}"


@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_panel_assets_wired_on_every_page(client, url):
    body = _body(client, url)
    # both asset tags ship with the cache-busting v= query (url_for(..., v=asset_version))
    assert "css/copilot.css" in body, f"copilot.css link missing on {url}"
    assert "js/copilot.js" in body, f"copilot.js script missing on {url}"
    # the JS is loaded as an ES module (it imports lib/api.js, lib/dom.js)
    assert re.search(r'<script[^>]+type="module"[^>]+js/copilot\.js', body) \
        or re.search(r'<script[^>]+js/copilot\.js[^>]+type="module"', body), \
        f"copilot.js must load as an ES module on {url}"


@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_exactly_one_csrf_token_meta_per_page(client, url):
    """Edge case incl. sites.html (no native meta before U4): the partial must
    NOT inject a duplicate — base.html / standalone heads own the single meta."""
    body = _body(client, url)
    count = body.count('name="csrf-token"')
    assert count == 1, f"{url} must have exactly one csrf-token meta, got {count}"


# ── interaction-state + locked Q&A markup ───────────────────────────────────

@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_interaction_state_containers_present(client, url):
    body = _body(client, url)
    # loading / error / empty / degraded / findings list / tool-status — the JS
    # toggles these; their containers must exist server-side.
    for marker in (
        'id="copilotLoading"',
        'id="copilotError"',
        'id="copilotEmpty"',
        'id="copilotDegraded"',
        'id="copilotFindings"',
        'id="copilotToolStatus"',
    ):
        assert marker in body, f"{marker} missing on {url}"


@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_locked_qa_state_present(client, url):
    """R21 locked state: the deterministic list ships, the Q&A area is locked
    (dark in v1) with a key-binding link — present on every page."""
    body = _body(client, url)
    assert "copilot-qa--locked" in body, f"locked Q&A markup missing on {url}"
    assert "/settings" in body  # the 绑定金钥 link target


@pytest.mark.parametrize("url", list(PAGE_ROUTES))
def test_no_inline_event_handlers_with_panel(client, url):
    """The panel is included into index.html (which has a no-inline-handler
    guard) and four other pages — assert the included markup added none."""
    body = _body(client, url)
    assert not re.search(r'\son(click|change|submit|input|keyup)=', body), \
        f"an inline on* handler is present on {url}"


# ── source-level: every target template includes the partial ────────────────

@pytest.mark.parametrize("tpl", list(PAGE_ROUTES.values()))
def test_template_source_includes_partial(tpl):
    src = (TEMPLATES_DIR / tpl).read_text(encoding="utf-8")
    assert "{% include '_copilot_panel.html' %}" in src, \
        f"{tpl} does not include the copilot panel partial"


def test_partial_is_self_contained():
    """The partial ships its own css link + ES-module script so a single
    include fully wires the panel (no per-page head edits required)."""
    src = (TEMPLATES_DIR / "_copilot_panel.html").read_text(encoding="utf-8")
    assert "css/copilot.css" in src
    assert "js/copilot.js" in src
    assert 'type="module"' in src
    # zero inline handlers in the partial itself
    assert not re.search(r'\son(click|change|submit|input|keyup)=', src)
