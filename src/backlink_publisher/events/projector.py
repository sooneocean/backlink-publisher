"""Projection from JSON state files into the read-side event store.

The projector reads checkpoint / publish-history / draft-queue JSON files,
diffs them against the per-source cursor in ``projection_cursor``, and
emits ``events`` + ``articles`` rows for the new state. Each reducer runs
inside a single ``EventStore.connect()`` transaction so a partial flush
can't half-update the database.

Idempotency is layered (plan §U4):

1. In-transaction ``seen_in_tx`` set keyed per kind catches duplicate
   logical changes emitted within one flush.
2. ``projection_cursor.last_seen_state_json`` diff keeps a no-op flush
   from inserting any rows.
3. ``articles.live_url`` UNIQUE rejects cross-source duplicates at the
   DB layer — caller catches ``IntegrityError`` and skips the matching
   ``publish.confirmed``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .._util.url import canonicalize_url
from .scrubber import scrub_text
from .store import EventStore

_log = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
_HISTORY_FILENAME = "publish-history.json"
_DRAFTS_FILENAME = "draft-queue.json"

#: Bounded retry for non-atomic JSON writers (publish-history.json).
_JSON_READ_RETRIES: int = 5
_JSON_READ_BACKOFF_S: float = 0.1


class ProjectionError(RuntimeError):
    """Cursor table corruption, unknown source dispatch, schema mismatch.

    Raised so the caller knows the projection cannot make progress; the
    enclosing transaction rolls back via the ``EventStore.connect()``
    context manager so no partial events leak into the read side.
    """


@dataclass(frozen=True)
class ProjectionResult:
    """Counts returned from a single ``flush_for`` call."""

    events_inserted: int = 0
    articles_inserted: int = 0
    skipped_due_to_dedup: int = 0
    cursor_updated: bool = False


def flush_for(
    path: Path,
    *,
    store: EventStore | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ProjectionResult:
    """Project the JSON source at ``path`` into the event store.

    Dispatches by detecting whether ``path`` is a checkpoint, history,
    or drafts file. Each reducer runs in one transaction; on any
    unexpected exception the transaction rolls back and the cursor is
    left unchanged.
    """
    store = store or EventStore()
    source_kind = _detect_source(path)
    if source_kind == "checkpoint":
        return _project_checkpoint(path, store)
    if source_kind == "history":
        return _project_history(path, store, sleep_fn=sleep_fn)
    if source_kind == "drafts":
        return _project_drafts(path, store)
    raise ProjectionError(f"unknown source for path: {path}")


# ── Source detection ──────────────────────────────────────────────


def _detect_source(path: Path) -> str:
    """Classify ``path`` as ``"checkpoint" | "history" | "drafts"``.

    The dispatch is filename-based: checkpoint files use the run-id
    pattern ``<ts>-<hex>.json`` (see ``checkpoint.generate_run_id``),
    while history and drafts have fixed filenames.
    """
    name = path.name
    if name == _HISTORY_FILENAME:
        return "history"
    if name == _DRAFTS_FILENAME:
        return "drafts"
    stem = path.stem
    if _RUN_ID_RE.match(stem):
        return "checkpoint"
    raise ProjectionError(f"cannot detect source kind for path: {path}")


# ── Cursor helpers ────────────────────────────────────────────────


def _cursor_load(conn: sqlite3.Connection, source: str) -> dict[str, Any]:
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


def _cursor_save(
    conn: sqlite3.Connection,
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


# ── Datetime helpers ──────────────────────────────────────────────


def _split_iso_with_offset(value: str) -> tuple[str, str]:
    """Checkpoint ``started_at`` / ``completed_at`` form (ISO with offset)."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return value, parsed.astimezone(timezone.utc).isoformat()


def _split_local_naive(value: str) -> tuple[str, str]:
    """History / drafts ``YYYY-MM-DD HH:MM`` form — assume operator local."""
    parsed = datetime.strptime(value, "%Y-%m-%d %H:%M")
    local = parsed.astimezone()
    return value, local.astimezone(timezone.utc).isoformat()


# ── JSON read with retry (history is non-atomic) ──────────────────


