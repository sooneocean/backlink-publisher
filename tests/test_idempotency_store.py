"""Unit 1 — dedup store: state round-trip, canonicalization, staleness, the
single-flight intent-write contract (incl. concurrency), and at-rest perms.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U1).
"""

from __future__ import annotations

import os
import stat
import threading

import pytest

from backlink_publisher.idempotency.store import DedupKey, DedupStore


@pytest.fixture()
def store(tmp_path):
    return DedupStore(path=tmp_path / "dedup.db")


def _key(target="https://money.example/page", platform="blogger", account="default"):
    return DedupKey(platform=platform, target_url=target, account=account)


# --------------------------------------------------------------------------- #
# Happy path: state round-trip
# --------------------------------------------------------------------------- #
def test_absent_key_returns_none(store):
    assert store.get(_key()) is None


def test_intent_then_done_with_verify_ok(store):
    key = _key()
    outcome = store.intent_write(key, run_id="r1")
    assert outcome.won is True

    rec = store.get(key)
    assert rec is not None and rec.state == "attempting"

    store.transition(key, "done", live_url="https://blogger.com/p/1")
    store.set_verify_ok(key, True)

    rec = store.get(key)
    assert rec.state == "done"
    assert rec.verify_ok is True
    assert rec.live_url == "https://blogger.com/p/1"


def test_verify_failure_leaves_done_state(store):
    """R6: a verify failure sets verify_ok=False but never changes state away
    from done — a flake must not make the key re-publishable."""
    key = _key()
    store.intent_write(key)
    store.transition(key, "done", live_url="https://blogger.com/p/2")
    store.set_verify_ok(key, False)

    rec = store.get(key)
    assert rec.state == "done"
    assert rec.verify_ok is False


# --------------------------------------------------------------------------- #
# Key canonicalization
# --------------------------------------------------------------------------- #
def test_canonicalization_collapses_variants(store):
    key = _key(target="https://money.example/page")
    store.intent_write(key)
    # Trailing slash + uppercase host + utm should collapse to the same key.
    variant = _key(target="https://MONEY.example/page/?utm_source=x")
    assert store.get(variant) is not None
    assert variant.target_url == key.target_url


def test_distinct_non_utm_query_is_distinct_key(store):
    a = _key(target="https://money.example/page?id=1")
    b = _key(target="https://money.example/page?id=2")
    store.intent_write(a)
    assert store.get(b) is None  # different non-utm query -> different key


def test_account_dimension_distinguishes_keys(store):
    a = _key(account="acct-1")
    b = _key(account="acct-2")
    store.intent_write(a)
    assert store.get(b) is None  # same platform+target, different account


# --------------------------------------------------------------------------- #
# Stale-attempting detection
# --------------------------------------------------------------------------- #
def test_attempting_with_dead_pid_is_stale(store):
    key = _key()
    store.intent_write(key, owner_pid=2_147_483_000)  # almost certainly dead
    rec = store.get(key)
    assert store.is_stale_attempting(rec) is True


def test_attempting_with_live_pid_recent_is_not_stale(store):
    key = _key()
    store.intent_write(key, owner_pid=os.getpid())
    rec = store.get(key)
    assert store.is_stale_attempting(rec) is False


def test_attempting_aged_past_ttl_is_stale_even_if_pid_alive(store):
    """PID-reuse backstop: a live (reused) PID can't keep an attempting row
    held forever — the absolute TTL ages it out."""
    key = _key()
    store.intent_write(key, owner_pid=os.getpid())
    rec = store.get(key)
    future = rec.updated_at + 10_000
    assert store.is_stale_attempting(rec, now=future, ttl_s=3600) is True


def test_done_is_never_stale(store):
    key = _key()
    store.intent_write(key)
    store.transition(key, "done", live_url="u")
    rec = store.get(key)
    assert store.is_stale_attempting(rec) is False


# --------------------------------------------------------------------------- #
# intent_write two-outcome contract + concurrency
# --------------------------------------------------------------------------- #
def test_second_intent_write_loses_without_raising(store):
    key = _key()
    first = store.intent_write(key)
    second = store.intent_write(key)
    assert first.won is True
    assert second.won is False
    assert second.existing_state == "attempting"


def test_forgotten_key_reinserts_cleanly(store):
    key = _key()
    store.intent_write(key)
    store.transition(key, "done", live_url="u")
    assert store.forget(key) == "done"
    assert store.get(key) is None
    # After forget, a fresh intent write wins again.
    assert store.intent_write(key).won is True


def test_concurrent_intent_write_exactly_one_wins(tmp_path):
    """Two threads racing the same key: exactly one inserts attempting, the
    other observes the existing row and loses. Proves the BEGIN IMMEDIATE
    single-flight closes the TOCTOU double-post window."""
    db = tmp_path / "dedup.db"
    key = _key()
    barrier = threading.Barrier(2)
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        s = DedupStore(path=db)
        barrier.wait()
        outcome = s.intent_write(key)
        with lock:
            results.append(outcome.won)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == [False, True]  # exactly one winner


