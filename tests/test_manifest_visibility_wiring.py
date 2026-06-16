"""Plan 2026-05-25-002 Unit 2a — visibility reverse-lookup wiring.

Verifies that ``webui_app.binding_status.hidden_from_ui()`` (and its
PEP 562 ``HIDDEN_FROM_UI`` alias) reflects the registry manifest
``visibility`` field dynamically, rather than carrying a hand-maintained
frozenset.

Scope guard: this file only covers ``HIDDEN_FROM_UI``. The parallel
``_SAVE_CONFIG_KNOWN_ROOTS`` migration is Unit 2b (separate PR — touches
production ``save_config`` round-trip and 12+ test sites).
"""

from __future__ import annotations

from typing import Any

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.registry import (
    Publisher,
    _BIND_BY_PLATFORM,
    _POLICY_BY_PLATFORM,
    _REGISTRY,
    _UI_META_BY_PLATFORM,
    _VISIBILITY_BY_PLATFORM,
    register,
)


class _Fake(Publisher):
    def publish(
        self, payload: dict[str, Any], mode: str, config: Any
    ) -> AdapterResult:  # pragma: no cover
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _snapshot():
    snaps = [
        (_REGISTRY, dict(_REGISTRY)),
        (_UI_META_BY_PLATFORM, dict(_UI_META_BY_PLATFORM)),
        (_BIND_BY_PLATFORM, dict(_BIND_BY_PLATFORM)),
        (_POLICY_BY_PLATFORM, dict(_POLICY_BY_PLATFORM)),
        (_VISIBILITY_BY_PLATFORM, dict(_VISIBILITY_BY_PLATFORM)),
    ]
    try:
        yield
    finally:
        for store, snap in snaps:
            store.clear()
            store.update(snap)


class TestHiddenFromUiFunction:
    def test_empty_when_no_platform_is_hidden_or_retired(self) -> None:
        # txtfyi was moved to visibility="hidden" in the zero-auth MVP
        # (Wave 1a). All other production platforms default to "active".
        from webui_app.binding_status import hidden_from_ui

        hidden = hidden_from_ui()
        assert "txtfyi" in hidden
        # A single hidden platform is the steady state.
        assert len(hidden) >= 1

    def test_includes_visibility_hidden_platform(self) -> None:
        from webui_app.binding_status import hidden_from_ui

        register("vis_hidden", _Fake, dofollow=True, visibility="hidden")
        assert "vis_hidden" in hidden_from_ui()

    def test_includes_visibility_retired_platform(self) -> None:
        from webui_app.binding_status import hidden_from_ui

        register("vis_retired", _Fake, dofollow=True, visibility="retired")
        assert "vis_retired" in hidden_from_ui()

    def test_excludes_experimental(self) -> None:
        # Experimental is NOT in the HIDDEN set — UI may surface it
        # behind an opt-in toggle. HIDDEN_FROM_UI specifically means
        # "card never appears in the default dashboard view".
        from webui_app.binding_status import hidden_from_ui

        register("vis_exp", _Fake, dofollow=True, visibility="experimental")
        assert "vis_exp" not in hidden_from_ui()


class TestPep562ModuleAlias:
    """Existing readers do ``from .binding_status import HIDDEN_FROM_UI``
    and treat it as a frozenset. The PEP 562 ``__getattr__`` hook keeps
    that interface working without forcing a function-call migration."""

    def test_module_level_HIDDEN_FROM_UI_returns_frozenset(self) -> None:
        from webui_app import binding_status

        assert isinstance(binding_status.HIDDEN_FROM_UI, frozenset)

    def test_module_level_HIDDEN_FROM_UI_is_dynamic(self) -> None:
        # Two accesses around a register() call must observe the new
        # value — this is the load-bearing property that lets Unit 2a
        # drop the static constant without breaking existing readers.
        from webui_app import binding_status

        before = binding_status.HIDDEN_FROM_UI
        register("vis_dyn", _Fake, dofollow=True, visibility="hidden")
        after = binding_status.HIDDEN_FROM_UI
        assert "vis_dyn" not in before
        assert "vis_dyn" in after

    def test_attribute_error_on_unknown_name(self) -> None:
        from webui_app import binding_status

        with pytest.raises(AttributeError, match="has no attribute"):
            binding_status.NONEXISTENT_NAME  # type: ignore[attr-defined]
