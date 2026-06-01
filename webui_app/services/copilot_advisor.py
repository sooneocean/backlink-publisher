"""Advisory aggregator for the Pro Mode Copilot (Plan U1).

Flask-free. Runs the read-only advisory engines **in-process** and normalizes
each engine's rows into the tool-agnostic ``Finding`` taxonomy via per-tool
adapters. Tool-specific semantics (canary status mapping, liveness→type) live
here; the ranking engine (U2) sees only ``Finding.type``.

On-render aggregation uses four in-process / cached sources:
``equity-ledger``, ``audit-state``, ``cull-channels``, ``canary`` (read from the
cached verdict store via ``get_health``/``list_all`` — never a live run, so
``record_verdict`` cannot fire on render). ``seo-viz`` (PipelineAPI subprocess)
and ``preflight`` (live fetch) are intentionally **excluded** from the per-render
path — both would re-create the synchronous-subprocess page-load freeze, and the
in-process ledger already supplies the anchor-concentration signal. They surface
only via an explicit operator "run live" action (a v3-style seam).

Per-tool failures are isolated into an ``ok=False`` ``ToolResult`` (never a
false-green); the aggregate carries a ``degraded`` flag so the UI can disclose
incomplete data. A short module-level TTL cache bounds per-render cost (the
panel mounts on every page).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from backlink_publisher.audit import (
    AuditReadError,
    find_divergences,
    read_snapshot,
)
from backlink_publisher.canary import store as canary_store
from backlink_publisher.ledger import build_ledger
from backlink_publisher.publishing.registry import (
    dofollow_status,
    referral_value,
    registered_platforms,
)

from .copilot_models import Finding, Freshness, ToolResult

# Anchor exact-match share (0–1 ratio) above which a target is flagged
# over-concentrated. Tunable against real data — see plan Deferred questions.
EXACT_MATCH_CONCENTRATION_THRESHOLD = 0.40

_LIVE = Freshness(kind="live")


@dataclass(frozen=True)
class AggregateResult:
    """Outcome of one aggregation pass across the on-render advisory tools."""

    tool_results: list[ToolResult]
    findings: list[Finding]
    degraded: bool
    considered: int
    problem_count: int

    def counts(self) -> dict[str, int]:
        """Non-identifying counts for RECON logging (no domains)."""
        return {
            "findings": len(self.findings),
            "tools": self.considered,
            "problems": self.problem_count,
            "degraded": 1 if self.degraded else 0,
        }


# --- per-tool adapters (own the Finding taxonomy) ------------------------------


def adapt_equity(*, stale_days: int = 30, build=build_ledger) -> ToolResult:
    try:
        rows = build(stale_days=stale_days)
    except Exception as exc:  # isolate: one tool's failure never sinks the rest
        return ToolResult(
            tool="equity-ledger", ok=False, outcome="quarantine",
            error_code="equity_unreadable", detail=_cap(str(exc)),
        )
    findings: list[Finding] = []
    for row in rows:
        ref = f"equity-ledger:{row.target_url}"
        if row.total_links > 0 and row.live_links == 0 and row.liveness == "failed":
            findings.append(_f("unreachable_target", "equity-ledger", ref,
                               f"All links dead for {row.target_url}",
                               {"total_links": row.total_links}))
        elif row.liveness == "stale":
            findings.append(_f("stale_link", "equity-ledger", ref,
                               f"Liveness stale for {row.target_url}",
                               {"verified_at": row.liveness_verified_at}))
        elif row.liveness == "unverified" and row.total_links > 0:
            findings.append(_f("unmeasured", "equity-ledger", ref,
                               f"No liveness evidence for {row.target_url}", {}))
        if row.total_links > 0 and row.live_dofollow == 0:
            findings.append(_f("no_dofollow_equity", "equity-ledger", ref,
                               f"No live dofollow links for {row.target_url}",
                               {"live_links": row.live_links}))
        if row.has_anchor_data and row.exact_match_pct >= EXACT_MATCH_CONCENTRATION_THRESHOLD:
            findings.append(_f("over_concentrated_anchor", "equity-ledger", ref,
                               f"Exact-match anchors {row.exact_match_pct:.0%} for {row.target_url}",
                               {"exact_match_pct": row.exact_match_pct}))
    return _result("equity-ledger", findings)


def adapt_audit(*, read=read_snapshot, diff=find_divergences) -> ToolResult:
    try:
        snapshot = read()
        records = diff(snapshot)
    except AuditReadError as exc:
        return ToolResult(
            tool="audit-state", ok=False, outcome="quarantine",
            error_code="audit_unreadable", detail=_cap(str(exc)),
        )
    except Exception as exc:
        return ToolResult(
            tool="audit-state", ok=False, outcome="quarantine",
            error_code="audit_error", detail=_cap(str(exc)),
        )
    findings = [
        _f("state_drift", "audit-state",
           f"audit-state:{rec.divergence_class}:{rec.canonical_url or rec.article_id or '?'}",
           f"State drift: {rec.divergence_class}",
           {"class": rec.divergence_class, "source_tier": rec.source_tier,
            "authority": rec.authority})
        for rec in records
    ]
    return _result("audit-state", findings)


def adapt_cull(*, platforms=registered_platforms, dofollow=dofollow_status,
               referral=referral_value) -> ToolResult:
    try:
        names = platforms()
    except Exception as exc:
        return ToolResult(
            tool="cull-channels", ok=False, outcome="quarantine",
            error_code="registry_error", detail=_cap(str(exc)),
        )
    findings: list[Finding] = []
    for name in names:
        # The authoritative rule (cull_channels._classify): a registered
        # platform is a cull-candidate iff dofollow is explicitly False AND its
        # nofollow referral sub-grade is "low". "uncertain" is never auto-culled.
        if dofollow(name) is False and referral(name) == "low":
            findings.append(_f("cull_candidate", "cull-channels",
                               f"cull-channels:{name}",
                               f"Low-equity nofollow channel: {name}",
                               {"platform": name}))
    return _result("cull-channels", findings)


def adapt_canary(*, list_all=canary_store.list_all) -> ToolResult:
    try:
        health = list_all()
    except Exception as exc:
        return ToolResult(
            tool="canary", ok=False, outcome="quarantine",
            error_code="canary_unreadable", detail=_cap(str(exc)),
        )
    findings: list[Finding] = []
    for platform, rec in sorted(health.items()):
        status = rec.get("status")
        as_of = rec.get("last_drift_at") or rec.get("last_ok_at")
        fresh = Freshness(kind="cached", as_of=as_of)
        ref = f"canary:{platform}"
        if rec.get("quarantined") or status == canary_store.STATUS_DRIFT_CONFIRMED:
            findings.append(Finding(
                type="failed_canary", source_tool="canary", source_ref=ref,
                summary=f"Canary drift on {platform}",
                raw_metric={"consecutive_failures": rec.get("consecutive_failures")},
                freshness=fresh))
        elif status == canary_store.STATUS_ADVISORY:
            findings.append(Finding(
                type="unmeasured", source_tool="canary", source_ref=ref,
                summary=f"Canary read inconclusive for {platform}",
                raw_metric={}, freshness=fresh))
        # link-alive / not-configured → no emit (healthy or out of scope)
    return _result("canary", findings)


# --- aggregation ---------------------------------------------------------------


def _default_adapters(stale_days: int) -> list[Callable[[], ToolResult]]:
    return [
        lambda: adapt_equity(stale_days=stale_days),
        adapt_audit,
        adapt_cull,
        adapt_canary,
    ]


def aggregate(
    *,
    stale_days: int = 30,
    adapters: list[Callable[[], ToolResult]] | None = None,
) -> AggregateResult:
    """Run every adapter, isolate failures, flatten findings, flag degraded."""
    runners = adapters if adapters is not None else _default_adapters(stale_days)
    results: list[ToolResult] = []
    for run in runners:
        try:
            results.append(run())
        except Exception as exc:  # an adapter that itself throws must not abort
            results.append(ToolResult(
                tool="unknown", ok=False, outcome="quarantine",
                error_code="adapter_crash", detail=_cap(str(exc))))
    findings = [fnd for r in results if r.ok for fnd in r.findings]
    problem_count = sum(
        1 for r in results if not r.ok or r.outcome == "quarantine"
    )
    return AggregateResult(
        tool_results=results,
        findings=findings,
        degraded=problem_count > 0,
        considered=len(results),
        problem_count=problem_count,
    )


# --- module-level TTL cache (panel mounts on every page) -----------------------

_CACHE: dict[int, tuple[float, AggregateResult]] = {}


def cached_aggregate(
    *,
    stale_days: int = 30,
    ttl_seconds: float = 30.0,
    clock: Callable[[], float] = time.monotonic,
) -> AggregateResult:
    """``aggregate`` behind a short per-process TTL keyed by ``stale_days``.

    Module-level (cross-request) on purpose — a per-request cache would give
    zero reuse across page navigations. ``clock`` is injectable for tests.
    """
    now = clock()
    hit = _CACHE.get(stale_days)
    if hit is not None and hit[0] > now:
        return hit[1]
    result = aggregate(stale_days=stale_days)
    _CACHE[stale_days] = (now + ttl_seconds, result)
    return result


def reset_cache() -> None:
    """Clear the aggregate cache (test hook / config-change invalidation)."""
    _CACHE.clear()


# --- helpers -------------------------------------------------------------------


def _f(ftype: str, tool: str, ref: str, summary: str, metric: dict) -> Finding:
    return Finding(type=ftype, source_tool=tool, source_ref=ref,
                   summary=summary, raw_metric=metric, freshness=_LIVE)


def _result(tool: str, findings: list[Finding]) -> ToolResult:
    return ToolResult(
        tool=tool, ok=True,
        outcome="kind" if findings else "no_emit",
        findings=findings,
    )


def _cap(message: str, limit: int = 4000) -> str:
    """Length-cap a surfaced error so a target URL / page snippet folded into an
    exception string can't bloat the response or logs."""
    return message if len(message) <= limit else message[:limit] + "…"
