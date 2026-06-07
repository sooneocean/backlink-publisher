"""Emit and query ``remediation.event`` rows in events.db.

Write side (``emit_event``) is WAL-safe: appends share ONE transaction and
quarantined floor-misses flush only AFTER the transaction commits — avoiding
the WAL deadlock documented in the projector silent-drop lesson.

Read side feeds the ``actions`` module's unresolved derivation.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from backlink_publisher.events._project_helpers import write_quarantines
from backlink_publisher.events.kinds import REMEDIATION_EVENT
from backlink_publisher.remediation.actions import validate_action

if TYPE_CHECKING:
    from backlink_publisher.events.store import EventStore

log = logging.getLogger(__name__)


def emit_event(
    store: "EventStore",
    live_url: str,
    action: str,
    *,
    snooze_until_utc: str | None = None,
    note: str | None = None,
    host: str | None = None,
    target_url: str | None = None,
) -> int:
    """Emit one ``remediation.event`` for the given ``live_url`` and ``action``.

    Returns the event id on success, or -1 if the event was quarantined (floor
    miss). Never raises.

    Args:
        store: The EventStore instance.
        live_url: The URL of the backlink being acted upon.
        action: One of ``ack``, ``resolve``, ``snooze``.
        snooze_until_utc: ISO-8601 timestamp for snooze expiry (required when
            action is ``snooze``).
        note: Optional operator note.
        host: Optional host of the live_url (stored as events.db first-class
            column for filtering).
        target_url: Optional target URL (stored as events.db first-class
            column).
    """
    if not validate_action(action):
        log.warning("remediation: invalid action %r — skipping", action)
        return -1

    payload: dict[str, object] = {
        "action": action,
        "live_url": live_url,
    }
    if snooze_until_utc is not None:
        payload["snooze_until_utc"] = snooze_until_utc
    if note is not None:
        payload["note"] = note

    pending_quarantines: list[dict] = []
    event_id = -1
    with store.connect() as conn:
        event_id = store.append(
            REMEDIATION_EVENT,
            payload,
            host=host,
            target_url=target_url,
            conn=conn,
            pending_quarantines=pending_quarantines,
        )
    # Flush quarantines AFTER the transaction commits (WAL-deadlock avoidance).
    write_quarantines(store, pending_quarantines)
    return event_id


def resolved_live_urls(store: "EventStore") -> set[str]:
    """Return the set of all ``live_url`` values whose latest remediation
    action is ``resolve``.

    Used by ``derive_decay_counts`` to filter out resolved links.
    """
    resolved: set[str] = set()
    # Query latest action per live_url from remediation events
    rows = store.query(
        "SELECT id, payload_json FROM events "
        "WHERE kind = 'remediation.event' AND payload_json IS NOT NULL "
        "ORDER BY id DESC",
    )
    seen: set[str] = set()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        live_url = payload.get("live_url")
        if not live_url or live_url in seen:
            continue
        seen.add(live_url)
        if payload.get("action") == "resolve":
            resolved.add(live_url)
    return resolved