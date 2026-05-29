"""/ce:health — publishing health dashboard (read-only).

Plan 2026-05-25-006 / U3. On load, runs the single-flight project-on-read
backstop (U1) so WebUI-sourced and crash-stranded outcomes are reflected, then
the read-only aggregations (U2), and renders them with honest empty / freshness
/ gap states. GET-only → the CSRF guard (mutating verbs only) does not apply.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone

from flask import Blueprint

from ..helpers.contexts import _render
from ..helpers._request_cache import _g_cache

bp = Blueprint("health", __name__)

_log = logging.getLogger(__name__)

# Last-resort body when even rendering the degraded dashboard fails (R5: a GET
# of /ce:health must never 500 — an honest "unavailable" beats a stack trace).
_FALLBACK_HTML = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
    "<title>Publishing Health</title></head><body>"
    "<main style=\"font-family:system-ui;max-width:40rem;margin:3rem auto;\">"
    "<h1>Publishing Health</h1>"
    "<p>The health dashboard is temporarily unavailable; data may be incomplete. "
    "Please retry shortly.</p><p><a href=\"/\">Home</a></p>"
    "</main></body></html>"
)


def _reconciliation_gaps():
    """Read-only count of reconciler gaps for the dashboard gap banner.

    Returns ``{"pending_checkpoints": int, "quarantine_gaps": int}`` on
    success. Returns ``{}`` on any read error so the dashboard never 500s.
    """
    try:
        from backlink_publisher.checkpoint import list_failed_items
        from backlink_publisher.events.store import EventStore

        pending = len(list_failed_items())
        rows = EventStore().query(
            "SELECT COUNT(*) FROM quarantine_log WHERE failure_type = ?",
            ("reconcile_gap",),
        )
        gaps = int(rows[0][0]) if rows else 0
        return {"pending_checkpoints": pending, "quarantine_gaps": gaps}
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        _log.warning("health: reconciliation gap check failed: %s", exc)
        return {}


def _decay_counts():
    """Read-only backlink decay counts for the dashboard banner (Plan
    2026-05-29-004 U6). Returns ``{host_gone, link_stripped, dofollow_lost,
    alive, probe_error}`` or ``{}`` on any read error so the page never 500s.
    """
    try:
        from ..health_metrics import decay_counts

        return decay_counts()
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        _log.warning("health: decay count read failed: %s", exc)
        return {}


@bp.route("/ce:health", methods=["GET"])
def ce_health():
    def _build():
        # U1 backstop first (single-flight, never raises) so the aggregates
        # below read freshened data; then U2 aggregations.
        from ..health_metrics import (
            DEFAULT_WINDOW_DAYS,
            Health,
            SuccessRate,
            _window_start,
            build_health,
        )
        from ..services.health_projection import project_on_read

        projection = project_on_read()
        try:
            health = build_health()
        except Exception as exc:  # noqa: BLE001 — R5: degrade, never 500 the page
            _log.warning("health: aggregation failed, rendering degraded: %s", exc)
            health = Health(
                window_days=DEFAULT_WINDOW_DAYS,
                since_utc=_window_start(
                    datetime.now(timezone.utc), DEFAULT_WINDOW_DAYS
                ),
                success=SuccessRate(),
            )
            projection = dataclasses.replace(
                projection,
                degraded=True,
                degraded_reason=projection.degraded_reason
                or f"{type(exc).__name__}: {exc}",
            )
        return projection, health

    def _canary_rows():
        """Read-side join of canary health (Plan 2026-05-27-001 Unit 4, R16).

        Reads ``canary_health_store.list_all()`` directly — NEVER writes canary
        state into ``channel_status_store`` (bind-scoped). Surfaces only
        non-sensitive fields (platform name, verdict, debounce counts,
        timestamps); no credentials/URLs. Fail-open: any read error → empty
        list so the dashboard never 500s on canary."""
        try:
            from backlink_publisher.canary.store import list_all

            rows = []
            for platform, rec in sorted((list_all() or {}).items()):
                rows.append({
                    "platform": platform,
                    "status": rec.get("status"),
                    "consecutive_failures": rec.get("consecutive_failures", 0),
                    "consecutive_oks": rec.get("consecutive_oks", 0),
                    "quarantined": bool(rec.get("quarantined", False)),
                    "last_ok_at": rec.get("last_ok_at"),
                    "last_drift_at": rec.get("last_drift_at"),
                })
            return rows
        except Exception as exc:  # noqa: BLE001 — never 500 the page on canary
            _log.warning("health: canary read failed: %s", exc)
            return []

    def _forward_path_rows():
        """Forward-path drift rows for the publish-path canary card.

        Reads ``list_publish_path_all()`` (Plan 2026-05-27-006 Unit 4) —
        the ``_publish_path`` sibling stream in ``canary-health.json``,
        disjoint from the evergreen ``_canary_rows()`` records.
        Advisory-only in v1: ``degraded`` flag shown but no gate.
        Fail-open: any read error → empty list."""
        try:
            from backlink_publisher.canary.store import list_publish_path_all

            rows = []
            for platform, rec in sorted((list_publish_path_all() or {}).items()):
                rows.append({
                    "platform": platform,
                    "status": rec.get("status"),
                    "consecutive_failures": rec.get("consecutive_failures", 0),
                    "consecutive_oks": rec.get("consecutive_oks", 0),
                    "degraded": bool(rec.get("degraded", False)),
                    "last_ok_at": rec.get("last_ok_at"),
                    "last_drift_at": rec.get("last_drift_at"),
                })
            return rows
        except Exception as exc:  # noqa: BLE001 — never 500 the page
            _log.warning("health: forward-path read failed: %s", exc)
            return []

    try:
        projection, health = _g_cache("health_agg", _build)
        canary = _g_cache("canary_health", _canary_rows)
        forward_path = _g_cache("forward_path_health", _forward_path_rows)
        reconciliation_gaps = _g_cache("reconciliation_gaps", _reconciliation_gaps)
        recheck_decay = _g_cache("recheck_decay", _decay_counts)
        return _render(
            "health.html",
            health=health,
            projection=projection,
            canary=canary,
            forward_path=forward_path,
            reconciliation_gaps=reconciliation_gaps,
            recheck_decay=recheck_decay,
        )
    except Exception as exc:  # noqa: BLE001 — R5: even a render/context error must not 500
        _log.error("health: dashboard render failed, serving minimal fallback: %s", exc)
        return _FALLBACK_HTML, 200
