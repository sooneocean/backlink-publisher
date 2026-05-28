"""Plan 2026-05-26-002 Unit 2 — auth-type classification.

``registry.auth_type(channel)`` classifies every platform into one binding
auth-type, driving WebUI binding-UI template selection (Unit 3). Two guards
keep the static ``_AUTH_TYPE_BY_PLATFORM`` map honest:

  1. **coverage** — every ``active_platforms()`` channel resolves a known
     auth_type (a new channel shipped without one fails loudly here);
  2. **consistency** — where a channel also declares ``bind[].backend``, its
     auth_type is compatible, so the map can never silently drift from the
     existing ``BindBackend`` descriptor.

The drift assertions live here (test-time), never at module import, per the
``invert-drift-check-when-invariant-becomes-dynamic`` learning.
"""

from __future__ import annotations

import backlink_publisher.publishing.adapters  # noqa: F401 — trigger registration
import pytest

from backlink_publisher.publishing.registry import (
    _AUTH_TYPE_BY_PLATFORM,
    _AUTH_TYPE_VALUES,
    _BACKEND_AUTH_TYPE_COMPAT,
    _BIND_BY_PLATFORM,
    active_platforms,
    auth_type,
)


def test_every_active_platform_has_known_auth_type():
    """Coverage guard: a new registered channel must be classified."""
    missing, bad = [], []
    for name in active_platforms():
        at = auth_type(name)
        if at is None:
            missing.append(name)
        elif at not in _AUTH_TYPE_VALUES:
            bad.append((name, at))
    assert missing == [], f"unclassified active platforms: {missing}"
    assert bad == [], f"auth_type not in the known set: {bad}"


def test_auth_type_consistent_with_declared_bind_backend():
    """Consistency guard: auth_type must agree with bind[].backend where one
    is declared — no drift between the two classifications."""
    violations = []
    for name, binds in _BIND_BY_PLATFORM.items():
        at = auth_type(name)
        for b in binds:
            compat = _BACKEND_AUTH_TYPE_COMPAT.get(b.backend)
            if compat is not None and at not in compat:
                violations.append((name, b.backend, at))
    assert violations == [], (
        f"auth_type ⟂ bind backend (channel, backend, auth_type): {violations}"
    )


def test_auth_type_returns_none_for_unknown():
    assert auth_type("definitely-not-a-channel") is None


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("telegraph", "anon"),
        ("txtfyi", "anon"),
        ("rentry", "anon"),
        ("devto", "token"),
        ("notion", "token_fields"),
        ("substack", "paste_blob"),
        ("livejournal", "userpass"),
        ("blogger", "oauth"),
        ("velog", "live_browser"),
        ("mastodon", "live_browser"),
    ],
)
def test_representative_classifications(platform, expected):
    assert auth_type(platform) == expected


def test_classification_covers_all_27_no_extras():
    """The static map should classify exactly the registered platforms — no
    stale entries for platforms that no longer exist."""
    extras = set(_AUTH_TYPE_BY_PLATFORM) - set(active_platforms())
    assert extras == set(), f"stale auth_type entries: {extras}"
