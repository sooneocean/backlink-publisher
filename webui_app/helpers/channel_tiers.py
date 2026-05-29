"""Group the settings overview channels into automation tiers.

Pure presentation-layer helper for the WebUI ``/settings`` overview panel
(Plan 2026-05-29-003). Takes the existing ``dashboard_channels`` list
``[(name, status_dict), ...]`` and buckets it into three independent
automation tiers derived solely from ``status['auth_type']``:

  - tier-1 「开箱即用」     anon                         — no credentials needed
  - tier-2 「填凭证即自动」  token / token_fields / oauth /
                           userpass / None / unknown     — fill credentials, then auto
  - tier-3 「需浏览器登录态」 paste_blob / live_browser     — browser login state

Within each tier, ready channels (already bound, or anon = no binding needed)
sort ahead of unconfigured ones; both segments preserve the caller's input
order (the caller passes ``active_platforms()`` order, i.e. alphabetical) for
stable rendering. Empty tiers are dropped.

Computed fresh on every call (never cached), mirroring
``registry.platforms_by_auth_type()`` — a newly registered platform is
reflected without a reload.
"""

from __future__ import annotations

from typing import Any

# Authoritative auth_type → tier mapping (Plan 2026-05-29-003 R4).
# ``None`` and any unrecognized auth_type fall back to tier-2 (R4a) so a
# channel never silently disappears from all groups.
TIER_BY_AUTH_TYPE: dict[str | None, str] = {
    "anon": "tier-1",
    "token": "tier-2",
    "token_fields": "tier-2",
    "oauth": "tier-2",
    "userpass": "tier-2",
    None: "tier-2",
    "paste_blob": "tier-3",
    "live_browser": "tier-3",
}

_FALLBACK_TIER = "tier-2"

# Ordered tier metadata: label/subtitle (R11) and default-open state (R2).
# tier-1 opens by default; tier-2/3 stay collapsed.
_TIER_META: tuple[dict[str, Any], ...] = (
    {
        "key": "tier-1",
        "label": "开箱即用",
        "subtitle": "无需任何配置即可发布",
        "open": True,
    },
    {
        "key": "tier-2",
        "label": "填凭证即自动",
        "subtitle": "填入凭证后即可自动发布",
        "open": False,
    },
    {
        "key": "tier-3",
        "label": "需浏览器登录态(半自动)",
        "subtitle": "需在浏览器中完成登录态后发布",
        "open": False,
    },
)


def _tier_for(auth_type: str | None) -> str:
    """Map an ``auth_type`` to its tier key; unknown values default to tier-2 (R4a)."""
    return TIER_BY_AUTH_TYPE.get(auth_type, _FALLBACK_TIER)


def _is_ready(status: dict[str, Any]) -> bool:
    """A channel is "ready" when it needs no binding (anon) or is already bound.

    Single source of truth for both the R3 ready-count and the R5 ready-first
    ordering, so the two can never diverge.
    """
    return status.get("auth_type") == "anon" or bool(status.get("bound"))


def group_channels_by_tier(
    dashboard_channels: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Bucket ``[(name, status), ...]`` into ordered automation tiers.

    Returns a list of tier dicts (tier-1 first), each shaped::

        {key, label, subtitle, open, total, ready, channels}

    where ``channels`` is ``[(name, status, ready), ...]`` with ready channels
    first and each segment preserving input order (R5/R6). Empty tiers are
    omitted (R12). Never cached — recomputed on every call.
    """
    buckets: dict[str, list[tuple[str, dict[str, Any], bool]]] = {
        meta["key"]: [] for meta in _TIER_META
    }
    for name, status in dashboard_channels:
        ready = _is_ready(status)
        buckets[_tier_for(status.get("auth_type"))].append((name, status, ready))

    tiers: list[dict[str, Any]] = []
    for meta in _TIER_META:
        members = buckets[meta["key"]]
        if not members:
            continue  # R12: drop empty tiers
        # R5/R6: ready first; stable sort keeps each segment in input order.
        ordered = sorted(members, key=lambda item: not item[2])
        ready_count = sum(1 for _, _, ready in ordered if ready)
        tiers.append(
            {
                "key": meta["key"],
                "label": meta["label"],
                "subtitle": meta["subtitle"],
                "open": meta["open"],
                "total": len(ordered),
                "ready": ready_count,
                "channels": ordered,
            }
        )
    return tiers
