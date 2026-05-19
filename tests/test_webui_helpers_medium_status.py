"""Tests for _get_medium_browser_status (Plan 013 Phase B, Unit 4).

_get_medium_browser_status is a pure filesystem probe — it must never launch
Playwright or make network calls.  Every test asserts that subprocess.run and
sync_playwright() were NOT called.

Config isolation provided by the session-scoped autouse _isolate_user_dirs
fixture in conftest.py (sets BACKLINK_PUBLISHER_CONFIG_DIR).
"""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.config import Config
from webui_app.helpers import _get_medium_browser_status


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    """Config using a per-test config_dir via env var override."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    return Config()


@pytest.fixture()
def cfg_with_cookies(cfg):
    """Config whose chrome-profile-default has a Default/Cookies file."""
    udd = cfg.config_dir / "chrome-profile-default"
    cookies = udd / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"fake-sqlite-db")
    return cfg


class TestMediumBrowserStatusNoNetwork:
    """_get_medium_browser_status must never touch Playwright or the network."""

    def test_does_not_call_sync_playwright(self, cfg):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        with patch.object(_mb, "sync_playwright") as mock_pw:
            _get_medium_browser_status(cfg)
        mock_pw.assert_not_called()

    def test_does_not_call_subprocess(self, cfg, monkeypatch):
        import subprocess
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a))
        _get_medium_browser_status(cfg)
        assert calls == []


class TestMediumBrowserStatusStates:
    def test_not_installed_when_no_playwright_no_macos(self, cfg, monkeypatch):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", None)
        monkeypatch.setattr("platform.system", lambda: "Linux")
        result = _get_medium_browser_status(cfg)
        assert result["state"] == "not_installed"
        assert not result["playwright_installed"]

    def test_no_profile_when_playwright_installed_no_cookies(self, cfg, monkeypatch):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        result = _get_medium_browser_status(cfg)
        assert result["state"] == "no_profile"
        assert not result["profile_has_cookies"]

    def test_no_profile_when_dir_exists_but_no_cookies(self, cfg, monkeypatch):
        """mkdir by medium_browser.py publish must not be mistaken for a session."""
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        (cfg.config_dir / "chrome-profile-default").mkdir()
        result = _get_medium_browser_status(cfg)
        assert result["state"] == "no_profile"

    def test_profile_exists_unverified_when_cookies_present(
        self, cfg_with_cookies, monkeypatch
    ):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        result = _get_medium_browser_status(cfg_with_cookies)
        assert result["state"] == "profile_exists_unverified"
        assert result["profile_has_cookies"]
        assert result["cookies_mtime"] is not None

    def test_logged_in_when_session_flag_set(self, cfg_with_cookies, monkeypatch):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        session = {"medium_probe_logged_in": True}
        result = _get_medium_browser_status(cfg_with_cookies, session=session)
        assert result["state"] == "logged_in"

    def test_singleton_lock_present_does_not_change_state(self, cfg, monkeypatch):
        """SingletonLock is hint-only — does not flip state."""
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        udd = cfg.config_dir / "chrome-profile-default"
        udd.mkdir()
        (udd / "SingletonLock").write_text("")
        result = _get_medium_browser_status(cfg)
        assert result["state"] == "no_profile"   # no cookies
        assert result["singleton_lock_present"]

    def test_stale_cookies_still_profile_exists_unverified(
        self, cfg_with_cookies, monkeypatch
    ):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", object())
        cookies = cfg_with_cookies.config_dir / "chrome-profile-default" / "Default" / "Cookies"
        old_ts = time.time() - 40 * 86_400
        os.utime(cookies, (old_ts, old_ts))
        result = _get_medium_browser_status(cfg_with_cookies)
        assert result["state"] == "profile_exists_unverified"
        assert result["cookies_age_days"] is not None
        assert result["cookies_age_days"] >= 40

    def test_default_profile_dir_name(self, cfg):
        result = _get_medium_browser_status(cfg)
        assert "chrome-profile-default" in result["profile_dir"]

    def test_brave_macos_on_darwin(self, cfg, monkeypatch):
        from backlink_publisher.publishing.adapters import medium_browser as _mb
        monkeypatch.setattr(_mb, "sync_playwright", None)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        result = _get_medium_browser_status(cfg)
        assert result["brave_macos"]
        assert result["state"] != "not_installed"
