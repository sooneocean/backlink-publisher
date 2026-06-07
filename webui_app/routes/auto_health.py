"""/auto-health — automation metrics dashboard (Plan 2026-06-07 R5).

Read-only observability for all automation metrics. GET-only → no CSRF.
Exits never 500 (R5 fail-open contract).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from flask import Blueprint

from ..helpers.contexts import _render
from ..helpers._request_cache import _g_cache

bp = Blueprint("auto_health", __name__)

_log = logging.getLogger(__name__)

# Last-resort fallback to prevent 500s on any read failure
_FALLBACK_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Automation Health</title></head>"
    "<body><main style=\"font-family:system-ui;max-width:40rem;margin:3rem auto;\">"
    "<h1>Automation Health</h1>"
    "<p>The automation dashboard is temporarily unavailable; data may be incomplete. "
    "Please retry shortly.</p><p><a href=\"/\">Home</a></p>"
    "</main></body></html>"
)


def _pipeline_throughput() -> dict[str, Any]:
    """Read-only pipeline throughput metrics.

    Returns publishes/day, success rate, error distribution.
    Returns empty dict on error (fail-open).
    """
    try:
        from webui_store import history_store

        history = history_store.load() or []
        total = len(history)
        successful = sum(1 for h in history if h.get("status") in ("published", "published_unverified"))
        failed = total - successful

        # Group by day
        days: dict[str, int] = {}
        for h in history:
            ts = h.get("created_at", "")
            if ts:
                day = ts.split()[0] if " " in ts else ts[:10]
                days[day] = days.get(day, 0) + 1

        return {
            "total": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(successful / total * 100, 1) if total else 0,
            "by_day": days,
        }
    except Exception as exc:
        _log.warning("auto_health: pipeline throughput read failed: %s", exc)
        return {}


def _recovery_queue() -> dict[str, Any]:
    """Read-only recovery queue state.

    Returns pending rebinds, pending republishes, completed recoveries.
    Returns empty dict on error (fail-open).
    """
    try:
        from webui_store import queue_store

        tasks = queue_store.load() or []

        pending_rebinds = [
            t for t in tasks
            if t.get("type") == "rebind" and t.get("status") in ("pending", "processing")
        ]
        pending_republishes = [
            t for t in tasks
            if t.get("type") == "republish" and t.get("status") in ("pending", "processing")
        ]
        completed = [
            t for t in tasks
            if t.get("status") in ("success", "resolved")
        ]

        return {
            "pending_rebinds": len(pending_rebinds),
            "pending_republishes": len(pending_republishes),
            "completed": len(completed),
            "rebind_tasks": pending_rebinds[:10],  # Latest 10 for display
        }
    except Exception as exc:
        _log.warning("auto_health: recovery queue read failed: %s", exc)
        return {}


def _resource_budget() -> dict[str, Any]:
    """Read-only resource budget metrics.

    Returns throttle usage, API quota, batch budgets.
    Returns empty dict on error (fail-open).
    """
    try:
        # Read throttle config
        from backlink_publisher.config.loader import _config_dir
        import json

        throttle_path = _config_dir() / "publish-throttle.json"
        throttle = {}
        if throttle_path.exists():
            try:
                throttle = json.loads(throttle_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        return {
            "throttle_min": throttle.get("min_seconds", 60),
            "throttle_max": throttle.get("max_seconds", 300),
            "per_run_cap": throttle.get("per_run_cap", 50),
        }
    except Exception as exc:
        _log.warning("auto_health: resource budget read failed: %s", exc)
        return {}


def _canary_health() -> list[dict[str, Any]]:
    """Read-only canary health rows for automation context."""
    try:
        from backlink_publisher.canary.store import list_all

        return [
            {
                "platform": p,
                "status": r.get("status"),
                "consecutive_failures": r.get("consecutive_failures", 0),
                "consecutive_oks": r.get("consecutive_oks", 0),
                "quarantined": r.get("quarantined", False),
                "last_drift_at": r.get("last_drift_at"),
            }
            for p, r in sorted((list_all() or {}).items())
        ]
    except Exception as exc:
        _log.warning("auto_health: canary read failed: %s", exc)
        return []


def _recent_alerts() -> list[dict[str, Any]]:
    """Read recent automation alerts."""
    try:
        from backlink_publisher.events import EventStore
        from backlink_publisher.events.kinds import PUBLISH_QUALITY_BLOCKED

        store = EventStore()
        rows = store.query(
            "SELECT ts_utc, target_url, host, payload_json FROM events "
            "WHERE kind = ? ORDER BY ts_utc DESC LIMIT 20",
            (PUBLISH_QUALITY_BLOCKED,),
        )
        alerts = []
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except (TypeError, ValueError):
                continue
            alerts.append({
                "alert_type": "quality blocked",
                "platform": row["host"] or payload.get("platform") or "unknown",
                "ts_utc": row["ts_utc"],
                "reason": payload.get("quality_check") or "unknown_quality_check",
                "action_taken": "review draft before publish",
                "draft_label": payload.get("draft_label") or "unknown_draft",
                "target_url": row["target_url"],
            })
        return alerts
    except Exception as exc:
        _log.warning("auto_health: recent alerts read failed: %s", exc)
        return []


@bp.route("/auto-health", methods=["GET"])
def auto_health():
    """Render the automation health dashboard."""
    try:
        throughput = _g_cache("auto_health_throughput", _pipeline_throughput)
        recovery = _g_cache("auto_health_recovery", _recovery_queue)
        resources = _g_cache("auto_health_resources", _resource_budget)
        canary = _g_cache("auto_health_canary", _canary_health)
        alerts = _g_cache("auto_health_alerts", _recent_alerts)

        return _render(
            "auto_health.html",
            throughput=throughput,
            recovery=recovery,
            resources=resources,
            canary=canary,
            alerts=alerts,
        )
    except Exception as exc:
        _log.error("auto_health: dashboard render failed: %s", exc)
        return _FALLBACK_HTML, 200
