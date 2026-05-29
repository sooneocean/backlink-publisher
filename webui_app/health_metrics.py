"""Read-only health aggregations for the publishing dashboard.

Plan 2026-05-25-006 / U2. Pure, deterministic queries over the (post-005-fix)
``events.db`` projection plus ``channel_status``. No mutation, no network.

Honesty rules baked into the queries
------------------------------------
- **Success = ``publish.confirmed`` only.** 005-fix emits a *distinct*
  ``publish.unverified`` kind for a ``done`` whose post-publish verify failed
  (CLI exit 5). The terminal universe is ``confirmed + unverified + failed`` so
  an unverified publish counts *against* success instead of silently vanishing
  from the denominator — that vanishing was exactly the lie this dashboard exists
  to not tell.
- **All queries filter to ``publish.*`` terminal kinds.** The ``events`` table
  also holds banner / image_gen kinds (direct ``EventStore.append``); a bare
  ``COUNT(*)`` would conflate them.
- **Latest-outcome-per-target uses ``ORDER BY ts_utc DESC, id DESC``** — ``ts_utc``
  alone is not a total order (ties across a run), so ``id`` is the tiebreak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backlink_publisher.events import EventStore

#: Terminal publish kinds. ``publish.intent`` is non-terminal and excluded.
_TERMINAL_KINDS = ("publish.confirmed", "publish.unverified", "publish.failed")

#: A per-adapter row with fewer than this many terminal events is flagged as a
#: small sample — its percentage is statistically noisy and the UI says so.
SMALL_SAMPLE_THRESHOLD = 5

#: Channel-status values that mean "operator must act".
_BROKEN_STATUSES = ("expired", "identity_mismatch")

#: Default look-back window.
DEFAULT_WINDOW_DAYS = 30


@dataclass(frozen=True)
class SuccessRate:
    """Overall hero: distinct targets by latest in-window terminal outcome."""

    targets: int = 0
    confirmed: int = 0
    pct: float | None = None  # None == "no data" (denominator 0), not "0%"

    @property
    def has_data(self) -> bool:
        return self.targets > 0


@dataclass(frozen=True)
class AdapterHealth:
    platform: str  # "Unattributed" when the event carried no platform
    confirmed: int
    unverified: int
    failed: int
    total: int
    pct: float | None
    small_sample: bool


@dataclass(frozen=True)
class ErrorBucket:
    error_class: str  # "unclassified" when the failed event carried no class
    count: int


@dataclass(frozen=True)
class BrokenChannel:
    channel: str
    status: str  # one of _BROKEN_STATUSES
    last_verified_at: str | None = None


@dataclass(frozen=True)
class Health:
    window_days: int
    since_utc: str
    success: SuccessRate
    per_adapter: list[AdapterHealth] = field(default_factory=list)
    errors: list[ErrorBucket] = field(default_factory=list)
    broken: list[BrokenChannel] = field(default_factory=list)


def _window_start(now: datetime, window_days: int) -> str:
    """ISO-8601 UTC lower bound, matching the projector's ts_utc format.

    Events store ``ts_utc`` as ``...+00:00`` ISO strings; the same format here
    makes the ``ts_utc >= ?`` comparison a correct lexicographic range check.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now.astimezone(timezone.utc) - timedelta(days=window_days)).isoformat()


def success_rate(store: EventStore, *, since_utc: str) -> SuccessRate:
    """Per distinct ``target_url``, take the latest in-window terminal event;
    success = that latest is ``publish.confirmed``."""
    placeholders = ",".join("?" for _ in _TERMINAL_KINDS)
    rows = store.query(
        f"""
        WITH latest AS (
            SELECT target_url, kind,
                   ROW_NUMBER() OVER (
                       PARTITION BY target_url
                       ORDER BY ts_utc DESC, id DESC
                   ) AS rn
            FROM events
            WHERE kind IN ({placeholders})
              AND ts_utc >= ?
              AND target_url IS NOT NULL
        )
        SELECT
            COUNT(*) AS targets,
            SUM(CASE WHEN kind = 'publish.confirmed' THEN 1 ELSE 0 END) AS confirmed
        FROM latest
        WHERE rn = 1
        """,
        (*_TERMINAL_KINDS, since_utc),
    )
    row = rows[0]
    targets = int(row["targets"] or 0)
    confirmed = int(row["confirmed"] or 0)
    pct = round(confirmed * 100.0 / targets, 1) if targets else None
    return SuccessRate(targets=targets, confirmed=confirmed, pct=pct)


