"""Age-based candidate selection for the recheck-backlinks CLI.

Selects which previously-published backlinks to re-probe this run, sourced from
the append-only ``publish.confirmed`` event stream — NOT the mutable ``articles``
projection. This keeps the recheck CLI decoupled from the plan-007
history_store->events.db migration, which re-plumbs the projection's write path
but leaves the ``publish.confirmed`` events stable (Plan 2026-05-29-004 D1/A1).

Each ``publish.confirmed`` event carries ``live_url``/``platform`` in its payload
and ``target_url``/``host``/``article_id``/``ts_utc`` as first-class columns
(see ``_project_reducers``). ``article_id`` is 1:1 with ``live_url`` (the
``articles.live_url`` UNIQUE constraint), so it is the natural per-placement
cursor key — a target published to N platforms has N article_ids and is probed
independently (resolves the live_url-vs-target_url granularity gap, F1).

Age cursor (D3): derived from the ``link.rechecked`` time series, NOT from any
mutable column.

* ``last_definitive_at`` — max ts of events whose verdict is *definitive*
  (everything except ``probe_error``). Drives the N-day re-selection threshold.
  Excluding ``probe_error`` keeps a persistently-unreachable link selectable.
* ``last_attempt_at`` — max ts of *all* recheck events. Drives the min-retry
  floor so a probe_error-only link is not hammered every run.

Coverage math: with corpus C placements, threshold N days, per-run cap M, and a
cron period of P days, steady state needs ~C placements re-probed every N days
while each N-day window can probe ``M * (N / P)``. Keep ``M * (N / P) >= C`` or
the oldest links starve. Defaults N=14 / M=50 / P=1 (daily) cover ~700
placements per 14-day window; for a larger corpus, raise M or shorten the cron
period rather than externalizing N/M (D8).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from backlink_publisher.events.kinds import LINK_RECHECKED, PUBLISH_CONFIRMED
from backlink_publisher.recheck import verdicts

if TYPE_CHECKING:
    from backlink_publisher.events.store import EventStore

log = logging.getLogger(__name__)

DEFAULT_DAYS = 14
DEFAULT_CAP = 50
DEFAULT_MIN_RETRY_DAYS = 1

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _parse_ts(value: object) -> datetime | None:
    """Parse an ISO ts_utc string to an aware UTC datetime; None-safe."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_http(url: object) -> bool:
    if not isinstance(url, str) or not url:
        return False
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def _host_of(url: str) -> str | None:
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


def _confirmed_universe(
    store: "EventStore",
    *,
    since: datetime | None,
    host: str | None,
    run_id: str | None,
) -> dict[int, dict]:
    """Latest ``publish.confirmed`` per article_id (≡ per live_url)."""
    sql = (
        "SELECT article_id, payload_json, target_url, host, ts_utc "
        "FROM events WHERE kind = ? AND article_id IS NOT NULL"
    )
    params: list[object] = [PUBLISH_CONFIRMED]
    if host:
        sql += " AND host = ?"
        params.append(host)
    if run_id:
        sql += " AND run_id = ?"
        params.append(run_id)
    sql += " ORDER BY ts_utc"  # ascending → last write per article_id wins

    universe: dict[int, dict] = {}
    for row in store.query(sql, tuple(params)):
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        live_url = payload.get("live_url")
        if not live_url:
            continue  # NULL-url confirms can't be re-probed
        published_at = _parse_ts(row["ts_utc"])
        if since is not None and published_at is not None and published_at < since:
            continue
        universe[row["article_id"]] = {
            "live_url": live_url,
            "target_url": row["target_url"],
            "host": row["host"],
            "platform": payload.get("platform"),
            "_published_at": published_at,
        }
    return universe


def _recheck_cursors(
    store: "EventStore",
) -> dict[int, tuple[datetime | None, datetime | None]]:
    """Per article_id ``(last_definitive_at, last_attempt_at)`` from events."""
    sql = (
        "SELECT article_id, payload_json, ts_utc FROM events "
        "WHERE kind = ? AND article_id IS NOT NULL"
    )
    cursors: dict[int, list[datetime | None]] = {}
    for row in store.query(sql, (LINK_RECHECKED,)):
        ts = _parse_ts(row["ts_utc"])
        if ts is None:
            continue
        try:
            verdict = json.loads(row["payload_json"] or "{}").get("verdict")
        except (ValueError, TypeError):
            verdict = None
        cur = cursors.setdefault(row["article_id"], [None, None])
        if cur[1] is None or ts > cur[1]:
            cur[1] = ts  # last_attempt_at (any verdict)
        if verdicts.advances_age_cursor(verdict) and (cur[0] is None or ts > cur[0]):
            cur[0] = ts  # last_definitive_at (excludes probe_error)
    return {aid: (v[0], v[1]) for aid, v in cursors.items()}


