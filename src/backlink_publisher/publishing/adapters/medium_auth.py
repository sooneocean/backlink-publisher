"""Medium browser-login helpers: probe, launch, and clear.

Extracted from webui_app/medium_login.py — Wave 1 thin-WebUI refactor.
Imports already use backlink_publisher core paths; no import changes needed.

Cross-process lock: fcntl.flock sidecar (following anchor/profile.py doctrine
and Plan 012 velog precedent).  The lock coordinates interactive login only,
since probe now uses non-persistent context isolation.

Probe isolation (post-Plan 005): ``probe_login_status`` uses headless
non-persistent context + cookies from ``medium-cookies.json`` — never shares
the persistent user_data_dir with ``launch_login_window``. This eliminates
contention and removes the need for 60s cooldown.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import threading
import time
from pathlib import Path

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config import Config

# ── Module-level in-process lock (gunicorn --threads safety) ─────────────────
_thread_lock = threading.Lock()

# ── Playwright import (None when not installed) ───────────────────────────────
try:
    from playwright.sync_api import sync_playwright, TimeoutError as _PWTimeout
    from playwright.sync_api import Error as _PWError
except ImportError:
    sync_playwright = None          # type: ignore[assignment]  # reason: fallback when playwright not installed; guarded by DependencyError
    _PWTimeout = Exception          # type: ignore[misc]  # reason: fallback type alias for optional dependency
    _PWError = Exception            # type: ignore[misc]  # reason: fallback type alias for optional dependency

_LOCK_FILENAME = "medium-browser.lock"
_UI_LOCK_TIMEOUT = 10      # seconds the UI waits before fail-fast
_MEDIUM_SIGNIN = "medium.com/m/signin"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _lock_path(config: Config) -> Path:
    return config.config_dir / _LOCK_FILENAME


def _user_data_dir(config: Config) -> Path:
    """Persistent Chromium profile for interactive login only.

    The probe uses non-persistent context, so this profile is NEVER shared
    between bind/publish/probe operations. Eliminates race conditions.
    """
    return config.medium_user_data_dir or (config.config_dir / "chrome-profile-default")


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


def _probe_playwright_context(config: Config):
    """Return a Playwright context for liveness probing.

    Non-persistent launch + browser.new_context() for probe-copy isolation.
    The live cookies.json is read into memory and injected via add_cookies,
    so even if Cloudflare flags this headless session, the persistent profile
    used by launch_login_window remains untouched.
    """
    if sync_playwright is None:
        raise DependencyError(
            "Playwright 未安装，请运行 playwright install chromium"
        )
    pw_cm = sync_playwright()
    pw = pw_cm.__enter__()
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context()
    return pw_cm, browser, context


def _playwright_context(config: Config):
    """Return a Playwright persistent context (headed, anti-detect args).

    Used only by launch_login_window for interactive operator login.
    Returns ``(pw_cm, ctx)``: ``pw_cm`` is the ``PlaywrightContextManager``
    (the object that owns ``__exit__``); ``pw_cm.__enter__()`` returns the
    ``Playwright`` *instance*, which itself has no ``__exit__``. Callers
    must call ``pw_cm.__exit__(None, None, None)`` to tear Playwright down.
    """
    if sync_playwright is None:
        raise DependencyError(
            "Playwright 未安装，请运行 playwright install chromium"
        )
    udd = _user_data_dir(config)
    udd.mkdir(parents=True, exist_ok=True, mode=0o700)
    pw_cm = sync_playwright()
    pw = pw_cm.__enter__()
    ctx = pw.chromium.launch_persistent_context(
        str(udd),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw_cm, ctx


def _load_cookies_for_probe(path: Path) -> list[dict]:
    """Load cookies from medium-cookies.json for probe context."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("cookies", [])
    except Exception:
        return []


# ── Public API ────────────────────────────────────────────────────────────────

def launch_login_window(config: Config) -> dict:
    """Open a headed Chromium to medium.com/m/signin and wait for the user to
    log in (wait_for_url until URL is no longer the signin page, max 180 s).

    Acquires the shared lock so CLI publish cannot overlap.
    """
    with _FileLock(_lock_path(config), timeout=_UI_LOCK_TIMEOUT):
        pw_cm, ctx = _playwright_context(config)
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
        except _PWError as e:
            # User-closed window or browser crash mid-login throws
            # ``Target page, context or browser has been closed``. Treat
            # any non-timeout Playwright runtime error as a clean cancel
            # rather than 500.
            msg = str(e)
            if "closed" in msg.lower():
                raise ExternalServiceError(
                    "登录窗口已关闭。如需重试请再次点击「打开浏览器登录」。"
                )
            raise ExternalServiceError(f"Medium 登录失败：{msg}")
        finally:
            try:
                ctx.close()
            except _PWError:
                pass  # context may already be closed (user-closed window)
            pw_cm.__exit__(None, None, None)


def probe_login_status(config: Config, timeout: int = 15) -> dict:
    """Navigate to medium.com/me and detect whether the profile is logged in.

    Uses non-persistent headless context for probe-copy isolation (live
    credentials untouched). No shared user_data_dir contention with bind/publish.
    Cooldown removed — probe is now safe to run without rate-limiting.
    """
    with _FileLock(_lock_path(config), timeout=_UI_LOCK_TIMEOUT):
        pw_cm, browser, ctx = _probe_playwright_context(config)
        # Load cookies from canonical credential (post-Plan 005)
        cookies_path = config.config_dir / "medium-cookies.json"
        cookies = _load_cookies_for_probe(cookies_path) if cookies_path.exists() else []
        if cookies:
            try:
                ctx.add_cookies(cookies)
            except Exception:
                pass  # Cookie format incompatibility; proceed without
        page = ctx.new_page()
        try:
            page.goto("https://medium.com/me", timeout=timeout * 1_000)
            final_url = page.url
            logged_in = _MEDIUM_SIGNIN not in final_url
            username: str | None = None
            if logged_in:
                m = re.search(r"medium\.com/@([^/?]+)", final_url)
                username = m.group(1) if m else None
            return {"logged_in": logged_in, "final_url": final_url, "username": username}
        except _PWTimeout:
            raise ExternalServiceError(
                f"Medium probe 超时（{timeout}s）；请确认网络正常后重试。"
            )
        except _PWError as e:
            msg = str(e)
            if "closed" in msg.lower():
                raise ExternalServiceError("Medium probe 中断：浏览器窗口被关闭。")
            raise ExternalServiceError(f"Medium probe 失败：{msg}")
        finally:
            try:
                ctx.close()
                browser.close()
            except _PWError:
                pass
            pw_cm.__exit__(None, None, None)


def clear_browser_profile(config: Config) -> None:
    """Delete the persistent Chromium profile directory.

    Post-Plan 005: probe uses non-persistent context, so this only affects
    launch_login_window (interactive bind). Safe to call without affecting
    publish or probe operations.
    """
    import shutil
    udd = _user_data_dir(config)
    if udd.exists():
        shutil.rmtree(udd, ignore_errors=True)
