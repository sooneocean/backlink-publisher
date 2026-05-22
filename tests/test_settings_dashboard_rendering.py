"""Unit 5 — Settings dashboard rendering (Plan 2026-05-19-006).

Verifies /settings GET renders the new "渠道綁定總覽" section with one
card per ``registered_platforms()`` entry, each carrying the
``data-channel`` attribute the Unit 5 JS binds against.

Companion: tests/test_generic_channel_api.py (Unit 4 — the API endpoints
the dashboard JS calls).
"""

from __future__ import annotations

import re

import pytest

from backlink_publisher.publishing.registry import registered_platforms
from webui_app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


class TestDashboardSection:
    """The dashboard section appears at the top of /settings."""

    def test_settings_page_renders_200(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_dashboard_section_heading_present(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        assert "渠道綁定總覽" in body

    def test_javascript_loaded(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        assert "/static/js/channel-binding.js" in body

    def test_csrf_meta_present(self, client):
        """JS reads csrf_token from <meta name="csrf-token"> — must exist."""
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        assert re.search(r'<meta\s+name="csrf-token"', body), body


class TestPerChannelCards:
    """Every registered platform must render one card with the data-channel
    attribute the JS uses for routing button clicks.
    """

    def _visible_channels(self):
        from webui_app.binding_status import HIDDEN_FROM_UI
        return [c for c in registered_platforms() if c not in HIDDEN_FROM_UI]

    def test_card_rendered_for_every_registered_channel(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        for channel in self._visible_channels():
            # data-channel="<name>" appears on the card div + each button.
            pattern = f'data-channel="{channel}"'
            assert pattern in body, (
                f"No dashboard card found for registered channel {channel!r}"
            )

    def test_each_card_has_verify_button(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        for channel in self._visible_channels():
            # Verify Token button per channel.
            assert re.search(
                rf'class="[^"]*dch-btn-verify[^"]*"[^>]*data-channel="{channel}"',
                body,
            ), f"No Verify button for {channel!r}"

    def test_each_card_has_dryrun_button(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        for channel in self._visible_channels():
            assert re.search(
                rf'class="[^"]*dch-btn-dryrun[^"]*"[^>]*data-channel="{channel}"',
                body,
            ), f"No Dry-Run button for {channel!r}"


class TestDofollowBadges:
    """Per-channel dofollow knowledge surfaces as a UI badge."""

    def test_telegraph_card_shows_dofollow_badge(self, client):
        """Telegraph is known dofollow per _DOFOLLOW_BY_CHANNEL."""
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        # Look for the telegraph card area + dofollow good badge nearby.
        # Crude but reliable for a 200-line section.
        assert "telegraph" in body
        assert 'badge-dofollow good' in body, (
            "Expected at least one dofollow badge for a dofollow-confirmed channel"
        )

    def test_dofollow_legend_classes_in_css(self, client):
        """All three dofollow badge styles are defined in the extracted CSS file (Plan B2 Unit 1)."""
        from pathlib import Path
        # CSS extracted to static/css/settings.css by Plan B2 Unit 1
        css_src = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "static" / "css" / "settings.css"
        ).read_text(encoding="utf-8")
        for css_class in ("badge-dofollow.good", "badge-dofollow.weak", "badge-dofollow.unknown"):
            assert css_class in css_src, f"Missing CSS class {css_class} in settings.css"


class TestDashboardDriftWithRegistry:
    """Drift between registry and dashboard cards must not happen silently.

    Per solution lesson `invert-drift-check-when-invariant-becomes-dynamic`:
    enforce at test-time with lazy import, never module-top-level assert.
    """

    def test_dashboard_card_count_equals_registered_platform_count(self, client):
        from webui_app.binding_status import HIDDEN_FROM_UI

        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        # Count of `dashboard-channel-card` outer divs.
        card_count = body.count('class="dashboard-channel-card"')
        # Some adapters are intentionally hidden from the UI (e.g. retired
        # channels whose source stays in repo for CLI use). The dashboard
        # shows every registered platform EXCEPT those.
        expected = len(registered_platforms()) - len(HIDDEN_FROM_UI)
        assert card_count == expected, (
            f"Dashboard cards ({card_count}) != registered platforms "
            f"minus hidden ({expected}). Drift detected — investigate "
            f"_settings_context.dashboard_channels and the card macro."
        )


class TestGracefulDegradation:
    """If status dispatch raises, /settings must still render (dashboard
    section omitted) — solution lesson: dashboard is summary, not load-bearing.
    """

    def test_settings_renders_when_dashboard_context_empty(self, client, monkeypatch):
        """Simulate context with empty dashboard list — page must still 200."""
        # We patch get_channel_status to raise; the helper try/except in
        # _settings_context already produces dashboard_channels=[] on failure.
        from webui_app import binding_status

        def _boom(name, config):
            raise RuntimeError("intentional test failure")

        monkeypatch.setattr(binding_status, "get_channel_status", _boom)
        resp = client.get("/settings")
        assert resp.status_code == 200
        # Dashboard heading should not appear when channels list is empty.
        body = resp.get_data(as_text=True)
        assert "渠道綁定總覽" not in body
