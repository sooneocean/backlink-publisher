"""Medium browser-login helpers: probe, launch, and clear.

Cross-process lock: fcntl.flock sidecar (following anchor/profile.py doctrine
and Plan 012 velog precedent).  Both the CLI (MediumBrowserAdapter.publish)
and the webui (probe / launch) share the same lock file so only one Chromium
persistent-context is open against user_data_dir at a time.

Probe cooldown: mirrors MEDIUM_THROTTLE_MIN (60 s) so repeated probe clicks
don't accumulate Medium session-trust cost alongside a running publish batch.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config import Config

# ── Module-level in-process lock (gunicorn --threads safety) ─────────────────
_thread_lock = threading.Lock()

# ── Playwright import (None when not installed) ───────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as _PWTimeout
except ImportError:
    sync_playwright = None          # type: ignore[assignment]
    _PWTimeout = Exception          # type: ignore[misc]

_LOCK_FILENAME = "medium-browser.lock"
_COOLDOWN_FILENAME = "medium-probe-cooldown.json"
_COOLDOWN_SECS = 60
_UI_LOCK_TIMEOUT = 10      # seconds the UI waits before fail-fast
_MEDIUM_SIGNIN = "medium.com/m/signin"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lock_path(config: Config) -> Path:
    return config.config_dir / _LOCK_FILENAME


def _cooldown_path(config: Config) -> Path:
    # Use config.cache_dir for test isolation (BACKLINK_PUBLISHER_CACHE_DIR env)
    return config.cache_dir / _COOLDOWN_FILENAME


def _user_data_dir(config: Config) -> Path:
    return config.medium_user_data_dir or (config.config_dir / "chrome-profile-default")


def _check_cooldown(config: Config) -> None:
    """Raise ExternalServiceError if the probe was run < COOLDOWN_SECS ago."""
    path = _cooldown_path(config)
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
        last = float(data.get("last_probe_ts", 0))
        elapsed = time.time() - last
        if elapsed < _COOLDOWN_SECS:
            remaining = int(_COOLDOWN_SECS - elapsed)
            raise ExternalServiceError(
                f"Medium probe 冷却中（{remaining}s 后可再次探测）；"
                "避免触发 Medium 反检测，请稍候再试。"
            )
    except ExternalServiceError:
        raise
    except Exception:
        pass


def _write_cooldown(config: Config) -> None:
    path = _cooldown_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_probe_ts": time.time()}))


class _FileLock:
    """Context manager: threading.Lock + fcntl.flock sidecar.

    UI callers use timeout=_UI_LOCK_TIMEOUT (fail-fast flash).
    CLI (MediumBrowserAdapter) callers can pass a longer timeout.
    """

    def __init__(self, lock_path: Path, timeout: float = _UI_LOCK_TIMEOUT) -> None:
        self._path = lock_path
        self._timeout = timeout
        self._fd: int | None = None

    def __enter__(self) -> "_FileLock":
        _thread_lock.acquire()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # 0o600: only the owning uid can read/write
            self._fd = os.open(str(self._path), os.O_CREAT | os.O_WRONLY, 0o600)
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        os.close(self._fd)
                        self._fd = None
                        raise ExternalServiceError(
                            "已有 Medium 浏览器会话在运行（CLI publish 或另一操作）；"
                            "请稍后重试。"
                        )
                    time.sleep(0.5)
            # Write PID for stale-lock detection
            os.write(self._fd, str(os.getpid()).encode())
        except Exception:
            _thread_lock.release()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        try:
            if self._fd is not None:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
                self._fd = None
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
        finally:
            _thread_lock.release()


def _playwright_context(config: Config):
    """Return a Playwright persistent context (headed, anti-detect args)."""
    if sync_playwright is None:
        raise DependencyError(
            "Playwright 未安装，请运行 playwright install chromium"
        )
    udd = _user_data_dir(config)
    udd.mkdir(parents=True, exist_ok=True, mode=0o700)
    pw = sync_playwright().__enter__()
    ctx = pw.chromium.launch_persistent_context(
        str(udd),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, ctx


# ── Public API ────────────────────────────────────────────────────────────────

def launch_login_window(config: Config) -> dict:
    """Open a headed Chromium to medium.com/m/signin and wait for the user to
    log in (wait_for_url until URL is no longer the signin page, max 180 s).

    Acquires the shared lock so CLI publish cannot overlap.
    """
    with _FileLock(_lock_path(config), timeout=_UI_LOCK_TIMEOUT):
        pw, ctx = _playwright_context(config)
        page = ctx.new_page()
        t0 = time.monotonic()
        try:
            page.goto("https://medium.com/m/signin", timeout=30_000)
            page.wait_for_url(
                re.compile(r"https://medium\.com/(?!m/signin)"),
                timeout=180_000,
            )
            duration = int(time.monotonic() - t0)
            return {"logged_in": True, "duration_seconds": duration}
        except _PWTimeout:
            raise ExternalServiceError(
                "Medium 登录超时（180 s）；若启用了 email 验证码或 2FA 请尽快完成，"
                "否则请重试。"
            )
        finally:
            ctx.close()
            pw.__exit__(None, None, None)


def probe_login_status(config: Config, timeout: int = 15) -> dict:
    """Navigate to medium.com/me and detect whether the profile is logged in.

    Respects a 60-second cooldown to avoid Medium anti-detection.
    Acquires the shared lock.
    """
    _check_cooldown(config)
    with _FileLock(_lock_path(config), timeout=_UI_LOCK_TIMEOUT):
        pw, ctx = _playwright_context(config)
        page = ctx.new_page()
        try:
            page.goto("https://medium.com/me", timeout=timeout * 1_000)
            final_url = page.url
            logged_in = _MEDIUM_SIGNIN not in final_url
            username: str | None = None
            if logged_in:
                m = re.search(r"medium\.com/@([^/?]+)", final_url)
                username = m.group(1) if m else None
            _write_cooldown(config)
            return {"logged_in": logged_in, "final_url": final_url, "username": username}
        except _PWTimeout:
            raise ExternalServiceError(
                f"Medium probe 超时（{timeout}s）；请确认网络正常后重试。"
            )
        finally:
            ctx.close()
            pw.__exit__(None, None, None)


def clear_browser_profile(config: Config) -> None:
    """Delete the persistent Chromium profile directory."""
    import shutil
    udd = _user_data_dir(config)
    if udd.exists():
        shutil.rmtree(udd, ignore_errors=True)