def _anchor_baselines(store: "EventStore") -> dict[int, dict[str, str]]:
    """Per article_id map of ``{anchor_url: anchor_text}`` from anchors_json.

    Best-effort: history/drafts-sourced and early article rows have
    ``anchors_json = '[]'`` so they yield no baseline (anchor drift degrades to
    ``anchor_baseline_missing`` — adversarial A7 / R3).
    """
    baselines: dict[int, dict[str, str]] = {}
    for row in store.query("SELECT article_id, anchors_json FROM articles"):
        try:
            anchors = json.loads(row["anchors_json"] or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(anchors, list):
            continue
        mapping = {
            a["url"]: a["anchor"]
            for a in anchors
            if isinstance(a, dict) and a.get("url") and a.get("anchor")
        }
        if mapping:
            baselines[row["article_id"]] = mapping
    return baselines


def _baseline_for(
    baselines: dict[int, dict[str, str]], article_id: int, target_url: str | None
) -> str | None:
    """Best-effort baseline anchor text for ``target_url`` on this article."""
    mapping = baselines.get(article_id)
    if not mapping or not target_url:
        return None
    if target_url in mapping:
        return mapping[target_url]
    # Fall back to canonical comparison, never raising.
    try:
        from backlink_publisher._util.url import canonicalize_url

        canon_target = canonicalize_url(target_url)
        for url, text in mapping.items():
            if canonicalize_url(url) == canon_target:
                return text
    except Exception:  # noqa: BLE001
        pass
    return None


def select_candidates(
    store: "EventStore",
    *,
    now: datetime,
    days: int = DEFAULT_DAYS,
    cap: int = DEFAULT_CAP,
    min_retry_days: int = DEFAULT_MIN_RETRY_DAYS,
    since: datetime | None = None,
    host: str | None = None,
    run_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return the backlink records due for re-probe this run, oldest-first.

    Eligibility: ``last_definitive_at`` is unset or older than ``days`` AND
    ``last_attempt_at`` is unset or older than ``min_retry_days``. Ordered
    never-checked-first then oldest-definitive-check-first then oldest-publish
    -first; capped at ``min(cap, limit)``.
    """
    universe = _confirmed_universe(store, since=since, host=host, run_id=run_id)
    cursors = _recheck_cursors(store)
    baselines = _anchor_baselines(store)

    candidates: list[dict] = []
    age_threshold = timedelta(days=days)
    retry_floor = timedelta(days=min_retry_days)
    for article_id, info in universe.items():
        last_def, last_att = cursors.get(article_id, (None, None))
        if last_def is not None and (now - last_def) <= age_threshold:
            continue
        if last_att is not None and (now - last_att) <= retry_floor:
            continue
        published_at = info["_published_at"]
        candidates.append(
            {
                "live_url": info["live_url"],
                "target_url": info["target_url"],
                "host": info["host"],
                "article_id": article_id,
                "platform": info["platform"],
                "baseline_anchor": _baseline_for(baselines, article_id, info["target_url"]),
                "published_age_days": (
                    (now - published_at).days if published_at else None
                ),
                "source": "events",
                "_last_definitive_at": last_def,
                "_published_at": published_at,
            }
        )

    # Oldest-first: never-definitively-checked (last_def None) sort to the front,
    # then by oldest definitive check, then by oldest publish time. ts_utc is
    # NOT NULL so the publish-time tiebreak never hits a NULL sort key (A4).
    candidates.sort(
        key=lambda r: (
            r["_last_definitive_at"] is not None,
            r["_last_definitive_at"] or r["_published_at"] or _EPOCH,
            r["_published_at"] or _EPOCH,
        )
    )

    effective_cap = cap if limit is None else min(cap, limit)
    selected = candidates[:effective_cap]
    for r in selected:
        r.pop("_last_definitive_at", None)
        r.pop("_published_at", None)
    return selected


def read_stdin_candidates(fh) -> list[dict] | None:
    """Read ``live_url`` candidates from stdin JSONL (R11), or None if no stdin.

    Trust boundary (SEC3): stdin is an externally-controlled URL source, so
    non-http(s) schemes are rejected here, and every accepted candidate is tagged
    ``source="stdin"`` so emitted ``link.rechecked`` events are distinguishable
    from events.db-sourced ones. The probe itself still routes through the same
    SSRF-guarded opener (probe_liveness → inspect_target_anchor) regardless of
    source, so the SSRF guard applies uniformly.
    """
    if fh is None or (hasattr(fh, "isatty") and fh.isatty()):
        return None
    rows: list[dict] = []
    saw_input = False
    for line in fh:
        stripped = line.strip()
        if not stripped:
            continue
        saw_input = True
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        live_url = obj.get("live_url")
        if not isinstance(live_url, str) or not _is_http(live_url):
            continue
        # A non-http(s) target_url is a trust-boundary hazard (and never a real
        # backlink target); drop it to None so the probe treats this as
        # liveness-only rather than string-matching an unintended anchor (SEC3).
        target_url = obj.get("target_url")
        if target_url is not None and not _is_http(target_url):
            target_url = None
        rows.append(
            {
                "live_url": live_url,
                "target_url": target_url,
                "host": obj.get("host") or _host_of(live_url),
                "article_id": obj.get("article_id"),
                "platform": obj.get("platform"),
                "baseline_anchor": obj.get("anchor"),
                "published_age_days": None,
                "source": "stdin",
            }
        )
    return rows if saw_input else None
