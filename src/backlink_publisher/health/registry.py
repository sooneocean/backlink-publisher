from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from backlink_publisher.events.kinds import (
    CHANNEL_PUBLISHED_TO,
    CHANNEL_RECHECK_OBSERVED,
    CHANNEL_ROUTED,
)
from backlink_publisher.events.store import EventStore

_ALIVE: Final[str] = "alive"
_DETERMINISTIC_DEAD: Final[set[str]] = {
    "host_gone",
    "link_stripped",
    "dofollow_lost",
    "probe_error",
}


@dataclass(frozen=True)
class ChannelHealth:
    """Aggregated health snapshot for a single publishing channel."""

    channel: str
    total_rechecks: int
    alive_count: int
    dead_count: int
    host_gone_count: int
    link_stripped_count: int
    dofollow_lost_count: int
    probe_error_count: int
    last_alive_at: str | None
    last_dead_at: str | None
    survival_rate: float | None

    def __init__(
        self,
        *,
        channel: str,
        total_rechecks: int,
        alive_count: int = 0,
        dead_count: int = 0,
        host_gone_count: int = 0,
        link_stripped_count: int = 0,
        dofollow_lost_count: int = 0,
        probe_error_count: int = 0,
        last_alive_at: str | None = None,
        last_dead_at: str | None = None,
    ) -> None:
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "total_rechecks", total_rechecks)
        object.__setattr__(self, "alive_count", alive_count)
        object.__setattr__(self, "dead_count", dead_count)
        object.__setattr__(self, "host_gone_count", host_gone_count)
        object.__setattr__(self, "link_stripped_count", link_stripped_count)
        object.__setattr__(self, "dofollow_lost_count", dofollow_lost_count)
        object.__setattr__(self, "probe_error_count", probe_error_count)
        object.__setattr__(self, "last_alive_at", last_alive_at)
        object.__setattr__(self, "last_dead_at", last_dead_at)
        sr = alive_count / total_rechecks if total_rechecks > 0 else None
        object.__setattr__(self, "survival_rate", sr)

    @property
    def has_data(self) -> bool:
        return self.total_rechecks > 0

    @property
    def primary_death_cause(self) -> str | None:
        counts: dict[str, int] = {
            "host_gone": self.host_gone_count,
            "link_stripped": self.link_stripped_count,
            "dofollow_lost": self.dofollow_lost_count,
            "probe_error": self.probe_error_count,
        }
        max_count = 0
        max_cause: str | None = None
        for cause, count in counts.items():
            if count > max_count:
                max_count = count
                max_cause = cause
        if max_cause == "host_gone":
            return "Host Gone"
        if max_cause == "link_stripped":
            return "Link Stripped"
        if max_cause == "dofollow_lost":
            return "Dofollow Lost"
        if max_cause == "probe_error":
            return "Probe Error"
        return None


