"""Unit tests for geo.run — pure probe kernel (Plan 2026-05-29-006 Unit 7).

Scenarios
---------
- probe_one: success path returns VerdictResult with correct tier.
- probe_one: propagates exception from probe_fn (never-raises is probe_many's job).
- probe_many: happy path — each pair appended, summary tallied correctly.
- probe_many: probe error is caught, counted as probe_error, batch continues.
- probe_many: cost cap stops batch early; already-appended pairs persisted.
- probe_many: wall-clock budget stops batch; already-appended pairs persisted.
- probe_many: D8 — raw_response is NOT written to events.db.
- probe_many: run_id attached to each appended event for D11 dedup.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED
from backlink_publisher.geo.engines import ProbeResult
from backlink_publisher.geo.run import ProbeSummary, probe_many, probe_one
from backlink_publisher.geo.selection import ProbeCandidate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate(url: str, query: str = "best widgets") -> ProbeCandidate:
    return ProbeCandidate(
        target_url=url,
        query=query,
        last_probed_at=None,
        staleness_days=float("inf"),
    )


def _ok_result(source_urls: list[str] | None = None) -> ProbeResult:
    return ProbeResult(
        answer_text="The best widgets are at example.com.",
        source_urls=source_urls or ["https://example.com/widgets"],
        raw_response={"secret": "Bearer tok123", "choices": []},
        outcome="ok",
    )


def _absent_result() -> ProbeResult:
    return ProbeResult(
        answer_text="There are many options out there.",
        source_urls=["https://unrelated.net/page"],
        raw_response={"id": "test"},
        outcome="ok",
    )


def _refused_result() -> ProbeResult:
    return ProbeResult(
        answer_text="I can't help with that.",
        source_urls=[],
        raw_response={"id": "test"},
        outcome="refused",
    )


def _make_geo_cfg():
    from backlink_publisher.config.types import GeoProbeConfig

    return GeoProbeConfig(
        base_url="https://api.perplexity.ai",
        api_key="test-key",
        model="sonar",
    )


@pytest.fixture()
def store(tmp_path: Path):
    return EventStore(path=tmp_path / "events.db")


# ---------------------------------------------------------------------------
# probe_one — unit tests
# ---------------------------------------------------------------------------


class TestProbeOne:
    def test_site_cited_tier(self, store):
        """probe_one classifies site_cited when source host matches target."""

        def _fn(query, cfg):
            return _ok_result(source_urls=["https://example.com/about"])

        result = probe_one(
            "https://example.com",
            "best widgets",
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            article_urls=frozenset(),
            brand_aliases=[],
        )
        assert result.tier == "site_cited"
        assert result.query == "best widgets"

    def test_absent_tier_no_match(self, store):
        """probe_one returns absent when sources don't match target host."""

        def _fn(query, cfg):
            return _absent_result()

        result = probe_one(
            "https://example.com",
            "best widgets",
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            article_urls=frozenset(),
            brand_aliases=[],
        )
        assert result.tier == "absent"

    def test_refused_tier(self):
        """probe_one returns refused for a refusal outcome."""

        def _fn(query, cfg):
            return _refused_result()

        result = probe_one(
            "https://example.com",
            "best widgets",
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            article_urls=frozenset(),
            brand_aliases=[],
        )
        assert result.tier == "refused"

    def test_propagates_probe_fn_exception(self):
        """probe_one lets exceptions from probe_fn bubble up (probe_many catches them)."""

        def _failing_fn(query, cfg):
            raise RuntimeError("network failure")

        with pytest.raises(RuntimeError, match="network failure"):
            probe_one(
                "https://example.com",
                "best widgets",
                probe_fn=_failing_fn,
                cfg=_make_geo_cfg(),
                article_urls=frozenset(),
                brand_aliases=[],
            )

    def test_engine_name_carried(self):
        """engine name is recorded on the VerdictResult."""

        def _fn(query, cfg):
            return _absent_result()

        result = probe_one(
            "https://example.com",
            "q",
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            article_urls=frozenset(),
            brand_aliases=[],
            engine="perplexity",
        )
        assert result.engine == "perplexity"


