"""GET /metrics — Prometheus-compatible metrics endpoint (E2).

Exposes publish counts, recheck counts, content-fetch cache stats, and
events.db size in OpenMetrics text format. Advisory only — errors return
a partial scrape rather than aborting, so Prometheus never marks the target
as down due to a single counter query failure.

Access: loopback-only WebUI (same threat model as all other routes).
No authentication beyond CSRF is needed for a GET endpoint that only reads
aggregate counters.
"""

from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, Response, current_app

bp = Blueprint("metrics", __name__)

_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def _gauge(name: str, value: float | int, labels: dict[str, str] | None = None) -> str:
    lstr = ""
    if labels:
        pairs = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lstr = f"{{{pairs}}}"
    return f"{name}{lstr} {value}"


def _scrape_events_db() -> list[str]:
    """Query events.db for publish / recheck counts."""
    lines: list[str] = []
    try:
        from backlink_publisher.config.loader import _config_dir
        db_path = _config_dir() / "events.db"
        if not db_path.exists():
            return lines

        import sqlite3
        con = sqlite3.connect(str(db_path), timeout=2)
        cur = con.cursor()

        # publish counts by platform
        cur.execute(
            "SELECT json_extract(data, '$.platform') as platform, COUNT(*) "
            "FROM events WHERE kind='article.published' GROUP BY platform"
        )
        for platform, count in cur.fetchall():
            plat = platform or "unknown"
            lines.append(_gauge("bp_publish_total", count, {"platform": plat, "status": "published"}))

        # recheck verdict counts
        cur.execute(
            "SELECT json_extract(data, '$.verdict') as verdict, COUNT(*) "
            "FROM events WHERE kind='link.rechecked' GROUP BY verdict"
        )
        for verdict, count in cur.fetchall():
            v = verdict or "unknown"
            lines.append(_gauge("bp_recheck_total", count, {"verdict": v}))

        # events.db file size
        lines.append(_gauge("bp_eventsdb_bytes", db_path.stat().st_size))

        con.close()
    except Exception:  # noqa: BLE001
        pass
    return lines


def _scrape_content_cache() -> list[str]:
    """Read in-process content-fetch cache stats."""
    lines: list[str] = []
    try:
        from backlink_publisher.content.fetch import stats_snapshot
        snap = stats_snapshot()
        lines.append(_gauge("bp_content_fetch_cache_hits_total", snap.get("cache_hits", 0)))
        lines.append(_gauge("bp_content_fetch_cache_misses_total", snap.get("cache_misses", 0)))
        lines.append(_gauge("bp_content_fetch_fetches_total", snap.get("fetches", 0)))
        latency = snap.get("total_latency_ms", 0)
        fetches = snap.get("fetches", 1) or 1
        lines.append(_gauge("bp_content_fetch_avg_latency_ms", round(latency / fetches, 1)))
    except Exception:  # noqa: BLE001
        pass
    return lines


def _scrape_publish_history() -> list[str]:
    """Count entries in publish-history.json."""
    lines: list[str] = []
    try:
        import json
        from backlink_publisher.config.loader import _config_dir
        hist = _config_dir() / "publish-history.json"
        if hist.exists():
            data = json.loads(hist.read_text())
            if isinstance(data, list):
                lines.append(_gauge("bp_publish_history_entries_total", len(data)))
    except Exception:  # noqa: BLE001
        pass
    return lines


@bp.route("/metrics")
def metrics() -> Response:
    """Prometheus-compatible metrics scrape endpoint."""
    ts = int(time.time() * 1000)
    lines: list[str] = [
        "# HELP bp_publish_total Total published articles by platform",
        "# TYPE bp_publish_total counter",
    ]
    lines += _scrape_events_db()

    lines += [
        "# HELP bp_recheck_total Recheck verdicts by outcome",
        "# TYPE bp_recheck_total counter",
    ]
    # (already populated by _scrape_events_db above — labels include verdict)

    lines += [
        "# HELP bp_publish_history_entries_total Total entries in publish-history.json",
        "# TYPE bp_publish_history_entries_total gauge",
    ]
    lines += _scrape_publish_history()

    lines += [
        "# HELP bp_content_fetch_cache_hits_total Content-fetch in-process cache hits",
        "# TYPE bp_content_fetch_cache_hits_total counter",
        "# HELP bp_content_fetch_fetches_total Real network fetches performed",
        "# TYPE bp_content_fetch_fetches_total counter",
    ]
    lines += _scrape_content_cache()

    lines.append(f"# scrape_timestamp_ms {ts}")

    body = "\n".join(lines) + "\n"
    return Response(body, mimetype=_CONTENT_TYPE)
