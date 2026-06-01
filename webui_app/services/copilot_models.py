"""Shared data types for the Pro Mode Copilot deterministic advisor.

Flask-free, pure data. The aggregator (``copilot_advisor``) produces
``ToolResult``/``Finding`` via per-tool adapters; the ranking engine
(``copilot_ranking``) consumes ``Finding.type`` only and never reaches back
into tool-specific shapes.

Plan: docs/plans/2026-06-01-002-feat-pro-mode-copilot-v1-plan.md (U1/U2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# Tool-agnostic finding vocabulary (the U1/U2 contract). Unit 1's adapters map
# each engine's raw rows into one of these; Unit 2 ranks purely on this enum.
FindingType = Literal[
    "unreachable_target",       # a target page with only dead/failed links
    "failed_canary",            # canary health says a dofollow link went bad
    "over_concentrated_anchor",  # exact-match anchor share over threshold
    "state_drift",              # audit-state divergence (orphan/duplicate/…)
    "stale_link",               # liveness not re-verified within the window
    "cull_candidate",           # low-value channel flagged for review
    "no_dofollow_equity",       # target has zero live+dofollow links
    "unmeasured",               # no verdict/evidence — explicitly NOT "passed"
]

Severity = Literal["critical", "warning", "info"]

# Three-outcome input classifier (projector-silent-drop lesson). A recognized
# row is ``kind``; a recognized-but-deliberately-ignored row is ``no_emit``; an
# unrecognized row from an authoritative source is ``quarantine`` (never
# silently dropped).
Outcome = Literal["kind", "no_emit", "quarantine"]


@dataclass(frozen=True)
class Freshness:
    """When the data behind a finding was produced.

    ``live`` = recomputed in-process this render (ledger/audit/cull).
    ``cached`` = read from a persisted verdict store (canary); ``as_of`` carries
    the verdict timestamp so the UI can show "measured Nd ago" and so a stale
    cached critical is never silently presented as current.
    """

    kind: Literal["live", "cached"] = "live"
    as_of: str | None = None  # ISO timestamp for cached data


@dataclass(frozen=True)
class Finding:
    """One normalized advisory finding, tool-agnostic.

    ``source_ref`` is the human-traceable pointer back to the originating tool
    row (R5) — e.g. ``"equity-ledger:https://t.example/x"``. ``raw_metric``
    carries the few numbers behind the finding for display; it never carries
    secrets and is not used by the ranking table.
    """

    type: str  # one of FindingType
    source_tool: str
    source_ref: str
    summary: str
    raw_metric: dict[str, Any] = field(default_factory=dict)
    freshness: Freshness = field(default_factory=Freshness)


@dataclass(frozen=True)
class ToolResult:
    """Per-tool aggregation outcome. Surfaced honestly — a tool that errored is
    ``ok=False`` with an ``error_code``, never a false-green empty result. An
    ``empty`` outcome (ran, zero findings) is distinct from ``nil`` (crashed)."""

    tool: str
    ok: bool
    outcome: Outcome
    findings: list[Finding] = field(default_factory=list)
    error_code: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RankedFinding:
    """A finding placed in the prioritized list by the deterministic rule table.

    Carries the ``rule_id`` and ``source_ref`` that justify its placement (R5)
    plus ``freshness`` so the UI can disclose staleness.
    """

    priority: int
    severity: Severity
    summary: str
    rule_id: str
    finding_type: str
    source_tool: str
    source_ref: str
    freshness: Freshness

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return out
