"""Direct unit tests for publishing/_registry_manifest.py.

The 7 public helpers (ui_meta, bind_descriptors, policy, visibility,
active_platforms, bound_platforms, legacy_platforms) are pure read-only
queries over _REGISTRY. This file verifies:

  - Unknown-platform fallbacks (return None / () / "active")
  - Concrete platform values for telegraph and linkedin
  - active_platforms() sort order and experimental exclusion
  - bound_platforms() callback contract
  - Type correctness of returned dataclasses
"""

from __future__ import annotations

import backlink_publisher.publishing.adapters as _adapters_import  # noqa: F401

import pytest

from backlink_publisher.publishing._manifest_types import BindDescriptor, Policy, UiMeta
from backlink_publisher.publishing._registry_manifest import (
    active_platforms,
    bind_descriptors,
    bound_platforms,
    legacy_platforms,
    policy,
    ui_meta,
    visibility,
)
from backlink_publisher.publishing.registry import registered_platforms
from backlink_publisher.config import Config


# ── Unknown-platform fallbacks ────────────────────────────────────────────────


class TestUnknownPlatformFallbacks:
    """Unregistered names must return safe sentinel values — not raise."""

    def test_ui_meta_unknown_returns_none(self) -> None:
        assert ui_meta("no_such_platform") is None

    def test_bind_descriptors_unknown_returns_empty_tuple(self) -> None:
        assert bind_descriptors("no_such_platform") == ()

    def test_policy_unknown_returns_none(self) -> None:
        assert policy("no_such_platform") is None

    def test_visibility_unknown_returns_active(self) -> None:
        # Default "active" means unregistered names are treated as active;
        # callers using visibility() as a gate must check registration first.
        assert visibility("no_such_platform") == "active"

    def test_bind_descriptors_returns_tuple_not_list(self) -> None:
        result = bind_descriptors("no_such_platform")
        assert isinstance(result, tuple)


# ── Telegraph: concrete manifest values ──────────────────────────────────────


class TestTelegraphManifest:
    """Telegraph has full manifest metadata — spot-check concrete values."""

    def test_ui_meta_returns_uimeta_instance(self) -> None:
        meta = ui_meta("telegraph")
        assert isinstance(meta, UiMeta)

    def test_ui_meta_display_name(self) -> None:
        meta = ui_meta("telegraph")
        assert meta is not None
        assert meta.display_name == "Telegraph"

    def test_ui_meta_domain(self) -> None:
        meta = ui_meta("telegraph")
        assert meta is not None
        assert meta.domain == "telegra.ph"

    def test_ui_meta_category_is_str(self) -> None:
        meta = ui_meta("telegraph")
        assert meta is not None
        assert isinstance(meta.category, str)
        assert meta.category  # non-empty

    def test_ui_meta_icon_is_str_or_none(self) -> None:
        meta = ui_meta("telegraph")
        assert meta is not None
        assert meta.icon is None or isinstance(meta.icon, str)

    def test_policy_returns_policy_instance(self) -> None:
        p = policy("telegraph")
        assert isinstance(p, Policy)

    def test_policy_language_whitelist_is_tuple(self) -> None:
        p = policy("telegraph")
        assert p is not None
        assert isinstance(p.language_whitelist, tuple)

    def test_visibility_telegraph_is_active(self) -> None:
        assert visibility("telegraph") == "active"

    def test_bind_descriptors_telegraph_is_tuple(self) -> None:
        result = bind_descriptors("telegraph")
        assert isinstance(result, tuple)


# ── LinkedIn: experimental visibility ────────────────────────────────────────


class TestLinkedInManifest:
    """LinkedIn is registered as experimental — gate checks."""

    def test_visibility_linkedin_experimental(self) -> None:
        assert visibility("linkedin") == "experimental"

    def test_linkedin_absent_from_active_platforms(self) -> None:
        assert "linkedin" not in active_platforms()

    def test_linkedin_present_in_registered_platforms(self) -> None:
        assert "linkedin" in registered_platforms()

    def test_ui_meta_linkedin_returns_uimeta(self) -> None:
        meta = ui_meta("linkedin")
        assert isinstance(meta, UiMeta)


# ── active_platforms() ───────────────────────────────────────────────────────


