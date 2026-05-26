"""Plan 2026-05-26-002 Unit 3 — auth-type-aware overview rendering.

The 渠道綁定總覽 macro reads ``status.auth_type``:
  * ANON channels render a "免綁定 · 就緒" badge (no bound/unbound);
  * all channels with auth_type in (anon/token/token_fields/paste_blob/userpass)
    get a "Configure ↓" anchor that points to an inline form block rendered
    below (#channel-<name> div exists — no dead anchors);
  * mastodon renders a non-actionable deferred stub (no Configure ↓, no form);
  * the 6 explicitly-carded channels keep their bespoke accordion partials.

U3 landed: inline forms for cardless channels now exist in the page.
"""

from __future__ import annotations

import pytest

from backlink_publisher.config import Config
from webui_app import create_app
from webui_app.binding_status import get_channel_status


@pytest.fixture
def body():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client().get("/settings").get_data(as_text=True)


# ── status dict now carries auth_type ─────────────────────────────────────────


def test_get_channel_status_includes_auth_type():
    cfg = Config()
    assert get_channel_status("txtfyi", cfg)["auth_type"] == "anon"
    assert get_channel_status("csdn", cfg)["auth_type"] == "paste_blob"
    assert get_channel_status("blogger", cfg)["auth_type"] == "oauth"


# ── ANON ready badge ──────────────────────────────────────────────────────────


def test_anon_channel_renders_ready_badge(body):
    assert "免綁定 · 就緒" in body


# ── inline form anchors (U3) ──────────────────────────────────────────────────


@pytest.mark.parametrize("cardless", [
    "csdn",       # paste_blob
    "txtfyi",     # anon
    "rentry",     # anon
    "livejournal",  # userpass
    "wordpresscom",  # token_fields
])
def test_cardless_channel_has_configure_anchor_and_form(body, cardless):
    """After U3, cardless channels emit a Configure ↓ anchor AND an inline
    form block — no dead anchors."""
    assert f'href="#channel-{cardless}"' in body
    assert f'id="channel-{cardless}"' in body


@pytest.mark.parametrize("carded", ["blogger", "medium", "velog", "ghpages", "devto", "notion"])
def test_carded_channel_keeps_configure_anchor(body, carded):
    """The 6 channels with a per-channel partial keep their Configure ↓."""
    assert f'href="#channel-{carded}"' in body


# ── mastodon deferred stub ────────────────────────────────────────────────────


def test_mastodon_renders_deferred_stub_no_dead_anchor(body):
    assert "即將支持" in body
    assert 'href="#channel-mastodon"' not in body
