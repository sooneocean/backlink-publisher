"""Per-channel circuit breaker — Plan 2026-05-28-001 Unit 3.

State is persisted in ``<config_dir>/publish-circuit-state.json`` and
protected by an ``fcntl.LOCK_EX`` flock on a sibling ``.lock`` file,
matching the ``velog_graphql._acquire_lock`` pattern.

Fail-CLOSED contract: any read failure (JSONDecodeError, OSError, etc.)
causes :func:`is_tripped` to return ``True`` — a corrupt state file is
treated as "all channels tripped" until the operator runs
``reset_circuit()``.

Trip condition (v1): :class:`~backlink_publisher._util.errors.AuthExpiredError`
whose message contains ``ban``, ``banned``, or ``suspended`` (case-insensitive).
Plain session-expiry does NOT trip the breaker.

Cooldown: ``BACKLINK_PUBLISHER_CIRCUIT_COOLDOWN_S`` env var (default 300 s).
After the cooldown the breaker auto-resets on the next :func:`is_tripped`
call — no half-open state.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backlink_publisher._util.errors import AuthExpiredError, ExternalServiceError
from backlink_publisher._util.io import atomic_write_json
from backlink_publisher._util.logger import opencli_logger as _log

if TYPE_CHECKING:
    from backlink_publisher.config import Config


_LOCK_TIMEOUT: float = 60.0
_LOCK_POLL_INTERVAL: float = 0.1
_DEFAULT_COOLDOWN_S: int = 300

_BAN_SIGNALS: tuple[str, ...] = ("ban", "banned", "suspended")

_STATE_FILE = "publish-circuit-state.json"
_LOCK_FILE = "publish-circuit-state.lock"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _cooldown_s() -> int:
    try:
        return int(os.environ.get("BACKLINK_PUBLISHER_CIRCUIT_COOLDOWN_S", _DEFAULT_COOLDOWN_S))
    except (ValueError, TypeError):
        return _DEFAULT_COOLDOWN_S


def _acquire_lock(lock_path: Path) -> int:
    """Open and LOCK_EX *lock_path*, polling up to 60 s.

    Returns the open file descriptor (caller must close + release).
    Raises ExternalServiceError if the lock cannot be acquired within timeout.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    finally:
        os.umask(old_umask)
    os.chmod(lock_path, 0o600)

    deadline = time.monotonic() + _LOCK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            time.sleep(_LOCK_POLL_INTERVAL)

    os.close(fd)
    raise ExternalServiceError(
        "publish-circuit-state lock held > 60 s; check for stale process"
    )


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


def _state_path(config: Config) -> Path:
    return config.config_dir / _STATE_FILE


def _lock_path(config: Config) -> Path:
    return config.config_dir / _LOCK_FILE


def _read_state_unsafe(state_path: Path) -> dict[str, Any]:
    """Read state file without flock — caller must hold the lock."""
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def _write_state_unsafe(state_path: Path, state: dict[str, Any]) -> None:
    """Write state atomically (tmp + ``os.replace``) without flock — caller holds the lock.

    Atomic replace guarantees the lockless :func:`is_tripped` reader never sees a
    torn write, and a crash mid-write cannot leave a corrupt state file — which
    would otherwise fail-CLOSED and block *every* channel until operator reset.
    """
    atomic_write_json(state_path, state)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_tripped(platform: str, config: Config) -> bool:
    """Return True if the circuit is open for *platform*.

    Fail-CLOSED: any read error (JSONDecodeError, OSError, etc.) returns
    True — a corrupt state file is treated as all channels tripped.
    Auto-resets after cooldown has elapsed (no RMW needed for read path).
    """
    try:
        state_path = _state_path(config)
        if not state_path.exists():
            return False
        state = json.loads(state_path.read_text(encoding="utf-8"))
        entry = state.get(platform)
        if not entry or not entry.get("tripped"):
            return False
        tripped_at_iso = entry.get("tripped_at_iso")
        if not tripped_at_iso:
            return True  # corrupt entry → treat as tripped
        cooldown = _cooldown_s()
        try:
            tripped_at = datetime.fromisoformat(tripped_at_iso).timestamp()
        except (ValueError, TypeError):
            return True  # unparseable timestamp → fail-CLOSED
        if time.time() - tripped_at >= cooldown:
            # Cooldown expired — auto-reset (write is best-effort; caller continues)
            _auto_reset(platform, config)
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warn(
            f"circuit state read error for {platform!r} (fail-CLOSED): {exc}"
        )
        return True


def trip(platform: str, config: Config) -> None:
    """Trip the circuit for *platform* (flock-across-RMW)."""
    fd = _acquire_lock(_lock_path(config))
    try:
        try:
            state = _read_state_unsafe(_state_path(config))
        except (json.JSONDecodeError, OSError):
            state = {}
        state[platform] = {"tripped": True, "tripped_at_iso": _now_iso()}
        _write_state_unsafe(_state_path(config), state)
        _log.info({
            "event": "circuit_tripped",
            "platform": platform,
            "tripped_at_iso": state[platform]["tripped_at_iso"],
        })
    finally:
        _release_lock(fd)


def reset_circuit(platform: str, config: Config) -> None:
    """Reset tripped circuit for *platform* (operator / test use, flock-across-RMW)."""
    fd = _acquire_lock(_lock_path(config))
    try:
        try:
            state = _read_state_unsafe(_state_path(config))
        except (json.JSONDecodeError, OSError):
            state = {}
        state[platform] = {"tripped": False, "tripped_at_iso": None}
        _write_state_unsafe(_state_path(config), state)
        _log.info({"event": "circuit_reset", "platform": platform})
    finally:
        _release_lock(fd)


def _auto_reset(platform: str, config: Config) -> None:
    """Best-effort auto-reset after cooldown. Errors are swallowed."""
    try:
        fd = _acquire_lock(_lock_path(config))
        try:
            try:
                state = _read_state_unsafe(_state_path(config))
            except (json.JSONDecodeError, OSError):
                return  # can't read, skip
            entry = state.get(platform, {})
            if not entry.get("tripped"):
                return  # already reset by another process
            # Re-check cooldown inside lock
            tripped_at_iso = entry.get("tripped_at_iso", "")
            try:
                tripped_at = datetime.fromisoformat(tripped_at_iso).timestamp()
            except (ValueError, TypeError):
                return
            if time.time() - tripped_at >= _cooldown_s():
                state[platform] = {"tripped": False, "tripped_at_iso": None}
                _write_state_unsafe(_state_path(config), state)
                _log.info({"event": "circuit_auto_reset", "platform": platform})
        finally:
            _release_lock(fd)
    except Exception:  # noqa: BLE001
        pass


def is_ban_signal(exc: AuthExpiredError) -> bool:
    """True if *exc* carries a ban/suspend signal."""
    msg = str(exc).lower()
    return any(w in msg for w in _BAN_SIGNALS)