# --------------------------------------------------------------------------- #
# Transition legality
# --------------------------------------------------------------------------- #
def test_transition_on_absent_key_raises(store):
    with pytest.raises(ValueError):
        store.transition(_key(), "done")


def test_terminal_state_blocks_retransition(store):
    key = _key()
    store.intent_write(key)
    store.transition(key, "done", live_url="u")
    with pytest.raises(ValueError):
        store.transition(key, "failed")


def test_uncertain_adjudicated_to_terminal(store):
    """uncertain -> done|failed is the adjudication path (legal without
    allow_from_terminal since uncertain is not terminal)."""
    key = _key()
    store.intent_write(key)
    store.transition(key, "uncertain")
    store.transition(key, "failed")
    assert store.get(key).state == "failed"


def test_illegal_target_state_raises(store):
    key = _key()
    store.intent_write(key)
    with pytest.raises(ValueError):
        store.transition(key, "attempting")  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# At-rest permissions (campaign URLs live in the db + WAL)
# --------------------------------------------------------------------------- #
def test_store_files_are_0600(tmp_path):
    db = tmp_path / "dedup.db"
    s = DedupStore(path=db)
    s.intent_write(_key())  # force a write so WAL/SHM appear
    for suffix in ("", "-wal", "-shm"):
        p = db.parent / (db.name + suffix)
        if p.exists():
            mode = stat.S_IMODE(p.stat().st_mode)
            assert mode == 0o600, f"{p.name} is {oct(mode)}, expected 0o600"


def test_transition_expect_from_mismatch_raises(store):
    """expect_from makes the from-state check atomic with the write — a row that
    is not in the expected state (e.g. reclaimed to attempting) is left untouched.
    Closes the adjudicate-bulk TOCTOU."""
    key = _key()
    store.intent_write(key)  # attempting
    with pytest.raises(ValueError):
        store.transition(key, "failed", expect_from=("uncertain",))
    assert store.get(key).state == "attempting"  # untouched


def test_transition_expect_from_match_succeeds(store):
    key = _key()
    store.intent_write(key)
    store.transition(key, "uncertain")
    store.transition(key, "done", live_url="u", expect_from=("uncertain",))
    assert store.get(key).state == "done"


# --------------------------------------------------------------------------- #
# get_many: single-connection batch read (the reconciler hot path)
# --------------------------------------------------------------------------- #
def _count_connects(store, monkeypatch):
    """Spy on ``_connect_raw`` and return a mutable counter dict."""
    calls = {"n": 0}
    orig = store._connect_raw

    def spy():
        calls["n"] += 1
        return orig()

    monkeypatch.setattr(store, "_connect_raw", spy)
    return calls


def test_get_many_returns_present_keys_and_omits_absent(store):
    a = _key(target="https://money.example/a")
    b = _key(target="https://money.example/b")
    c = _key(target="https://money.example/c")  # never written
    store.seed(a, "done", live_url="https://blogger.com/p/a")
    store.intent_write(b)  # stays attempting

    out = store.get_many([a, b, c])

    assert set(out) == {a.as_tuple(), b.as_tuple()}  # absent key c omitted
    assert out[a.as_tuple()].state == "done"
    assert out[b.as_tuple()].state == "attempting"


def test_get_many_matches_per_key_get(store):
    """Each batch entry must equal what get() returns for that key."""
    a = _key(target="https://money.example/a")
    store.seed(a, "done", live_url="https://blogger.com/p/a")
    out = store.get_many([a])
    assert out[a.as_tuple()] == store.get(a)


def test_get_many_empty_input_returns_empty_without_connecting(store, monkeypatch):
    calls = _count_connects(store, monkeypatch)
    assert store.get_many([]) == {}
    assert calls["n"] == 0  # no DB touched for an empty batch


def test_get_many_dedups_repeated_keys(store):
    a = _key(target="https://money.example/a")
    store.seed(a, "done")
    # Second key canonicalizes to the same tuple (trailing slash + host case).
    variant = _key(target="https://MONEY.example/a/")
    out = store.get_many([a, variant])
    assert set(out) == {a.as_tuple()}


def test_get_many_opens_one_connection_for_many_keys(store, monkeypatch):
    """The whole point: N keys -> 1 connection, not N (the reconciler regression
    guard). Per-key get() would open one connection each."""
    keys = []
    for i in range(5):
        k = _key(target=f"https://money.example/p{i}")
        store.seed(k, "done")
        keys.append(k)

    calls = _count_connects(store, monkeypatch)  # count only the batch read
    out = store.get_many(keys)

    assert len(out) == 5
    assert calls["n"] == 1


def test_get_many_distinguishes_account_dimension(store):
    """Same platform+target but different account are distinct keys — the batch
    SELECT must include the account column, not collapse the two."""
    a = _key(account="acct-1")
    b = _key(account="acct-2")
    store.seed(a, "done")
    store.intent_write(b)  # attempting

    out = store.get_many([a, b])

    assert out[a.as_tuple()].state == "done"
    assert out[b.as_tuple()].state == "attempting"
    assert a.as_tuple() != b.as_tuple()  # account is part of the dict key
