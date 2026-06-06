"""build_channel_scorecard — the single per-channel engine entry (Wave-0 MVP).

Re-keys the shared ``ledger.sources.build_target_buckets`` join by **channel**
(platform) and reuses ``ledger.aggregate``'s dofollow classification and liveness
rules verbatim, so the scorecard and the equity-ledger can never disagree on what
"dofollow" or "live" means. Both the CLI verb and the WebUI card call this
in-process (the ledger CLI/WebUI parity pattern).

Platform sourcing: ``build_target_buckets`` attaches ``platform`` only from
``history_store``, which is sparse in practice. We augment it with a
``canonical live_url → platform`` index read from the authoritative
``publish.confirmed`` / ``publish.unverified`` event payloads (joined to
``articles`` by ``article_id``) — the same store, the missing dimension only.
Links + liveness still come solely from ``build_target_buckets`` (no divergent
re-derivation of the core set).

**Precedence:** when a link has a platform from *both* sources, the history
value wins (``link.platform or plat_index.get(...)``) — consistent with how the
equity-ledger attributes platform (history-only). The two should agree (both
record the same publish); the event-payload index only *fills* the gap left by a
sparse history, it does not override it.

Read-only; no writes, no network, no LLM.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.events import EventStore, kinds
from backlink_publisher.publishing import registry

# Importing the adapters package populates the registry via ``register()`` side
# effects — without it every channel would read as unregistered.
import backlink_publisher.publishing.adapters  # noqa: F401,E402

from ..ledger.aggregate import _classify, _link_liveness
from ..ledger.sources import build_target_buckets
from .model import AXIS_INERT, ChannelScoreRow

#: Confirmed-publish kinds whose payload carries the authoritative ``platform``.
_CONFIRMED_KINDS: tuple[str, ...] = (kinds.PUBLISH_CONFIRMED, kinds.PUBLISH_UNVERIFIED)

#: Channels with this many links or fewer are flagged "insufficient-data"
#: (plan R12: low sample is NOT zero value). Operator-tunable per call.
DEFAULT_SMALL_SAMPLE_MAX = 4

#: Channel label for live links whose platform could not be resolved.
UNATTRIBUTED = "(unattributed)"

_LIVENESS_KEYS = ("failed", "stale", "live", "unverified")


def _platform_by_live_url(store: EventStore) -> dict[str, str]:
    """``canonical live_url → platform`` from confirmed-publish event payloads.

    Joins ``events`` to ``articles`` by ``article_id`` (most confirmed payloads
    carry ``platform`` but a NULL ``live_url``; the article row holds the URL).
    First write wins. Never raises on a malformed row.
    """
    idx: dict[str, str] = {}
    placeholders = ",".join("?" for _ in _CONFIRMED_KINDS)
    sql = (
        "SELECT a.live_url AS live_url, "
        "json_extract(e.payload_json, '$.platform') AS platform "
        "FROM events e JOIN articles a ON e.article_id = a.article_id "
        f"WHERE e.kind IN ({placeholders}) "
        "AND a.live_url IS NOT NULL "
        "AND json_extract(e.payload_json, '$.platform') IS NOT NULL"
    )
    for row in store.query(sql, _CONFIRMED_KINDS):
        live = canonicalize_url(row["live_url"]) if row["live_url"] else ""
        plat = row["platform"]
        if live and plat:
            idx.setdefault(live, plat)
    return idx


def _declared(channel: str) -> tuple[str, str | None]:
    """``(declared_dofollow, declared_referral_value)`` from the registry."""
    status = registry.dofollow_status(channel)
    if status is True:
        dofollow = "dofollow"
    elif status is False:
        dofollow = "nofollow"
    elif status == "uncertain":
        dofollow = "uncertain"
    else:
        dofollow = "unregistered"
    return dofollow, registry.referral_value(channel)


def _divergence(
    *, declared_dofollow: str, declared_referral: str | None,
    total: int, live: int, live_dofollow: int,
) -> list[str]:
    """Advisory declared-vs-measured tension flags (never a verdict)."""
    flags: list[str] = []
    if total == 0:
        return flags  # nothing measured yet — divergence is undefined, not a flag
    if declared_dofollow == "dofollow" and live_dofollow == 0:
        flags.append("declared-dofollow:no-live-dofollow-observed")
    if declared_referral == "high" and live == 0:
        flags.append("declared-high-value:no-live-links")
    if declared_dofollow == "uncertain" and live > 0:
        flags.append("uncertain-dofollow:has-live-links(run-canary-to-confirm)")
    return flags


def build_channel_scorecard(
    *,
    stale_days: int = 30,
    small_sample_max: int = DEFAULT_SMALL_SAMPLE_MAX,
    store: EventStore | None = None,
    history: list[dict[str, Any]] | None = None,
) -> list[ChannelScoreRow]:
    """Build the per-channel signal-vector scorecard.

    Every **registered** channel gets a row (declared half) even with zero
    measured links, plus any channel observed in the data. ``store``/``history``
    are injectable for tests. Default sort surfaces weakest-presence channels
    first (live links ascending) — a raw dimension, not a composite rank.
    """
    now = datetime.now()
    store = store or EventStore()
    buckets = build_target_buckets(store=store, history=history)
    plat_index = _platform_by_live_url(store)

    # Pivot every link by its resolved channel.
    seen: dict[str, dict] = {}

    def channel_acc(ch: str) -> dict:
        return seen.setdefault(ch, {
            "total": 0, "live": 0, "live_dofollow": 0,
            "liveness": {k: 0 for k in _LIVENESS_KEYS},
        })

    for bucket in buckets.values():
        for link in bucket.links.values():
            resolved = link.platform or plat_index.get(link.live_url)
            ch = resolved or UNATTRIBUTED
            acc = channel_acc(ch)
            acc["total"] += 1
            status = _link_liveness(link, now, stale_days)
            acc["liveness"][status] = acc["liveness"].get(status, 0) + 1
            if status == "live":
                acc["live"] += 1
                if _classify(resolved)[0] == "dofollow":
                    acc["live_dofollow"] += 1

    # Union of registered channels (declared half) and observed channels.
    channels = set(registry.registered_platforms()) | set(seen.keys())

    rows: list[ChannelScoreRow] = []
    for ch in channels:
        acc = seen.get(ch)
        total = acc["total"] if acc else 0
        live = acc["live"] if acc else 0
        live_dofollow = acc["live_dofollow"] if acc else 0
        breakdown = acc["liveness"] if acc else {k: 0 for k in _LIVENESS_KEYS}
        declared_dofollow, declared_referral = _declared(ch)
        small = total <= small_sample_max
        rows.append(ChannelScoreRow(
            channel=ch,
            declared_dofollow=declared_dofollow,
            declared_referral_value=declared_referral,
            total_links=total,
            live_links=live,
            live_pct=(round(live / total, 3) if total else None),
            live_dofollow=live_dofollow,
            liveness_breakdown=dict(breakdown),
            small_sample=small,
            sample_note="insufficient-data" if small else "ok",
            divergence=_divergence(
                declared_dofollow=declared_dofollow,
                declared_referral=declared_referral,
                total=total, live=live, live_dofollow=live_dofollow,
            ),
            referral_traffic=AXIS_INERT,
            gsc_discovery=AXIS_INERT,
            ai_retrievability=AXIS_INERT,
        ))

    rows.sort(key=lambda r: (r.live_links, r.total_links, r.channel))
    return rows
