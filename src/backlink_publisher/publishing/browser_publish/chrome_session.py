"""Shared Chrome/CDP helpers + ChromeAttachSession context manager.

Plan 2026-05-21-001 Unit 1. Bridges bind (cli/_bind/chrome_backend.py)
and publish phases on a single Chrome lifecycle abstraction.

Design notes calibrated against Unit 0 spike
(`docs/spikes/2026-05-21-chrome-lifecycle-spike.md`):

- Probe 1: teardown uses ``proc.terminate()`` + ``proc.wait(timeout=5)``;
  ``os.killpg`` raises EPERM from outside the new session leader's lineage
  on macOS and is not necessary — Chrome reaps helpers on SIGTERM.
- Probe 2: attach-mode listener identity check uses ``lsof -iTCP:<port>
  -Fp`` + ``ps -o command=`` substring match against chrome_bin AND
  profile dir. ``ps -o comm=`` truncates to ~15 chars on macOS, unusable.
- Probe 3: ``os.chmod(profile, 0o700)`` works in ``$TMPDIR`` / user config
  dir — no SIP fail-soft fallback needed.
- Probe 4: ``_chrome_profile_dir()`` now honors
  ``BACKLINK_PUBLISHER_BIND_CHANNEL`` (net-new in Unit 1, not in main's
  PR #129 baseline). Channel name is whitelisted ``[a-z0-9_-]+`` to
  prevent path traversal.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from backlink_publisher._util.errors import DependencyError
from backlink_publisher.config.loader import _config_dir

if TYPE_CHECKING:
    from playwright.sync_api import Page


_DEFAULT_PORT = 9222
_CONNECT_TIMEOUT_S = 10.0
_POLL_INTERVAL_S = 0.25
_TERMINATE_TIMEOUT_S = 5.0
_CHANNEL_RE = re.compile(r"^[a-z0-9_-]+$")
_PID_FILE_NAME = "real-chrome-publish.pid"
_PROFILE_LOCK_NAME = "chrome-profile.lock"


class ChromeSessionError(DependencyError):
    """Chrome lifecycle errors — dispatcher falls through to next adapter."""


# ---------------------------------------------------------------------------
# Path / binary discovery helpers (single source of truth for bind + publish)
# ---------------------------------------------------------------------------


def _chrome_binary() -> str | None:
    """Resolve Chrome executable path. ``None`` if not found.

    Honors ``BACKLINK_PUBLISHER_REAL_CHROME_BIN`` env override; otherwise
    walks the macOS/Linux candidate list.
    """
    raw = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_BIN")
    if raw:
        path = Path(raw).expanduser()
        return str(path) if path.exists() else None

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _chrome_port() -> int:
    """Resolve CDP debugging port. Honors ``BACKLINK_PUBLISHER_REAL_CHROME_PORT``.

    Raises ``ChromeSessionError("chrome_cdp_unavailable")`` for invalid or
    out-of-range values.
    """
    raw = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_PORT")
    if not raw:
        return _DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ChromeSessionError("chrome_cdp_unavailable") from exc
    if port < 1 or port > 65535:
        raise ChromeSessionError("chrome_cdp_unavailable")
    return port


def _chrome_profile_dir() -> Path:
    """Resolve profile dir, with per-channel split when bind-channel env set.

    Precedence:
      1. ``BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR`` env override → as-is.
      2. ``BACKLINK_PUBLISHER_BIND_CHANNEL`` env (whitelisted ``[a-z0-9_-]+``) →
         ``<config_dir>/real-chrome-profile/<channel>``.
      3. Default → ``<config_dir>/real-chrome-profile``.

    Per-channel split prevents cross-channel anti-bot fingerprint
    contamination (telegraph's Cloudflare cookies leaking into velog's
    Google SSO session, etc.) — see plan D3.
    """
    raw = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR")
    if raw:
        return Path(raw).expanduser()

    base = _config_dir() / "real-chrome-profile"
    channel = os.environ.get("BACKLINK_PUBLISHER_BIND_CHANNEL")
    if channel:
        if not _CHANNEL_RE.fullmatch(channel):
            raise ChromeSessionError("chrome_invalid_bind_channel")
        return base / channel
    return base


def _websocket_available() -> bool:
    """``websocket-client`` import probe (bind uses raw websocket, kept for parity)."""
    try:
        import websocket  # noqa: F401
    except ImportError:
        return False
    return True


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _cdp_available() -> bool:
    """Both binary and a CDP client library must be importable."""
    return _chrome_binary() is not None and (
        _websocket_available() or _playwright_available()
    )


# ---------------------------------------------------------------------------
# Listener identity verification (attach mode)
# ---------------------------------------------------------------------------


def _lsof_listener_pid(port: int) -> int | None:
    """Parse ``lsof -iTCP:<port> -sTCP:LISTEN -Fp`` for the listener PID.

    Returns ``None`` if lsof is missing or no listener is found. macOS
    ships lsof at ``/usr/sbin/lsof``; Linux distros vary.
    """
    try:
        result = subprocess.run(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp", "-n", "-P"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                return int(line[1:])
            except ValueError:
                continue
    return None


def _ps_command(pid: int) -> str | None:
    """Return full cmdline via ``ps -o command= -p <pid>``. ``None`` on failure.

    Uses ``command=`` (full cmdline) not ``comm=`` — macOS truncates
    ``comm`` to ~15 chars, unusable for substring identity checks
    (Probe 2 finding).
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    out = result.stdout.strip()
    return out or None


