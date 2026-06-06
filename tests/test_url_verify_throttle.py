"""Tests for ``webui_app.services.url_verify_throttle`` (Plan v1.0 Unit 2).

Covers the three-layer rate-limit policy:
* per-session sliding 10s window (cap 6)
* process-wide in-flight semaphore (cap 3)
* per-host concurrent-fetch=1 lock
plus RECON cap (1/10s/session) + suppressed digest.

Concurrency tests use ``threading.Barrier`` to align worker starts.
Time-sliding tests monkeypatch ``time.monotonic`` on the module.
"""

from __future__ import annotations

import threading
from typing import List, Optional

import pytest

from webui_app.services import url_verify_throttle as tht


@pytest.fixture(autouse=True)
def _reset_throttle_state():
    """Each test starts with a clean module state."""
    tht.reset_state()
    yield
    tht.reset_state()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_single_call_returns_none_and_release_clears():
    assert tht.try_acquire("s1", "example.com") is None
    tht.release("example.com")
    # Subsequent acquire on same host works (lock was released).
    assert tht.try_acquire("s1", "example.com") is None
    tht.release("example.com")


def test_release_safe_to_call_repeatedly_no_crash():
    assert tht.try_acquire("s1", "example.com") is None
    tht.release("example.com")
    # Defensive: second release must not crash (already released).
    tht.release("example.com")


# ---------------------------------------------------------------------------
# Per-session sliding window (cap 6 / 10s)
# ---------------------------------------------------------------------------