def _read_json_with_retry(
    path: Path,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Any | None:
    """Read+parse ``path``; retry parse errors up to ``_JSON_READ_RETRIES``.

    Returns ``None`` after the final retry so the reducer can WARN and
    keep the cursor unchanged. ``FileNotFoundError`` bubbles to caller.
    """
    last_exc: json.JSONDecodeError | None = None
    for attempt in range(1, _JSON_READ_RETRIES + 1):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            last_exc = exc
            if attempt < _JSON_READ_RETRIES:
                sleep_fn(_JSON_READ_BACKOFF_S)
    _log.warning(
        "projector: gave up parsing %s after %d retries: %s",
        path,
        _JSON_READ_RETRIES,
        last_exc,
    )
    return None


# ── Anchor extraction (matches cli/report_anchors.py:80) ──────────


def _extract_anchors(payload: Any) -> list[dict[str, Any]]:
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


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    netloc = urlparse(url).netloc
    return netloc or None


# ── Checkpoint reducer ────────────────────────────────────────────


def _project_checkpoint(path: Path, store: EventStore) -> ProjectionResult:
    """Diff a checkpoint file against the cursor and emit events."""
    source = str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ProjectionError(f"checkpoint payload not an object: {path}")

    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise ProjectionError(f"checkpoint missing run_id: {path}")
    started_at = data.get("started_at") or ""
    items = data.get("items") or []

    events_inserted = 0
    articles_inserted = 0
    skipped_due_to_dedup = 0
    seen_intent_or_failed: set[tuple[str, str, str]] = set()

    with store.connect() as conn:
        prior = _cursor_load(conn, source)
        prior_items: dict[str, dict[str, Any]] = prior.get("items", {})
        next_items: dict[str, dict[str, Any]] = {}

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            status = item.get("status")
            if not isinstance(item_id, str) or not isinstance(status, str):
                continue

            published_url = item.get("published_url") or None
            target_url = (item.get("payload") or {}).get("target_url") or None
            host = _host_of(target_url)
            ts_raw, ts_utc = _checkpoint_event_timestamp(item, started_at)

            next_items[item_id] = {
                "status": status,
                "published_url": published_url,
            }
            prior_state = prior_items.get(item_id)
            if prior_state == next_items[item_id]:
                continue

            if status == "pending":
                if prior_state is not None:
                    # An item already projected past pending should not
                    # regress; ignore unexpected rewinds.
                    continue
                dedup_key = (run_id, target_url or "", "publish.intent")
                if dedup_key in seen_intent_or_failed:
                    continue
                seen_intent_or_failed.add(dedup_key)
                store.append(
                    "publish.intent",
                    {"target_url": target_url, "title": item.get("title")},
                    run_id=run_id,
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                )
                events_inserted += 1

            elif status == "succeeded":
                live_host = _host_of(published_url) or host
                article_payload = _build_article(
                    item, host=live_host, target_url=target_url, run_id=run_id
                )
                try:
                    article_id = store.add_article(article_payload, conn=conn)
                except sqlite3.IntegrityError:
                    skipped_due_to_dedup += 1
                    continue
                articles_inserted += 1
                store.append(
                    "publish.confirmed",
                    {
                        "live_url": published_url,
                        "target_url": target_url,
                        "live_url_canonical": (
                            canonicalize_url(published_url)
                            if published_url else None
                        ),
                    },
                    run_id=run_id,
                    target_url=target_url,
                    host=live_host,
                    article_id=article_id,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                )
                events_inserted += 1

            elif status == "failed":
                dedup_key = (run_id, target_url or "", "publish.failed")
                if dedup_key in seen_intent_or_failed:
                    continue
                seen_intent_or_failed.add(dedup_key)
                error_class = item.get("error_class")
                error_message = item.get("error") or ""
                cleaned, hits = scrub_text(error_message)
                store.append(
                    "publish.failed",
                    {
                        "error_class": error_class,
                        "error_message_clean": cleaned,
                        "scrub_hits": hits or {},
                    },
                    run_id=run_id,
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                )
                events_inserted += 1

        _cursor_save(
            conn,
            source,
            {"items": next_items},
            mtime=path.stat().st_mtime,
        )

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
    )


def _checkpoint_event_timestamp(
    item: dict[str, Any], started_at: str
) -> tuple[str | None, str | None]:
    completed_at = item.get("completed_at")
    if isinstance(completed_at, str) and completed_at:
        try:
            return _split_iso_with_offset(completed_at)
        except ValueError:
            pass
    if started_at:
        try:
            return _split_iso_with_offset(started_at)
        except ValueError:
            pass
    return None, None