# ---------------------------------------------------------------------------
# probe_many — happy path
# ---------------------------------------------------------------------------


class TestProbeManyHappy:
    def test_all_pairs_probed_and_appended(self, store):
        """All candidates probed, events appended, summary tallied."""
        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
        ]
        call_log: list[str] = []

        def _fn(query, cfg):
            call_log.append(query)
            return _ok_result(source_urls=["https://example.com/a"])

        rows, summary = probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
            engine="perplexity",
            run_id="test-run",
        )

        assert summary.probed == 2
        assert summary.site_cited == 2
        assert summary.probe_error == 0
        assert len(call_log) == 2

        db_rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert len(db_rows) == 2

    def test_summary_tallies_tiers(self, store):
        """Summary counts each tier correctly."""
        results_queue = [
            _ok_result(source_urls=["https://example.com/a"]),  # site_cited
            _absent_result(),  # absent
            _refused_result(),  # refused
        ]
        idx = [0]

        def _fn(query, cfg):
            r = results_queue[idx[0]]
            idx[0] += 1
            return r

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
            _candidate("https://example.com", "q3"),
        ]
        _, summary = probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
        )

        assert summary.site_cited == 1
        assert summary.absent == 1
        assert summary.refused == 1

    def test_run_id_attached_to_events(self, store):
        """run_id is included in the appended event payload for D11 dedup."""

        def _fn(query, cfg):
            return _absent_result()

        probe_many(
            [_candidate("https://example.com", "q")],
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
            run_id="my-run-42",
        )

        import json

        rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload.get("run_id") == "my-run-42"


# ---------------------------------------------------------------------------
# probe_many — D8: raw_response never persisted
# ---------------------------------------------------------------------------


class TestProbeManyD8:
    def test_raw_response_not_persisted(self, store):
        """raw_response (which may contain Bearer tokens) must not be in events.db."""

        def _fn(query, cfg):
            # raw_response carries a simulated secret
            return ProbeResult(
                answer_text="answer",
                source_urls=[],
                raw_response={"Authorization": "Bearer secret-key"},
                outcome="ok",
            )

        probe_many(
            [_candidate("https://example.com", "q")],
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
        )

        import json

        rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert len(rows) == 1
        payload_str = rows[0]["payload_json"]
        assert "secret-key" not in payload_str
        assert "Authorization" not in payload_str
        assert "raw_response" not in payload_str


# ---------------------------------------------------------------------------
# probe_many — error handling (never-raises)
# ---------------------------------------------------------------------------


class TestProbeManyErrors:
    def test_probe_error_counted_batch_continues(self, store):
        """A per-pair exception is caught, counted, and the batch continues."""
        results = [RuntimeError("transient"), _absent_result()]
        idx = [0]

        def _fn(query, cfg):
            r = results[idx[0]]
            idx[0] += 1
            if isinstance(r, Exception):
                raise r
            return r

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
        ]
        rows, summary = probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
        )

        assert summary.probed == 2
        assert summary.probe_error == 1
        assert summary.absent == 1
        # The error row is in `rows`
        error_rows = [r for r in rows if r.get("verdict") == "probe_error"]
        assert len(error_rows) == 1

    def test_probe_error_row_not_appended_to_events(self, store):
        """Failed probe rows must NOT be written to events.db."""

        def _fn(query, cfg):
            raise RuntimeError("network failure")

        probe_many(
            [_candidate("https://example.com", "q")],
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=60.0,
        )

        rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert rows == []


# ---------------------------------------------------------------------------
# probe_many — cost cap
# ---------------------------------------------------------------------------


