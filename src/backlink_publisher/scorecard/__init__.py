"""Per-channel realized-value scorecard (Wave-0 MVP).

Re-pivots the shared ``ledger.sources.build_target_buckets`` join by **channel**
(platform) instead of by target, placing each channel's *declared* registry
signals (dofollow status, referral_value) side-by-side with *measured* signals
(placements, liveness) as a **signal vector** — there is deliberately no
composite "channel score" number (plan D-B / doc⑤ veto).

The GA4 referral, GSC discovery, and AI-retrievability axes are **deferred**
(Wave-0 measurement → DESCOPE): they render as ``axis inert / not landed`` via
graceful degradation rather than being computed here.

Read-only. No writes, no network, no LLM. Advisory-only — nothing here feeds the
publish path or auto-gates a channel.
"""

from __future__ import annotations

from .engine import build_channel_scorecard
from .model import AXIS_INERT, ChannelScoreRow

__all__ = ["build_channel_scorecard", "ChannelScoreRow", "AXIS_INERT"]