def _build_article(
    item: dict[str, Any],
    *,
    host: str | None,
    target_url: str | None,
    run_id: str,
) -> dict[str, Any]:
    payload = item.get("payload") or {}
    published_url = item.get("published_url") or None
    body = payload.get("content_markdown") if isinstance(payload, dict) else None
    anchors = _extract_anchors(payload)
    completed_at = item.get("completed_at")
    if isinstance(completed_at, str) and completed_at:
        try:
            pub_raw, pub_utc = _split_iso_with_offset(completed_at)
        except ValueError:
            pub_raw, pub_utc = completed_at, None
    else:
        pub_raw, pub_utc = None, None

    article: dict[str, Any] = {
        "body": body,
        "anchors_json": json.dumps(anchors, sort_keys=True, ensure_ascii=False),
        "target_urls_json": json.dumps(
            [target_url] if target_url else [],
            sort_keys=True, ensure_ascii=False,
        ),
        "host": host,
        "live_url": canonicalize_url(published_url) if published_url else None,
        "run_id": run_id,
    }
    if pub_raw is not None:
        article["published_at_raw"] = pub_raw
    if pub_utc is not None:
        article["published_at_utc"] = pub_utc
    lang = payload.get("lang") if isinstance(payload, dict) else None
    if isinstance(lang, str) and lang:
        article["lang"] = lang
    return article


# ── History reducer ───────────────────────────────────────────────


def _project_history(
    path: Path,
    store: EventStore,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> ProjectionResult:
    """Append-only history list: diff by ``id``, emit per row."""
    source = str(path)
    rows = _read_json_with_retry(path, sleep_fn=sleep_fn)
    if rows is None:
        # Non-atomic writer mid-write; leave cursor untouched.
        return ProjectionResult()
    if not isinstance(rows, list):
        raise ProjectionError(f"history payload not a list: {path}")

    events_inserted = 0
    articles_inserted = 0
    skipped_due_to_dedup = 0

    with store.connect() as conn:
        prior = _cursor_load(conn, source)
        seen_ids: set[str] = set(prior.get("seen_ids") or [])
        next_seen: list[str] = list(seen_ids)

        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                continue
            if row_id in seen_ids:
                continue

            status = row.get("status")
            target_url = row.get("target_url")
            host = _host_of(target_url) if isinstance(target_url, str) else None
            created_at = row.get("created_at") or ""
            try:
                ts_raw, ts_utc = (
                    _split_local_naive(created_at) if created_at else (None, None)
                )
            except ValueError:
                ts_raw, ts_utc = created_at or None, None

            article_urls = row.get("article_urls") or []
            language = row.get("language") if isinstance(row.get("language"), str) else None

            if status == "published":
                if not isinstance(article_urls, list) or not article_urls:
                    # No live URL means cross-source dedup cannot fire;
                    # still emit a confirmed event so consumers see the
                    # row, but article row is skipped.
                    store.append(
                        "publish.confirmed",
                        {"live_url": None, "target_url": target_url},
                        target_url=target_url,
                        host=host,
                        ts_raw=ts_raw,
                        ts_utc=ts_utc,
                        conn=conn,
                    )
                    events_inserted += 1
                    next_seen.append(row_id)
                    seen_ids.add(row_id)
                    continue
                emitted_any = False
                for live_url in article_urls:
                    if not isinstance(live_url, str) or not live_url:
                        continue
                    article = {
                        "anchors_json": "[]",
                        "target_urls_json": json.dumps(
                            [target_url] if target_url else [],
                            sort_keys=True, ensure_ascii=False,
                        ),
                        "host": _host_of(live_url),
                        "live_url": canonicalize_url(live_url),
                    }
                    if language:
                        article["lang"] = language
                    if ts_raw is not None:
                        article["published_at_raw"] = ts_raw
                    if ts_utc is not None:
                        article["published_at_utc"] = ts_utc
                    try:
                        article_id = store.add_article(article, conn=conn)
                    except sqlite3.IntegrityError:
                        skipped_due_to_dedup += 1
                        continue
                    articles_inserted += 1
                    store.append(
                        "publish.confirmed",
                        {"live_url": live_url, "target_url": target_url},
                        target_url=target_url,
                        host=_host_of(live_url),
                        article_id=article_id,
                        ts_raw=ts_raw,
                        ts_utc=ts_utc,
                        conn=conn,
                    )
                    events_inserted += 1
                    emitted_any = True
                if emitted_any or skipped_due_to_dedup:
                    next_seen.append(row_id)
                    seen_ids.add(row_id)

            elif status == "failed":
                error = row.get("error") or ""
                cleaned, hits = scrub_text(error)
                store.append(
                    "publish.failed",
                    {
                        "error_message_clean": cleaned,
                        "scrub_hits": hits or {},
                    },
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                )
                events_inserted += 1
                next_seen.append(row_id)
                seen_ids.add(row_id)
            else:
                # "drafted" or other transient statuses: tracked in cursor
                # so we don't reprocess, but no event emitted from history.
                next_seen.append(row_id)
                seen_ids.add(row_id)

        _cursor_save(
            conn,
            source,
            {"seen_ids": next_seen},
            mtime=path.stat().st_mtime,
        )

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
    )