def _verify_listener_is_chrome(
    port: int, chrome_bin: str, profile: Path
) -> tuple[bool, str]:
    """Confirm the CDP listener on ``port`` is OUR Chrome (not port squatter).

    Returns ``(verified, reason)``. Verified iff:
      - lsof returns a PID;
      - ``ps -o command=`` returns a cmdline string;
      - ``chrome_bin`` substring AND ``str(profile)`` substring both occur
        in the cmdline.

    Both substrings must match to defeat the trivial "I'm a Chrome running
    against a different profile" case (which we must not silently adopt).
    """
    pid = _lsof_listener_pid(port)
    if pid is None:
        return False, "lsof_no_listener"
    cmdline = _ps_command(pid)
    if cmdline is None:
        return False, "ps_no_cmdline"
    if chrome_bin not in cmdline:
        return False, "cmdline_missing_chrome_bin"
    if str(profile) not in cmdline:
        return False, "cmdline_missing_profile_path"
    return True, "ok"


# ---------------------------------------------------------------------------
# Profile perms
# ---------------------------------------------------------------------------


def _ensure_profile_perms(profile: Path) -> None:
    """``stat`` + ``chmod 0o700`` profile dir. Raises on permission mismatch.

    Per Probe 3: chmod works in normal user config locations. Failure is
    surfaced as ``ChromeSessionError("chrome_profile_unsafe_perms")`` so
    the dispatcher falls through.
    """
    try:
        profile.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":  # Windows: no posix perm model
            return
        st = profile.stat()
        if st.st_uid != os.geteuid():
            raise ChromeSessionError("chrome_profile_unsafe_perms")
        if stat.S_IMODE(st.st_mode) & 0o077:
            os.chmod(profile, 0o700)
    except OSError as exc:
        raise ChromeSessionError("chrome_profile_locked") from exc


# ---------------------------------------------------------------------------
# PID file (orphan reap)
# ---------------------------------------------------------------------------


def _pid_file_path() -> Path:
    return _config_dir() / _PID_FILE_NAME


def _write_pid_file(pid: int, start_time: float) -> None:
    """Atomic 0o600 write of ``{"pid": ..., "start_time": ...}``."""
    path = _pid_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = json.dumps({"pid": pid, "start_time": round(start_time, 3)})
    tmp.write_text(payload)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _read_pid_file() -> dict | None:
    path = _pid_file_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _unlink_pid_file() -> None:
    try:
        _pid_file_path().unlink()
    except FileNotFoundError:
        pass