def _window_start(window_days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()


def _parse_payload_json(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except (ValueError, TypeError):
        return {}


class ChannelHealthRegistry:
    """Read-side aggregation: queries events.db for per-channel survival metrics.

    Construction does not open the DB; pass an ``EventStore`` instance.
    All methods are pure queries — never write.
    """

    def __init__(self, store: EventStore) -> None:
        self._store = store

    def get_health(self, channel: str, *, window_days: int = 30) -> ChannelHealth:
        ws = _window_start(window_days)
        rows = self._store.query(
            _SURVIVAL_SQL_SINGLE, (CHANNEL_RECHECK_OBSERVED, channel, ws),
        )
        if not rows:
            return ChannelHealth(channel=channel, total_rechecks=0)
        return _row_to_health(rows[0])

    def get_all_health(self, *, window_days: int = 30) -> dict[str, ChannelHealth]:
        ws = _window_start(window_days)
        rows = self._store.query(
            _SURVIVAL_SQL_ALL, (CHANNEL_RECHECK_OBSERVED, ws),
        )
        result: dict[str, ChannelHealth] = {}
        for row in rows:
            health = _row_to_health(row)
            result[health.channel] = health
        return result

    def get_routing_history(
        self,
        *,
        limit: int = 50,
        since_dt: datetime | None = None,
    ) -> list[dict[str, Any]]:
        if since_dt is not None:
            sql = _ROUTING_HISTORY_SQL_SINCE
            rows = self._store.query(
                sql, (CHANNEL_ROUTED, since_dt.isoformat(), limit),
            )
        else:
            sql = _ROUTING_HISTORY_SQL
            rows = self._store.query(sql, (CHANNEL_ROUTED, limit))
        results: list[dict[str, Any]] = []
        for row in rows:
            payload = _parse_payload_json(row["payload_json"])
            results.append({
                "ts_utc": row["ts_utc"],
                "source_channel": payload.get("source_channel"),
                "target_channel": payload.get("target_channel"),
                "reason": payload.get("reason"),
                "source_survival_rate": payload.get("source_survival_rate"),
                "target_survival_rate": payload.get("target_survival_rate"),
                "dead_live_url": payload.get("dead_live_url"),
                "target_url": payload.get("target_url"),
            })
        return results

    def get_available_channels(
        self,
        *,
        min_survival_rate: float = 0.1,
        exclude_channels: set[str] | None = None,
    ) -> list[str]:
        exclude = exclude_channels or set()
        all_health = self.get_all_health()
        candidates: list[tuple[float, str]] = []
        for ch, health in all_health.items():
            if ch in exclude:
                continue
            if not health.has_data:
                continue
            if health.survival_rate is None:
                continue
            if health.survival_rate < min_survival_rate:
                continue
            candidates.append((health.survival_rate, ch))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [ch for _rate, ch in candidates]


_SURVIVAL_SQL_SINGLE: Final[str] = """
SELECT
    json_extract(payload_json, '$.platform') AS channel,
    COUNT(*) AS total,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'alive' THEN 1 ELSE 0 END) AS alive_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') IN ('host_gone', 'link_stripped') THEN 1 ELSE 0 END) AS dead_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'host_gone' THEN 1 ELSE 0 END) AS host_gone_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'link_stripped' THEN 1 ELSE 0 END) AS link_stripped_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'dofollow_lost' THEN 1 ELSE 0 END) AS dofollow_lost_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'probe_error' THEN 1 ELSE 0 END) AS probe_error_count,
    MAX(CASE WHEN json_extract(payload_json, '$.verdict') = 'alive' THEN ts_utc ELSE NULL END) AS last_alive_at,
    MAX(CASE WHEN json_extract(payload_json, '$.verdict') IN ('host_gone', 'link_stripped', 'dofollow_lost', 'probe_error') THEN ts_utc ELSE NULL END) AS last_dead_at
FROM events
WHERE kind = ?
  AND json_extract(payload_json, '$.platform') = ?
  AND ts_utc >= ?
GROUP BY channel
"""

_SURVIVAL_SQL_ALL: Final[str] = """
SELECT
    json_extract(payload_json, '$.platform') AS channel,
    COUNT(*) AS total,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'alive' THEN 1 ELSE 0 END) AS alive_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') IN ('host_gone', 'link_stripped') THEN 1 ELSE 0 END) AS dead_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'host_gone' THEN 1 ELSE 0 END) AS host_gone_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'link_stripped' THEN 1 ELSE 0 END) AS link_stripped_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'dofollow_lost' THEN 1 ELSE 0 END) AS dofollow_lost_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'probe_error' THEN 1 ELSE 0 END) AS probe_error_count,
    MAX(CASE WHEN json_extract(payload_json, '$.verdict') = 'alive' THEN ts_utc ELSE NULL END) AS last_alive_at,
    MAX(CASE WHEN json_extract(payload_json, '$.verdict') IN ('host_gone', 'link_stripped', 'dofollow_lost', 'probe_error') THEN ts_utc ELSE NULL END) AS last_dead_at
FROM events
WHERE kind = ?
  AND ts_utc >= ?
GROUP BY channel
"""

_ROUTING_HISTORY_SQL: Final[str] = """
SELECT ts_utc, payload_json
FROM events
WHERE kind = ?
ORDER BY ts_utc DESC
LIMIT ?
"""

_ROUTING_HISTORY_SQL_SINCE: Final[str] = """
SELECT ts_utc, payload_json
FROM events
WHERE kind = ?
  AND ts_utc >= ?
ORDER BY ts_utc DESC
LIMIT ?
"""


def _row_to_health(row: sqlite3.Row) -> ChannelHealth:
    return ChannelHealth(
        channel=str(row["channel"]),
        total_rechecks=int(row["total"]),
        alive_count=int(row["alive_count"]),
        dead_count=int(row["dead_count"]),
        host_gone_count=int(row["host_gone_count"]),
        link_stripped_count=int(row["link_stripped_count"]),
        dofollow_lost_count=int(row["dofollow_lost_count"]),
        probe_error_count=int(row["probe_error_count"]),
        last_alive_at=row["last_alive_at"],
        last_dead_at=row["last_dead_at"],
    )


# --- Write helpers -------------------------------------------------------


def write_recheck_observed(
    store: EventStore,
    *,
    verdict: str,
    platform: str,
    live_url: str,
    target_url: str,
    run_id: str | None = None,
) -> int:
    return store.append(
        CHANNEL_RECHECK_OBSERVED,
        payload={
            "verdict": verdict,
            "platform": platform,
            "live_url": live_url,
            "target_url": target_url,
        },
        target_url=target_url,
        host=platform,
        run_id=run_id,
    )


def write_routed_event(
    store: EventStore,
    *,
    source_channel: str,
    target_channel: str,
    reason: str,
    source_survival_rate: float | None,
    target_survival_rate: float | None,
    dead_live_url: str,
    target_url: str,
    run_id: str | None = None,
) -> int:
    payload: dict[str, Any] = {
        "source_channel": source_channel,
        "target_channel": target_channel,
        "reason": reason,
        "source_survival_rate": source_survival_rate,
        "target_survival_rate": target_survival_rate,
        "dead_live_url": dead_live_url,
        "target_url": target_url,
    }
    return store.append(
        CHANNEL_ROUTED,
        payload=payload,
        target_url=target_url,
        host=source_channel,
        run_id=run_id,
    )


def write_published_to_event(
    store: EventStore,
    *,
    platform: str,
    live_url: str,
    target_url: str,
    status: str,
    run_id: str | None = None,
) -> int:
    return store.append(
        CHANNEL_PUBLISHED_TO,
        payload={
            "platform": platform,
            "live_url": live_url,
            "target_url": target_url,
            "status": status,
        },
        target_url=target_url,
        host=platform,
        run_id=run_id,
    )
