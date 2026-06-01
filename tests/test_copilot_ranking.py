"""Tests for the Copilot deterministic ranking engine (Plan U2)."""

from __future__ import annotations

from webui_app.services.copilot_models import Finding, Freshness
from webui_app.services.copilot_ranking import rank


def _finding(ftype: str, ref: str, *, freshness: Freshness | None = None) -> Finding:
    return Finding(
        type=ftype,
        source_tool=ref.split(":", 1)[0],
        source_ref=ref,
        summary=f"{ftype} for {ref}",
        freshness=freshness or Freshness(),
    )


def test_orders_by_severity_then_type_then_ref():
    findings = [
        _finding("stale_link", "equity-ledger:t-b"),       # warning
        _finding("failed_canary", "canary:medium"),         # critical
        _finding("unmeasured", "canary:devto"),             # info
        _finding("unreachable_target", "equity-ledger:t-a"),  # critical
    ]
    ranked = rank(findings)
    severities = [r.severity for r in ranked]
    assert severities == ["critical", "critical", "warning", "info"]
    # priorities are 1-based and contiguous
    assert [r.priority for r in ranked] == [1, 2, 3, 4]
    # within critical tier, type order (unreachable_target before failed_canary)
    assert ranked[0].finding_type == "unreachable_target"
    assert ranked[1].finding_type == "failed_canary"


def test_is_deterministic_across_runs_and_input_order():
    a = _finding("failed_canary", "canary:m")
    b = _finding("stale_link", "equity-ledger:x")
    c = _finding("cull_candidate", "cull:lowsite")
    first = [r.to_dict() for r in rank([a, b, c])]
    shuffled = [r.to_dict() for r in rank([c, a, b])]
    assert first == shuffled


def test_advisory_types_never_rank_critical():
    findings = [
        _finding("cull_candidate", "cull:s1"),
        _finding("unmeasured", "canary:s2"),
    ]
    ranked = rank(findings)
    assert all(r.severity in ("warning", "info") for r in ranked)
    # cull stays advisory, unmeasured is explicitly not "passed"
    by_type = {r.finding_type: r for r in ranked}
    assert by_type["cull_candidate"].rule_id == "cull-candidate-advisory"
    assert by_type["unmeasured"].rule_id == "unmeasured-not-passed"


def test_freshness_passes_through_without_severity_mutation():
    stale = Freshness(kind="cached", as_of="2026-05-01T00:00:00+00:00")
    live = Freshness(kind="live")
    ranked = rank([
        _finding("failed_canary", "canary:old", freshness=stale),
        _finding("failed_canary", "canary:new", freshness=live),
    ])
    # both stay critical in v1 (disclosure, not decay); freshness preserved
    assert {r.severity for r in ranked} == {"critical"}
    by_ref = {r.source_ref: r for r in ranked}
    assert by_ref["canary:old"].freshness == stale
    assert by_ref["canary:new"].freshness == live


def test_empty_findings_yield_empty_list():
    assert rank([]) == []


def test_unknown_type_is_ranked_not_dropped():
    ranked = rank([_finding("brand_new_signal", "futuretool:x")])
    assert len(ranked) == 1
    assert ranked[0].severity == "info"
    assert ranked[0].rule_id == "unranked-type"


def test_every_ranked_item_is_traceable():
    fresh = Freshness(kind="cached", as_of="2026-05-20T12:00:00+00:00")
    ranked = rank([_finding("state_drift", "audit-state:row-7", freshness=fresh)])
    item = ranked[0]
    assert item.rule_id
    assert item.source_ref == "audit-state:row-7"
    assert item.source_tool == "audit-state"
    assert item.freshness == fresh
