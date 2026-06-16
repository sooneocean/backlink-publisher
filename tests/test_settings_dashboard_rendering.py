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

from backlink_publisher.publishing.registry import active_platforms
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
        # The dashboard template iterates ``active_platforms()`` which
        # filters by manifest ``visibility`` (excludes experimental/hidden/retired).
        # Sync the test with the template rather than duplicating the filter.
        return list(active_platforms())

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

    def test_bindable_channels_have_bind_button(self, client):
        from backlink_publisher.cli._bind.channels import CHANNELS
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        from webui_app.binding_status import HIDDEN_FROM_UI
        bindable = CHANNELS - HIDDEN_FROM_UI
        for channel in sorted(bindable):
            assert re.search(
                rf'class="[^"]*dch-btn-bind[^"]*"[^>]*data-channel="{channel}"',
                body,
            ), f"No Bind button for bindable channel {channel!r}"

    def test_non_bindable_channels_have_no_bind_button(self, client):
        from backlink_publisher.cli._bind.channels import CHANNELS
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        for channel in self._visible_channels():
            if channel not in CHANNELS:
                assert not re.search(
                    rf'class="[^"]*dch-btn-bind[^"]*"[^>]*data-channel="{channel}"',
                    body,
                ), f"Unexpected Bind button for non-bindable channel {channel!r}"

    def test_no_channel_has_dryrun_button(self, client):
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        assert "dch-btn-dryrun" not in body, "Stale Dry-Run button found in dashboard"


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
        # The dashboard template uses ``active_platforms()`` (visibility-filtered),
        # not ``registered_platforms()``.  Sync the expected count with
        # the actual rendering source.
        resp = client.get("/settings")
        body = resp.get_data(as_text=True)
        # Count of `dashboard-channel-card` outer divs.
        card_count = body.count('class="dashboard-channel-card"')
        expected = len(active_platforms())
        assert card_count == expected, (
            f"Dashboard cards ({card_count}) != active platforms "
            f"({expected}). Drift detected — investigate "
            f"_settings_context.dashboard_channels and the card macro."
        )


class TestChannelTierContext:
    """Plan 2026-05-29-003 Unit 2 — _settings_context() exposes
    ``dashboard_channel_tiers`` whose members are exactly active_platforms().
    """

    def _tiers(self):
        """Build the real settings context via an app/request context."""
        from webui_app import create_app
        from webui_app.helpers.contexts import _settings_context

        app = create_app()
        app.config["TESTING"] = True
        with app.test_request_context("/settings"):
            return _settings_context()["dashboard_channel_tiers"]

    def test_tiers_present_and_partition_active_platforms(self):
        tiers = self._tiers()
        assert tiers, "expected at least one tier group"
        members = [name for g in tiers for name, _, _ in g["channels"]]
        # No channel appears in more than one tier.
        assert len(members) == len(set(members)), "channel duplicated across tiers"
        # Union == active_platforms() (no channel lost, none invented).
        assert set(members) == set(active_platforms())

    def test_tier_keys_are_ordered_subset(self):
        keys = [g["key"] for g in self._tiers()]
        # Order preserved (tier-1 before tier-2 before tier-3), no duplicates.
        assert keys == sorted(set(keys), key=["tier-1", "tier-2", "tier-3"].index)

    def test_none_auth_type_channel_stays_in_tier_2(self, monkeypatch):
        """R4a integration: a live channel with auth_type=None lands in tier-2,
        never vanishing from every group. Patch the registry auth_type so the
        first active platform reports None.
        """
        from backlink_publisher.publishing import registry

        target = active_platforms()[0]
        real_auth_type = registry.auth_type

        def _fake_auth_type(name):
            return None if name == target else real_auth_type(name)

        # get_channel_status imports auth_type lazily from the registry module,
        # so patching the module attribute is enough.
        monkeypatch.setattr(registry, "auth_type", _fake_auth_type)

        tiers = self._tiers()
        members_by_tier = {g["key"]: {n for n, _, _ in g["channels"]} for g in tiers}
        # target must still be present somewhere, specifically tier-2.
        all_members = {n for s in members_by_tier.values() for n in s}
        assert target in all_members, f"{target} vanished from all tiers"
        assert target in members_by_tier.get("tier-2", set())

    def test_csdn_juejin_absent_from_all_tiers(self):
        members = {name for g in self._tiers() for name, _, _ in g["channels"]}
        assert "csdn" not in members
        assert "juejin" not in members

    def test_grouping_failure_falls_back_to_empty(self, monkeypatch):
        """Error path: if group_channels_by_tier raises, the key is [] and
        _settings_context() does not propagate the error.
        """
        from webui_app.helpers import channel_tiers

        def _boom(_channels):
            raise RuntimeError("intentional grouping failure")

        monkeypatch.setattr(channel_tiers, "group_channels_by_tier", _boom)

        from webui_app import create_app
        from webui_app.helpers.contexts import _settings_context

        app = create_app()
        app.config["TESTING"] = True
        with app.test_request_context("/settings"):
            ctx = _settings_context()
        assert ctx["dashboard_channel_tiers"] == []


