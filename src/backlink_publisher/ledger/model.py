"""LedgerRow — the per-target scorecard row and its dimension types.

Pure data types shared by the aggregation engine (``ledger.aggregate``),
the CLI verb, and the WebUI route. No I/O here. Decomposed dimensions only
— there is deliberately no composite "equity index" field (plan R4).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

# Liveness display states (plan R7). Ordered worst-first for precedence:
# failed > stale > live > unverified.
LivenessStatus = Literal["failed", "stale", "live", "unverified"]

# Dofollow classification buckets (plan R3/R3a). ``unknown`` is reserved for
# platforms where ``registry.dofollow_status()`` is None (unregistered /
# retired) — never conflated with an explicit nofollow.
DofollowClass = Literal["dofollow", "uncertain", "nofollow", "unknown"]

_LIVENESS_RANK: dict[str, int] = {
    "failed": 3,
    "stale": 2,
    "live": 1,
    "unverified": 0,
}


def worst_liveness(statuses: list[str]) -> LivenessStatus:
    """Return the worst-status-wins liveness across a target's rows (plan R7a).

    Empty input ⇒ ``"unverified"`` (a target with no liveness evidence).
    """
    worst = "unverified"
    for status in statuses:
        if _LIVENESS_RANK.get(status, 0) > _LIVENESS_RANK[worst]:
            worst = status
    return worst  # type: ignore[return-value]


@dataclass
class DofollowBreakdown:
    """Per-target link counts by dofollow classification.

    ``high``/``low`` are the *nofollow* referral sub-grade only — dofollow
    links carry no high/low tier (the registry returns ``referral_value
    is None`` for dofollow platforms by design). See plan R3/R5.
    """

    dofollow: int = 0
    uncertain: int = 0
    nofollow: int = 0
    unknown: int = 0
    nofollow_high: int = 0
    nofollow_low: int = 0


@dataclass
class LedgerRow:
    """One target page's decomposed scorecard.

    ``target_url`` is the canonical dedup key (``_util.url.canonicalize_url``).
    A row with zero links (a target that was attempted but never successfully
    published) renders as ``0/0`` with ``liveness="unverified"`` (plan R1a).
    """

    target_url: str
    total_links: int = 0
    live_links: int = 0
    dofollow: DofollowBreakdown = field(default_factory=DofollowBreakdown)
    # Headline: live AND dofollow links. No tier — dofollow has no high/low.
    live_dofollow: int = 0
    platform_count: int = 0
    platforms: list[str] = field(default_factory=list)
    # Platforms where this target has a link that is BOTH live AND dofollow.
    # A strict subset of ``platforms`` (which counts any link, incl. nofollow /
    # dead). Consumed by ``plan-gap`` so its fan-out subtracts the live-dofollow
    # set (not ``platforms``), letting a nofollow/dead platform be re-proposed.
    live_dofollow_platforms: list[str] = field(default_factory=list)
    exact_match_pct: float = 0.0
    # False when the target has no per-target anchor entries — U5 renders "—"
    # rather than a misleading 0.0% (plan U3 / silent-0.0 guard).
    has_anchor_data: bool = False
    liveness: LivenessStatus = "unverified"
    # ISO timestamp of the most recent successful verify backing ``liveness``,
    # always shown inline so a fresh label never implies present-tense rel.
    liveness_verified_at: str | None = None
    # True when liveness is row-level evidence (a history row bundling several
    # article_urls) rather than per-link (plan R7a qualifier).
    liveness_row_level: bool = False
    # WebUI recheck (U6) reschecks every backing history row by id.
    history_item_ids: list[str] = field(default_factory=list)

    def to_jsonl_dict(self) -> dict:
        """Serialize to the flat dict the CLI emits one-per-line on stdout."""
        return asdict(self)
