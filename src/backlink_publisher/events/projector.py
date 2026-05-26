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
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ._project_helpers import (
    ProjectionError,
    article_payload,
    checkpoint_event_timestamp,
    cursor_load,
    cursor_save,
    detect_source,
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

_log = logging.getLogger(__name__)



# Checkpoint success statuses ("done" in production, "succeeded" legacy/test)
# now live in events/kinds.py STATUS_MAP, classified via kinds.classify().


@dataclass(frozen=True)
class ProjectionResult:
    """Counts returned from a single ``flush_for`` call."""

    events_inserted: int = 0
    articles_inserted: int = 0
    skipped_due_to_dedup: int = 0
    cursor_updated: bool = False
    quarantined: int = 0
    records_considered: int = 0


def flush_for(
    path: Path,
    *,
    store: EventStore | None = None,
) -> ProjectionResult:
    """Project the JSON source at ``path`` into the event store.

    Dispatches by detecting whether ``path`` is a checkpoint, history,
    or drafts file. Each reducer runs in one transaction; on any
    unexpected exception the transaction rolls back and the cursor is
    left unchanged.
    """
    store = store or EventStore()
    source_kind = detect_source(path)
    if source_kind == "checkpoint":
        return _project_checkpoint(path, store)
    if source_kind == "history":
        return _project_history(path, store)
    if source_kind == "drafts":
        return _project_drafts(path, store)
    raise ProjectionError(f"unknown source for path: {path}")


# Reserved projection_cursor key for the projection-health marker (Plan 005 /
# U4). Reuses the existing table — no schema migration — so the dashboard and
# an operator can see whether the projection is fresh or silently failing.
_HEALTH_SOURCE = "__projection_health__"

# R10 mass-quarantine alarm: a run whose quarantine ratio reaches this fraction
# of considered records records a `degraded` health signal — so a flood (an
# upstream status vocabulary drifting wholesale) can't pass as a clean run even
# though quarantine-and-continue lets the run finish. Relative (not absolute) so
# it catches a small all-quarantined run, not just large ones.
_QUARANTINE_DEGRADED_RATIO = 0.25


def record_projection_health(
    store: EventStore,
    *,
    ok: bool,
    error: str | None = None,
    quarantine_ratio: float | None = None,
) -> None:
    """Persist the last projection outcome so swallowed failures are visible.

    Fail-safe in its own right: never raises (the DB may be the thing that is
    locked/broken). Stored under a reserved ``projection_cursor`` row. When
    ``quarantine_ratio`` is supplied, sets a ``degraded`` flag (R10) once it
    reaches ``_QUARANTINE_DEGRADED_RATIO`` — a healthy ``ok=True`` run can still
    be degraded if a flood of records quarantined.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        with store.connect() as conn:
            state = dict(cursor_load(conn, _HEALTH_SOURCE))
            if ok:
                state["last_ok_at"] = now
                state["last_error"] = None
            else:
                state["last_error"] = error
                state["last_error_at"] = now
            if quarantine_ratio is not None:
                state["last_quarantine_ratio"] = quarantine_ratio
                state["degraded"] = quarantine_ratio >= _QUARANTINE_DEGRADED_RATIO
            cursor_save(conn, _HEALTH_SOURCE, state, mtime=None)
    except Exception as exc:  # noqa: BLE001 — health recording is best-effort
        _log.warning("projector: could not record projection health: %s", exc)


def project_run_safe(
    run_id: str, *, store: EventStore | None = None
) -> ProjectionResult | None:
    """Project a finished run's checkpoint into ``events.db`` — fail-safe.

    Called inline at the end of publish/resume (Plan 005 / R2). It MUST NOT
    raise: a projection failure (a locked DB ``sqlite3.OperationalError`` from a
    concurrent writer, a ``ProjectionError``, a missing checkpoint) is logged
    and swallowed so the publish result is unaffected. ``flush_for`` is
    idempotent, so the dashboard's project-on-read remains a safe backstop.
    """
    store = store or EventStore()
    try:
        from ..checkpoint import checkpoint_path

        result = flush_for(checkpoint_path(run_id), store=store)
        ratio = (
            result.quarantined / result.records_considered
            if result.records_considered
            else 0.0
        )
        record_projection_health(store, ok=True, quarantine_ratio=ratio)
        return result
    except Exception as exc:  # noqa: BLE001 — projection must never fail publish
        _log.warning(
            "projector: projection after run %s failed (non-fatal): %s",
            run_id, exc,
        )
        record_projection_health(
            store, ok=False, error=f"{type(exc).__name__}: {exc}"
        )
        return None





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
    # Quarantine intents are collected during the loop and written AFTER the
    # reducer transaction commits — quarantine() opens its own connection, and
    # writing it while this reducer holds the WAL write lock would deadlock
    # ("database is locked"). Deferring also means only records from a committed
    # flush are quarantined; a rolled-back flush re-derives them next run.
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

            # Classify through the registry (Seam B). checkpoint is the
            # authoritative publish-outcome source, so an unrecognized status
            # is genuine drift -> quarantine (the P0 class), never a silent
            # fall-through.
            outcome = kinds.classify("checkpoint", status)

            if outcome is kinds.PUBLISH_INTENT:  # status == "pending"
                if prior_state is not None:
                    # An item already projected past pending should not
                    # regress; ignore unexpected rewinds.
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

            elif outcome is kinds.CONFIRMED_FAMILY:  # status in done/succeeded
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
                # D5: a `done` whose run failed verification (CLI exits 5)
                # writes `verified=False` into the checkpoint item. Such a
                # publish MUST NOT count as a confirmed success — emit a
                # distinct `publish.unverified` so a naive "WHERE
                # kind='publish.confirmed'" count stays honest. Legacy items
                # without the key default to verified (pre-D5 indistinguishable).
                _verified = item.get("verified", True)
                _kind = kinds.PUBLISH_CONFIRMED if _verified else kinds.PUBLISH_UNVERIFIED
                store.append(
                    _kind,
                    {
                        "live_url": published_url,
                        "target_url": target_url,
                        "live_url_canonical": (
                            canonicalize_url(published_url)
                            if published_url else None
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

            elif outcome is kinds.PUBLISH_FAILED:  # status == "failed"
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
                # Declared intentional no-op for this source; skip silently.
                pass

            else:  # kinds.QUARANTINE — unrecognized checkpoint status (drift)
                # Collect now; write after the transaction commits (see above).
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

    # A required-field floor miss (append() returns -1) is collected in
    # pending_quarantines as a missing_field record but writes NO event row;
    # subtract those optimistic increments so events_inserted counts only rows
    # actually written. (unmapped_status entries never incremented it.)
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





# ── History reducer ───────────────────────────────────────────────


def _project_history(
    path: Path,
    store: EventStore,
) -> ProjectionResult:
    """Append-only history list: diff by ``id``, emit per row."""
    source = str(path)
    rows = read_json(path)
    if rows is None:
        # Parse error; leave cursor untouched.
        return ProjectionResult()
    if not isinstance(rows, list):
        raise ProjectionError(f"history payload not a list: {path}")

    events_inserted = 0
    articles_inserted = 0
    skipped_due_to_dedup = 0
    records_considered = 0
    # R9 required-field misses (deferred like the checkpoint reducer's, since
    # quarantine() opens its own connection — writing under the held WAL write
    # lock would deadlock). Today's emitters satisfy their floors, so this stays
    # empty unless a future edit drops a load-bearing payload key.
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
            try:
                ts_raw, ts_utc = (
                    split_local_naive(created_at) if created_at else (None, None)
                )
            except ValueError:
                ts_raw, ts_utc = created_at or None, None

            article_urls = row.get("article_urls") or []
            language = row.get("language") if isinstance(row.get("language"), str) else None

            # history emits only for published/failed; every other status is a
            # transient state owned by another source -> NO_EMIT default (no
            # quarantine).
            outcome = kinds.classify("history", status)
            if outcome is kinds.PUBLISH_CONFIRMED:  # status == "published"
                if not isinstance(article_urls, list) or not article_urls:
                    # No live URL means cross-source dedup cannot fire;
                    # still emit a confirmed event so consumers see the
                    # row, but article row is skipped.
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
                    events_inserted += 1
                    next_seen.append(row_id)
                    seen_ids.add(row_id)
                    continue
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
                        skipped_due_to_dedup += 1
                        continue
                    articles_inserted += 1
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
                    events_inserted += 1
                    emitted_any = True
                if emitted_any or skipped_due_to_dedup:
                    next_seen.append(row_id)
                    seen_ids.add(row_id)

            elif outcome is kinds.PUBLISH_FAILED:  # status == "failed"
                error = row.get("error") or ""
                cleaned, hits = scrub_text(error)
                store.append(
                    kinds.PUBLISH_FAILED,
                    {
                        # D3: always present so checkpoint- and history-sourced
                        # failed events share one shape; None when the row has
                        # no class → the explicit "unclassified" bucket.
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
                # NO_EMIT: "drafted"/other transient statuses are owned by the
                # drafts queue — tracked in cursor so we don't reprocess, but no
                # event emitted from history. Intentional, NOT quarantined.
                next_seen.append(row_id)
                seen_ids.add(row_id)

        cursor_save(
            conn,
            source,
            {"seen_ids": next_seen},
            mtime=path.stat().st_mtime,
        )

    # A required-field floor miss (append() returns -1) is collected in
    # pending_quarantines as a missing_field record but writes NO event row;
    # subtract those optimistic increments so events_inserted counts only rows
    # actually written. (unmapped_status entries never incremented it.)
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
    # R9 required-field misses, deferred past the reducer transaction (see the
    # history/checkpoint reducers — quarantine() would deadlock under the held
    # WAL write lock). Empty unless a future edit drops a floor field.
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

            # drafts owns scheduled/drafted; "failed"/other are owned by
            # history -> NO_EMIT default (no quarantine).
            outcome = kinds.classify("drafts", status)

            if outcome is kinds.PUBLISH_CONFIRMED:  # status == "published"
                article_urls = row.get("article_urls") or []
                if not isinstance(article_urls, list) or not article_urls:
                    # Published without URL: emit event, skip article row.
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
                    events_inserted += 1
                    continue
                for live_url in article_urls:
                    if not isinstance(live_url, str) or not live_url:
                        continue
                    _lang = row.get("language")
                    article = article_payload(
                        live_url=live_url,
                        target_url=target_url,
                        host=host_of(live_url),
                        lang=_lang if isinstance(_lang, str) and _lang else None,
                        published_at_raw=ts_raw,
                        published_at_utc=ts_utc,
                    )
                    try:
                        article_id = store.add_article(article, conn=conn)
                    except sqlite3.IntegrityError:
                        skipped_due_to_dedup += 1
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

            elif outcome is kinds.DRAFT_SCHEDULED:  # status == "scheduled"
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

            elif outcome is kinds.DRAFT_CREATED:  # status == "drafted"
                if prior_status is not None:
                    # Already past first sight; only emit draft.created
                    # on the first encounter.
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
                # NO_EMIT: "failed"/other states are owned by history (the
                # system of record for failure events) — tracked in cursor
                # (next_items above) but no event emitted. Intentional, NOT
                # quarantined.
                pass

        cursor_save(
            conn,
            source,
            {"items": next_items},
            mtime=path.stat().st_mtime,
        )

    # A required-field floor miss (append() returns -1) is collected in
    # pending_quarantines as a missing_field record but writes NO event row;
    # subtract those optimistic increments so events_inserted counts only rows
    # actually written. (unmapped_status entries never incremented it.)
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
