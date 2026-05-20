"""Tests for ``image_gen.caps`` — Plan 2026-05-20-001 Unit 3.

Daily + per-run usage caps + auto-disable safety.  Events are
persisted into the existing ``events.EventStore`` (SQLite under
``<cache_dir>/events.db``) using free-form ``kind=image_gen_invoked``
/ ``image_gen_capped`` / ``image_gen_disabled_auto`` rows.  No
projector / event-registry changes are needed — ``EventStore.kind``
is a free-form string column.
"""

from __future__ import annotations

import pytest

from backlink_publisher.config import ImageGenConfig
from backlink_publisher.events.store import EventStore
from backlink_publisher.publishing.adapters.image_gen.caps import (
    AutoDisableTracker,
    CapDecision,
    check_caps,
    record_cap_hit,
    record_invocation,
)


def _config(*, daily_cap: int = 50, per_run_cap: int = 10) -> ImageGenConfig:
    return ImageGenConfig(
        base_url="https://example/v1",
        model="m",
        daily_cap=daily_cap,
        per_run_cap=per_run_cap,
    )


@pytest.fixture
def store(tmp_path):
    return EventStore(path=tmp_path / "events.db")


# ── check_caps happy paths ─────────────────────────────────────────────────


def test_check_caps_allows_when_under_both_caps(store):
    """daily_cap=10, today=0, run_counter=0 → allowed."""
    decision = check_caps(store, _config(daily_cap=10, per_run_cap=3), run_counter=0)
    assert decision == CapDecision(allowed=True, reason=None)


def test_check_caps_blocks_per_run_first(store):
    """When both caps would block, per_run wins — it is the tighter
    of the two and signals to the operator that THIS run is done."""
    cfg = _config(daily_cap=10, per_run_cap=3)
    # Pretend 9 invocations were already logged today
    for _ in range(9):
        record_invocation(store, "shaX")
    decision = check_caps(store, cfg, run_counter=3)
    assert decision.allowed is False
    assert decision.reason == "per_run_cap"


def test_check_caps_blocks_daily_when_per_run_below(store):
    """daily_cap reached but per_run_counter below → ``daily_cap``."""
    cfg = _config(daily_cap=5, per_run_cap=20)
    for _ in range(5):
        record_invocation(store, "shaX")
    decision = check_caps(store, cfg, run_counter=2)
    assert decision.allowed is False
    assert decision.reason == "daily_cap"


def test_check_caps_zero_cap_blocks_immediately(store):
    """``per_run_cap=0`` → always blocked.  Useful as an emergency
    operator off-switch without rewriting ``use_image_gen``."""
    cfg = _config(per_run_cap=0)
    decision = check_caps(store, cfg, run_counter=0)
    assert decision.allowed is False
    assert decision.reason == "per_run_cap"


# ── record_invocation persistence ─────────────────────────────────────────


def test_record_invocation_persists_across_queries(store):
    """Recorded invocations are visible to a subsequent ``check_caps``
    in the SAME run AND across process restarts (durable in SQLite)."""
    cfg = _config(daily_cap=3, per_run_cap=99)
    record_invocation(store, "sha1")
    record_invocation(store, "sha2")
    record_invocation(store, "sha3")
    decision = check_caps(store, cfg, run_counter=3)
    assert decision.reason == "daily_cap"


def test_record_invocation_stores_prompt_sha_in_payload(store, tmp_path):
    """The ``image_gen_invoked`` event carries the prompt_sha in its
    payload so downstream ``report-anchors`` can show "you generated
    N banners this month for these prompts"."""
    record_invocation(store, "deadbeef00000001")

    rows = store.query(
        "SELECT kind, payload_json FROM events WHERE kind = 'image_gen_invoked'"
    )
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload_json"])
    assert payload.get("prompt_sha") == "deadbeef00000001"


def test_record_cap_hit_emits_distinct_event(store):
    """``record_cap_hit`` emits a different ``kind`` so it doesn't
    inflate the daily counter."""
    record_cap_hit(store, reason="daily_cap")
    rows = store.query("SELECT kind FROM events ORDER BY id")
    assert [r["kind"] for r in rows] == ["image_gen_capped"]


# ── AutoDisableTracker ────────────────────────────────────────────────────


def test_auto_disable_after_threshold_consecutive_failures():
    tracker = AutoDisableTracker(threshold=3)
    assert not tracker.disabled

    tracker.record_failure()
    assert not tracker.disabled  # 1
    tracker.record_failure()
    assert not tracker.disabled  # 2
    tracker.record_failure()
    assert tracker.disabled  # 3 — tripped


def test_auto_disable_success_resets_counter():
    """A success in the middle of a failure streak resets the
    counter — only CONSECUTIVE failures count.  This avoids a slow
    background failure rate eventually tripping auto-disable."""
    tracker = AutoDisableTracker(threshold=3)
    tracker.record_failure()
    tracker.record_failure()
    tracker.record_success()
    tracker.record_failure()  # only 1 after reset
    tracker.record_failure()  # 2
    assert not tracker.disabled
    tracker.record_failure()  # 3
    assert tracker.disabled


def test_auto_disable_threshold_zero_disabled_immediately():
    """``threshold=0`` is invalid (would auto-disable before any
    work) — guard with ValueError."""
    with pytest.raises(ValueError, match="threshold"):
        AutoDisableTracker(threshold=0)


def test_auto_disable_threshold_one_trips_on_first_failure():
    """``threshold=1`` → single failure trips."""
    tracker = AutoDisableTracker(threshold=1)
    tracker.record_failure()
    assert tracker.disabled


# ── Date scoping ──────────────────────────────────────────────────────────


def test_daily_count_only_counts_today(store, monkeypatch):
    """An ``image_gen_invoked`` row from yesterday must NOT count
    toward today's daily_cap."""
    cfg = _config(daily_cap=3, per_run_cap=99)

    # Inject a row with a manually-set ts_utc of yesterday
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO events (kind, payload_json, ts_raw, ts_utc) "
            "VALUES (?, '{}', ?, ?)",
            ("image_gen_invoked", "2026-05-19T10:00:00+00:00", "2026-05-19T10:00:00+00:00"),
        )

    # Today's two invocations should still leave headroom
    record_invocation(store, "today1")
    record_invocation(store, "today2")

    # Pin "today" so the test isn't midnight-flaky
    import backlink_publisher.publishing.adapters.image_gen.caps as caps_mod
    monkeypatch.setattr(caps_mod, "_today_utc_date_str", lambda: "2026-05-20")

    decision = check_caps(store, cfg, run_counter=2)
    # daily count = 2 (today only), cap = 3 → allowed
    assert decision.allowed, f"expected allowed, got {decision}"


def test_daily_count_does_not_include_cap_hits(store, monkeypatch):
    """``image_gen_capped`` rows do NOT count toward the daily limit —
    they are misses, not successful generations."""
    cfg = _config(daily_cap=2, per_run_cap=99)

    record_invocation(store, "real1")
    record_cap_hit(store, reason="per_run_cap")
    record_cap_hit(store, reason="per_run_cap")

    decision = check_caps(store, cfg, run_counter=1)
    # daily = 1 invocation (cap_hits ignored), cap = 2 → allowed
    assert decision.allowed
