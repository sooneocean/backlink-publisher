"""Tests for geo.share — rolling-window citation share with honest states.

Scenarios
---------
- Never-probed target → state=never_probed, share=None, no alarm.
- Fewer than min_sample probes → state=warming_up, share=None, NOT 0%.
- At least min_sample probes → state=measured, share=float, maybe low_confidence.
- refused-heavy target → share over cited+absent only; refused_rate separate.
- possibly_cited_unresolved → unresolved_rate is populated.
- Excluded target → state=excluded, share=None, NOT 0%.
- Two targets with very different n are not directly comparable (no combined point estimate).
- Read-time dedup: duplicate (target, query, run_id) rows don't double-count.
- float shares from different arithmetic paths compare equal after round(.,6).
- Rolling window: only the W most-recent deduped events are used.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backlink_publisher.geo.share import (
    TargetShare,
    compute_share,
    compute_shares,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_WINDOW,
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TARGET = "https://target-a.com"
_TARGET_B = "https://target-b.com"

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _utc(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def _append_citation(
    store,
    target_url: str,
    verdict: str,
    *,
    query: str = "best widgets",
    engine: str = "perplexity",
    run_id: str = "run-1",
    ts_offset_days: float = 0.0,
    unresolved_urls: list[str] | None = None,
):
    """Insert one citation.observed event into ``store``."""
    ts = NOW - timedelta(days=ts_offset_days)
    payload: dict = {
        "verdict": verdict,
        "engine": engine,
        "query": query,
        "run_id": run_id,
    }
    if unresolved_urls:
        payload["possibly_cited_unresolved"] = unresolved_urls

    store.append(
        "citation.observed",
        payload,
        target_url=target_url,
        run_id=run_id,
        ts_utc=_utc(ts),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path: Path):
    from backlink_publisher.events import EventStore
    return EventStore(path=tmp_path / "events.db")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNeverProbed:
    def test_never_probed_state(self, store):
        result = compute_share(_TARGET, store=store)
        assert result.state == "never_probed"
        assert result.share is None
        assert result.n == 0
        assert result.refused_rate == 0.0
        assert result.unresolved_rate == 0.0
        assert result.low_confidence is False

    def test_never_probed_has_no_share_no_alarm(self, store):
        result = compute_share(_TARGET, store=store)
        # share must be None — never present 0% for un-probed target
        assert result.share is None


class TestWarmingUp:
    """Below min_sample floor → warming_up, never 0%."""

    def test_single_absent_is_warming_up(self, store):
        """1 absent event is below floor (5) → warming_up, not 0%."""
        _append_citation(store, _TARGET, "absent", run_id="r1")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "warming_up"
        assert result.share is None  # NOT 0%

    def test_floor_minus_one_is_warming_up(self, store):
        """4 events (< floor=5) → warming_up."""
        for i in range(4):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "warming_up"
        assert result.share is None

    def test_warming_up_n_reflects_denominator(self, store):
        """n should reflect the cited+absent count (not refused)."""
        _append_citation(store, _TARGET, "absent", run_id="r0", query="q")
        _append_citation(store, _TARGET, "refused", run_id="r1", query="q")
        _append_citation(store, _TARGET, "absent", run_id="r2", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "warming_up"
        # denominator = cited(0) + absent(2) = 2 < 5
        assert result.n == 2


class TestMeasured:
    """At or above floor → measured state with a float share."""

    def test_all_cited_is_1_0(self, store):
        for i in range(5):
            _append_citation(store, _TARGET, "site_cited", run_id=f"r{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.share == 1.0

    def test_all_absent_is_0_0(self, store):
        for i in range(5):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.share == 0.0

    def test_mixed_share(self, store):
        """3 cited + 2 absent → share = 3/5 = 0.6."""
        for i in range(3):
            _append_citation(store, _TARGET, "site_cited", run_id=f"r{i}", query="q")
        for i in range(3, 5):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.share == pytest.approx(0.6)

    def test_article_cited_counts(self, store):
        """article_cited also counts toward cited."""
        for i in range(3):
            _append_citation(store, _TARGET, "article_cited", run_id=f"r{i}", query="q")
        for i in range(3, 5):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.share == pytest.approx(0.6)

    def test_low_confidence_badge_below_threshold(self, store):
        """n in [min_sample, low_confidence_threshold) → low_confidence=True."""
        for i in range(5):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        # min_sample=5, low_conf_threshold=10 → 5 is in [5, 10) → low_confidence
        result = compute_share(
            _TARGET, store=store, min_sample=5, low_confidence_threshold=10
        )
        assert result.state == "measured"
        assert result.low_confidence is True

    def test_no_low_confidence_at_threshold(self, store):
        """n >= low_confidence_threshold → low_confidence=False."""
        for i in range(10):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(
            _TARGET, store=store, min_sample=5, low_confidence_threshold=10
        )
        assert result.state == "measured"
        assert result.low_confidence is False


class TestRefusedSeparateRate:
    """refused events are excluded from share denominator; tracked separately."""

    def test_refused_excluded_from_denominator(self, store):
        """5 refused + 5 cited → share = 5/5 = 1.0 (refused excluded)."""
        for i in range(5):
            _append_citation(store, _TARGET, "refused", run_id=f"ref{i}", query="q")
        for i in range(5):
            _append_citation(store, _TARGET, "site_cited", run_id=f"cit{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.share == 1.0  # refused not in denominator

    def test_refused_rate_populated(self, store):
        """refused_rate = refused_count / total_window_events."""
        for i in range(5):
            _append_citation(store, _TARGET, "refused", run_id=f"ref{i}", query="q")
        for i in range(5):
            _append_citation(store, _TARGET, "absent", run_id=f"abs{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        # 5 refused / 10 total = 0.5
        assert result.refused_rate == pytest.approx(0.5)

    def test_refused_heavy_warming_up(self, store):
        """Refused-heavy target with < floor answered probes → warming_up."""
        for i in range(8):
            _append_citation(store, _TARGET, "refused", run_id=f"r{i}", query="q")
        for i in range(3):
            _append_citation(store, _TARGET, "absent", run_id=f"abs{i}", query="q")

        # denominator = 3 cited+absent < floor=5 → warming_up
        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "warming_up"
        assert result.share is None


class TestUnresolvedRate:
    """possibly_cited_unresolved is tracked as a separate rate."""

    def test_unresolved_rate_populated(self, store):
        for i in range(5):
            _append_citation(
                store,
                _TARGET,
                "absent",
                run_id=f"r{i}",
                query="q",
                unresolved_urls=["https://t.co/abc"],
            )

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.unresolved_rate > 0.0

    def test_unresolved_does_not_affect_share(self, store):
        """Unresolved URLs do not contribute to cited or absent counts."""
        for i in range(5):
            _append_citation(
                store,
                _TARGET,
                "absent",
                run_id=f"r{i}",
                query="q",
                unresolved_urls=["https://t.co/abc"],
            )

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.share == 0.0  # still all absent


class TestExcluded:
    """Excluded targets return excluded state, never 0%."""

    def test_excluded_state(self, store):
        result = compute_share(
            _TARGET,
            store=store,
            excluded_targets=frozenset({_TARGET}),
        )
        assert result.state == "excluded"
        assert result.share is None  # NOT 0%
        assert result.n == 0

    def test_excluded_with_existing_events_still_excluded(self, store):
        """Even if events exist, excluded trumps measurement."""
        for i in range(10):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        result = compute_share(
            _TARGET,
            store=store,
            excluded_targets=frozenset({_TARGET}),
        )
        assert result.state == "excluded"
        assert result.share is None


class TestDedupReadTime:
    """(target, query, run_id) dedup prevents double-counting on at-least-once."""

    def test_duplicate_run_id_not_double_counted(self, store):
        """Inserting the same (query, run_id) twice must count as one probe."""
        for _ in range(2):
            _append_citation(
                store, _TARGET, "site_cited", run_id="run-dupe", query="q"
            )

        # With dedup: 1 cited, 0 absent → n=1 < floor=5 → warming_up
        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "warming_up"
        assert result.n <= 1  # deduped to 1 cited

    def test_different_run_ids_not_collapsed(self, store):
        """Different run_ids for the same query must each count separately."""
        for i in range(5):
            _append_citation(
                store, _TARGET, "site_cited", run_id=f"run-{i}", query="q"
            )

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.n == 5

    def test_different_queries_same_run_not_collapsed(self, store):
        """Different queries for the same run_id are different pairs."""
        for q in ["q1", "q2", "q3", "q4", "q5"]:
            _append_citation(store, _TARGET, "absent", run_id="same-run", query=q)

        # 5 distinct dedup keys (different query) → all count
        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        assert result.n == 5


class TestRollingWindow:
    """Only the W most-recent deduped probes are considered."""

    def test_window_caps_older_events(self, store):
        """Events beyond W are ignored even when stale."""
        # Insert 15 absent events, then 5 cited.
        # With window=10: only the 10 most-recent (5 cited + 5 absent) count.
        for i in range(15):
            _append_citation(
                store, _TARGET, "absent",
                run_id=f"old{i}", query="q",
                ts_offset_days=float(15 - i),  # oldest first in time
            )
        for i in range(5):
            _append_citation(
                store, _TARGET, "site_cited",
                run_id=f"new{i}", query="q",
                ts_offset_days=0.0,
            )

        result = compute_share(_TARGET, store=store, window=10, min_sample=5)
        assert result.state == "measured"
        # window=10: 5 cited + 5 absent → share = 5/10 = 0.5
        assert result.share == pytest.approx(0.5)
        assert result.n == 10


class TestFloatPrecision:
    """Shares from different arithmetic paths compare equal after round(.,6)."""

    def test_round_6_float_equality(self, store):
        """1/3 computed two ways must match after rounding to 6 dp."""
        for i in range(2):
            _append_citation(store, _TARGET, "absent", run_id=f"a{i}", query="q")
        _append_citation(store, _TARGET, "site_cited", run_id="c0", query="q")
        for i in range(2):
            _append_citation(store, _TARGET_B, "absent", run_id=f"b{i}", query="q")
        _append_citation(store, _TARGET_B, "site_cited", run_id="bc0", query="q")

        r_a = compute_share(_TARGET, store=store, min_sample=3)
        r_b = compute_share(_TARGET_B, store=store, min_sample=3)

        assert r_a.state == "measured"
        assert r_b.state == "measured"
        # Both are 1/3; both must round the same way.
        assert r_a.share == r_b.share
        assert r_a.share == round(1 / 3, 6)

    def test_share_is_rounded_to_6_dp(self, store):
        """Verify share is exactly round(.,6) — not more decimal places."""
        for i in range(7):
            _append_citation(store, _TARGET, "site_cited", run_id=f"c{i}", query="q")
        for i in range(3):
            _append_citation(store, _TARGET, "absent", run_id=f"a{i}", query="q")

        result = compute_share(_TARGET, store=store, min_sample=5)
        assert result.state == "measured"
        # 7/10 = 0.7 exactly; verify it matches round(7/10, 6)
        assert result.share == round(7 / 10, 6)


class TestComputeSharesBatch:
    """compute_shares handles multiple targets."""

    def test_batch_returns_correct_order(self, store):
        _append_citation(store, _TARGET, "absent", run_id="r0", query="q")
        targets = [_TARGET, _TARGET_B]
        results = compute_shares(targets, store=store)
        assert len(results) == 2
        assert results[0].target_url == _TARGET
        assert results[1].target_url == _TARGET_B

    def test_batch_different_states(self, store):
        """One measured target + one never-probed in same batch."""
        for i in range(5):
            _append_citation(store, _TARGET, "absent", run_id=f"r{i}", query="q")

        results = compute_shares([_TARGET, _TARGET_B], store=store, min_sample=5)
        states = {r.target_url: r.state for r in results}
        assert states[_TARGET] == "measured"
        assert states[_TARGET_B] == "never_probed"


class TestTargetNotDirectlyComparable:
    """Targets with very different n must not present a unified point estimate."""

    def test_different_n_is_visible(self, store):
        """Each TargetShare carries its own n so callers can judge comparability."""
        # TARGET_A: 5 probes (low confidence), TARGET_B: 20 probes (high confidence)
        for i in range(5):
            _append_citation(store, _TARGET, "absent", run_id=f"a{i}", query="q")
        for i in range(10):
            _append_citation(store, _TARGET_B, "absent", run_id=f"b{i}", query="q")

        r_a = compute_share(_TARGET, store=store, min_sample=5, low_confidence_threshold=10)
        r_b = compute_share(
            _TARGET_B, store=store, min_sample=5, low_confidence_threshold=10
        )

        # A has n=5 with low_confidence; B has n=10 without
        assert r_a.n < r_b.n
        assert r_a.low_confidence is True
        assert r_b.low_confidence is False
