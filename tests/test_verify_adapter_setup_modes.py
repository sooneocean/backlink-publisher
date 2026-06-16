"""Unit 2 — verify_adapter_setup mode= extension (Plan 2026-05-19-006).

Three contracts:
  - mode='offline' (default): backward-compatible — raise DependencyError on
    failure, return None on success. Covers the 14+ existing call sites.
  - mode='live':   returns VerifyResult; calls platform's lightweight verify
    endpoint; never raises for auth failures.
  - mode='dry-run': returns VerifyResult; builds payload but never hits the
    network (defense-in-depth via Session.send monkey-patch).

Companion infrastructure tests for `_verify.DryRunInterceptError` /
`_verify.dry_run_intercept()` context manager live in this file.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config import Config
from backlink_publisher.publishing._verify import (
    DryRunInterceptError,
    VerifyResult,
    dry_run_intercept,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _empty_config() -> Config:
    """Config in the autouse-isolated tmp dir → no credentials anywhere.

    See ``tests/conftest.py:_isolate_user_dirs`` (session autouse). Constructor
    reads from ``BACKLINK_PUBLISHER_CONFIG_DIR`` which the fixture points at a
    fresh tmp dir, so every adapter reports unbound.
    """
    return Config()


# ── Backward compat: mode='offline' (default) ─────────────────────────────────


class TestBackwardCompat:
    """The 14+ existing call sites must not break.

    Pre-Unit-2 contract: raise DependencyError on fail, return None on success.
    """

    def test_default_call_signature_unchanged(self):
        """`verify_adapter_setup(platform, config)` still works without mode kwarg."""
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with pytest.raises(DependencyError, match="Blogger OAuth not configured"):
            verify_adapter_setup("blogger", _empty_config())

    def test_offline_mode_equivalent_to_default(self):
        """mode='offline' produces identical behavior to omitting the kwarg."""
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with pytest.raises(DependencyError):
            verify_adapter_setup("blogger", _empty_config(), mode="offline")

    def test_offline_unknown_platform_still_raises(self):
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with pytest.raises(DependencyError, match="No adapter configured"):
            verify_adapter_setup("nonexistent", _empty_config())

    def test_offline_returns_none_on_success(self):
        """Telegraph has no required prereqs — offline verify should return None."""
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup("telegraph", _empty_config())
        assert result is None


# ── New API surface: mode='live' returns VerifyResult ─────────────────────────


class TestLiveModeContract:
    """mode='live' returns a VerifyResult — never raises for auth failures."""

    def test_live_mode_returns_verify_result_type(self):
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup(
            "blogger", _empty_config(), mode="live"
        )
        assert isinstance(result, VerifyResult)

    def test_live_mode_unbound_returns_not_ok(self):
        """No credentials → ok=False, last_verify_result='never' (not 'token_expired')."""
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup(
            "blogger", _empty_config(), mode="live"
        )
        assert result.ok is False
        assert result.last_verify_result == "never"

    def test_live_mode_unknown_platform_returns_not_ok(self):
        """Unknown platform in live mode should NOT raise — return ok=False."""
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup(
            "nonexistent", _empty_config(), mode="live"
        )
        assert result.ok is False
        assert "no adapter" in " ".join(result.blockers).lower()

    def test_live_mode_precheck_failure_propagates_offline_message(self):
        """Live mode runs an offline precheck before any HTTP. After the
        _setup_checks/_verify_live split that precheck is `_verify_offline_setup`
        (formerly a re-entrant `verify_adapter_setup(mode="offline")` call). A
        registered-but-unconfigured platform must short-circuit to
        last_verify_result='never' with the offline DependencyError message as
        the blocker — pins the one intentional logic change of the split.
        """
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup("ghpages", _empty_config(), mode="live")
        assert result.ok is False
        assert result.last_verify_result == "never"
        assert "GitHub Pages config missing" in " ".join(result.blockers)


# ── Dry-run mode contract ─────────────────────────────────────────────────────


class TestDryRunModeContract:
    """mode='dry-run' returns VerifyResult and emits ZERO real HTTP."""

    def test_dry_run_returns_verify_result(self):
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        result = verify_adapter_setup(
            "telegraph",
            _empty_config(),
            mode="dry-run",
            payload={"id": "x", "title": "T", "content_markdown": "body"},
        )
        assert isinstance(result, VerifyResult)

    def test_dry_run_emits_zero_real_http(self):
        """If the adapter tried to POST, Session.send monkey-patch raises.

        We assert no real Session.send happens by patching it to MagicMock
        and confirming call_count == 0 after dry-run.
        """
        from backlink_publisher.publishing.adapters import verify_adapter_setup

        with patch.object(
            requests.Session, "send", side_effect=AssertionError("real HTTP escaped")
        ) as mock_send:
            verify_adapter_setup(
                "telegraph",
                _empty_config(),
                mode="dry-run",
                payload={"id": "x", "title": "T", "content_markdown": "b"},
            )
            assert mock_send.call_count == 0


# ── DryRunInterceptError + context manager (infrastructure) ───────────────────


class TestDryRunIntercept:
    """The monkey-patch on requests.Session.send is the defense-in-depth layer."""

    def test_intercept_raises_on_real_session_send(self):
        """Inside the context manager, any requests call must raise."""
        with dry_run_intercept(), patch("requests.utils.should_bypass_proxies", return_value=True):
            with pytest.raises(DryRunInterceptError, match="dry-run intercept"):
                requests.get("https://example.com/")

    def test_intercept_intercepts_post_too(self):
        with dry_run_intercept(), patch("requests.utils.should_bypass_proxies", return_value=True):
            with pytest.raises(DryRunInterceptError):
                requests.post("https://example.com/", json={"x": 1})

    def test_intercept_restores_on_normal_exit(self):
        """After the `with` block, real Session.send is restored."""
        before = requests.Session.send
        with dry_run_intercept():
            pass
        after = requests.Session.send
        assert before is after, "Session.send not restored after dry_run_intercept()"

    def test_intercept_restores_on_exception(self):
        """Even if an exception fires inside, the patch is undone."""
        before = requests.Session.send
        with pytest.raises(ValueError):
            with dry_run_intercept():
                raise ValueError("application error")
        after = requests.Session.send
        assert before is after, "Session.send not restored after exception"

    def test_intercept_error_message_includes_method_and_url(self):
        with dry_run_intercept(), patch("requests.utils.should_bypass_proxies", return_value=True):
            with pytest.raises(DryRunInterceptError) as exc_info:
                requests.put("https://api.telegra.ph/createPage", json={"a": 1})
        msg = str(exc_info.value)
        assert "PUT" in msg
        assert "api.telegra.ph" in msg


# ── VerifyResult dataclass shape (smoke) ──────────────────────────────────────


class TestVerifyResultDataclass:
    """VerifyResult is the public contract returned to dashboard JSON endpoints."""

    def test_minimal_construction(self):
        r = VerifyResult(ok=True)
        assert r.ok is True
        assert r.identity is None
        assert r.last_verified_at is None
        assert r.last_verify_result == "never"
        assert r.blockers == []
        assert r.dofollow is None

    def test_full_construction(self):
        r = VerifyResult(
            ok=True,
            identity="user@example",
            last_verified_at="2026-05-19T16:30:00Z",
            last_verify_result="ok",
            blockers=[],
            dofollow=True,
        )
        assert r.identity == "user@example"
        assert r.dofollow is True
