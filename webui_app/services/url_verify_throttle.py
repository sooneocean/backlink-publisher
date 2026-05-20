"""URL verify throttle — R8c rate-limit policy (Plan v1.0 Unit 2).

Process-wide rate limiting for live URL verification. Three independent caps
layered atop one another:

1. Per-session sliding window: <=6 acquires per 10s per ``session_id``.
2. Process-wide in-flight concurrency: ``BoundedSemaphore(3)``.
3. Per-host concurrent-fetch=1: an in-flight fetch for host H blocks others.

Plus a parallel RECON emission cap (<=1 RECON/10s/session) with a suppressed
counter that callers flush as a digest line.

Atomicity invariants (load-bearing for security review)
-------------------------------------------------------
* ``_window_locks_guard`` is the single source-of-truth lock that serialises
  ALL mutations to ``_session_windows``, ``_host_locks``, ``_host_last_used``,
  ``_recon_windows``, and ``_recon_suppressed``.
* Inside ``try_acquire`` the *check-then-reserve* of the session window happens
  in one critical section. We never observe ``len(window) < 6`` and then race
  another thread into appending past 6.
* The session-window slot is appended **before** the concurrency/host gates so
  the reservation is visible to peers. If a downstream gate fails the slot is
  popped under the same guard — the rollback is observable as
  ``len(window) == 0`` from the caller's perspective.
* ``release`` only releases the concurrency semaphore and the host lock; it
  does NOT pop the window slot (the slot models accepted requests over time
  and slides out naturally after 10s).
* Host locks are reused (never deleted on release) so two callers racing on
  the same host see the same lock. An opportunistic prune removes locks
  unused for >60s; the prune runs under guard.

The module is pure stdlib and never imports Flask — callers pass session_id
in as a plain string.
"""

from __future__ import annotations

import collections
import threading
import time
from typing import Deque, Dict, Optional

__all__ = [
    "try_acquire",
    "release",
    "should_emit_recon",
    "flush_recon_digest",
    "reset_state",
]

# ---- tunables ---------------------------------------------------------------
_WINDOW_SECONDS = 10.0
_MAX_PER_WINDOW = 6
_CONCURRENCY_CAP = 3
_RECON_WINDOW_SECONDS = 10.0
_RECON_MAX_PER_WINDOW = 1
_HOST_LOCK_IDLE_PRUNE_SECONDS = 60.0

# ---- module-level state -----------------------------------------------------
_session_windows: Dict[str, Deque[float]] = {}
_recon_windows: Dict[str, Deque[float]] = {}
_recon_suppressed: Dict[str, int] = {}
_host_locks: Dict[str, threading.Lock] = {}
_host_last_used: Dict[str, float] = {}

_concurrency_sem = threading.BoundedSemaphore(_CONCURRENCY_CAP)
_window_locks_guard = threading.Lock()


def _evict_old(window: Deque[float], now: float, span: float) -> None:
    """Drop entries older than ``now - span``. Caller holds the guard."""
    cutoff = now - span
    while window and window[0] <= cutoff:
        window.popleft()


def _prune_idle_host_locks(now: float) -> None:
    """Drop ``_host_locks`` entries idle for >60s. Caller holds the guard.

    A lock is only droppable if it's currently free (acquire(False) succeeds);
    if we drop a held lock we'd lose the host-busy semantics for the holder.
    """
    stale_hosts = [
        h
        for h, ts in _host_last_used.items()
        if now - ts > _HOST_LOCK_IDLE_PRUNE_SECONDS
    ]
    for host in stale_hosts:
        lock = _host_locks.get(host)
        if lock is None:
            _host_last_used.pop(host, None)
            continue
        if lock.acquire(blocking=False):
            try:
                _host_locks.pop(host, None)
                _host_last_used.pop(host, None)
            finally:
                lock.release()