class TestActivePlatforms:
    """active_platforms() returns the default-visible channel list."""

    def test_returns_list(self) -> None:
        result = active_platforms()
        assert isinstance(result, list)

    def test_is_sorted(self) -> None:
        result = active_platforms()
        assert result == sorted(result)

    def test_subset_of_registered(self) -> None:
        registered = set(registered_platforms())
        active = set(active_platforms())
        assert active <= registered

    def test_excludes_experimental_platforms(self) -> None:
        experimental = [
            name for name in registered_platforms()
            if visibility(name) == "experimental"
        ]
        active = active_platforms()
        for name in experimental:
            assert name not in active, (
                f"{name!r} has visibility=experimental but appears in active_platforms()"
            )

    def test_excludes_hidden_and_retired(self) -> None:
        active = set(active_platforms())
        for name in registered_platforms():
            if visibility(name) in {"hidden", "retired"}:
                assert name not in active

    def test_all_active_have_valid_visibility(self) -> None:
        for name in active_platforms():
            assert visibility(name) == "active"

    def test_includes_telegraph(self) -> None:
        assert "telegraph" in active_platforms()

    def test_nonempty(self) -> None:
        assert len(active_platforms()) > 0


# ── bound_platforms() ────────────────────────────────────────────────────────


class TestBoundPlatforms:
    """bound_platforms() injects is_bound to filter the active list."""

    def _cfg(self) -> Config:
        return Config()

    def test_always_false_returns_empty(self) -> None:
        result = bound_platforms(self._cfg(), lambda cfg, name: False)
        assert result == []

    def test_always_true_returns_active_platforms(self) -> None:
        result = bound_platforms(self._cfg(), lambda cfg, name: True)
        assert result == active_platforms()

    def test_selective_filter(self) -> None:
        only = {"telegraph"}
        result = bound_platforms(self._cfg(), lambda cfg, name: name in only)
        assert result == ["telegraph"]

    def test_returns_sorted_list(self) -> None:
        result = bound_platforms(self._cfg(), lambda cfg, name: True)
        assert result == sorted(result)

    def test_excludes_experimental_even_if_is_bound_true(self) -> None:
        # is_bound can return True for experimental channels but
        # bound_platforms must not include them (active_platforms gate).
        experimental = [
            name for name in registered_platforms()
            if visibility(name) == "experimental"
        ]
        if not experimental:
            pytest.skip("no experimental platforms registered")
        result = bound_platforms(self._cfg(), lambda cfg, name: True)
        for name in experimental:
            assert name not in result

    def test_is_bound_receives_config_and_name(self) -> None:
        calls: list[tuple] = []

        def capture(cfg: Config, name: str) -> bool:
            calls.append((cfg, name))
            return False

        cfg = self._cfg()
        bound_platforms(cfg, capture)
        assert len(calls) == len(active_platforms())
        assert all(c[0] is cfg for c in calls)
        assert sorted(c[1] for c in calls) == sorted(active_platforms())


# ── Per-platform ui_meta type checks ─────────────────────────────────────────


class TestAllPlatformsUiMeta:
    """Every registered platform has a UiMeta (legacy gate passed)."""

    @pytest.mark.parametrize("platform", registered_platforms())
    def test_ui_meta_returns_uimeta_or_none(self, platform: str) -> None:
        meta = ui_meta(platform)
        assert meta is None or isinstance(meta, UiMeta)

    @pytest.mark.parametrize("platform", registered_platforms())
    def test_ui_meta_fields_are_strings(self, platform: str) -> None:
        meta = ui_meta(platform)
        if meta is None:
            pytest.skip(f"{platform!r} has no ui_meta")
        assert isinstance(meta.display_name, str) and meta.display_name
        assert isinstance(meta.domain, str)
        assert isinstance(meta.category, str) and meta.category


# ── bind_descriptors type checks ─────────────────────────────────────────────


class TestBindDescriptorsTypes:
    """bind_descriptors() always returns tuple[BindDescriptor, ...]."""

    @pytest.mark.parametrize("platform", registered_platforms())
    def test_each_entry_is_bind_descriptor(self, platform: str) -> None:
        for descriptor in bind_descriptors(platform):
            assert isinstance(descriptor, BindDescriptor)


# ── policy type checks ───────────────────────────────────────────────────────


class TestPolicyTypes:
    """policy() returns Policy or None; throttle_band shape when present."""

    @pytest.mark.parametrize("platform", registered_platforms())
    def test_policy_returns_policy_or_none(self, platform: str) -> None:
        p = policy(platform)
        assert p is None or isinstance(p, Policy)

    @pytest.mark.parametrize("platform", registered_platforms())
    def test_policy_throttle_band_shape(self, platform: str) -> None:
        p = policy(platform)
        if p is None or p.throttle_band is None:
            return
        lo, hi = p.throttle_band
        assert isinstance(lo, int)
        assert isinstance(hi, int)
        assert lo <= hi
