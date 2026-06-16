"""Emit ``link.rechecked`` events and derive decay counts from the time series.

Write side (``emit_recheck``) is WAL-safe: all appends share ONE transaction and
quarantined floor-misses flush only AFTER the transaction commits — writing a
quarantine row while holding the WAL write lock deadlocks (the projector
silent-drop lesson, docs/solutions/logic-errors/projector-silent-drop-...).

Read side (``derive_decay_counts``) reports the *latest* verdict per link with
NO age window: a link that went ``host_gone`` 40 days ago and was never
re-probed must still count as decayed — windowing it out would make abandonment
look like recovery. (``suspected_dead`` derivation is deferred to a fast-follow,
Plan 2026-05-29-004 D5.)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from backlink_publisher.events._project_helpers import write_quarantines
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import indexability, verdicts
from backlink_publisher.recheck.selection import _parse_ts

if TYPE_CHECKING:
    from backlink_publisher.events.store import EventStore

log = logging.getLogger(__name__)


def emit_recheck(store: "EventStore", results: list[dict]) -> int:
    """Append one ``link.rechecked`` event per probed result. Returns the number
    of events written (floor-misses are quarantined, not counted).

    ``results`` are :func:`recheck.probe.recheck_link` outputs; dry-preview rows
    (no ``verdict``) are skipped.
    """
    pending_quarantines: list[dict] = []
    written = 0
    with store.connect() as conn:
        for r in results:
            verdict = r.get("verdict")
            if verdict is None:
                continue
            payload = {
                "verdict": verdict,
                "reason": r.get("reason"),
                "live_url": r.get("live_url"),
                "platform": r.get("platform"),
                "expected_nofollow": bool(r.get("expected_nofollow")),
                "anchor_drift": bool(r.get("anchor_drift")),
                "anchor_baseline_missing": bool(r.get("anchor_baseline_missing")),
                # Orthogonal indexability axis (additive — NOT in the floor).
                # Fail-open to UNKNOWN if absent so a reader never mistakes an
                # unclassified page for indexable. ``indexability_reason`` is
                # clamped to the closed vocab AT THIS SEAM (never raw bytes).
                "indexability": r.get("indexability") or indexability.UNKNOWN,
                "indexability_reason": (
                    r.get("indexability_reason")
                    if r.get("indexability_reason") in indexability.REASON_VOCAB
                    else None
                ),
                "source": r.get("source", "events"),
            }
            event_id = store.append(
                LINK_RECHECKED,
                payload,
                target_url=r.get("target_url"),
                host=r.get("host"),
                article_id=r.get("article_id"),
                conn=conn,
                pending_quarantines=pending_quarantines,
            )
            if event_id != -1:
                written += 1
    # Flush quarantines AFTER the transaction commits (WAL-deadlock avoidance).
    write_quarantines(store, pending_quarantines)
    return written


def derive_decay_counts(store: "EventStore") -> dict[str, int]:
    """Count links by their latest ``link.rechecked`` verdict (current state).

    Returns a count for every verdict in :data:`verdicts.VERDICTS` (0 when
    absent). The dashboard banner (U6) keys off ``host_gone`` / ``link_stripped``
    / ``dofollow_lost``; ``alive`` / ``probe_error`` are returned for context.
    """
    counts = {v: 0 for v in verdicts.VERDICTS}
    latest: dict[int, tuple[datetime | None, str]] = {}  # article_id -> (ts, verdict)
    sql = (
        "SELECT article_id, payload_json, ts_utc FROM events "
        "WHERE kind = ? AND article_id IS NOT NULL"
    )
    for row in store.query(sql, (LINK_RECHECKED,)):
        try:
            verdict = json.loads(row["payload_json"] or "{}").get("verdict")
        except (ValueError, TypeError):
            continue
        if verdict not in verdicts.VERDICTS:
            continue
        ts = _parse_ts(row["ts_utc"])
        aid = row["article_id"]
        prev = latest.get(aid)
        if prev is None or (ts is not None and (prev[0] is None or ts > prev[0])):
            latest[aid] = (ts, verdict)
    for _ts, verdict in latest.values():
        counts[verdict] += 1
    return counts