class TestTierGroupingDom:
    """Plan 2026-05-29-003 Unit 3 — the overview panel renders 3 automation
    tiers as independent collapses with same-source show/aria-expanded.
    """

    def _overview(self, client):
        """Return the #overview-panel..#section-channels slice of /settings."""
        body = client.get("/settings").get_data(as_text=True)
        start = body.index('id="overview-panel"')
        end = body.index('id="section-channels"')
        return body[start:end]

    def test_three_tier_panels_render(self, client):
        ov = self._overview(client)
        for key in ("tier-1", "tier-2", "tier-3"):
            assert re.search(rf'id="{key}"\s+class="collapse', ov), f"missing panel {key}"

    def test_tier1_open_others_collapsed_same_source(self, client):
        """R2: tier-1 panel has `show` + toggle aria-expanded=true; tier-2/3 not."""
        ov = self._overview(client)
        # Panel show-class state.
        assert re.search(r'id="tier-1"\s+class="collapse show"', ov)
        assert re.search(r'id="tier-2"\s+class="collapse"(?!\s*show)', ov)
        assert re.search(r'id="tier-3"\s+class="collapse"(?!\s*show)', ov)
        # Toggle aria-expanded must match the panel state (same source).
        assert re.search(
            r'data-bs-target="#tier-1"\s+aria-expanded="true"', ov
        ), "tier-1 toggle must be aria-expanded=true to match its show panel"
        assert re.search(r'data-bs-target="#tier-2"\s+aria-expanded="false"', ov)
        assert re.search(r'data-bs-target="#tier-3"\s+aria-expanded="false"', ov)

    def test_each_tier_header_shows_ready_count(self, client):
        """R3: every tier header carries a '就緒 X/Y' count (not '已綁定')."""
        ov = self._overview(client)
        counts = re.findall(r'就緒\s+(\d+)/(\d+)', ov)
        assert len(counts) == 3, f"expected 3 ready-count headers, got {counts}"
        for ready, total in counts:
            assert int(ready) <= int(total)

    def test_cards_partition_active_platforms_exactly(self, client):
        """R12 (mandatory): every active platform renders exactly once across
        the three tier panels — no loss, no duplication.
        """
        ov = self._overview(client)
        carded = re.findall(r'<div class="dashboard-channel-card" data-channel="([^"]+)"', ov)
        assert len(carded) == len(set(carded)), "a channel rendered in two tiers"
        assert set(carded) == set(active_platforms())

    def test_anon_channels_in_tier1(self, client):
        """telegraph (anon) must live in the tier-1 panel."""
        ov = self._overview(client)
        tier1 = ov[ov.index('id="tier-1"'):ov.index('id="tier-2"')]
        assert 'data-channel="telegraph"' in tier1

    def test_badges_and_buttons_preserved(self, client):
        """R7: regrouping doesn't strip the per-card badges/action buttons."""
        ov = self._overview(client)
        assert 'dch-btn-verify' in ov
        assert 'dch-btn-dry-run' in ov
        assert 'badge-dofollow' in ov

    def test_divider_separates_ready_from_unconfigured(self, client, monkeypatch):
        """R5: in a mixed tier, ready cards precede the divider, unconfigured
        cards follow it. Force devto (tier-2) ready and notion (tier-2)
        unconfigured to make the boundary deterministic regardless of which
        other channels happen to verify offline.
        """
        from webui_app import binding_status

        real = binding_status.get_channel_status

        def _patched(name, config):
            st = real(name, config)
            if name == "devto":
                return {**st, "bound": True}
            if name == "notion":
                return {**st, "bound": False}
            return st

        monkeypatch.setattr(binding_status, "get_channel_status", _patched)
        ov = self._overview(client)
        tier2 = ov[ov.index('id="tier-2"'):ov.index('id="tier-3"')]
        assert 'tier-divider' in tier2, "mixed tier must render a divider"
        devto_pos = tier2.index('data-channel="devto"')
        divider_pos = tier2.index('tier-divider')
        notion_pos = tier2.index('data-channel="notion"')
        assert devto_pos < divider_pos, "ready channel must precede the divider"
        assert notion_pos > divider_pos, "unconfigured channel must follow the divider"

    def test_all_ready_tier_has_no_divider(self, client):
        """R5: tier-1 (all anon = all ready) renders no divider."""
        ov = self._overview(client)
        tier1 = ov[ov.index('id="tier-1"'):ov.index('id="tier-2"')]
        assert 'tier-divider' not in tier1, "all-ready tier must not render a divider"

    def test_homogeneous_tier_has_no_divider(self, client, monkeypatch):
        """R5/R12: a tier whose members are all unready renders no divider.
        Force every tier-2 channel unbound so tier-2 is homogeneous.
        """
        from backlink_publisher.publishing.registry import auth_type
        from webui_app import binding_status

        real = binding_status.get_channel_status
        tier2_auth = {"token", "token_fields", "oauth", "userpass", None}

        def _patched(name, config):
            st = real(name, config)
            if auth_type(name) in tier2_auth:
                return {**st, "bound": False}
            return st

        monkeypatch.setattr(binding_status, "get_channel_status", _patched)
        ov = self._overview(client)
        tier2 = ov[ov.index('id="tier-2"'):ov.index('id="tier-3"')]
        assert 'tier-divider' not in tier2, "homogeneous (all-unready) tier needs no divider"


