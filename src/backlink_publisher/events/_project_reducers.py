"""Reducer functions for ``events.projector`` — one per JSON source type.

Each reducer diffs a JSON state file (checkpoint / history / drafts) against
the per-source cursor in ``projection_cursor`` and emits events + articles
rows for the new state. All three share the same signature::

    (path: Path, store: EventStore) -> ProjectionResult
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._project_helpers import (
    ProjectionError,
    article_payload,
    checkpoint_event_timestamp,
    cursor_load,
    cursor_save,
    extract_anchors,
    host_of,
    read_json,
    split_iso_with_offset,
    split_local_naive,
    write_quarantines,
)
from .._util.url import canonicalize_url
from . import kinds
from .scrubber import scrub_text
from .store import EventStore


@dataclass(frozen=True)
class ProjectionResult:
    """Counts returned from a single ``flush_for`` call."""

    events_inserted: int = 0
    articles_inserted: int = 0
    skipped_due_to_dedup: int = 0
    cursor_updated: bool = False
    quarantined: int = 0
    records_considered: int = 0


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
    records_considered = 0
    seen_intent_or_failed: set[tuple[str, str, str]] = set()
    pending_quarantines: list[dict[str, Any]] = []

    with store.connect() as conn:
        prior = cursor_load(conn, source)
        prior_items: dict[str, dict[str, Any]] = prior.get("items", {})
        next_items: dict[str, dict[str, Any]] = {}

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            status = item.get("status")
            if not isinstance(item_id, str) or not isinstance(status, str):
                continue
            records_considered += 1

            published_url = item.get("published_url") or None
            target_url = (item.get("payload") or {}).get("target_url") or None
            host = host_of(target_url)
            ts_raw, ts_utc = checkpoint_event_timestamp(item, started_at)

            next_items[item_id] = {
                "status": status,
                "published_url": published_url,
            }
            prior_state = prior_items.get(item_id)
            if prior_state == next_items[item_id]:
                continue

            outcome = kinds.classify("checkpoint", status)

            if outcome is kinds.PUBLISH_INTENT:
                if prior_state is not None:
                    continue
                dedup_key = (run_id, target_url or "", kinds.PUBLISH_INTENT)
                if dedup_key in seen_intent_or_failed:
                    continue
                seen_intent_or_failed.add(dedup_key)
                store.append(
                    kinds.PUBLISH_INTENT,
                    {
                        "target_url": target_url,
                        "title": item.get("title"),
                        "platform": item.get("adapter"),
                    },
                    run_id=run_id,
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1

            elif outcome is kinds.CONFIRMED_FAMILY:
                live_host = host_of(published_url) or host
                payload = item.get("payload") or {}
                _body = payload.get("content_markdown") if isinstance(payload, dict) else None
                _anchors = extract_anchors(payload)
                _completed_at = item.get("completed_at")
                if isinstance(_completed_at, str) and _completed_at:
                    try:
                        _pub_raw, _pub_utc = split_iso_with_offset(_completed_at)
                    except ValueError:
                        _pub_raw, _pub_utc = _completed_at, None
                else:
                    _pub_raw, _pub_utc = None, None
                _lang = payload.get("lang") if isinstance(payload, dict) else None
                art = article_payload(
                    live_url=published_url,
                    target_url=target_url,
                    host=live_host,
                    anchors_json=json.dumps(_anchors, sort_keys=True, ensure_ascii=False),
                    run_id=run_id,
                    body=_body,
                    lang=_lang if isinstance(_lang, str) and _lang else None,
                    published_at_raw=_pub_raw,
                    published_at_utc=_pub_utc,
                )
                try:
                    article_id = store.add_article(art, conn=conn)
                except sqlite3.IntegrityError:
                    skipped_due_to_dedup += 1
                    continue
                articles_inserted += 1
                _verified = item.get("verified", True)
                _kind = kinds.PUBLISH_CONFIRMED if _verified else kinds.PUBLISH_UNVERIFIED
                store.append(
                    _kind,
                    {
                        "live_url": published_url,
                        "target_url": target_url,
                        "live_url_canonical": (
                            canonicalize_url(published_url)
                            if published_url
                            else None
                        ),
                        "platform": item.get("adapter"),
                    },
                    run_id=run_id,
                    target_url=target_url,
                    host=live_host,
                    article_id=article_id,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1

            elif outcome is kinds.PUBLISH_FAILED:
                dedup_key = (run_id, target_url or "", kinds.PUBLISH_FAILED)
                if dedup_key in seen_intent_or_failed:
                    continue
                seen_intent_or_failed.add(dedup_key)
                error_class = item.get("error_class")
                error_message = item.get("error") or ""
                cleaned, hits = scrub_text(error_message)
                store.append(
                    kinds.PUBLISH_FAILED,
                    {
                        "error_class": error_class,
                        "error_message_clean": cleaned,
                        "scrub_hits": hits or {},
                        "platform": item.get("adapter"),
                    },
                    run_id=run_id,
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1

            elif outcome is kinds.NO_EMIT:
                pass

            else:
                pending_quarantines.append(
                    {
                        "reason": f"unmapped_status: checkpoint/{status}",
                        "failure_type": "unmapped_status",
                        "source": "checkpoint",
                        "run_id": run_id,
                        "source_status": status,
                        "record_identity": item_id,
                        "raw_payload": {"target_url": target_url, "adapter": item.get("adapter")},
                    }
                )

        cursor_save(
            conn,
            source,
            {"items": next_items},
            mtime=path.stat().st_mtime,
        )

    events_inserted -= sum(
        1 for q in pending_quarantines if q.get("failure_type") == "missing_field"
    )
    write_quarantines(store, pending_quarantines)

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
        quarantined=len(pending_quarantines),
        records_considered=records_considered,
    )


# ── History reducer helpers ───────────────────────────────────────


def _parse_row_timestamps(created_at: str) -> tuple[str | None, str | None]:
    """Return *(ts_raw, ts_utc)* for *created_at*; ``(None, None)`` on blank/bad input."""
    if not created_at:
        return None, None
    try:
        return split_local_naive(created_at)
    except ValueError:
        return created_at or None, None


def _emit_confirmed_history_row(
    row: dict,
    article_urls: object,
    target_url: str | None,
    host: str | None,
    language: str | None,
    ts_raw: str | None,
    ts_utc: str | None,
    store: EventStore,
    conn: object,
    pending_quarantines: list,
) -> tuple[int, int, int, bool]:
    """Emit PUBLISH_CONFIRMED event(s) for one history row.

    Returns *(events_delta, articles_delta, skipped_delta, always_mark)*.
    ``always_mark=True`` means the row must be added to seen_ids regardless of
    the running skipped_due_to_dedup total (i.e. no-article-URL path).
    """
    if not isinstance(article_urls, list) or not article_urls:
        store.append(
            kinds.PUBLISH_CONFIRMED,
            {
                "live_url": None,
                "target_url": target_url,
                "platform": row.get("platform"),
            },
            target_url=target_url,
            host=host,
            ts_raw=ts_raw,
            ts_utc=ts_utc,
            conn=conn,
            pending_quarantines=pending_quarantines,
        )
        return 1, 0, 0, True

    events = 0
    articles = 0
    skipped = 0
    emitted_any = False
    for live_url in article_urls:
        if not isinstance(live_url, str) or not live_url:
            continue
        article = article_payload(
            live_url=live_url,
            target_url=target_url,
            host=host_of(live_url),
            lang=language,
            published_at_raw=ts_raw,
            published_at_utc=ts_utc,
        )
        try:
            article_id = store.add_article(article, conn=conn)
        except sqlite3.IntegrityError:
            skipped += 1
            continue
        articles += 1
        store.append(
            kinds.PUBLISH_CONFIRMED,
            {
                "live_url": live_url,
                "target_url": target_url,
                "platform": row.get("platform"),
            },
            target_url=target_url,
            host=host_of(live_url),
            article_id=article_id,
            ts_raw=ts_raw,
            ts_utc=ts_utc,
            conn=conn,
            pending_quarantines=pending_quarantines,
        )
        events += 1
        emitted_any = True
    return events, articles, skipped, False


def _emit_drafts_confirmed(
    draft_id: str,
    article_urls: list,
    language: str | None,
    target_url: str | None,
    host: str | None,
    ts_raw: str | None,
    ts_utc: str | None,
    store: EventStore,
    conn: sqlite3.Connection,
    pending_quarantines: list[dict[str, Any]],
) -> tuple[int, int, int]:
    """Emit PUBLISH_CONFIRMED event(s) for one drafts row.

    Returns (events_inserted, articles_inserted, skipped_due_to_dedup).
    """
    if not isinstance(article_urls, list) or not article_urls:
        store.append(
            kinds.PUBLISH_CONFIRMED,
            {"live_url": None, "draft_id": draft_id},
            target_url=target_url,
            host=host,
            ts_raw=ts_raw,
            ts_utc=ts_utc,
            conn=conn,
            pending_quarantines=pending_quarantines,
        )
        return 1, 0, 0

    events_inserted = 0
    articles_inserted = 0
    skipped = 0
    for live_url in article_urls:
        if not isinstance(live_url, str) or not live_url:
            continue
        article = article_payload(
            live_url=live_url,
            target_url=target_url,
            host=host_of(live_url),
            lang=language if isinstance(language, str) and language else None,
            published_at_raw=ts_raw,
            published_at_utc=ts_utc,
        )
        try:
            article_id = store.add_article(article, conn=conn)
        except sqlite3.IntegrityError:
            skipped += 1
            continue
        articles_inserted += 1
        store.append(
            kinds.PUBLISH_CONFIRMED,
            {"live_url": live_url, "draft_id": draft_id},
            target_url=target_url,
            host=host_of(live_url),
            article_id=article_id,
            ts_raw=ts_raw,
            ts_utc=ts_utc,
            conn=conn,
            pending_quarantines=pending_quarantines,
        )
        events_inserted += 1
    return events_inserted, articles_inserted, skipped


def _project_history(
    path: Path,
    store: EventStore,
) -> ProjectionResult:
    """Append-only history list: diff by ``id``, emit per row."""
    source = str(path)
    rows = read_json(path)
    if rows is None:
        return ProjectionResult()
    if not isinstance(rows, list):
        raise ProjectionError(f"history payload not a list: {path}")

    events_inserted = 0
    articles_inserted = 0
    skipped_due_to_dedup = 0
    records_considered = 0
    pending_quarantines: list[dict[str, Any]] = []

    with store.connect() as conn:
        prior = cursor_load(conn, source)
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
            records_considered += 1

            status = row.get("status")
            target_url = row.get("target_url")
            host = host_of(target_url) if isinstance(target_url, str) else None
            created_at = row.get("created_at") or ""
            ts_raw, ts_utc = _parse_row_timestamps(created_at)

            article_urls = row.get("article_urls") or []
            language = row.get("language") if isinstance(row.get("language"), str) else None

            outcome = kinds.classify("history", status)
            if outcome is kinds.PUBLISH_CONFIRMED:
                ev, art, sk, always_mark = _emit_confirmed_history_row(
                    row, article_urls, target_url, host, language,
                    ts_raw, ts_utc, store, conn, pending_quarantines,
                )
                events_inserted += ev
                articles_inserted += art
                skipped_due_to_dedup += sk
                if always_mark or ev or skipped_due_to_dedup:
                    next_seen.append(row_id)
                    seen_ids.add(row_id)

            elif outcome is kinds.PUBLISH_FAILED:
                error = row.get("error") or ""
                cleaned, hits = scrub_text(error)
                store.append(
                    kinds.PUBLISH_FAILED,
                    {
                        "error_class": row.get("error_class"),
                        "error_message_clean": cleaned,
                        "scrub_hits": hits or {},
                        "platform": row.get("platform"),
                    },
                    target_url=target_url,
                    host=host,
                    ts_raw=ts_raw,
                    ts_utc=ts_utc,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1
                next_seen.append(row_id)
                seen_ids.add(row_id)
            else:
                next_seen.append(row_id)
                seen_ids.add(row_id)

        cursor_save(
            conn,
            source,
            {"seen_ids": next_seen},
            mtime=path.stat().st_mtime,
        )

    events_inserted -= sum(
        1 for q in pending_quarantines if q.get("failure_type") == "missing_field"
    )
    write_quarantines(store, pending_quarantines)

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
        quarantined=len(pending_quarantines),
        records_considered=records_considered,
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
    records_considered = 0
    pending_quarantines: list[dict[str, Any]] = []

    with store.connect() as conn:
        prior = cursor_load(conn, source)
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
            records_considered += 1

            target_url = row.get("target_url")
            host = host_of(target_url) if isinstance(target_url, str) else None
            published_at = row.get("published_at")
            try:
                ts_raw, ts_utc = (
                    split_local_naive(published_at)
                    if isinstance(published_at, str) and published_at
                    else (None, None)
                )
            except ValueError:
                ts_raw, ts_utc = published_at, None

            outcome = kinds.classify("drafts", status)

            if outcome is kinds.PUBLISH_CONFIRMED:
                article_urls = row.get("article_urls") or []
                _lang = row.get("language")
                ev, art, sk = _emit_drafts_confirmed(
                    draft_id, article_urls,
                    _lang if isinstance(_lang, str) and _lang else None,
                    target_url, host, ts_raw, ts_utc,
                    store, conn, pending_quarantines,
                )
                events_inserted += ev
                articles_inserted += art
                skipped_due_to_dedup += sk

            elif outcome is kinds.DRAFT_SCHEDULED:
                if prior_status == "scheduled":
                    continue
                store.append(
                    kinds.DRAFT_SCHEDULED,
                    {"draft_id": draft_id},
                    target_url=target_url,
                    host=host,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1

            elif outcome is kinds.DRAFT_CREATED:
                if prior_status is not None:
                    continue
                store.append(
                    kinds.DRAFT_CREATED,
                    {"draft_id": draft_id},
                    target_url=target_url,
                    host=host,
                    conn=conn,
                    pending_quarantines=pending_quarantines,
                )
                events_inserted += 1

            else:
                pass

        cursor_save(
            conn,
            source,
            {"items": next_items},
            mtime=path.stat().st_mtime,
        )

    events_inserted -= sum(
        1 for q in pending_quarantines if q.get("failure_type") == "missing_field"
    )
    write_quarantines(store, pending_quarantines)

    return ProjectionResult(
        events_inserted=events_inserted,
        articles_inserted=articles_inserted,
        skipped_due_to_dedup=skipped_due_to_dedup,
        cursor_updated=True,
        quarantined=len(pending_quarantines),
        records_considered=records_considered,
    )
