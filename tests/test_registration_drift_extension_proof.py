"""Extension proof (Plan 2026-05-27-008, U6): registering + classifying a new
platform automatically expands the drift-guard authority sets — the guards
themselves (U1 helper, U3 save-dispatch guard) never need editing.

This is the positive complement to the U3 drift guard: U3 proves a stale/wrong
key fails; this proves a *correctly added* platform flows through with zero
edits to ``platforms_by_auth_type`` or the save-dispatch guard. Together they
show the guard authority tracks the registry, not a hand-maintained list.

Self-contained snapshot/restore via ``monkeypatch.setitem`` (no coupling to the
conftest ``fake_platform_registered`` fixture, which does not set an auth_type).
Test-time registry population, never an import-time assert.
"""

import backlink_publisher.publishing.adapters  # noqa: F401 — trigger registration

from backlink_publisher.publishing import registry
from backlink_publisher.publishing.registry import (
    Publisher,
    active_platforms,
    platforms_by_auth_type,
)
from webui_app.routes.channel_bind_save import _TOKEN_DISPATCH


class _FakeExtAdapter(Publisher):
    @classmethod
    def available(cls, config) -> bool:  # noqa: ANN001
        return True

    def publish(self, *a, **k):  # noqa: ANN002, ANN003
        raise NotImplementedError


_FAKE = "fakeext"


def test_new_registration_auto_expands_guard_authority(monkeypatch):
    """Registering ``fakeext`` and classifying it ``token`` makes it appear in
    the token bucket with ZERO edits to ``platforms_by_auth_type`` — and wiring
    it into ``_TOKEN_DISPATCH`` would pass the U3 subset guard with ZERO edits
    to that guard."""
    # Sanity: the fake is absent before registration.
    assert _FAKE not in active_platforms()
    assert _FAKE not in platforms_by_auth_type("token")

    # Register + classify the fake (auto-restored on teardown).
    monkeypatch.setitem(registry._REGISTRY, _FAKE, (_FakeExtAdapter,))
    monkeypatch.setitem(registry._AUTH_TYPE_BY_PLATFORM, _FAKE, "token")

    # U1 helper auto-includes it — no edit to the helper.
    assert _FAKE in active_platforms()
    assert _FAKE in platforms_by_auth_type("token")

    # U3 guard authority auto-expands: wiring the fake into the token dispatch
    # map would remain a valid subset, so the guard never needs editing.
    hypothetical_token_dispatch = set(_TOKEN_DISPATCH) | {_FAKE}
    assert hypothetical_token_dispatch <= platforms_by_auth_type("token")


def test_teardown_restores_registry():
    """After the monkeypatch test, the fake is gone — no state leak into the
    rest of the suite (the global-state-pollution class)."""
    assert _FAKE not in active_platforms()
    assert _FAKE not in platforms_by_auth_type("token")
