"""Tests for the Copilot advisory aggregator and its per-tool adapters (Plan U1)."""

from __future__ import annotations

import pytest

from backlink_publisher.audit import AuditReadError
from backlink_publisher.ledger import LedgerRow
from webui_app.services import copilot_advisor as adv
from webui_app.services.copilot_models import ToolResult


# --- equity-ledger adapter -----------------------------------------------------


def test_equity_adapter_maps_liveness_and_anchor_to_types():
    rows = [
        LedgerRow(target_url="t-dead", total_links=2, live_links=0, liveness="failed"),
        LedgerRow(target_url="t-stale", total_links=1, live_links=1, liveness="stale"),
        LedgerRow(target_url="t-conc", total_links=3, live_links=3, live_dofollow=3,
                  liveness="live", has_anchor_data=True, exact_match_pct=0.55),
    ]
    res = adv.adapt_equity(build=lambda **_: rows)
    types = {f.type for f in res.findings}
    assert res.ok and res.outcome == "kind"
    assert "unreachable_target" in types
    assert "stale_link" in types
    assert "over_concentrated_anchor" in types
    # the dead target also has zero live-dofollow
    assert any(f.type == "no_dofollow_equity" for f in res.findings)
    assert all(f.freshness.kind == "live" for f in res.findings)


def test_equity_adapter_empty_is_no_emit_not_error():
    res = adv.adapt_equity(build=lambda **_: [])
    assert res.ok is True
    assert res.outcome == "no_emit"  # ran, zero findings — NOT a crash
    assert res.findings == []


def test_equity_adapter_isolates_engine_failure():
    def boom(**_):
        raise RuntimeError("events.db locked")
    res = adv.adapt_equity(build=boom)
    assert res.ok is False
    assert res.error_code == "equity_unreadable"
    assert "events.db locked" in res.detail


# --- audit-state adapter -------------------------------------------------------


class _Rec:
    def __init__(self, cls, tier="high-signal"):
        self.divergence_class = cls
        self.source = "history"
        self.source_tier = tier
        self.authority = "indeterminate"
        self.canonical_url = "https://t/x"
        self.article_id = None


def test_audit_adapter_emits_state_drift():
    res = adv.adapt_audit(read=lambda: object(),
                          diff=lambda _s: [_Rec("history_orphan"), _Rec("duplicate_key")])
    assert res.ok and len(res.findings) == 2
    assert all(f.type == "state_drift" for f in res.findings)


def test_audit_adapter_handles_unreadable_store():
    def boom():
        raise AuditReadError("cannot read events.db")
    res = adv.adapt_audit(read=boom, diff=lambda _s: [])
    assert res.ok is False
    assert res.error_code == "audit_unreadable"


# --- cull adapter --------------------------------------------------------------


def test_cull_adapter_only_flags_false_and_low():
    df = {"low_nf": False, "high_nf": False, "uncertain_one": "uncertain", "df_one": True}
    rv = {"low_nf": "low", "high_nf": "high", "uncertain_one": None, "df_one": None}
    res = adv.adapt_cull(
        platforms=lambda: list(df),
        dofollow=lambda n: df[n],
        referral=lambda n: rv[n],
    )
    refs = {f.source_ref for f in res.findings}
    assert refs == {"cull-channels:low_nf"}  # uncertain never auto-culled
    assert all(f.type == "cull_candidate" for f in res.findings)


# --- canary adapter ------------------------------------------------------------


def test_canary_adapter_maps_status_to_types_with_cached_freshness():
    health = {
        "medium": {"status": "drift-confirmed", "quarantined": True,
                   "last_drift_at": "2026-05-01T00:00:00+00:00",
                   "consecutive_failures": 3},
        "devto": {"status": "link-alive", "last_ok_at": "2026-05-30T00:00:00+00:00"},
        "x": {"status": "advisory"},
    }
    res = adv.adapt_canary(list_all=lambda: health)
    by_type = {f.type: f for f in res.findings}
    assert "failed_canary" in by_type           # drift-confirmed/quarantined
    assert "unmeasured" in by_type              # advisory
    assert "devto" not in {f.source_ref.split(":")[1] for f in res.findings}  # link-alive → no emit
    assert by_type["failed_canary"].freshness.kind == "cached"
    assert by_type["failed_canary"].freshness.as_of == "2026-05-01T00:00:00+00:00"


def test_canary_adapter_never_writes_verdicts(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("record_verdict must not be called on render")
    monkeypatch.setattr(adv.canary_store, "record_verdict", explode)
    res = adv.adapt_canary(list_all=lambda: {"m": {"status": "drift-confirmed"}})
    assert res.ok and res.findings  # no write happened


# --- aggregate -----------------------------------------------------------------


def _ok(tool, *findings):
    return ToolResult(tool=tool, ok=True,
                      outcome="kind" if findings else "no_emit",
                      findings=list(findings))


def _err(tool):
    return ToolResult(tool=tool, ok=False, outcome="quarantine",
                      error_code=f"{tool}_err", detail="boom")


def test_aggregate_isolates_failures_and_flags_degraded():
    from webui_app.services.copilot_models import Finding
    good = _ok("equity-ledger", Finding(type="stale_link", source_tool="equity-ledger",
                                        source_ref="equity-ledger:t", summary="s"))
    result = adv.aggregate(adapters=[lambda: good, lambda: _err("audit-state")])
    assert result.considered == 2
    assert result.problem_count == 1
    assert result.degraded is True
    # the healthy tool's finding still aggregates despite the other failing
    assert len(result.findings) == 1
    # the failed tool is present in tool_results (not silently dropped)
    assert any(not r.ok for r in result.tool_results)


def test_aggregate_clean_run_not_degraded():
    result = adv.aggregate(adapters=[lambda: _ok("equity-ledger"), lambda: _ok("canary")])
    assert result.degraded is False
    assert result.problem_count == 0
    assert result.findings == []


def test_aggregate_survives_adapter_that_itself_throws():
    def crash():
        raise RuntimeError("adapter bug")
    result = adv.aggregate(adapters=[crash, lambda: _ok("canary")])
    assert result.problem_count == 1
    assert result.degraded is True


# --- TTL cache -----------------------------------------------------------------


def test_cached_aggregate_recomputes_only_after_ttl(monkeypatch):
    adv.reset_cache()
    calls = {"n": 0}

    def fake_aggregate(*, stale_days=30):
        calls["n"] += 1
        return adv.AggregateResult([], [], False, 0, 0)

    monkeypatch.setattr(adv, "aggregate", fake_aggregate)
    t = {"now": 1000.0}
    clock = lambda: t["now"]

    adv.cached_aggregate(ttl_seconds=30.0, clock=clock)
    adv.cached_aggregate(ttl_seconds=30.0, clock=clock)  # within TTL → cached
    assert calls["n"] == 1
    t["now"] = 1031.0  # past TTL
    adv.cached_aggregate(ttl_seconds=30.0, clock=clock)
    assert calls["n"] == 2
    adv.reset_cache()


def test_counts_are_non_identifying():
    result = adv.aggregate(adapters=[lambda: _ok("canary")])
    counts = result.counts()
    assert set(counts) == {"findings", "tools", "problems", "degraded"}
    assert all(isinstance(v, int) for v in counts.values())
