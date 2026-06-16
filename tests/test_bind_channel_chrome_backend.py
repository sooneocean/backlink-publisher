"""Tests for the Real Chrome / CDP binding backend.

Plan 2026-05-20-007 Unit 2. Mocks Chrome process, HTTP, and websocket
surfaces — CI never launches a real browser.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backlink_publisher.cli._bind.chrome_backend import (
    RealChromeBrowserRunner,
    _chrome_binary,
    _chrome_port,
    _chrome_profile_dir,
    _websocket_available,
)


class TestStaticHelpers:
    def test_chrome_profile_dir_default(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", raising=False)
        # The default uses _config_dir() which we can't easily mock here;
        # just verify it returns a Path with the expected suffix.
        result = _chrome_profile_dir()
        assert isinstance(result, Path)
        assert result.name == "real-chrome-profile"

    def test_chrome_profile_dir_env_override(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR", "/tmp/custom-profile")
        assert _chrome_profile_dir() == Path("/tmp/custom-profile")

    def test_chrome_port_default(self, monkeypatch):
        monkeypatch.delenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", raising=False)
        assert _chrome_port() == 9222

    def test_chrome_port_env_override(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "9999")
        assert _chrome_port() == 9999

    def test_chrome_port_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "not-a-number")
        with pytest.raises(RuntimeError, match="chrome_cdp_unavailable"):
            _chrome_port()

    def test_chrome_port_out_of_range_raises(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_PORT", "0")
        with pytest.raises(RuntimeError, match="chrome_cdp_unavailable"):
            _chrome_port()

    def test_chrome_binary_not_found(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent/chrome")
        assert _chrome_binary() is None

    def test_websocket_available(self):
        # The module depends on websocket-client being installed
        assert _websocket_available() is True


class TestRealChromeBrowserRunnerAvailable:
    def test_available_when_binary_and_websocket_present(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/bin/ls")
        assert RealChromeBrowserRunner.available() is True

    def test_not_available_without_binary(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent")
        assert RealChromeBrowserRunner.available() is False


class TestRealChromeBrowserRunner:
    def test_launch_and_connect_starts_chrome(self, monkeypatch):
        """When no existing CDP is found, Chrome is launched.

        The constructor injects fake popen/requests; the real stall was
        ``_wait_for_version`` polling the (never-arriving) CDP for the full
        ``_CONNECT_TIMEOUT_S=10s``. Collapse the connect deadline to 0 so the
        loop exits immediately — the asserted verdict (launch happened, then
        ``chrome_cdp_unavailable``) is unchanged, the 10s real sleep is gone.
        """
        monkeypatch.setattr(
            "backlink_publisher.cli._bind.chrome_backend._CONNECT_TIMEOUT_S", 0.0
        )
        monkeypatch.setattr(
            "backlink_publisher.cli._bind.chrome_backend._POLL_INTERVAL_S", 0.0
        )
        fake_popen = MagicMock()
        fake_popen.return_value = MagicMock()
        fake_requests = MagicMock()
        # First call (get_version) returns None → launches
        fake_requests.get.side_effect = [
            Exception("not available"),  # _get_version fails → launch
            Exception("not available"),  # _wait_for_version also fails
        ]
        runner = RealChromeBrowserRunner(
            chrome_bin="/bin/ls",
            popen=fake_popen,
            requests_module=fake_requests,
        )
        with pytest.raises(RuntimeError, match="chrome_cdp_unavailable"):
            runner._launch_or_connect("https://example.com/login")
        fake_popen.assert_called_once()

    def test_launch_or_connect_attach_to_existing(self):
        """When BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1, existing CDP is used."""
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH", "1")
        _recv_id = [1]
        def _recv_side_effect():
            i = _recv_id[0]
            _recv_id[0] = i + 1
            return json.dumps({"id": i, "result": {}})
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = _recv_side_effect
        fake_ws_factory = MagicMock(return_value=mock_ws)
        fake_requests = MagicMock()
        fake_requests.get.side_effect = [
            MagicMock(status_code=200, json=lambda: {"webSocketDebuggerUrl": "ws://test"}),
            MagicMock(status_code=200, json=lambda: {"id": "tab1", "webSocketDebuggerUrl": "ws://test"}),
        ]
        runner = RealChromeBrowserRunner(
            chrome_bin="/bin/ls",
            requests_module=fake_requests,
            websocket_factory=fake_ws_factory,
        )
        cdp = runner._launch_or_connect("https://example.com/login")
        assert cdp is not None
        monkeypatch.undo()

    def test_attach_refused_without_env(self):
        """Without BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1, existing CDP raises."""
        fake_requests = MagicMock()
        fake_requests.get.return_value = MagicMock(status_code=200, json=lambda: {"Browser": "Chrome"})
        runner = RealChromeBrowserRunner(
            chrome_bin="/bin/ls",
            requests_module=fake_requests,
        )
        with pytest.raises(RuntimeError, match="chrome_cdp_unavailable"):
            runner._launch_or_connect("https://example.com/login")

    def test_launch_chrome_missing_binary_raises(self, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_REAL_CHROME_BIN", "/nonexistent/bin")
        runner = RealChromeBrowserRunner()
        with pytest.raises(RuntimeError, match="chrome_not_available"):
            runner._launch_chrome(9222)

    def test_launch_chrome_binary_not_found_raises_launch_failed(self, tmp_path):
        runner = RealChromeBrowserRunner(chrome_bin="/nonexistent")
        runner.profile_dir = tmp_path
        with pytest.raises(RuntimeError, match="chrome_launch_failed"):
            runner._launch_chrome(9222)

    def test_launch_chrome_oserror_raises(self, tmp_path):
        fake_popen = MagicMock(side_effect=OSError("denied"))
        runner = RealChromeBrowserRunner(
            chrome_bin="/bin/ls",
            profile_dir=tmp_path,
            popen=fake_popen,
        )
        with pytest.raises(RuntimeError, match="chrome_launch_failed"):
            runner._launch_chrome(9222)

    def test_launch_chrome_success(self, tmp_path):
        fake_proc = MagicMock()
        fake_popen = MagicMock(return_value=fake_proc)
        runner = RealChromeBrowserRunner(
            chrome_bin="/bin/ls",
            profile_dir=tmp_path,
            popen=fake_popen,
        )
        runner._launch_chrome(9222)
        fake_popen.assert_called_once()
        assert runner._proc is fake_proc

    def test_get_version_returns_none_on_failure(self):
        fake_requests = MagicMock()
        fake_requests.get.side_effect = Exception("timeout")
        runner = RealChromeBrowserRunner(requests_module=fake_requests)
        assert runner._get_version("http://127.0.0.1:9222", timeout_s=0.1) is None

    def test_open_tab_uses_put_then_get(self):
        fake_requests = MagicMock()
        # PUT fails → GET succeeds with tab info
        fake_resp = MagicMock(status_code=200, json=lambda: {"id": "tab1"})
        fake_requests.put.side_effect = Exception("put failed")
        fake_requests.get.return_value = fake_resp
        runner = RealChromeBrowserRunner(requests_module=fake_requests)
        tab = runner._open_tab("http://127.0.0.1:9222", "https://example.com")
        assert tab["id"] == "tab1"

    def test_terminate_proc_does_nothing_when_none(self):
        runner = RealChromeBrowserRunner()
        runner._terminate_proc()  # should not raise

    def test_terminate_proc_calls_terminate(self):
        fake_proc = MagicMock()
        runner = RealChromeBrowserRunner()
        runner._proc = fake_proc
        runner._terminate_proc()
        fake_proc.terminate.assert_called_once()

    def test_all_cookies_fallback_to_storage(self):
        """When Network.getAllCookies fails, fall back to Storage.getCookies."""
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),  # Runtime.enable
            json.dumps({"id": 2, "result": {}}),  # Network.enable
            json.dumps({"id": 3, "result": {}}),  # Page.enable
            json.dumps({"id": 4, "error": {"message": "not available"}}),  # Network.getAllCookies fails
            json.dumps({"id": 5, "result": {"cookies": [{"name": "fallback"}]}}),  # Storage.getCookies
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        cookies = cdp.all_cookies()
        assert cookies == [{"name": "fallback"}]

    def test_cdp_client_evaluate(self):
        """_CdpClient.evaluate sends Runtime.evaluate and returns value."""
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),  # Runtime.enable
            json.dumps({"id": 2, "result": {}}),  # Network.enable
            json.dumps({"id": 3, "result": {}}),  # Page.enable
            json.dumps({"id": 4, "result": {"result": {"value": "hello"}}}),  # evaluate
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        assert cdp.evaluate("1+1") == "hello"

    def test_cdp_client_current_url(self):
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
            json.dumps({"id": 4, "result": {"result": {"value": "https://example.com"}}}),
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        assert cdp.current_url() == "https://example.com"

    def test_cdp_page_url(self):
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
            json.dumps({"id": 4, "result": {"result": {"value": "https://example.com"}}}),
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpPage
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        page = _CdpPage(cdp)
        assert page.url == "https://example.com"

    def test_cdp_element_get_attribute(self):
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
            json.dumps({"id": 4, "result": {"result": {"value": "click me"}}}),
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpElement
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        el = _CdpElement(cdp, "#submit")
        assert el.get_attribute("value") == "click me"

    def test_cdp_context_cookies(self):
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
            json.dumps({"id": 4, "result": {"cookies": [{"name": "session"}]}}),
        ]
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpContext
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        ctx = _CdpContext(cdp)
        cookies = ctx.cookies("https://example.com")
        assert cookies == [{"name": "session"}]

    def test_page_on_registers_callback(self):
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpPage
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
        ]
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        page = _CdpPage(cdp)
        called = []
        page.on("framenavigated", lambda: called.append(1))
        assert len(cdp._event_callbacks.get("framenavigated", [])) == 1
        assert called == []

    def test_page_on_dispatch_framenavigated_event(self):
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpPage
        fake_ws = MagicMock()
        recv_responses = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
        ]
        fake_ws.recv.side_effect = iter(recv_responses)
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        page = _CdpPage(cdp)
        called = [False]
        page.on("framenavigated", lambda: called.__setitem__(0, True))
        cdp._dispatch_event("Page.frameNavigated", {})
        assert called[0] is True

    def test_page_on_ignores_unregistered_events(self):
        runner = RealChromeBrowserRunner()
        from backlink_publisher.cli._bind.chrome_backend import _CdpClient, _CdpPage
        fake_ws = MagicMock()
        fake_ws.recv.side_effect = [
            json.dumps({"id": 1, "result": {}}),
            json.dumps({"id": 2, "result": {}}),
            json.dumps({"id": 3, "result": {}}),
        ]
        cdp = _CdpClient("ws://test", websocket_factory=lambda u, **kw: fake_ws)
        page = _CdpPage(cdp)
        page.on("framenavigated", lambda: None)
        cdp._dispatch_event("Network.requestWillBeSent", {})
        cdp._dispatch_event("Unknown.event", {})
        assert True
