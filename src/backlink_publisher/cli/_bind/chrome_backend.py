"""Real Chrome / CDP binding backend.

This backend is deliberately small: it launches a dedicated visible Chrome
profile, opens the channel login URL, lets the operator complete Cloudflare /
OAuth / 2FA manually, and exports host-filtered cookies in the same
storage_state shape the existing bind driver persists.

It is product code, not a dependency on Codex's Chrome extension. Tests mock
the process, HTTP, and websocket surfaces; CI never launches Chrome.

Path/binary helpers (``_chrome_binary``, ``_chrome_port``,
``_chrome_profile_dir``, ``_websocket_available``) are re-exported from
``publishing.browser_publish.chrome_session`` so bind and publish share a
single source of truth (Plan 2026-05-21-001 Unit 1). ``_chrome_port`` and
``_chrome_profile_dir`` keep raising ``ChromeLaunchError`` here (vs
``ChromeSessionError`` upstream) to preserve the bind error contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from backlink_publisher.publishing.browser_publish.chrome_session import (
    ChromeSessionError as _ChromeSessionError,
    _chrome_binary as _shared_chrome_binary,
    _chrome_port as _shared_chrome_port,
    _chrome_profile_dir as _shared_chrome_profile_dir,
    _websocket_available as _shared_websocket_available,
)
from .driver import BIND_TIMEOUT_MS, ChromeLaunchError


_CONNECT_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.25


def _chrome_profile_dir() -> Path:
    try:
        return _shared_chrome_profile_dir()
    except _ChromeSessionError as exc:
        raise ChromeLaunchError(str(exc)) from exc


def _chrome_port() -> int:
    try:
        return _shared_chrome_port()
    except _ChromeSessionError as exc:
        raise ChromeLaunchError("chrome_cdp_unavailable") from exc


def _chrome_binary() -> str | None:
    return _shared_chrome_binary()


def _websocket_available() -> bool:
    return _shared_websocket_available()


class RealChromeBrowserRunner:
    """BrowserRunner implementation backed by Google Chrome + CDP."""

    def __init__(
        self,
        *,
        chrome_bin: str | None = None,
        profile_dir: Path | None = None,
        port: int | None = None,
        popen: Any = None,
        requests_module: Any = requests,
        websocket_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.chrome_bin = chrome_bin
        self.profile_dir = profile_dir
        self.port = port
        self._popen = popen or subprocess.Popen
        self._requests = requests_module
        self._websocket_factory = websocket_factory
        self._proc: subprocess.Popen | None = None

    @classmethod
    def available(cls) -> bool:
        return _chrome_binary() is not None and _websocket_available()

    def launch_and_wait(
        self,
        *,
        recipe,
        on_browser_ready: Callable[[], None],
        on_login_detected: Callable[[], None],
    ) -> Callable[..., None]:
        cdp = self._launch_or_connect(recipe.login_url)
        on_browser_ready()

        page = _CdpPage(cdp)
        try:
            recipe.bound_predicate(page)
        except Exception:
            cdp.close()
            self._terminate_proc()
            raise

        on_login_detected()

        # Plan 2026-05-20-016 Unit 0 Fix 1: apply recipe.cookie_host_filter
        # to drop cookies whose host is outside the channel's expected set.
        # Mirrors driver._apply_host_filter — same dict shape, same semantics.
        # Fail CLOSED on missing filter: persist-all would silently re-leak
        # the operator's entire real-Chrome cookie jar (banking, SSO, email)
        # into <channel>-storage-state.json, which is exactly the security
        # bug this fix addresses.  Recipe-registration test in
        # tests/test_recipe_host_filter_registration.py guards against
        # future recipes forgetting the field.
        host_filter = getattr(recipe, "cookie_host_filter", None)
        if host_filter is None:
            cdp.close()
            self._terminate_proc()
            raise ChromeLaunchError("recipe_missing_host_filter")

        def _provider(*, path) -> None:
            try:
                raw_cookies = cdp.all_cookies()
                filtered = [
                    c for c in raw_cookies
                    if isinstance(c, dict) and host_filter(c.get("domain", ""))
                ]
                state = {
                    "cookies": filtered,
                    "origins": [],
                }
                Path(path).write_text(json.dumps(state, ensure_ascii=False))
            finally:
                cdp.close()
                self._terminate_proc()

        return _provider

    def _launch_or_connect(self, login_url: str) -> "_CdpClient":
        port = self.port if self.port is not None else _chrome_port()
        base = f"http://127.0.0.1:{port}"

        version = self._get_version(base, timeout_s=0.5)
        if version is not None:
            if os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH") != "1":
                raise ChromeLaunchError("chrome_cdp_unavailable")
        else:
            self._launch_chrome(port)
            version = self._wait_for_version(base)
        if version is None:
            raise ChromeLaunchError("chrome_cdp_unavailable")

        tab = self._open_tab(base, login_url)
        ws_url = tab.get("webSocketDebuggerUrl") or version.get("webSocketDebuggerUrl")
        if not ws_url:
            raise ChromeLaunchError("chrome_cdp_unavailable")
        return _CdpClient(ws_url, websocket_factory=self._websocket_factory)

    def _launch_chrome(self, port: int) -> None:
        chrome_bin = self.chrome_bin or _chrome_binary()
        if not chrome_bin:
            raise ChromeLaunchError("chrome_not_available")

        profile = self.profile_dir or _chrome_profile_dir()
        try:
            profile.mkdir(parents=True, exist_ok=True)
            if os.name != "nt":
                os.chmod(profile, 0o700)
        except OSError as exc:
            raise ChromeLaunchError("chrome_profile_locked") from exc

        args = [
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        try:
            self._proc = self._popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise ChromeLaunchError("chrome_launch_failed") from exc

    def _get_version(self, base: str, *, timeout_s: float) -> dict[str, Any] | None:
        try:
            resp = self._requests.get(f"{base}/json/version", timeout=timeout_s)
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def _wait_for_version(self, base: str) -> dict[str, Any] | None:
        deadline = time.monotonic() + _CONNECT_TIMEOUT_S
        while time.monotonic() < deadline:
            version = self._get_version(base, timeout_s=1.0)
            if version is not None:
                return version
            time.sleep(_POLL_INTERVAL_S)
        return None

    def _open_tab(self, base: str, login_url: str) -> dict[str, Any]:
        encoded = quote(login_url, safe="")
        for method in ("put", "get"):
            try:
                resp = getattr(self._requests, method)(
                    f"{base}/json/new?{encoded}", timeout=5.0
                )
            except Exception:
                continue
            if resp.status_code not in (200, 201):
                continue
            try:
                data = resp.json()
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
        raise ChromeLaunchError("chrome_cdp_unavailable")

    def _terminate_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass


_CDP_TO_PLAYWRIGHT_EVENT = {
    "Page.frameNavigated": "framenavigated",
}


class _CdpClient:
    def __init__(self, ws_url: str, *, websocket_factory=None) -> None:
        if websocket_factory is None:
            try:
                import websocket
            except ImportError as exc:
                raise ChromeLaunchError("chrome_cdp_unavailable") from exc
            websocket_factory = websocket.create_connection
        try:
            self._ws = websocket_factory(ws_url, timeout=5)
        except Exception as exc:
            raise ChromeLaunchError("chrome_cdp_unavailable") from exc
        self._next_id = 1
        self._event_callbacks: dict[str, list[Callable[..., Any]]] = {}
        self.send("Runtime.enable")
        self.send("Network.enable")
        self.send("Page.enable")

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self._event_callbacks.setdefault(event, []).append(callback)

    def _dispatch_event(self, method: str, params: dict[str, Any]) -> None:
        pw_event = _CDP_TO_PLAYWRIGHT_EVENT.get(method)
        if pw_event is None:
            return
        for cb in self._event_callbacks.get(pw_event, []):
            cb()

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        payload = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._ws.send(json.dumps(payload))
        while True:
            raw = self._ws.recv()
            data = json.loads(raw)
            data_id = data.get("id")
            if data_id is None:
                self._dispatch_event(data.get("method", ""), data.get("params", {}))
                continue
            if data_id != msg_id:
                continue
            if "error" in data:
                raise RuntimeError(data["error"])
            result = data.get("result", {})
            return result if isinstance(result, dict) else {}

    def evaluate(self, expression: str) -> Any:
        result = self.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        remote = result.get("result", {})
        if not isinstance(remote, dict):
            return None
        return remote.get("value")

    def current_url(self) -> str:
        value = self.evaluate("window.location.href")
        return value if isinstance(value, str) else ""

    def cookies_for(self, url: str) -> list[dict[str, Any]]:
        result = self.send("Network.getCookies", {"urls": [url]})
        cookies = result.get("cookies", [])
        return cookies if isinstance(cookies, list) else []

    def all_cookies(self) -> list[dict[str, Any]]:
        try:
            result = self.send("Network.getAllCookies")
        except Exception:
            result = self.send("Storage.getCookies")
        cookies = result.get("cookies", [])
        return cookies if isinstance(cookies, list) else []

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass


class _CdpContext:
    def __init__(self, cdp: _CdpClient) -> None:
        self._cdp = cdp

    def cookies(self, url: str) -> list[dict[str, Any]]:
        return self._cdp.cookies_for(url)


class _CdpElement:
    def __init__(self, cdp: _CdpClient, selector: str) -> None:
        self._cdp = cdp
        self._selector = selector

    def get_attribute(self, name: str) -> str | None:
        selector = json.dumps(self._selector)
        attr = json.dumps(name)
        value = self._cdp.evaluate(
            "(() => {"
            f"const el = document.querySelector({selector});"
            f"return el ? el.getAttribute({attr}) : null;"
            "})()"
        )
        return value if isinstance(value, str) else None


class _CdpPage:
    def __init__(self, cdp: _CdpClient) -> None:
        self._cdp = cdp
        self.context = _CdpContext(cdp)

    @property
    def url(self) -> str:
        return self._cdp.current_url()

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self._cdp.on(event, callback)

    def wait_for_url(self, pattern, timeout: int = BIND_TIMEOUT_MS) -> None:
        deadline = time.monotonic() + (timeout / 1000.0)
        while time.monotonic() < deadline:
            if pattern.search(self.url):
                return
            time.sleep(_POLL_INTERVAL_S)
        from playwright.sync_api import TimeoutError as PWTimeoutError

        raise PWTimeoutError(f"timed out waiting for URL matching {pattern.pattern}")

    def query_selector(self, selector: str) -> _CdpElement | None:
        exists = self._cdp.evaluate(
            "(() => !!document.querySelector(" + json.dumps(selector) + "))()"
        )
        if exists:
            return _CdpElement(self._cdp, selector)
        return None

    def evaluate(self, expression: str) -> Any:
        return self._cdp.evaluate(expression)


__all__ = ["RealChromeBrowserRunner", "_chrome_binary", "_chrome_profile_dir"]
