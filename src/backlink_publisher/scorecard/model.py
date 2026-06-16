"""ChannelScoreRow — the per-channel scorecard row.

Pure data type shared by the engine (``scorecard.engine``), the CLI verb, and
the WebUI card. No I/O. A **signal vector**, not a composite — there is
deliberately no single "channel score" field (plan D-B; mirrors
``ledger.model.LedgerRow``'s no-composite stance).

Each row pairs a channel's **declared** registry signals with its **measured**
liveness signals so the operator can see *declared-vs-measured divergence* at a
glance. Axes that are not yet built (GA4 referral, GSC discovery, AI
retrievability) carry the :data:`AXIS_INERT` sentinel — honest "not measured",
never a misleading zero.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

#: Sentinel for a value axis that is deliberately not built yet (Wave-0 DESCOPE).
#: Rendered verbatim so a reader never mistakes "not measured" for "measured zero".
AXIS_INERT = "inert:not-landed"


@dataclass
class ChannelScoreRow:
    """One channel's decomposed scorecard (declared ‖ measured, vector).

    ``channel`` is the registry platform slug, or ``"(unattributed)"`` for live
    links whose platform could not be resolved (orphan article + sparse
    history). Channels are never silently dropped.
    """

    channel: str

    # --- Declared (registry, static human-authored) ---
    #: "dofollow" | "nofollow" | "uncertain" | "unregistered"
    declared_dofollow: str = "unregistered"
    #: "high" | "low" | None  (None for the dofollow tier / unregistered, by design)
    declared_referral_value: str | None = None

    # --- Measured (events.db + history, live read) ---
    total_links: int = 0
    live_links: int = 0
    #: live / total, or None when there are no links (avoids a misleading 0.0).
    live_pct: float | None = None
    #: Live links whose channel's registry dofollow status is True (the channel's
    #: own tier — equals ``declared_dofollow == "dofollow"`` for any attributed
    #: channel; the ``(unattributed)`` bucket classifies as unknown so never
    #: contributes). Always ``<= live_links``.
    live_dofollow: int = 0
    #: counts by per-link liveness status: failed / stale / live / unverified.
    liveness_breakdown: dict[str, int] = field(default_factory=dict)

    # --- Sample honesty (plan R12: low sample ≠ zero value) ---
    small_sample: bool = True
    #: "insufficient-data" | "ok" — paired with small_sample for display.
    sample_note: str = "insufficient-data"

    # --- Declared-vs-measured divergence (advisory flags, not a verdict) ---
    divergence: list[str] = field(default_factory=list)

    # --- Deferred value axes (Wave-0 DESCOPE → inert, graceful degradation) ---
    referral_traffic: str = AXIS_INERT  # GA4 — Phase 2
    gsc_discovery: str = AXIS_INERT     # GSC — Phase 2
    ai_retrievability: str = AXIS_INERT  # GEO/doc③ — not landed

    def to_jsonl_dict(self) -> dict:
        """Serialize to the flat dict the CLI emits one-per-line on stdout."""
        return asdict(self)
