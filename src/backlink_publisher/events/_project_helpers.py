"""Shared projection helpers extracted from ``projector.py``.

Owns cursor I/O, datetime normalisation, JSON file reading, and shared
article-payload construction — the three reducers import from here rather
than carrying their own copies.

Plan: ``docs/plans/2026-05-26-004-opt-projector-budget-rescue-plan.md``
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .._util.url import canonicalize_url
from .store import EventStore

_log = logging.getLogger(__name__)


class ProjectionError(RuntimeError):
    """Cursor corruption, unknown source dispatch, schema mismatch."""


_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_HISTORY_FILENAME = "publish-history.json"
_DRAFTS_FILENAME = "draft-queue.json"


# ── Source detection ────────────────────────────────────────────────


def detect_source(path: Path) -> str:
    """Classify ``path`` as ``"checkpoint" | "history" | "drafts"``."""
    name = path.name
    if name == _HISTORY_FILENAME:
        return "history"
    if name == _DRAFTS_FILENAME:
        return "drafts"
    stem = path.stem
    if _RUN_ID_RE.match(stem):
        return "checkpoint"
    raise ProjectionError(f"cannot detect source kind for path: {path}")


# ── Cursor helpers ─────────────────────────────────────────────────


def cursor_load(conn, source: str) -> dict[str, Any]:
    """Read the prior projection state for ``source``. Empty dict on miss."""
    row = conn.execute(
        "SELECT last_seen_state_json FROM projection_cursor WHERE source = ?",
        (source,),
    ).fetchone()
    if row is None or row[0] is None:
        return {}
    try:
        return json.loads(row[0])
    except json.JSONDecodeError as exc:
        raise ProjectionError(
            f"projection_cursor for {source!r} is corrupted"
        ) from exc


def cursor_save(
    conn,
    source: str,
    state: dict[str, Any],
    *,
    mtime: float | None,
) -> None:
    """Upsert the cursor row for ``source``."""
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO projection_cursor (source, last_mtime, last_seen_state_json)
        VALUES (?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_mtime = excluded.last_mtime,
            last_seen_state_json = excluded.last_seen_state_json
        """,
        (source, mtime, payload),
    )


# ── Datetime helpers ────────────────────────────────────────────────


def split_iso_with_offset(value: str) -> tuple[str, str]:
    """Checkpoint ``started_at`` / ``completed_at`` form (ISO with offset)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return value, parsed.astimezone(timezone.utc).isoformat()


def split_local_naive(value: str) -> tuple[str, str]:
    """History / drafts ``YYYY-MM-DD HH:MM`` form — assume operator local."""
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    local = parsed.astimezone()
    return value, local.astimezone(timezone.utc).isoformat()


# ── JSON read ───────────────────────────────────────────────────────


def read_json(path: Path) -> Any | None:
    """Read+parse ``path``. Returns ``None`` on parse error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _log.warning("projector: failed to parse %s: %s", path, exc)
        return None


# ── Anchor extraction (matches cli/report_anchors.py:80) ────────────


def extract_anchors(payload: Any) -> list[dict[str, Any]]:
    """Filter ``payload.links[]`` to the kinds the read side cares about."""
    if not isinstance(payload, dict):
        return []
    links = payload.get("links") or []
    if not isinstance(links, list):
        return []
    return [
        link
        for link in links
        if isinstance(link, dict)
        and link.get("kind") in ("main_domain", "target")
        and link.get("anchor")
    ]


# ── URL helpers ─────────────────────────────────────────────────────


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url).netloc
    return netloc or None


# ── Quarantine writer ───────────────────────────────────────────────


def write_quarantines(store: EventStore, pending: list[dict[str, Any]]) -> None:
    """Write collected quarantine intents AFTER the reducer transaction commits."""
    for q in pending:
        try:
            store.quarantine(**q)
            _log.warning(
                "RECON projector: quarantined [%s] %s (run=%s id=%s)",
                q.get("failure_type"), q.get("reason"),
                q.get("run_id"), q.get("record_identity"),
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "RECON projector: FAILED to quarantine [%s] %s (run=%s id=%s): "
                "%s — continuing",
                q.get("failure_type"), q.get("reason"),
                q.get("run_id"), q.get("record_identity"), exc,
            )


# ── Checkpoint-event timestamp helpers ──────────────────────────────


def checkpoint_event_timestamp(
    item: dict[str, Any], started_at: str
) -> tuple[str | None, str | None]:
    completed_at = item.get("completed_at")
    if isinstance(completed_at, str) and completed_at:
        try:
            return split_iso_with_offset(completed_at)
        except ValueError:
            pass
    if started_at:
        try:
            return split_iso_with_offset(started_at)
        except ValueError:
            pass
    return None, None


def article_payload(
    *,
    live_url: str | None,
    target_url: str | None,
    host: str | None,
    anchors_json: str = "[]",
    run_id: str | None = None,
    body: str | None = None,
    lang: str | None = None,
    published_at_raw: str | None = None,
    published_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build an article row dict shared by all three reducers."""
    payload: dict[str, Any] = {
        "anchors_json": anchors_json,
        "target_urls_json": json.dumps(
            [target_url] if target_url else [],
            sort_keys=True, ensure_ascii=False,
        ),
        "host": host,
        "live_url": canonicalize_url(live_url) if live_url else None,
    }
    if body is not None:
        payload["body"] = body
    if run_id is not None:
        payload["run_id"] = run_id
    if lang:
        payload["lang"] = lang
    if published_at_raw is not None:
        payload["published_at_raw"] = published_at_raw
    if published_at_utc is not None:
        payload["published_at_utc"] = published_at_utc
    return payload