def try_acquire(session_id: str, host: str) -> Optional[str]:
    """Reserve a verify slot. Return None on success, else closed-enum reason.

    Reasons:
      * ``"rate_limited"``      session window already has 6 entries in 10s.
      * ``"upstream_overloaded"`` process-wide in-flight cap (3) exhausted.
      * ``"host_busy"``         another in-flight fetch holds the host lock.

    Atomicity: the session-window check-then-reserve runs under
    ``_window_locks_guard`` so concurrent callers cannot both pass the check.
    On any downstream failure the reserved slot is rolled back under the same
    guard, so observers see ``len(window) == 0`` immediately after a failed
    acquire (modulo other concurrent successful acquires).

    On success the caller MUST eventually call ``release(host)``.
    """
    now = time.monotonic()

    # --- Critical section: session window check + reservation ---
    with _window_locks_guard:
        window = _session_windows.setdefault(session_id, collections.deque())
        _evict_old(window, now, _WINDOW_SECONDS)
        if len(window) >= _MAX_PER_WINDOW:
            return "rate_limited"
        window.append(now)  # provisional reservation

    # --- Process-wide concurrency cap ---
    if not _concurrency_sem.acquire(blocking=False):
        with _window_locks_guard:
            # Pop our own reservation (LIFO — we just appended).
            w = _session_windows.get(session_id)
            if w:
                try:
                    w.pop()
                except IndexError:
                    pass
        return "upstream_overloaded"

    # --- Per-host concurrent-fetch=1 ---
    with _window_locks_guard:
        host_lock = _host_locks.get(host)
        if host_lock is None:
            host_lock = threading.Lock()
            _host_locks[host] = host_lock
        _host_last_used[host] = now
        _prune_idle_host_locks(now)
        # Re-fetch in case prune removed-and-recreated; the lock object we
        # just stored should still be ours (it's pinned via the dict) but
        # be defensive:
        host_lock = _host_locks.setdefault(host, host_lock)

    if not host_lock.acquire(blocking=False):
        _concurrency_sem.release()
        with _window_locks_guard:
            w = _session_windows.get(session_id)
            if w:
                try:
                    w.pop()
                except IndexError:
                    pass
        return "host_busy"

    return None


def release(host: str) -> None:
    """Release the concurrency semaphore + host lock acquired in ``try_acquire``.

    Safe to call only after a successful ``try_acquire`` (one that returned
    None). The session window slot is intentionally NOT popped — it models
    accepted requests over time and slides out naturally.
    """
    # Release host lock first so a peer waiting on the same host can grab it
    # the instant the semaphore frees up.
    with _window_locks_guard:
        host_lock = _host_locks.get(host)
        _host_last_used[host] = time.monotonic()
    if host_lock is not None:
        try:
            host_lock.release()
        except RuntimeError:
            # Lock was not actually held — defensive no-op.
            pass
    try:
        _concurrency_sem.release()
    except ValueError:
        # BoundedSemaphore raises if released beyond initial value.
        pass


def should_emit_recon(session_id: str) -> bool:
    """Return True if RECON should emit now (<=1/10s/session).

    On suppression, increments ``_recon_suppressed[session_id]`` so a later
    ``flush_recon_digest`` can summarise the gap.
    """
    now = time.monotonic()
    with _window_locks_guard:
        window = _recon_windows.setdefault(session_id, collections.deque())
        _evict_old(window, now, _RECON_WINDOW_SECONDS)
        if len(window) >= _RECON_MAX_PER_WINDOW:
            _recon_suppressed[session_id] = _recon_suppressed.get(session_id, 0) + 1
            return False
        window.append(now)
        return True


def flush_recon_digest(session_id: str) -> Optional[int]:
    """Return suppressed-count and reset; or None if zero suppressed."""
    with _window_locks_guard:
        count = _recon_suppressed.get(session_id, 0)
        if count == 0:
            return None
        _recon_suppressed[session_id] = 0
        return count


def reset_state() -> None:
    """Test helper: clear all module-level state.

    Re-creates the bounded semaphore (its internal counter may have drifted
    if tests forgot to release). Use only in fixture teardown.
    """
    global _concurrency_sem
    with _window_locks_guard:
        _session_windows.clear()
        _recon_windows.clear()
        _recon_suppressed.clear()
        _host_locks.clear()
        _host_last_used.clear()
        _concurrency_sem = threading.BoundedSemaphore(_CONCURRENCY_CAP)