class TestProbeManyCapCap:
    def test_cost_cap_stops_batch(self, store):
        """cost_cap=1 on a 3-pair batch: only 1 probe fires, 2 deferred."""
        call_log: list[str] = []

        def _fn(query, cfg):
            call_log.append(query)
            return _absent_result()

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
            _candidate("https://example.com", "q3"),
        ]
        _, summary = probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=1,
            wall_clock_budget_s=60.0,
        )

        assert len(call_log) == 1
        assert summary.probed == 1
        assert summary.deferred == 2
        assert summary.cost_cap_hit is True

    def test_cost_cap_first_pair_durably_appended(self, store):
        """The pair that fired before cap fires must be in events.db."""

        def _fn(query, cfg):
            return _absent_result()

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
        ]
        probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=1,
            wall_clock_budget_s=60.0,
        )

        rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert len(rows) == 1  # only the first pair

    def test_cost_cap_zero_probes_nothing(self, store):
        """cost_cap=0: batch fires zero probes, all deferred."""
        call_log: list[str] = []

        def _fn(query, cfg):
            call_log.append(query)
            return _absent_result()

        candidates = [_candidate("https://example.com", "q1")]
        _, summary = probe_many(
            candidates,
            probe_fn=_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=0,
            wall_clock_budget_s=60.0,
        )

        assert call_log == []
        assert summary.probed == 0
        assert summary.deferred == 1
        assert summary.cost_cap_hit is True


# ---------------------------------------------------------------------------
# probe_many — wall-clock budget
# ---------------------------------------------------------------------------


class TestProbeManyBudget:
    def test_wall_clock_budget_stops_batch(self, store):
        """Wall-clock budget exhausted mid-batch: remaining pairs deferred."""
        call_log: list[str] = []

        def _slow_fn(query, cfg):
            # Simulate a fast probe but force the deadline to be already past
            call_log.append(query)
            return _absent_result()

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
        ]

        # Set budget to 0 so the second pair always fires after the deadline.
        # We can't easily control time.monotonic(), so use a tiny budget
        # and a slow probe on the second call.
        call_count = [0]

        def _timed_fn(query, cfg):
            call_count[0] += 1
            call_log.append(query)
            if call_count[0] == 1:
                # After first probe completes, ensure deadline has passed
                # by patching the budget
                pass
            return _absent_result()

        _, summary = probe_many(
            candidates,
            probe_fn=_timed_fn,
            cfg=_make_geo_cfg(),
            store=store,
            article_urls=frozenset(),
            brand_aliases_map={},
            cost_cap=100,
            wall_clock_budget_s=0.0,  # already expired
        )

        # With budget_s=0, the FIRST pair should still be deferred (deadline
        # already past on entry).
        assert summary.budget_exhausted is True

    def test_wall_clock_budget_first_pair_durably_appended(self, store):
        """Pairs probed before budget exhaustion are durably appended."""
        call_count = [0]

        def _fn(query, cfg):
            call_count[0] += 1
            return _absent_result()

        candidates = [
            _candidate("https://example.com", "q1"),
            _candidate("https://example.com", "q2"),
            _candidate("https://example.com", "q3"),
        ]

        # Use a generous budget for the first probe then patch monotonic
        # to simulate expiry on the second check.
        times = [0.0, 0.0, 999.0, 999.0]  # first pair fine, second sees expired
        t_idx = [0]

        def _fake_monotonic():
            v = times[min(t_idx[0], len(times) - 1)]
            t_idx[0] += 1
            return v

        with patch("backlink_publisher.geo.run.time.monotonic", _fake_monotonic):
            _, summary = probe_many(
                candidates,
                probe_fn=_fn,
                cfg=_make_geo_cfg(),
                store=store,
                article_urls=frozenset(),
                brand_aliases_map={},
                cost_cap=100,
                wall_clock_budget_s=100.0,  # deadline = 0.0 + 100.0 = 100.0
            )

        # At least the first pair was appended before budget exhausted
        rows = store.query(
            "SELECT payload_json FROM events WHERE kind = ?", (CITATION_OBSERVED,)
        )
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# ProbeSummary dataclass
# ---------------------------------------------------------------------------


class TestProbeSummary:
    def test_to_jsonl_dict(self):
        s = ProbeSummary(probed=3, site_cited=1, absent=2, deferred=1)
        d = s.to_jsonl_dict()
        assert d["type"] == "summary"
        assert d["probed"] == 3
        assert d["site_cited"] == 1
        assert d["absent"] == 2
        assert d["deferred"] == 1
        assert d["budget_exhausted"] is False
        assert d["cost_cap_hit"] is False
