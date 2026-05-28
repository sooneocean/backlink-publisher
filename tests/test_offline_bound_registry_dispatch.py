"""Plan 2026-05-26-002 Unit 1 — registry-driven offline ``bound`` dispatch.

``verify_adapter_setup(mode='offline')`` previously hit a terminal
``raise DependencyError("No adapter configured for platform: X")`` for the
~20 registered channels without a bespoke branch, so every one of them
misreported ``bound=False`` with that misleading blocker. Unit 1 replaces the
terminal raise with registry delegation:

* credential adapters delegate to their ``available(config)`` (False when
  unconfigured, True once bound);
* ANON adapters (txtfyi/rentry) report bound with no credentials;
* the two false-positive traps — livejournal (USERPASS inherits base
  ``available()==True``) and mastodon (chrome dispatcher gates on environment,
  not login) — probe their stored artifact instead.

See ``tests/conftest.py:_isolate_user_dirs`` (session autouse) for the
sandboxed config dir.
"""

from __future__ import annotations

import json

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config import Config
from backlink_publisher.publishing.adapters import verify_adapter_setup


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Per-test isolated config dir.

    The repo's ``_isolate_user_dirs`` autouse fixture is *session*-scoped, so
    credential files written by one test leak into later tests. These tests
    write credential artifacts, so each needs its own fresh dir.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return Config()


def _is_bound(platform: str, config: Config) -> bool:
    try:
        verify_adapter_setup(platform, config, mode="offline")
        return True
    except DependencyError:
        return False


def _blocker(platform: str, config: Config) -> str:
    with pytest.raises(DependencyError) as exc:
        verify_adapter_setup(platform, config, mode="offline")
    return str(exc.value)


# ── The misreport is gone ─────────────────────────────────────────────────────


def test_no_registered_channel_reports_no_adapter_configured(cfg):
    """The core bug: every registered platform used to fall through to the
    generic 'No adapter configured' blocker. Now none does."""
    from backlink_publisher.publishing.registry import active_platforms
    offenders = []
    for name in active_platforms():
        try:
            verify_adapter_setup(name, cfg, mode="offline")
        except DependencyError as e:
            if "No adapter configured" in str(e):
                offenders.append(name)
    assert offenders == [], f"channels still misreporting: {offenders}"


# ── ANON channels: ready with no credentials ──────────────────────────────────


@pytest.mark.parametrize("platform", ["txtfyi", "rentry"])
def test_anon_channels_bound_without_credentials(platform, cfg):
    assert _is_bound(platform, cfg) is True


# ── Credential adapters: unbound (specific blocker) → bound once configured ───


@pytest.mark.parametrize(
    "platform",
    ["tumblr", "wordpresscom", "substack"],
)
def test_credential_channel_unbound_without_credentials(platform, cfg):
    assert _is_bound(platform, cfg) is False
    # Specific blocker, not the old generic terminal raise.
    assert "No adapter configured" not in _blocker(platform, cfg)


def test_cookie_channel_bound_once_cookies_present(cfg):
    """A cookie-export adapter (substack) flips to bound when its credential file
    exists — delegating to its ``available()`` probe."""
    assert _is_bound("substack", cfg) is False
    cred = cfg.config_dir / "substack-credentials.json"
    cred.write_text(json.dumps({"cookies": [{"name": "x", "value": "y"}]}))
    assert _is_bound("substack", cfg) is True


# ── P0 false-positive guards: the two traps ───────────────────────────────────


def test_livejournal_not_falsely_bound_without_credentials(cfg):
    """USERPASS adapter inherits base ``available()==True``; must NOT be bound
    until its credential file exists."""
    assert _is_bound("livejournal", cfg) is False
    assert "throwaway" in _blocker("livejournal", cfg).lower()

    (cfg.config_dir / "livejournal-credentials.json").write_text(
        json.dumps({"username": "u", "hpassword": "h"})
    )
    assert _is_bound("livejournal", cfg) is True


def test_mastodon_not_falsely_bound_without_profile(cfg):
    """Chrome dispatcher ``available()`` is env-only (True); must NOT be bound
    until a per-channel Chrome profile exists."""
    assert _is_bound("mastodon", cfg) is False

    profile = cfg.config_dir / "real-chrome-profile" / "mastodon"
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("x")  # non-empty profile dir
    assert _is_bound("mastodon", cfg) is True


# ── Unregistered platform still raises the generic blocker ────────────────────


def test_unregistered_platform_still_raises_no_adapter(cfg):
    assert "No adapter configured" in _blocker("definitely-not-a-channel", cfg)


# ── Publish-select gate consumer (P0 ripple) ──────────────────────────────────


def test_uncredentialed_channel_not_admitted_to_publish_select(cfg):
    """``bound`` feeds the publish-select predicate; an uncredentialed
    credential channel must report unbound there too (no false admit)."""
    # livejournal is the canonical trap: base available()==True.
    assert _is_bound("livejournal", cfg) is False
