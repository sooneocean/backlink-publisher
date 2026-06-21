"""Action taxonomy for remediation events: ack / resolve / snooze.

Unresolved view derivation — given a ``remediation.event`` time series, the
*latest* action per ``live_url`` determines its resolved/unresolved state:

- ``resolve`` → resolved
- ``ack`` → unresolved (operator has seen it, not yet fixed)
- ``snooze`` with unexpired ``snooze_until_utc`` → unresolved but suppressed
- ``snooze`` with expired ``snooze_until_utc`` → unresolved (re-surfaced)
- no record → unresolved (never acted upon)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from backlink_publisher.events.store import EventStore


ACK: Final[str] = "ack"
RESOLVE: Final[str] = "resolve"
SNOOZE: Final[str] = "snooze"

_ACTIONS: Final[frozenset[str]] = frozenset({ACK, RESOLVE, SNOOZE})


def validate_action(action: str) -> bool:
    """Return True if ``action`` is a known remediation action."""
    return action in _ACTIONS


def is_unresolved(
    store: "EventStore",
    live_url: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Return True if the ``live_url`` is currently unresolved.

    An unresolved link is one whose latest remediation event is **not** a
    ``resolve``, and not an unexpired ``snooze``. A link with **no**
    remediation record at all is also unresolved.
    """
    rows = store.query(
        "SELECT payload_json FROM events "
        "WHERE kind = 'remediation.event' AND payload_json LIKE ? "
        "ORDER BY id DESC LIMIT 1",
        (f"%{live_url}%",),
    )
    if not rows:
        return True
    try:
        import json

        payload = json.loads(rows[0]["payload_json"] or "{}")
    except (ValueError, TypeError, IndexError):
        return True
    action = payload.get("action")
    if action == RESOLVE:
        return False
    if action == SNOOZE:
        snooze_until = payload.get("snooze_until_utc")
        if snooze_until:
            try:
                if isinstance(snooze_until, str):
                    snooze_dt = datetime.fromisoformat(snooze_until)
                else:
                    snooze_dt = datetime.fromisoformat(str(snooze_until))
                if snooze_dt > (now or datetime.now(timezone.utc)):
                    return False  # snooze still active
            except (ValueError, TypeError):
                pass
        # If snooze_until is missing, expired, or unparseable → unresolved
        return True
    # ack or unknown action → unresolved
    return True


def _parse_payload_json(raw: str | None) -> dict:
    """Safely parse a payload_json column value."""
    if not raw:
        return {}
    try:
        import json
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def list_unresolved(store: "EventStore") -> list[dict]:
    """Return all currently unresolved live_urls with their metadata.

    Each dict contains ``live_url``, ``latest_action``, ``acked_at``,
    ``snoozed_until``, and any note. Ordered by most recent remediation
    event first.
    """
    # Get all remediation events, GROUP BY live_url for latest action
    rows = store.query(
        "SELECT id, payload_json, ts_utc FROM events "
        "WHERE kind = 'remediation.event' AND payload_json IS NOT NULL "
        "ORDER BY id DESC",
    )
    # Build latest-per-live_url map
    latest: dict[str, dict] = {}
    for row in rows:
        payload = _parse_payload_json(row["payload_json"])
        live_url = payload.get("live_url")
        if not live_url:
            continue
        if live_url in latest:
            continue  # already have newer row (DESC order)
        latest[live_url] = {
            "live_url": live_url,
            "latest_action": payload.get("action"),
            "acked_at": (
                row["ts_utc"] if payload.get("action") == ACK else None
            ),
            "snoozed_until": payload.get("snooze_until_utc"),
            "note": payload.get("note"),
        }
    # Filter to unresolved only
    unresolved: list[dict] = []
    now = datetime.now(timezone.utc)
    for live_url, info in latest.items():
        if info["latest_action"] == RESOLVE:
            continue
        if info["latest_action"] == SNOOZE and info["snoozed_until"]:
            try:
                snooze_dt = datetime.fromisoformat(str(info["snoozed_until"]))
                if snooze_dt > now:
                    continue  # snooze still active
            except (ValueError, TypeError):
                pass
        unresolved.append(info)
    return unresolved