def reap_orphan_publish_chrome() -> dict:
    """Webui startup hook: reap any leftover publish-launched Chrome.

    Reads the PID file; verifies (a) the recorded PID still exists,
    (b) its cmdline contains chrome_bin AND profile path — defending
    against PID reuse — and terminates the parent process if both hold.
    Returns a small dict for telemetry.
    """
    record = _read_pid_file()
    if record is None:
        return {"action": "noop", "reason": "no_pid_file"}
    pid = record.get("pid")
    if not isinstance(pid, int):
        _unlink_pid_file()
        return {"action": "cleaned_stale", "reason": "malformed_pid_file"}

    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        _unlink_pid_file()
        return {"action": "cleaned_stale", "reason": "pid_gone", "pid": pid}

    chrome_bin = _chrome_binary()
    profile = _chrome_profile_dir()
    cmdline = _ps_command(pid)
    if (
        chrome_bin is None
        or cmdline is None
        or chrome_bin not in cmdline
        or str(profile) not in cmdline
    ):
        # PID exists but is NOT our Chrome — refuse to signal it (PID reuse).
        _unlink_pid_file()
        return {
            "action": "cleaned_stale",
            "reason": "pid_reused_by_unrelated_process",
            "pid": pid,
        }

    try:
        os.kill(pid, signal_SIGTERM())
    except ProcessLookupError:
        pass
    _unlink_pid_file()
    return {"action": "reaped", "pid": pid}


def signal_SIGTERM() -> int:
    """Indirection to keep ``signal`` import lazy + Windows-safe."""
    import signal as _signal

    return _signal.SIGTERM


# ---------------------------------------------------------------------------
# BrowserPublishRecipe
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserPublishRecipe:
    """One channel's browser-publish flow.

    ``channel`` mirrors the bind ``ChannelRecipe.channel`` slug. ``compose_url``
    is the post-login landing URL; ``publish_flow`` drives the Playwright
    Page to publish ``payload`` and return the final post URL.

    Recipes are registered in ``recipes/__init__.py`` and consumed by
    ``BrowserPublishDispatcher`` (Unit 2).
    """

    channel: str
    compose_url: str
    publish_flow: Callable[..., str]


# ---------------------------------------------------------------------------
# ChromeAttachSession
# ---------------------------------------------------------------------------