class TestTierPersistenceContract:
    """Plan 2026-05-29-003 Unit 4 — structural proxy for the JS that persists
    tier collapse state across verify/dry-run re-renders (R10). JS behavior
    itself is exercised by manual smoke (recorded in the PR); here we assert
    the DOM contract the JS depends on, and that the JS is actually wired.
    """

    def test_each_tier_has_a_collapse_toggle(self, client):
        body = client.get("/settings").get_data(as_text=True)
        for key in ("tier-1", "tier-2", "tier-3"):
            assert re.search(
                rf'data-bs-toggle="collapse"\s+data-bs-target="#{key}"', body
            ), f"missing collapse toggle for {key}"
            assert re.search(rf'id="{key}"\s+class="collapse', body)

    def test_tier_panels_nested_inside_overview_panel(self, client):
        """The persistence JS scopes to '#overview-panel .collapse[id^="tier-"]',
        so the tier panels must live inside #overview-panel (not just exist).
        """
        body = client.get("/settings").get_data(as_text=True)
        overview = body[body.index('id="overview-panel"'):body.index('id="section-channels"')]
        for key in ("tier-1", "tier-2", "tier-3"):
            assert f'id="{key}"' in overview, f"{key} not nested in #overview-panel"

    def test_settings_js_generalizes_persistence_to_tiers(self):
        from pathlib import Path

        js = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "static" / "js" / "settings.js"
        ).read_text(encoding="utf-8")
        # Per-tier key namespace + tier-scoped selector.
        assert "settings:collapse:" in js
        assert '[id^="tier-"]' in js


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
