"""Read-time reconciler: cross-reference checkpoints ↔ dedup store.

Runs inside the publish-backlinks same pass (R1). After all articles are
published and projected, the reconciler scans all checkpoint items with
status ``pending``/``failed`` and cross-references them against the
dedup store. Items that have a matching ``done`` dedup record are
auto-fixed to ``done`` (R2). Items with no matching dedup record are
quarantined as ``reconcile_gap`` (R3). Previously-quarantined items that
now have a dedup match are cleared (R8). Items already in quarantine are
skipped (R10).

A reverse check compares ``publish-history.json`` published URLs against
the dedup store — report-only (R4).

All events are written to RECON.log (R5).

Plan: docs/plans/2026-05-28-004-feat-readtime-reconciliation-hub-plan.md
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .._util.url import canonicalize_url as _canonicalize_url_fn
from ..checkpoint import list_failed_items, update_item as _update_checkpoint_item
from ..config import _cache_dir, _config_dir
from ..idempotency import DedupKey, DedupStore
from ._project_helpers import _HISTORY_FILENAME
from .store import EventStore

_log = logging.getLogger(__name__)

#: Sentinal canonical URL for checkpoint items that cannot be canonicalized.
_UNPARSEABLE_URL = "__unparseable__"


@dataclass
class ReconciliationSummary:
    """Aggregate outcome of one reconciler pass."""

    auto_fixed: int = 0
    quarantined: int = 0
    cleared: int = 0
    history_gaps: int = 0
    history_checked: int = 0
    total_checkpoints: int = 0
    skipped_quarantined: int = 0


# -------------------------------------------------------------------------- #
# RECON.log writer
# -------------------------------------------------------------------------- #

_RECON_LOG_FILENAME = "RECON.log"


def _recon_log_path() -> Path:
    return _cache_dir() / _RECON_LOG_FILENAME


def _log_recon_event(event_type: str, **fields: Any) -> None:
    """Append one structured line to RECON.log. Best-effort (never raises)."""
    try:
        ts = datetime.now(timezone.utc).isoformat()
        line = json.dumps({
            "ts": ts,
            "event": event_type,
            **fields,
        }, sort_keys=True)
        path = _recon_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(path), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        _log.warning("RECON.log write failed: %s", exc)


# -------------------------------------------------------------------------- #
# Core reconciler
# -------------------------------------------------------------------------- #


def _canonicalize_url(url: str | None) -> str | None:
    """Wrap ``canonicalize_url`` to return ``None`` on error."""
    if not url:
        return None
    try:
        return _canonicalize_url_fn(url)
    except Exception as exc:
        _log.debug("canonicalize_url failed for %r: %s", url, exc)
        return None


def _reconcile_checkpoints(
    store: EventStore,
    dedup_store: DedupStore,
) -> ReconciliationSummary:
    """Cross-reference pending/failed checkpoint items against the dedup store.

    This is the core reconciler pass (R2/R3/R8/R10). It runs inside the
    ``EventStore`` context so that ``_quarantine``/``_clear_quarantine``
    writes share the same lock as projection.

    Returns a ``ReconciliationSummary`` with counts of outcomes.
    """
    from .reconcile import (
        _clear_quarantine_by_dedup_key,
        _get_reconciler_quarantine_set,
        _quarantine,
    )

    summary = ReconciliationSummary()
    items = list_failed_items()
    summary.total_checkpoints = len(items)

    if not items:
        return summary

    # R10: build skip set from existing reconciler-gap quarantines.
    skip_set = _get_reconciler_quarantine_set(store)

    for item in items:
        platform = item.get("platform") or ""
        target_url = item.get("payload", {}).get("target_url") or ""
        item_id = item.get("id", "")
        run_id = item.get("_run_id", "")
        item_status = item.get("status", "")

        canon = _canonicalize_url(target_url)
        if canon is None or not platform:
            # Missing or unparseable — cannot build a DedupKey.
            _log_recon_event(
                "reconciler_skip",
                item_id=item_id,
                run_id=run_id,
                reason="missing_platform_or_url",
                status=item_status,
            )
            continue

        # R10: skip if this URL is already in quarantine.
        if canon in skip_set:
            summary.skipped_quarantined += 1
            continue

        try:
            key = DedupKey(platform=platform, target_url=canon)
        except Exception as exc:
            _log_recon_event(
                "reconciler_skip",
                item_id=item_id,
                run_id=run_id,
                reason=f"dedup_key_construction_failed: {exc}",
            )
            continue

        try:
            record = dedup_store.get(key)
        except Exception as exc:
            _log.warning(
                "reconciler: dedup_store.get failed for %s: %s",
                key.as_tuple(), exc,
            )
            continue

        if record is not None and record.state == "done":
            # R2: auto-fix — checkpoint item has a matching done dedup record.
            try:
                _update_checkpoint_item(run_id, item_id, "done")
                summary.auto_fixed += 1
                _log_recon_event(
                    "reconciler_auto_fix",
                    item_id=item_id,
                    run_id=run_id,
                    url=canon,
                    old_status=item_status,
                    new_status="done",
                    platform=platform,
                )
                # R8: clear any stale quarantine entry for this URL.
                _clear_quarantine_by_dedup_key(store, canon)
                if canons := record.target_url or canon:
                    summary.cleared += 1
            except Exception as exc:
                _log.warning(
                    "reconciler: auto-fix failed for item %s/%s: %s",
                    run_id, item_id, exc,
                )
        else:
            # R3 (stale gap) vs keep-for-next-run (recent). Items that have
            # been pending for a while get quarantined; recent ones are left
            # for the next reconciler pass.
            created_at = item.get("completed_at") or item.get("created_at")
            if created_at:
                try:
                    age_seconds = (
                        datetime.now(timezone.utc) -
                        datetime.fromisoformat(created_at)
                    ).total_seconds()
                except (ValueError, TypeError):
                    age_seconds = 0
            else:
                age_seconds = 0

            age_threshold = 86400  # 24 hours in seconds

            if age_seconds >= age_threshold:
                # Stale gap -> quarantine.
                source = f"reconciler:{canon}"
                reason = (
                    f"checkpoint item {item_id} is {item_status} "
                    f"but no done dedup record found (age={age_seconds:.0f}s)"
                )
                _quarantine(
                    store,
                    source=source,
                    reason=reason,
                    row_id=item_id,
                    run_id=run_id,
                    dedup_key=canon,
                )
                summary.quarantined += 1
                _log_recon_event(
                    "reconciler_gap",
                    item_id=item_id,
                    run_id=run_id,
                    url=canon,
                    status=item_status,
                    age_seconds=age_seconds,
                    platform=platform,
                )

    return summary


def _reconcile_history(
    store: EventStore,
    dedup_store: DedupStore,
) -> tuple[int, int]:
    """Cross-reference publish-history.json published URLs against dedup.

    Report-only (R4): missing entries are logged to RECON.log but no
    state change is made. Returns (gaps, checked).
    """
    history_path = _config_dir() / _HISTORY_FILENAME
    if not history_path.exists():
        return (0, 0)

    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("reconciler: could not read history: %s", exc)
        return (0, 0)

    # Normalise history to a list of entries.
    entries: list[dict[str, Any]]
    if isinstance(history, dict):
        entries = [history]
    elif isinstance(history, list):
        entries = history
    else:
        entries = []

    gaps = 0
    checked = 0

    for entry in entries:
        if entry.get("status") != "published":
            continue
        article_urls = entry.get("article_urls", [])
        if not article_urls:
            continue

        platform = entry.get("platform", "")
        if not platform:
            platform = "unknown"

        for url in article_urls:
            if not url:
                continue
            canon = _canonicalize_url(url)
            if canon is None:
                continue
            checked += 1

            try:
                key = DedupKey(platform=platform, target_url=canon)
            except Exception:
                continue

            try:
                record = dedup_store.get(key)
            except Exception:
                continue

            if record is None or record.state != "done":
                gaps += 1
                _log_recon_event(
                    "reconciler_history_gap",
                    history_id=entry.get("id", ""),
                    url=canon,
                    published_at=entry.get("published_at", entry.get("created_at", "")),
                    platform=platform,
                )

    # Log summary line.
    _log_recon_event(
        "reconciler_history_summary",
        checked=checked,
        gaps=gaps,
    )

    return gaps, checked


def reconcile_all(
    *,
    store: EventStore | None = None,
    dedup_store: DedupStore | None = None,
) -> ReconciliationSummary:
    """Run the full reconciler pass (checkpoints + history).

    Creates its own EventStore and DedupStore if not provided. Never
    raises — errors degrade to partial results. Safe to call during or
    after any publish run.

    Returns a ``ReconciliationSummary`` with counts of all outcomes.
    """
    summary = ReconciliationSummary()
    _store = store or EventStore()
    _dedup_store = dedup_store or DedupStore()

    try:
        # Phase 1: checkpoint ← dedup auto-fix.
        ckpt_summary = _reconcile_checkpoints(_store, _dedup_store)
        summary.auto_fixed = ckpt_summary.auto_fixed
        summary.quarantined = ckpt_summary.quarantined
        summary.cleared = ckpt_summary.cleared
        summary.total_checkpoints = ckpt_summary.total_checkpoints
        summary.skipped_quarantined = ckpt_summary.skipped_quarantined

        # Phase 2: history reverse-check.
        gaps, checked = _reconcile_history(_store, _dedup_store)
        summary.history_gaps = gaps
        summary.history_checked = checked

    except Exception as exc:
        _log.warning("reconciler: reconcile_all failed (partial result): %s", exc)

    return summary