class ChromeAttachSession(AbstractContextManager):
    """Attach to existing Chrome CDP, or launch a new one. Yields a Page.

    Lifecycle (per plan D2 + Unit 0 spike):

      __enter__:
        1. Resolve profile dir (per-channel via env), enforce 0o700 perms.
        2. Probe ``http://127.0.0.1:<port>/json/version``:
           - 200 + ``BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1`` → verify
             listener identity (lsof+ps), then attach (``self._owned=False``).
           - 200 without attach opt-in → ``ChromeSessionError`` (refuse
             to silently take over operator's personal Chrome).
           - Not 200 → launch our own Chrome (``self._owned=True``),
             write PID file, poll until ready.
        3. Playwright ``connect_over_cdp(ws_url)`` → ``new_page()`` → yield.

      __exit__:
        - Close the page.
        - ``_owned=True`` → ``proc.terminate()`` + ``proc.wait(timeout=5)``
          + unlink PID file. **NOT** ``killpg`` (Probe 1: EPERM on macOS).
        - ``_owned=False`` → leave the attached Chrome alone.

    All error paths raise ``ChromeSessionError`` (a ``DependencyError``)
    so the dispatcher (Unit 2) can fall through to the next adapter.
    """

    def __init__(
        self,
        channel: str,
        *,
        port: int | None = None,
        profile_dir: Path | None = None,
        chrome_bin: str | None = None,
        # Injection seams for tests:
        popen: Callable[..., Any] = subprocess.Popen,
        playwright_factory: Callable[[], Any] | None = None,
        version_probe: Callable[[str], dict | None] | None = None,
        verify_listener: Callable[[int, str, Path], tuple[bool, str]] | None = None,
    ) -> None:
        self.channel = channel
        self._port = port
        self._profile_dir = profile_dir
        self._chrome_bin = chrome_bin
        self._popen = popen
        self._playwright_factory = playwright_factory
        self._version_probe = version_probe or _default_version_probe
        self._verify_listener = verify_listener or _verify_listener_is_chrome

        self._proc: Any | None = None
        self._pw_cm: Any | None = None
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._page: Any | None = None
        self._owned: bool = False

    # ------------------------------------------------------------------
    # context manager protocol
    # ------------------------------------------------------------------

    def __enter__(self):
        chrome_bin = self._chrome_bin or _chrome_binary()
        if not chrome_bin:
            raise ChromeSessionError("chrome_not_available")

        port = self._port if self._port is not None else _chrome_port()
        profile = self._profile_dir or _chrome_profile_dir()
        _ensure_profile_perms(profile)

        base = f"http://127.0.0.1:{port}"
        version = self._version_probe(base)

        if version is not None:
            # CDP listener present. Operator must explicitly opt-in to
            # attach to it — otherwise it's likely the operator's daily
            # Chrome and we must not silently take it over.
            if os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_ATTACH") != "1":
                raise ChromeSessionError("chrome_cdp_unavailable")
            verified, reason = self._verify_listener(port, chrome_bin, profile)
            if not verified:
                raise ChromeSessionError(f"chrome_cdp_foreign_listener:{reason}")
            self._owned = False
        else:
            self._launch_chrome(chrome_bin, port, profile)
            version = self._wait_for_version(base)
            if version is None:
                self._teardown_owned_proc()
                raise ChromeSessionError("chrome_launch_failed")
            self._owned = True

        ws_url = version.get("webSocketDebuggerUrl")
        if not ws_url:
            self._teardown_owned_proc()
            raise ChromeSessionError("chrome_cdp_unavailable")

        self._page = self._connect_playwright(ws_url)
        return self._page

    def __exit__(self, exc_type, exc, tb):
        # Close page first (best-effort).
        for closer in (self._page, self._browser):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:
                pass

        if self._pw_cm is not None:
            try:
                self._pw_cm.__exit__(None, None, None)
            except Exception:
                pass

        if self._owned:
            self._teardown_owned_proc()
        # Never suppress exceptions.
        return False

    # ------------------------------------------------------------------
    # internal: launch + connect
    # ------------------------------------------------------------------

    def _launch_chrome(self, chrome_bin: str, port: int, profile: Path) -> None:
        args = [
            chrome_bin,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
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
            raise ChromeSessionError("chrome_launch_failed") from exc

        start_time = time.time()
        try:
            _write_pid_file(self._proc.pid, start_time)
        except OSError:
            # PID file is for orphan reap — non-fatal if write fails;
            # teardown still runs via __exit__'s in-memory proc handle.
            pass

    def _wait_for_version(self, base: str) -> dict | None:
        deadline = time.monotonic() + _CONNECT_TIMEOUT_S
        while time.monotonic() < deadline:
            version = self._version_probe(base)
            if version is not None:
                return version
            time.sleep(_POLL_INTERVAL_S)
        return None

    def _connect_playwright(self, ws_url: str) -> "Page":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            self._teardown_owned_proc()
            raise ChromeSessionError("chrome_cdp_unavailable") from exc

        factory = self._playwright_factory or sync_playwright
        try:
            self._pw_cm = factory()
            self._pw = self._pw_cm.__enter__()
            self._browser = self._pw.chromium.connect_over_cdp(ws_url)
            contexts = self._browser.contexts
            ctx = contexts[0] if contexts else self._browser.new_context()
            pages = ctx.pages
            return pages[0] if pages else ctx.new_page()
        except Exception as exc:
            # Tear down any partial state then propagate as ChromeSessionError.
            for closer in (self._browser,):
                if closer is not None:
                    try:
                        closer.close()
                    except Exception:
                        pass
            if self._pw_cm is not None:
                try:
                    self._pw_cm.__exit__(None, None, None)
                except Exception:
                    pass
            self._teardown_owned_proc()
            raise ChromeSessionError("chrome_cdp_unavailable") from exc

    def _teardown_owned_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=_TERMINATE_TIMEOUT_S)
        except Exception:
            # Process didn't exit; kill() is last resort. Note: not
            # killpg — Probe 1 found EPERM on macOS even as parent.
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
        finally:
            self._proc = None
            _unlink_pid_file()


def _default_version_probe(base: str, timeout_s: float = 1.0) -> dict | None:
    """Lightweight ``/json/version`` HTTP probe. Returns dict on 200, else None."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}/json/version", timeout=timeout_s) as resp:
            if resp.status != 200:
                return None
            raw = resp.read()
    except (urllib.error.URLError, socket.timeout, ConnectionError):
        return None
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


__all__ = [
    "BrowserPublishRecipe",
    "ChromeAttachSession",
    "ChromeSessionError",
    "_chrome_binary",
    "_chrome_port",
    "_chrome_profile_dir",
    "_websocket_available",
    "_cdp_available",
    "_verify_listener_is_chrome",
    "_ensure_profile_perms",
    "reap_orphan_publish_chrome",
]
