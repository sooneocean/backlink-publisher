"""PR opportunity queue route — read-only advisory view.

GET  /pr-queue          HTML queue listing
GET  /api/pr-queue      JSON queue data
POST /api/pr-queue/status  Update a single opportunity's status (CSRF-guarded)
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, render_template, request

bp = Blueprint("pr_queue", __name__)

_STATUS_COLORS = {
    "pending": "yellow",
    "draft": "blue",
    "sent": "purple",
    "won": "green",
    "lost": "red",
    "skipped": "gray",
}


def _load() -> list[dict[str, Any]]:
    try:
        from backlink_publisher.pr_outreach.store import load_opportunities

        rows = load_opportunities()
        rows.sort(key=lambda r: r.get("relevance_score") or 0, reverse=True)
        return rows
    except Exception:
        return []


@bp.get("/pr-queue")
def pr_queue_page() -> Any:
    items = _load()
    for item in items:
        item["_status_color"] = _STATUS_COLORS.get(item.get("status", "pending"), "gray")
    return render_template("pr_queue.html", items=items)


@bp.get("/api/pr-queue")
def api_pr_queue() -> Any:
    return jsonify({"ok": True, "items": _load()})


@bp.post("/api/pr-queue/status")
def api_update_status() -> Any:
    data = request.get_json(silent=True) or {}
    opp_id = data.get("id")
    status = data.get("status")
    draft = data.get("draft")

    if not opp_id or not status:
        return jsonify({"ok": False, "error": "id and status required"}), 400

    try:
        from backlink_publisher.pr_outreach.store import STATUS_ENUM, update_status

        if status not in STATUS_ENUM:
            return jsonify({"ok": False, "error": f"invalid status {status!r}"}), 400

        saved = update_status(opp_id, status, draft=draft)
        return jsonify({"ok": True, "item": saved})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
