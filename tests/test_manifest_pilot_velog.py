"""Plan 2026-05-25-002 Unit 3 — Velog pilot manifest assertions.

Verifies the velog ``register()`` call declares a complete manifest
(ui + bind + policy) that round-trips through the U1 helpers. The
underlying adapter, browser recipe, bind recipe, login route, and
selectors module are *not* relocated — this is the design-validation
unit confirming the manifest's expressiveness is sufficient to describe
a real channel with five special files.

Unit 3 is the canary: if BindDescriptor / Policy / UiMeta are missing
fields velog needs, that gap surfaces here before the rest of the 7
existing channels migrate (Phase 2 of the plan).
"""

from __future__ import annotations

import pytest

# Import the production adapters module so its register() calls run.
# Without this import the registry only carries whatever the conftest
# fake_platform_registered fixture installs.
from backlink_publisher.publishing import adapters as _production_adapters  # noqa: F401
from backlink_publisher.publishing._manifest_types import (
    BindDescriptor,
    Policy,
    UiMeta,
)
from backlink_publisher.publishing.registry import (
    bind_descriptors,
    legacy_platforms,
    policy,
    registered_platforms,
    ui_meta,
    visibility,
)


class TestVelogManifestPresence:
    """The velog register() call carries all 4 manifest kwargs."""

    def test_velog_is_registered(self) -> None:
        assert "velog" in registered_platforms()

    def test_velog_has_ui_meta(self) -> None:
        meta = ui_meta("velog")
        assert meta is not None
        assert isinstance(meta, UiMeta)
        assert meta.display_name == "Velog"
        assert meta.domain == "velog.io"
        assert meta.category == "dev-blog"

    def test_velog_has_at_least_one_bind_descriptor(self) -> None:
        descriptors = bind_descriptors("velog")
        assert len(descriptors) >= 1
        assert all(isinstance(d, BindDescriptor) for d in descriptors)

    def test_velog_primary_bind_is_cookie_backend(self) -> None:
        descriptors = bind_descriptors("velog")
        assert descriptors[0].backend == "cookie"

    def test_velog_bind_declares_login_endpoint(self) -> None:
        descriptors = bind_descriptors("velog")
        assert descriptors[0].login_endpoint == "/api/velog/login"

    def test_velog_bind_declares_card_template(self) -> None:
        descriptors = bind_descriptors("velog")
        assert descriptors[0].card_template == "_settings_channel_velog.html"

    def test_velog_bind_extras_reference_five_special_modules(self) -> None:
        # The whole point of the pilot — these 5 paths are exactly the
        # specialisation Channel Manifest needs to absorb so future
        # consumers (Unit 4 WebUI wiring) don't hardcode them.
        extras = bind_descriptors("velog")[0].extras
        assert "browser_recipe" in extras
        assert "bind_recipe" in extras
        assert "login_module" in extras
        assert "selectors_module" in extras
        assert "velog" in extras["browser_recipe"]
        assert "velog" in extras["bind_recipe"]

    def test_velog_has_policy(self) -> None:
        pol = policy("velog")
        assert pol is not None
        assert isinstance(pol, Policy)

    def test_velog_policy_throttle_matches_adapter_constants(self) -> None:
        # _VELOG_JITTER_MIN_S / MAX_S in velog_graphql.py are 60 and 180.
        # If those constants change, this assertion catches the manifest
        # drift — the policy MUST reflect adapter reality.
        pol = policy("velog")
        assert pol is not None
        assert pol.throttle_band == (60, 180)

    def test_velog_policy_languages_include_korean_and_english(self) -> None:
        pol = policy("velog")
        assert pol is not None
        assert "ko" in pol.language_whitelist
        assert "en" in pol.language_whitelist


class TestVelogVisibilityDefault:
    def test_velog_defaults_to_active(self) -> None:
        # No explicit visibility= kwarg => "active". Load-bearing for
        # Unit 2a: velog should NOT appear in hidden_from_ui().
        assert visibility("velog") == "active"

    def test_velog_is_not_hidden_or_retired(self) -> None:
        from webui_app.binding_status import hidden_from_ui

        assert "velog" not in hidden_from_ui()


class TestVelogLeavesLegacyPool:
    """legacy_platforms() should NO LONGER contain velog after Unit 3."""

    def test_velog_not_in_legacy(self) -> None:
        # Unit 3 is the first manifest migration. legacy_platforms()
        # excludes velog because ui/bind/policy are all populated.
        # Unit 5 will track this as a migration progress board number.
        assert "velog" not in legacy_platforms()


class TestZeroBehaviourChange:
    """The 4 new manifest kwargs must not affect existing publish flow.

    The legacy fallback chain (VelogGraphQLAdapter →
    BrowserPublishDispatcher.for_channel("velog")) is unchanged — Unit 3
    is purely additive metadata.
    """

    def test_velog_chain_still_has_graphql_first(self) -> None:
        from backlink_publisher.publishing.registry import _REGISTRY

        chain = _REGISTRY["velog"]
        # First chain entry stays the VelogGraphQLAdapter class
        # (lazy-instantiated). The browser dispatcher remains the
        # fallback (DependencyError → next).
        from backlink_publisher.publishing.adapters.velog_graphql import (
            VelogGraphQLAdapter,
        )

        assert chain[0] is VelogGraphQLAdapter or isinstance(
            chain[0], VelogGraphQLAdapter
        )

    def test_velog_chain_length_unchanged(self) -> None:
        from backlink_publisher.publishing.registry import _REGISTRY

        # Pre-Unit-3: 2 entries (VelogGraphQLAdapter +
        # BrowserPublishDispatcher.for_channel). Manifest kwargs don't
        # touch the chain.
        assert len(_REGISTRY["velog"]) == 2

    def test_velog_dofollow_unchanged(self) -> None:
        from backlink_publisher.publishing.registry import dofollow_status

        assert dofollow_status("velog") is True


# Phase 2 migrations expand the set of platforms with a manifest. The
# scope guard below excludes every already-migrated channel so it stays
# meaningful as a regression net for the *remaining* legacy channels.
# When you migrate a channel, add it to ``_MIGRATED`` here.
_MIGRATED = {"velog", "telegraph", "blogger"}


@pytest.mark.parametrize(
    "platform",
    sorted(set(registered_platforms()) - _MIGRATED),
)
class TestOtherPlatformsRemainLegacy:
    """Scope guard: only platforms listed in ``_MIGRATED`` have a manifest.

    If a channel accidentally gains a manifest in a PR that does not
    update ``_MIGRATED``, this test catches the scope creep.
    """

    def test_other_platform_has_no_ui_meta(self, platform: str) -> None:
        assert ui_meta(platform) is None, (
            f"{platform!r} unexpectedly gained ui_meta in Unit 3 — "
            f"pilot scope is velog only."
        )

    def test_other_platform_has_no_bind_descriptors(
        self, platform: str
    ) -> None:
        assert bind_descriptors(platform) == (), (
            f"{platform!r} unexpectedly gained bind descriptors."
        )

    def test_other_platform_has_no_policy(self, platform: str) -> None:
        assert policy(platform) is None, (
            f"{platform!r} unexpectedly gained policy."
        )
