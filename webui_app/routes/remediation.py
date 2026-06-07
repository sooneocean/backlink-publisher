"""Remediation queue WebUI routes — Plan 2026-06-07-001 Phase A Unit 4.

``POST /ce:health/remediation`` accepts ack / resolve / snooze actions on
unresolved backlinks, protected by the global CSRF guard. Returns JSON.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from backlink_publisher.events import EventStore
from backlink_publisher.remediation.events_io import emit_event

bp = Blueprint("remediation", __name__)

_log = logging.getLogger(__name__)

#: Maximum note length to prevent abuse.
_MAX_NOTE_LENGTH = 500


@bp.route("/ce:health/remediation", methods=["POST"])
def remediation_action():
    """Handle ack / resolve / snooze actions from the health dashboard.

    Request body (JSON):
        action: "ack" | "resolve" | "snooze" (required)
        live_url: str (required)
        days: int (optional, default 7, used with snooze)
        note: str (optional)

    Returns JSON ``{"ok": true}`` on success, or ``{"ok": false, error": "..."}``
    on failure (never 500s).
    """
    try:
        data = request.get_json(silent=True) or {}
    except Exception:  # noqa: BLE001
        data = {}

    action = data.get("action")
    live_url = data.get("live_url")
    if not action or not live_url:
        return jsonify({"ok": False, "error": "Missing action or live_url"}), 400

    from backlink_publisher.remediation.actions import validate_action

    if not validate_action(action):
        return jsonify({"ok": False, "error": f"Invalid action: {action}"}), 400

    if not live_url.startswith("http://") and not live_url.startswith("https://"):
        return jsonify({"ok": False, "error": "live_url must be http(s) scheme"}), 400

    note = str(data.get("note", ""))[: _MAX_NOTE_LENGTH] or None

    snooze_until_utc = None
    if action == "snooze":
        days = int(data.get("days", 7))
        if days < 1 or days > 365:
            return jsonify({"ok": False, "error": "days must be 1-365"}), 400
        from datetime import datetime, timedelta, timezone

        snooze_until_utc = (
            datetime.now(timezone.utc) + timedelta(days=days)
        ).isoformat()

    try:
        store = EventStore()
        event_id = emit_event(
            store,
            live_url,
            action,
            snooze_until_utc=snooze_until_utc,
            note=note,
        )
        if event_id == -1:
            _log.warning("remediation: emit_event returned -1 for %s on %s", action, live_url)
            return jsonify({"ok": False, "error": "Event was quarantined"}), 500
    except Exception as exc:  # noqa: BLE001 — never 500
        _log.warning("remediation: emit_event failed: %s", exc)
        return jsonify({"ok": False, "error": f"Write failed: {exc}"}), 500

    return jsonify({"ok": True})