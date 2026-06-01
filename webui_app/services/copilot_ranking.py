"""Deterministic ranking rule engine for the Copilot advisor (Plan U2).

Pure ``Finding.type -> (severity, priority, rule_id)`` table with no
tool-specific knowledge — the canary ``rel``-weighting and "unmeasured vs
passed" decisions already happened in Unit 1's adapters. Output is reproducible
and every ranked item is traceable to a rule + source row + freshness (R5).

"Surface, don't decide": ``cull_candidate``/``unmeasured`` rank as advisory and
are never emitted as auto-destructive recommendations. v1 ships freshness as
*disclosure* (a badge), not severity decay — the decay rule is deferred.
"""

from __future__ import annotations

from .copilot_models import Finding, RankedFinding, Severity

# severity rank for ordering — lower sorts first (critical at the top).
_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "warning": 1, "info": 2}

# The rule table: finding type -> (severity, rule_id). This IS the product
# behaviour; weights are tuned against real data later, but the mapping is
# locked by tests. A type absent here is ranked conservatively as info under
# ``unranked-type`` rather than dropped (never silently lose a finding).
_RULES: dict[str, tuple[Severity, str]] = {
    "unreachable_target": ("critical", "unreachable-target"),
    "failed_canary": ("critical", "failed-canary"),
    "over_concentrated_anchor": ("critical", "anchor-over-concentration"),
    "state_drift": ("warning", "state-drift"),
    "stale_link": ("warning", "stale-link"),
    "cull_candidate": ("warning", "cull-candidate-advisory"),
    "no_dofollow_equity": ("warning", "no-dofollow-equity"),
    "unmeasured": ("info", "unmeasured-not-passed"),
}

_UNKNOWN_RULE = ("info", "unranked-type")

# Stable secondary ordering by finding type so output is reproducible across
# runs regardless of input order. Types not listed sort last, then by ref.
_TYPE_ORDER: dict[str, int] = {t: i for i, t in enumerate(_RULES)}


def _rule_for(finding_type: str) -> tuple[Severity, str]:
    return _RULES.get(finding_type, _UNKNOWN_RULE)  # type: ignore[return-value]


def rank(findings: list[Finding]) -> list[RankedFinding]:
    """Rank findings into a deterministic, severity-ordered priority list.

    Ordering: severity (critical→warning→info), then finding-type order, then
    ``source_ref`` — all stable, so repeated runs over the same input produce
    byte-identical output. ``freshness`` is passed through unchanged (no
    severity mutation in v1).
    """
    ranked: list[RankedFinding] = []
    for finding in findings:
        severity, rule_id = _rule_for(finding.type)
        ranked.append(
            RankedFinding(
                priority=0,  # filled after sort, below
                severity=severity,
                summary=finding.summary,
                rule_id=rule_id,
                finding_type=finding.type,
                source_tool=finding.source_tool,
                source_ref=finding.source_ref,
                freshness=finding.freshness,
            )
        )

    ranked.sort(
        key=lambda r: (
            _SEVERITY_ORDER.get(r.severity, 99),
            _TYPE_ORDER.get(r.finding_type, len(_TYPE_ORDER)),
            r.source_ref,
        )
    )

    # Assign 1-based priority reflecting final order (frozen dataclass → rebuild).
    return [
        RankedFinding(
            priority=i + 1,
            severity=r.severity,
            summary=r.summary,
            rule_id=r.rule_id,
            finding_type=r.finding_type,
            source_tool=r.source_tool,
            source_ref=r.source_ref,
            freshness=r.freshness,
        )
        for i, r in enumerate(ranked)
    ]
