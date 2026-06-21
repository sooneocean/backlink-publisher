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
        import json

        from backlink_publisher.checkpoint import list_failed_items
        from backlink_publisher.events.store import EventStore

        pending = len(list_failed_items())
        rows = EventStore().query(
            "SELECT raw_payload_json FROM quarantine_log",
        )
        gaps = 0
        for row in rows:
            try:
                payload = json.loads(row[0] or "{}")
            except (TypeError, ValueError):
                continue
            if payload.get("failure_type") == "reconcile_gap":
                gaps += 1
        return {"pending_checkpoints": pending, "quarantine_gaps": gaps}
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        _log.warning("health: reconciliation gap check failed: %s", exc)
        return {}


def _geo_panel() -> dict:
    """Read-only GEO citation-share panel data (Plan 2026-05-29-006 U9).

    Returns ``{"targets": [<per-target dicts>]}`` on success, or ``{}`` on any
    read error so the health dashboard never 500s (fail-open contract — R5).
    Per-target dicts carry honest state labels matching
    :class:`~backlink_publisher.geo.share.TargetShare` — never a misleading 0%.
    Advisory only; nothing here gates publishing.
    """
    try:
        from backlink_publisher.events import EventStore

        from ..health_metrics import geo_citation_share

        rows = geo_citation_share(EventStore())
        return {"targets": rows} if rows else {}
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        _log.warning("health: geo citation-share read failed: %s", exc)
        return {}


def _decay_counts(exclude_resolved: bool = True):
    """Read-only backlink decay counts for the dashboard banner (Plan
    2026-05-29-004 U6). Returns ``{host_gone, link_stripped, dofollow_lost,
    alive, probe_error}`` or ``{}`` on any read error so the page never 500s.

    When ``exclude_resolved=True`` (default), links that have been marked
    ``resolve`` in the remediation queue are excluded — only **unresolved**
    decay is shown in the banner. Pass ``?show_all=1`` to see total decay.
    """
    try:
        from ..health_metrics import decay_counts

        return decay_counts(exclude_resolved=exclude_resolved)
    except Exception as exc:  # noqa: BLE001 — never 500 the page
        _log.warning("health: decay count read failed: %s", exc)
        return {}


def _total_decay_counts():
    """Wrapper that always returns total (unfiltered) decay counts."""
    return _decay_counts(exclude_resolved=False)


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

    def _channel_health_card():
        """Channel health overview card (Plan 2026-06-08-001 U4).
        Fail-open: any read error -> empty card so the dashboard never 500s."""
        try:
            from backlink_publisher.events import EventStore
            from ..health_metrics import build_channel_health_card

            return build_channel_health_card(EventStore())
        except Exception:  # noqa: BLE001 — never 500 the page
            _log.warning("health: channel health card read failed", exc_info=True)
            from ..health_metrics import ChannelHealthCard
            return ChannelHealthCard()

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

    def _remediation_rows():
        """Unresolved backlinks list for the remediation card.
        Fail-open: any read error → empty list so the dashboard never 500s."""
        try:
            from backlink_publisher.remediation.actions import list_unresolved
            from backlink_publisher.events import EventStore

            unresolved = list_unresolved(EventStore())
            return unresolved
        except Exception as exc:  # noqa: BLE001 — never 500 the page
            _log.warning("health: remediation rows failed: %s", exc)
            return []

    def _scorecard_rows():
        """Per-channel value scorecard card (Plan 2026-06-01-005, Unit 8 MVP).

        Reads the same stores the equity-ledger reads, re-keyed by channel —
        declared registry signals (dofollow / referral_value) beside measured
        liveness, as a signal vector (no composite). The GA4 referral / GSC
        discovery / AI-retrievability axes render as ``inert:not-landed``
        (Wave-0 DESCOPE). Read-only, advisory — never gates publishing.
        Fail-open: any read error → empty list so the dashboard never 500s."""
        try:
            from backlink_publisher.scorecard import build_channel_scorecard

            return [r.to_jsonl_dict() for r in build_channel_scorecard()]
        except Exception as exc:  # noqa: BLE001 — never 500 the page on scorecard
            _log.warning("health: channel scorecard read failed: %s", exc)
            return []

    def _zero_auth_rows():
        """Zero-auth backlink outcome card (Wave 4 of the zero-auth MVP).

        Reads publish history and groups latest backlink outcomes for every
        zero-auth platform.  Fail-open: any read error → empty list."""
        try:
            from backlink_publisher.publishing.registry import (
                dofollow_status,
                referral_value,
                zero_auth_backlink_platforms,
            )
            from webui_app.binding_status import _get_latest_backlink_outcome_details
            from webui_store import history_store

            # Force history load to happen inside the try/except so a corrupt
            # history file does not 500 the dashboard.
            history_store.load()

            rows = []
            for name in sorted(zero_auth_backlink_platforms() or []):
                details = _get_latest_backlink_outcome_details(name)
                rows.append({
                    "platform": name,
                    "outcome": details.get("backlink_outcome") or "no_data",
                    "reason": details.get("backlink_outcome_reason"),
                    "dofollow": dofollow_status(name),
                    "referral_value": referral_value(name),
                    "ttl_days": details.get("brewpage_ttl_days"),
                    "expires_at": (
                        details.get("brewpage_expires_at")
                        or details.get("posteasy_expires_at")
                        or details.get("expires_at")
                    ),
                })
            return rows
        except Exception as exc:  # noqa: BLE001 — never 500 the page
            _log.warning("health: zero-auth rows failed: %s", exc)
            return []

    try:
        projection, health = _g_cache("health_agg", _build)
        channel_health = _g_cache("channel_health_card", _channel_health_card)
        canary = _g_cache("canary_health", _canary_rows)
        forward_path = _g_cache("forward_path_health", _forward_path_rows)
        reconciliation_gaps = _g_cache("reconciliation_gaps", _reconciliation_gaps)
        recheck_decay = _g_cache("recheck_decay", _decay_counts)
        remediation_rows = _g_cache("remediation_rows", _remediation_rows)
        channel_scorecard = _g_cache("channel_scorecard", _scorecard_rows)
        geo_panel = _g_cache("geo_panel", _geo_panel)
        zero_auth = _g_cache("zero_auth_health", _zero_auth_rows)
        return _render(
            "health.html",
            health=health,
            projection=projection,
            channel_health=channel_health,
            canary=canary,
            forward_path=forward_path,
            reconciliation_gaps=reconciliation_gaps,
            recheck_decay=recheck_decay,
            remediation_rows=remediation_rows,
            channel_scorecard=channel_scorecard,
            geo_panel=geo_panel,
            zero_auth=zero_auth,
        )
    except Exception as exc:  # noqa: BLE001 — R5: even a render/context error must not 500
        _log.error("health: dashboard render failed, serving minimal fallback: %s", exc)
        return _FALLBACK_HTML, 200
