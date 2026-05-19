"""Tests for Plan 013 Phase B — Medium browser-login routes and functions.

Patch target for Playwright: webui_app.medium_login.sync_playwright
(consumer-reference rule: the module imports it at the top).

Config isolation provided by the session-scoped autouse _isolate_user_dirs
fixture in conftest.py.  Per-test isolation done via monkeypatch.setenv.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import DependencyError, ExternalServiceError


# ── Fixtures shared with test_webui_route_contract.py ────────────────────────
# (inlined; no cross-test import precedent in this codebase)

@pytest.fixture(autouse=True)
def _webui_state_isolated(tmp_path, monkeypatch):
    """Redirect webui_store paths so tests don't touch real files."""
    import webui_store as ws
    state_dir = tmp_path / "webui_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws.history_store, "path", state_dir / "publish-history.json")
    monkeypatch.setattr(ws.profiles_store, "path", state_dir / "campaign-profiles.json")
    monkeypatch.setattr(ws.drafts_store, "path", state_dir / "draft-queue.json")
    monkeypatch.setattr(ws.schedule_store, "path", state_dir / "schedule-settings.json")


@pytest.fixture()
def client(monkeypatch, tmp_path):
    """Flask test client with per-test config/cache isolation.

    Sets BACKLINK_PUBLISHER_CONFIG_DIR and _CACHE_DIR to per-test tmp paths
    so cooldown files and Chromium profiles don't bleed between tests.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cache").mkdir()
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    return webui.app.test_client()


# ── Mock factory (inlined; codebase has zero cross-test import precedent) ─────

def _make_mock_pw(page_url: str = "https://medium.com/@testuser"):
    """Return (mock_spw, page, ctx, pw_instance) for mocking Playwright."""
    page = MagicMock()
    page.url = page_url
    page.goto = MagicMock()
    page.wait_for_url = MagicMock()

    ctx = MagicMock()
    ctx.new_page.return_value = page

    pw_instance = MagicMock()
    pw_instance.chromium.launch_persistent_context.return_value = ctx
    pw_instance.__enter__ = MagicMock(return_value=pw_instance)
    pw_instance.__exit__ = MagicMock(return_value=False)

    mock_spw = MagicMock(return_value=pw_instance)
    return mock_spw, page, ctx, pw_instance


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def isolated_cfg(monkeypatch, tmp_path):
    """Per-test config_dir isolation."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))
    from backlink_publisher.config import Config
    return Config()


@pytest.fixture()
def csrf_client(client):
    """client with a pre-seeded CSRF token in flask session."""
    with client.session_transaction() as sess:
        sess["medium_csrf"] = "test-csrf-abc"
    return client, "test-csrf-abc"


# ══════════════════════════════════════════════════════════════════════════════
# CSRF protection
# ══════════════════════════════════════════════════════════════════════════════

class TestCSRFProtection:
    def test_launch_without_token_redirects_danger(self, client):
        resp = client.post("/settings/medium/launch-browser-login", data={})
        assert resp.status_code == 302
        assert "flash_type=danger" in resp.headers["Location"]

    def test_probe_without_token_redirects_danger(self, client):
        resp = client.post("/settings/medium/probe-browser-login", data={})
        assert resp.status_code == 302
        assert "flash_type=danger" in resp.headers["Location"]

    def test_clear_without_token_redirects_danger(self, client):
        resp = client.post("/settings/medium/clear-browser-login", data={})
        assert resp.status_code == 302
        assert "flash_type=danger" in resp.headers["Location"]