def per_adapter(store: EventStore, *, since_utc: str) -> list[AdapterHealth]:
    """Per-platform terminal-event counts, worst success-rate first."""
    placeholders = ",".join("?" for _ in _TERMINAL_KINDS)
    rows = store.query(
        f"""
        SELECT
            json_extract(payload_json, '$.platform') AS platform,
            SUM(CASE WHEN kind = 'publish.confirmed' THEN 1 ELSE 0 END) AS confirmed,
            SUM(CASE WHEN kind = 'publish.unverified' THEN 1 ELSE 0 END) AS unverified,
            SUM(CASE WHEN kind = 'publish.failed' THEN 1 ELSE 0 END) AS failed,
            COUNT(*) AS total
        FROM events
        WHERE kind IN ({placeholders})
          AND ts_utc >= ?
        GROUP BY platform
        """,
        (*_TERMINAL_KINDS, since_utc),
    )
    out: list[AdapterHealth] = []
    for r in rows:
        total = int(r["total"] or 0)
        confirmed = int(r["confirmed"] or 0)
        out.append(
            AdapterHealth(
                platform=r["platform"] if r["platform"] is not None else "Unattributed",
                confirmed=confirmed,
                unverified=int(r["unverified"] or 0),
                failed=int(r["failed"] or 0),
                total=total,
                pct=round(confirmed * 100.0 / total, 1) if total else None,
                small_sample=total < SMALL_SAMPLE_THRESHOLD,
            )
        )
    # Worst-first: lowest success pct leads; treat None pct as worst; break ties
    # by larger sample so a 0/20 outranks a 0/1.
    out.sort(key=lambda a: (a.pct if a.pct is not None else -1.0, -a.total))
    return out


def error_distribution(store: EventStore, *, since_utc: str) -> list[ErrorBucket]:
    """Counts of ``publish.failed`` events grouped by ``error_class``."""
    rows = store.query(
        """
        SELECT
            json_extract(payload_json, '$.error_class') AS error_class,
            COUNT(*) AS count
        FROM events
        WHERE kind = 'publish.failed'
          AND ts_utc >= ?
        GROUP BY error_class
        ORDER BY count DESC, error_class
        """,
        (since_utc,),
    )
    return [
        ErrorBucket(
            error_class=r["error_class"] if r["error_class"] is not None else "unclassified",
            count=int(r["count"] or 0),
        )
        for r in rows
    ]


def decay_counts(store: EventStore | None = None) -> dict[str, int]:
    """Backlink decay counts by latest recheck verdict (Plan 2026-05-29-004 U6).

    Thin wrapper over ``recheck.events_io.derive_decay_counts`` so the /ce:health
    route reads decay state through the same health-metrics surface as the other
    aggregations. Reports current state (latest verdict per link, no age window —
    an old un-rechecked dead link still counts).
    """
    from backlink_publisher.recheck.events_io import derive_decay_counts

    return derive_decay_counts(store if store is not None else EventStore())


def broken_channels() -> list[BrokenChannel]:
    """Channels currently flagged expired / identity_mismatch (display-only).

    Reactive: ``channel_status`` only models the channels that publish through
    it (velog/medium/blogger) and flips on failure, not proactively — the UI
    labels this scope honestly (R9). No network.
    """
    from webui_store.channel_status import list_all

    out: list[BrokenChannel] = []
    for channel, rec in sorted(list_all().items()):
        if not isinstance(rec, dict):
            continue
        status = rec.get("status")
        if status in _BROKEN_STATUSES:
            out.append(
                BrokenChannel(
                    channel=channel,
                    status=status,
                    last_verified_at=rec.get("last_verified_at"),
                )
            )
    return out


def build_health(
    store: EventStore | None = None,
    *,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Health:
    """Assemble the four aggregates for a single dashboard render."""
    store = store or EventStore()
    now = now or datetime.now(timezone.utc)
    since = _window_start(now, window_days)
    return Health(
        window_days=window_days,
        since_utc=since,
        success=success_rate(store, since_utc=since),
        per_adapter=per_adapter(store, since_utc=since),
        errors=error_distribution(store, since_utc=since),
        broken=broken_channels(),
    )