def test_six_sequential_acquires_seventh_rate_limited(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    for i in range(6):
        # Use distinct hosts so host_busy never triggers, and release after
        # each so semaphore stays free.
        assert tht.try_acquire("s1", f"h{i}.com") is None
        tht.release(f"h{i}.com")
        fake_now[0] += 0.1  # Within window

    # 7th must be rate_limited (still inside 10s).
    assert tht.try_acquire("s1", "h6.com") == "rate_limited"


def test_window_slides_after_eleven_seconds(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    for i in range(6):
        assert tht.try_acquire("s1", f"h{i}.com") is None
        tht.release(f"h{i}.com")
        fake_now[0] += 0.1

    assert tht.try_acquire("s1", "h6.com") == "rate_limited"

    # Slide past window.
    fake_now[0] += 11.0
    assert tht.try_acquire("s1", "h6.com") is None
    tht.release("h6.com")


def test_empty_session_id_groups_anonymous(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    for i in range(6):
        assert tht.try_acquire("", f"h{i}.com") is None
        tht.release(f"h{i}.com")
        fake_now[0] += 0.1

    assert tht.try_acquire("", "h6.com") == "rate_limited"


def test_separate_sessions_have_independent_windows(monkeypatch):
    fake_now = [1000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    for i in range(6):
        assert tht.try_acquire("s1", f"h{i}.com") is None
        tht.release(f"h{i}.com")
        fake_now[0] += 0.1

    # s1 is rate-limited, s2 is fresh.
    assert tht.try_acquire("s1", "x.com") == "rate_limited"
    assert tht.try_acquire("s2", "x.com") is None
    tht.release("x.com")


# ---------------------------------------------------------------------------
# Reservation rollback
# ---------------------------------------------------------------------------


def test_host_busy_rolls_back_session_window():
    # Hold the host lock with caller A.
    assert tht.try_acquire("s1", "busy.com") is None
    # Caller B (different session) hits host_busy.
    assert tht.try_acquire("s2", "busy.com") == "host_busy"
    # s2's reservation must have been popped.
    window = tht._session_windows.get("s2")
    assert window is None or len(window) == 0
    tht.release("busy.com")


def test_upstream_overloaded_rolls_back_session_window():
    # Fill concurrency cap (3) with three distinct sessions/hosts.
    assert tht.try_acquire("a", "ha.com") is None
    assert tht.try_acquire("b", "hb.com") is None
    assert tht.try_acquire("c", "hc.com") is None
    # 4th tries — must hit upstream_overloaded.
    assert tht.try_acquire("d", "hd.com") == "upstream_overloaded"
    # session "d" window must be empty (rolled back).
    window = tht._session_windows.get("d")
    assert window is None or len(window) == 0

    tht.release("ha.com")
    tht.release("hb.com")
    tht.release("hc.com")


# ---------------------------------------------------------------------------
# Concurrency: same host
# ---------------------------------------------------------------------------


def test_three_concurrent_same_host_first_wins_others_host_busy():
    barrier = threading.Barrier(3)
    results: List[Optional[str]] = [None, None, None]
    sessions = ["s0", "s1", "s2"]

    def worker(idx: int) -> None:
        barrier.wait()
        results[idx] = tht.try_acquire(sessions[idx], "same.com")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one success, two host_busy.
    assert results.count(None) == 1
    assert results.count("host_busy") == 2

    # Session windows: the two losers must have rolled back.
    losers = [sessions[i] for i, r in enumerate(results) if r == "host_busy"]
    for s in losers:
        w = tht._session_windows.get(s)
        assert w is None or len(w) == 0, f"loser {s} window should be empty"

    # Winner has exactly 1 entry.
    winners = [sessions[i] for i, r in enumerate(results) if r is None]
    assert len(winners) == 1
    w = tht._session_windows.get(winners[0])
    assert w is not None and len(w) == 1

    tht.release("same.com")


def test_two_simultaneous_same_host_second_host_busy():
    assert tht.try_acquire("s1", "x.com") is None
    assert tht.try_acquire("s2", "x.com") == "host_busy"
    tht.release("x.com")
    # After release, host is available again.
    assert tht.try_acquire("s2", "x.com") is None
    tht.release("x.com")


# ---------------------------------------------------------------------------
# Concurrency cap (process-wide)
# ---------------------------------------------------------------------------


def test_three_in_flight_then_fourth_upstream_overloaded_then_recover():
    assert tht.try_acquire("a", "ha.com") is None
    assert tht.try_acquire("b", "hb.com") is None
    assert tht.try_acquire("c", "hc.com") is None
    # 4th — different session, different host — must hit cap.
    assert tht.try_acquire("d", "hd.com") == "upstream_overloaded"

    # Release one, the next try succeeds.
    tht.release("ha.com")
    assert tht.try_acquire("e", "he.com") is None

    tht.release("hb.com")
    tht.release("hc.com")
    tht.release("he.com")


# ---------------------------------------------------------------------------
# Stress: 50 sessions × 50 hosts, threading.Barrier(50) — no deadlock
# ---------------------------------------------------------------------------


def test_fifty_concurrent_acquires_no_deadlock_clean_fanin():
    n = 50
    barrier = threading.Barrier(n)
    results: List[Optional[str]] = [None] * n

    def worker(idx: int) -> None:
        sess = f"sess-{idx}"
        host = f"host-{idx}.com"
        barrier.wait()
        outcome = tht.try_acquire(sess, host)
        results[idx] = outcome
        if outcome is None:
            tht.release(host)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    # Generous timeout — any hang is a deadlock bug.
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), "worker deadlocked"

    # All acquires must be either success or one of the closed-enum reasons.
    allowed = {None, "rate_limited", "upstream_overloaded", "host_busy"}
    for r in results:
        assert r in allowed

    # At least one succeeded (concurrency cap is 3 but they release fast).
    assert any(r is None for r in results)


# ---------------------------------------------------------------------------
# RECON cap
# ---------------------------------------------------------------------------


def test_recon_one_emit_then_five_suppressed_then_digest(monkeypatch):
    fake_now = [2000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    assert tht.should_emit_recon("s1") is True
    for _ in range(5):
        assert tht.should_emit_recon("s1") is False
        fake_now[0] += 0.1  # well inside window

    # Digest returns 5, then resets to None.
    assert tht.flush_recon_digest("s1") == 5
    assert tht.flush_recon_digest("s1") is None


def test_recon_window_slides_after_ten_seconds(monkeypatch):
    fake_now = [3000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    assert tht.should_emit_recon("s1") is True
    assert tht.should_emit_recon("s1") is False
    fake_now[0] += 11.0
    assert tht.should_emit_recon("s1") is True


def test_recon_sessions_independent(monkeypatch):
    fake_now = [4000.0]
    monkeypatch.setattr(tht.time, "monotonic", lambda: fake_now[0])

    assert tht.should_emit_recon("s1") is True
    # s2 has its own window.
    assert tht.should_emit_recon("s2") is True
    assert tht.should_emit_recon("s1") is False
    assert tht.flush_recon_digest("s1") == 1
    assert tht.flush_recon_digest("s2") is None


# ---------------------------------------------------------------------------
# reset_state
# ---------------------------------------------------------------------------


def test_rollback_removes_own_slot_not_peers(monkeypatch):
    """Regression: rollback must remove the caller's own timestamp via
    ``remove(now)``, not the last-appended entry via ``pop()``.

    Under concurrent same-session calls the window deque can be
    [our_ts, peer_ts] if another thread appended AFTER our reservation but
    BEFORE our rollback.  ``pop()`` (LIFO) would remove ``peer_ts``, leaking
    ``our_ts``.  ``remove(now)`` removes exactly ``our_ts``.

    Simulated by injecting a peer slot inside the semaphore-acquire hook so
    the deque becomes [our_ts=1.0, peer_ts=2.0] before rollback fires.
    """
    sid = "shared-sess-ovl"
    our_ts = 1.0
    peer_ts = 2.0

    monkeypatch.setattr(tht.time, "monotonic", lambda: our_ts)

    class _InjectAndFail:
        def acquire(self, blocking=True):
            with tht._window_locks_guard:
                w = tht._session_windows.get(sid)
                if w is not None:
                    w.append(peer_ts)
            return False

        def release(self):  # pragma: no cover
            pass

    original_sem = tht._concurrency_sem
    tht._concurrency_sem = _InjectAndFail()
    try:
        result = tht.try_acquire(sid, "x.com")
        w = tht._session_windows.get(sid)
        window_after = list(w) if w else []
    finally:
        tht._concurrency_sem = original_sem

    assert result == "upstream_overloaded"
    assert window_after == [peer_ts], (
        f"rollback must remove only our slot ({our_ts}), leaving peer's "
        f"({peer_ts}). Got {window_after!r}. "
        "Buggy pop() removes the LIFO entry (peer's slot); "
        "fix uses remove(now) to target our own."
    )


def test_rollback_host_busy_removes_own_slot_not_peers(monkeypatch):
    """Same rollback-correctness invariant for the host_busy path.

    Deque becomes [our_ts=1.0, peer_ts=2.0] when a peer appends between our
    window reservation and our host-busy rollback.  ``pop()`` removes
    ``peer_ts``; ``remove(now)`` removes ``our_ts`` and leaves ``peer_ts``.
    """
    sid = "shared-sess-hb"
    our_ts = 1.0
    peer_ts = 2.0

    monkeypatch.setattr(tht.time, "monotonic", lambda: our_ts)

    class _HoldHostThenInject:
        """Semaphore that succeeds; host lock then fails after injecting peer slot."""
        _acquired = False

        def acquire(self, blocking=True):
            self._acquired = True
            return True

        def release(self):
            pass

    sem_fake = _HoldHostThenInject()
    original_sem = tht._concurrency_sem
    tht._concurrency_sem = sem_fake

    # Pre-hold the host lock so host_busy fires.
    tht.try_acquire("anchor-hb", "busy-hb.com")
    # restore semaphore so next try_acquire can get it
    tht._concurrency_sem = original_sem

    # Patch host-lock acquisition to inject the peer slot first.
    original_host_acquire = None

    class _InjectingLock:
        def __init__(self, real_lock):
            self._real = real_lock

        def acquire(self, blocking=True):
            with tht._window_locks_guard:
                w = tht._session_windows.get(sid)
                if w is not None:
                    w.append(peer_ts)
            return False  # busy

        def release(self):
            self._real.release()

    # Force host lock creation then swap it out.
    with tht._window_locks_guard:
        real_lock = tht._host_locks.setdefault("busy-hb.com", threading.Lock())
        tht._host_locks["busy-hb.com"] = _InjectingLock(real_lock)

    monkeypatch.setattr(tht.time, "monotonic", lambda: our_ts)
    result = tht.try_acquire(sid, "busy-hb.com")
    w = tht._session_windows.get(sid)
    window_after = list(w) if w else []

    tht.release("busy-hb.com")

    assert result == "host_busy"
    assert window_after == [peer_ts], (
        f"rollback must remove only our slot ({our_ts}), leaving peer's "
        f"({peer_ts}). Got {window_after!r}. "
        "Buggy pop() removes the LIFO entry (peer's slot)."
    )


def test_reset_state_clears_everything():
    assert tht.try_acquire("s1", "a.com") is None
    tht.should_emit_recon("s1")
    tht.should_emit_recon("s1")  # suppressed
    tht.reset_state()

    assert tht._session_windows == {}
    assert tht._recon_windows == {}
    assert tht._recon_suppressed == {}
    assert tht._host_locks == {}
    assert tht._host_last_used == {}

    # Semaphore was rebuilt — fresh 3 slots.
    assert tht.try_acquire("a", "ha.com") is None
    assert tht.try_acquire("b", "hb.com") is None
    assert tht.try_acquire("c", "hc.com") is None
    assert tht.try_acquire("d", "hd.com") == "upstream_overloaded"
    tht.release("ha.com")
    tht.release("hb.com")
    tht.release("hc.com")