# ── Drafts reducer ────────────────────────────────────────────────


def _project_drafts(path: Path, store: EventStore) -> ProjectionResult:
    """Per-draft state machine — see ``plan §U4 Design notes``."""
    source = str(path)
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ProjectionError(f"drafts payload not a list: {path}")

    events_inserted = 0
    articles_inserted = 0
    skipped_due_to_dedup = 0

    with store.connect() as conn:
        prior = _cursor_load(conn, source)
        prior_items: dict[str, str] = prior.get("items", {})
        next_items: dict[str, str] = {}

        for row in rows:
            if not isinstance(row, dict):
                continue
            draft_id = row.get("id")
            status = row.get("status")
            if not isinstance(draft_id, str) or not isinstance(status, str):
                continue
            next_items[draft_id] = status

            prior_status = prior_items.get(draft_id)
            if prior_status == status:
                continue

            target_url = row.get("target_url")
            host = _host_of(target_url) if isinstance(target_url, str) else None
            published_at = row.get("published_at")
            try:
                ts_raw, ts_utc = (
                    _split_local_naive(published_at)
                    if isinstance(published_at, str) and published_at
                    else (None, None)
                )
            except ValueError:
                ts_raw, ts_utc = published_at, None

            transitioned_to_published = status == "published"

            if transitioned_to_published:
                article_urls = row.get("article_urls") or []
                if not isinstance(article_urls, list) or not article_urls:
                    # Published without URL: emit event, skip article row.
                    store.append(
                        "publish.confirmed",
                        {"live_url": None, "draft_id": draft_id},
                        target_url=target_url,
                        host=host,
                        ts_raw=ts_raw,
                        ts_utc=ts_utc,
                        conn=conn,
                    )
                    events_inserted += 1
                    continue
                for live_url in article_urls:
                    if not isinstance(live_url, str) or not live_url:
                        continue
                    article = {
                        "anchors_json": "[]",
                        "target_urls_json": json.dumps(
                            [target_url] if target_url else [],
                            sort_keys=True, ensure_ascii=False,
                        ),
                        "host": _host_of(live_url),
                        "live_url": canonicalize_url(live_url),
                    }
                    lang = row.get("language")
                    if isinstance(lang, str) and lang:
                        article["lang"] = lang
                    if ts_raw is not None:
                        article["published_at_raw"] = ts_raw
                    if ts_utc is not None:
                        article["published_at_utc"] = ts_utc
                    try:
                        article_id = store.add_article(article, conn=conn)
                    except sqlite3.IntegrityError:
                        skipped_due_to_dedup += 1
                        continue
                    articles_inserted += 1
                    store.append(
                        "publish.confirmed",
                        {"live_url": live_url, "draft_id": draft_id},
                        target_url=target_url,
                        host=_host_of(live_url),
                        article_id=article_id,
                        ts_raw=ts_raw,
                        ts_utc=ts_utc,
                        conn=conn,
                    )
                    events_inserted += 1

            elif status == "scheduled":
                if prior_status == "scheduled":
                    continue
                store.append(
                    "draft.scheduled",
                    {"draft_id": draft_id},
                    target_url=target_url,
                    host=host,
                    conn=conn,
                )
                events_inserted += 1

            elif status == "drafted":
                if prior_status is not None:
                    # Already past first sight; only emit draft.created
                    # on the first encounter.
                    continue
                store.append(
                    "draft.created",
                    {"draft_id": draft_id},
                    target_url=target_url,
                    host=host,
                    conn=conn,
                )
                events_inserted += 1

            # "failed" or other states: tracked but not emitted (history
            # is the system of record for failure events).

        _cursor_save(
            conn,
            source,
            {"items": next_items},
            mtime=path.stat().st_mtime,
        )

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
    )