# ══════════════════════════════════════════════════════════════════════════════
# probe_login_status function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestProbeLoginStatusFn:
    def test_logged_in_when_not_signin_url(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status
        mock_spw, *_ = _make_mock_pw("https://medium.com/@alice")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            result = probe_login_status(isolated_cfg, timeout=5)
        assert result["logged_in"] is True
        assert result["username"] == "alice"

    def test_not_logged_in_when_signin_url(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status
        mock_spw, *_ = _make_mock_pw("https://medium.com/m/signin?redirect=x")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            result = probe_login_status(isolated_cfg, timeout=5)
        assert result["logged_in"] is False
        assert result["username"] is None

    def test_dependency_error_when_playwright_none(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status
        with patch("webui_app.medium_login.sync_playwright", None):
            with pytest.raises(DependencyError):
                probe_login_status(isolated_cfg)

    def test_cooldown_blocks_immediate_retry(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status, _cooldown_path
        path = _cooldown_path(isolated_cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_probe_ts": time.time()}))
        with pytest.raises(ExternalServiceError, match="冷却"):
            probe_login_status(isolated_cfg)

    def test_cooldown_allows_after_expiry(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status, _cooldown_path
        path = _cooldown_path(isolated_cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_probe_ts": time.time() - 61}))
        mock_spw, *_ = _make_mock_pw("https://medium.com/@bob")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            result = probe_login_status(isolated_cfg, timeout=5)
        assert result["logged_in"] is True

    def test_timeout_raises_external_service_error(self, isolated_cfg):
        from webui_app.medium_login import probe_login_status, _PWTimeout
        mock_spw, page, *_ = _make_mock_pw()
        page.goto.side_effect = _PWTimeout("timeout")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError, match="超时"):
                probe_login_status(isolated_cfg, timeout=5)


# ══════════════════════════════════════════════════════════════════════════════
# launch_login_window function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestLaunchLoginWindowFn:
    def test_happy_path_navigates_and_waits(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window
        mock_spw, page, ctx, _ = _make_mock_pw()
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            result = launch_login_window(isolated_cfg)
        assert result["logged_in"] is True
        page.goto.assert_called_once()
        assert "medium.com/m/signin" in page.goto.call_args[0][0]
        page.wait_for_url.assert_called_once()
        ctx.close.assert_called_once()

    def test_dependency_error_when_playwright_none(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window
        with patch("webui_app.medium_login.sync_playwright", None):
            with pytest.raises(DependencyError):
                launch_login_window(isolated_cfg)

    def test_lock_released_after_exception(self, isolated_cfg):
        from webui_app.medium_login import launch_login_window, _lock_path, _PWTimeout
        mock_spw, page, *_ = _make_mock_pw()
        page.goto.side_effect = _PWTimeout("timeout")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            with pytest.raises(ExternalServiceError):
                launch_login_window(isolated_cfg)
        assert not _lock_path(isolated_cfg).exists()


# ══════════════════════════════════════════════════════════════════════════════
# clear_browser_profile function unit tests
# ══════════════════════════════════════════════════════════════════════════════

class TestClearBrowserProfileFn:
    def test_removes_profile_dir(self, isolated_cfg):
        from webui_app.medium_login import clear_browser_profile
        udd = isolated_cfg.config_dir / "chrome-profile-default"
        (udd / "Default").mkdir(parents=True)
        (udd / "Default" / "Cookies").write_bytes(b"fake")
        clear_browser_profile(isolated_cfg)
        assert not udd.exists()

    def test_noop_when_dir_missing(self, isolated_cfg):
        from webui_app.medium_login import clear_browser_profile
        clear_browser_profile(isolated_cfg)  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# Route integration tests (with valid CSRF token)
# ══════════════════════════════════════════════════════════════════════════════

class TestMediumLoginRoutes:
    def test_probe_logged_in_flashes_info(self, csrf_client):
        client, token = csrf_client
        mock_spw, *_ = _make_mock_pw("https://medium.com/@alice")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"_csrf_token": token},
            )
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert "flash_type=info" in loc

    def test_probe_not_logged_in_flashes_info(self, csrf_client):
        client, token = csrf_client
        mock_spw, *_ = _make_mock_pw("https://medium.com/m/signin?x=1")
        with patch("webui_app.medium_login.sync_playwright", mock_spw):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"_csrf_token": token},
            )
        assert resp.status_code == 302
        assert "flash_type=info" in resp.headers["Location"]

    def test_probe_no_playwright_flashes_warning(self, csrf_client):
        client, token = csrf_client
        with patch("webui_app.medium_login.sync_playwright", None):
            resp = client.post(
                "/settings/medium/probe-browser-login",
                data={"_csrf_token": token},
            )
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.headers["Location"]

    def test_launch_no_playwright_flashes_warning(self, csrf_client):
        client, token = csrf_client
        with patch("webui_app.medium_login.sync_playwright", None):
            resp = client.post(
                "/settings/medium/launch-browser-login",
                data={"_csrf_token": token},
            )
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.headers["Location"]

    def test_clear_redirects_success(self, csrf_client):
        client, token = csrf_client
        resp = client.post(
            "/settings/medium/clear-browser-login",
            data={"_csrf_token": token},
        )
        assert resp.status_code == 302
        loc = resp.headers["Location"]
        assert loc.startswith("/settings?")
