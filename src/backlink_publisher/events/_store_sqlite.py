"""SQLite plumbing helpers extracted from ``store.py``.

Connection helpers, retry logic, statement-analysis, and file-hygiene
utilities shared by EventStore and any future SQLite-backed store.

Plan: ``docs/plans/2026-05-26-004-opt-projector-budget-rescue-plan.md``
Stage 2.1: Reader connection reuse with PID invalidation.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..config import _config_dir

#: Default filename inside ``_config_dir()``.
_DB_FILENAME: str = "events.db"

#: Max retry attempts on ``sqlite3.OperationalError`` (typically "disk I/O
#: error" or "database is locked" if it slips past busy_timeout). Three is
#: a deliberate small number; persistent errors should bubble to the
#: caller rather than mask a real problem.
_MAX_RETRIES: int = 3

#: Base backoff between retries (seconds). Multiplied by attempt number
#: for crude linear backoff. Injectable so tests can use a no-op sleep.
_BASE_BACKOFF_S: float = 0.05

#: macOS xattr key for Time Machine / iCloud backup exclusion.
_XATTR_BACKUP_EXCLUDE: str = "com.apple.metadata:com_apple_backup_excludeItem"


def _default_db_path() -> Path:
    return _config_dir() / _DB_FILENAME


def _is_select_statement(sql: str) -> bool:
    """Return True iff ``sql`` is a single SELECT or WITH-prefixed SELECT.

    Trims leading whitespace and SQL block comments. Rejects multi-
    statement input (anything past the first ``;`` that isn't trailing
    whitespace) — caller in the SELECT-only contract must not chain.
    """
    stripped = sql.lstrip()
    # Strip leading ``/* ... */`` block comments (one level is enough; we
    # do not need to support nested comments here).
    while stripped.startswith("/*"):
        end = stripped.find("*/")
        if end == -1:
            return False
        stripped = stripped[end + 2 :].lstrip()
    head = stripped[:10].upper()
    if not (head.startswith("SELECT ") or head.startswith("SELECT\n")
            or head.startswith("WITH ") or head.startswith("WITH\n")):
        return False
    # Multi-statement guard: a trailing ``;`` is fine, but anything
    # non-whitespace after it is rejected.
    tail = stripped.rstrip()
    if ";" in tail[:-1]:
        return False
    return True


def _tighten_wal_sidecars(db_path: Path) -> None:
    """Chmod ``db_path``-wal / ``-shm`` to 0o600 if present.

    SQLite creates WAL/SHM lazily on first write using the process umask,
    which is typically 0o022 → 0o644 — wide enough to leak uncheckpointed
    event payloads. Best-effort: missing files and chmod failures are
    silent (Windows POSIX modes are not meaningful; macOS sandbox or
    file-system flags can also reject chmod).
    """
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            try:
                os.chmod(side, 0o600)
            except OSError:
                pass


def _set_backup_exclude_xattr(path: Path) -> None:
    """Best-effort backup-exclusion mark on macOS; no-op elsewhere.

    Plan §U10 extends this to ``persona.salt``, ``token/``, and the WAL
    side files. U1 only handles ``events.db`` itself at first create.
    Failures are silent (subprocess missing, kernel rejects) — the file
    is still created and usable; backup exclusion is defense-in-depth.
    """
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["xattr", "-w", _XATTR_BACKUP_EXCLUDE, "1", str(path)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        # xattr binary missing or subprocess died. Don't crash event-store
        # init over a backup-hygiene improvement.
        pass


def _is_transient_sqlite_error(exc: sqlite3.OperationalError) -> bool:
    """Whether ``exc`` is worth retrying.

    Restricted to the two messages we expect to recover from automatically
    — disk-I/O glitches and lock-contention misses. Anything else (table
    missing, syntax error, type mismatch) is a programming error and
    should surface immediately.
    """
    msg = str(exc).lower()
    return "disk i/o error" in msg or "database is locked" in msg


def _retry_sqlite(
    op: Callable[[], Any],
    *,
    max_retries: int = _MAX_RETRIES,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any:
    """Run ``op`` with bounded retry on transient ``OperationalError``.

    Tests inject ``sleep_fn`` to skip real sleeping. The same error class
    is re-raised after ``max_retries`` exhausted so callers see the
    underlying failure rather than a synthetic wrapper.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return op()
        except sqlite3.OperationalError as exc:
            if not _is_transient_sqlite_error(exc):
                raise
            last_exc = exc
            if attempt < max_retries:
                sleep_fn(_BASE_BACKOFF_S * attempt)
    assert last_exc is not None
    raise last_exc


def _now_iso_utc() -> str:
    """ISO-8601 UTC timestamp, e.g. ``2026-05-18T12:00:00+00:00``."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    """Return True if ``pid`` names a live process on this host.

    Uses POSIX ``kill(pid, 0)``: ``ProcessLookupError`` (ESRCH) means the
    PID does not exist; ``PermissionError`` (EPERM) means the PID exists
    but is owned by a different user — still treated as alive so we never
    steal a lease from a live process. ``OSError`` from any other errno
    also resolves to alive to fail safe (don't take over on unknown
    state). PID 0 / negative is treated as not alive (sentinel).
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


# --- Stage 2.1: Reader connection cache with PID invalidation ----------

#: Cache for reader connections keyed by db_path (Path objects are hashable)
#: Includes special "_pid" key to track the owning PID
_reader_cache: dict[Path, sqlite3.Connection | int] = {}
_reader_lock = threading.Lock()

#: Environment variable to control reader reuse (default 0 in CLI, 1 in WebUI)
_REUSE_ENV_VAR = "BACKLINK_EVENTSTORE_REUSE_CONN"


def get_read_connection(db_path: Path | None = None) -> sqlite3.Connection | None:
    """Get a cached reader connection, or open one if none exists.

    Reader connections are cached for the process lifetime and invalidated
    when the PID changes (e.g., after os.fork() in multiprocessing scenarios).
    Controlled by BACKLINK_EVENTSTORE_REUSE_CONN env var (default: 0/False).

    Returns None if env var is not set or reader reuse is disabled.
    Caller does NOT own the connection — it is cached and reused.
    Do NOT call close() on the returned connection.
    """
    try:
        reuse = os.environ.get(_REUSE_ENV_VAR, "0") == "1"
    except (ValueError, TypeError):
        return None

    if not reuse:
        return None

    current_pid = os.getpid()
    if db_path is None:
        db_path = _default_db_path()

    with _reader_lock:
        cached_pid = _reader_cache.get(Path("_pid"))  # type: ignore[arg-type]
        if cached_pid != current_pid:
            # PID changed (fork scenario) — clear the cache
            for key, conn in list(_reader_cache.items()):
                if isinstance(conn, sqlite3.Connection):
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            _reader_cache.clear()
            _reader_cache[Path("_pid")] = current_pid  # type: ignore[arg-type]

        if db_path not in _reader_cache:
            # Open a new reader connection (only PRAGMAs, no schema upgrade needed for readers)
            if not db_path.exists():
                # No database yet — return None to let caller handle
                return None
            conn = sqlite3.connect(str(db_path), timeout=5.0)
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN")  # Keep a shared lock for reader consistency
            _reader_cache[db_path] = conn
            return conn

        conn = _reader_cache.get(db_path)
        if isinstance(conn, sqlite3.Connection):
            return conn
        return None


def reset_reader_cache() -> None:
    """Clear cached reader connections. Used by tests and fork detection."""
    with _reader_lock:
        for key, conn in list(_reader_cache.items()):
            if isinstance(conn, sqlite3.Connection):
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        _reader_cache.clear()
