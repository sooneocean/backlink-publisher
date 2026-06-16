"""/schedule — Plan 2026-05-29-001 Unit 2."""

from __future__ import annotations

import json
from typing import Any

from flask import Blueprint, jsonify

from ..api.scheduled_api import list_scheduled
from ..helpers.contexts import _render

bp = Blueprint("schedule", __name__)


@bp.get("/schedule")
def schedule_list() -> Any:
    """Return CSRF-time-safe page_data bootstrap with scheduled drafts."""
    scheduled = list_scheduled()
    return _render(
        "schedule.html",
        scheduled_items=scheduled.get("items", []) if scheduled.get("ok") else [],
    )


@bp.get("/api/scheduled")
def api_scheduled() -> Any:
    """Return scheduled drafts as JSON for the schedule page ESM."""
    data = list_scheduled()
    resp = jsonify(data)
    resp.headers["Content-Type"] = "application/json; charset=utf-8"
    return resp
