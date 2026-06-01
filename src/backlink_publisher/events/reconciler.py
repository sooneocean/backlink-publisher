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

#: Age (seconds) past which a pending/failed checkpoint item with no matching
#: ``done`` dedup record is treated as a stale gap and quarantined (R3). Younger
#: items are left for the next reconciler pass. 24 hours.
_STALE_GAP_THRESHOLD_S = 86400


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


@dataclass
class _CheckpointProbe:
    """A checkpoint item resolved to a dedup key in pass 1, carried into pass 2.

    The reconciler splits its work so every dedup read happens in one batch:
    pass 1 turns each raw checkpoint item into a probe (or drops it), pass 2
    cross-references each probe against the batch-read dedup records. Carrying
    the already-computed fields here avoids recomputing canon/platform/age inputs
    in pass 2.
    """

    item_id: str
    run_id: str
    item_status: str
    platform: str
    canon: str
    created_at: str | None
    key: DedupKey


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


def _resolve_checkpoint_probe(
    item: dict[str, Any],
    skip_set: set[str],
    summary: ReconciliationSummary,
) -> _CheckpointProbe | None:
    """Pass 1: turn a raw checkpoint item into a :class:`_CheckpointProbe`, or
    return ``None`` when it cannot be cross-referenced.

    Mirrors the original per-item front half exactly: unparseable URL / missing
    platform and dedup-key-construction failures are logged as ``reconciler_skip``
    and dropped; an already-quarantined URL (R10) is counted into
    ``skipped_quarantined`` and dropped. No dedup read happens here — that is
    batched after every probe is collected.
    """
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
        return None

    # R10: skip if this URL is already in quarantine.
    if canon in skip_set:
        summary.skipped_quarantined += 1
        return None

    try:
        key = DedupKey(platform=platform, target_url=canon)
    except Exception as exc:
        _log_recon_event(
            "reconciler_skip",
            item_id=item_id,
            run_id=run_id,
            reason=f"dedup_key_construction_failed: {exc}",
        )
        return None

    return _CheckpointProbe(
        item_id=item_id,
        run_id=run_id,
        item_status=item_status,
        platform=platform,
        canon=canon,
        created_at=item.get("completed_at") or item.get("created_at"),
        key=key,
    )


def _auto_fix_checkpoint(
    store: EventStore,
    probe: _CheckpointProbe,
    summary: ReconciliationSummary,
) -> None:
    """R2: a matching ``done`` dedup record exists — settle the checkpoint item
    to ``done`` and clear any stale quarantine entry for its URL (R8).

    ``cleared`` increments once per auto-fix (one clear attempt per settled
    item), preserving the original pass's count.
    """
    from .reconcile import _clear_quarantine_by_dedup_key

    try:
        _update_checkpoint_item(probe.run_id, probe.item_id, "done")
        summary.auto_fixed += 1
        _log_recon_event(
            "reconciler_auto_fix",
            item_id=probe.item_id,
            run_id=probe.run_id,
            url=probe.canon,
            old_status=probe.item_status,
            new_status="done",
            platform=probe.platform,
        )
        # R8: clear any stale quarantine entry for this URL.
        _clear_quarantine_by_dedup_key(store, probe.canon)
        summary.cleared += 1
    except Exception as exc:
        _log.warning(
            "reconciler: auto-fix failed for item %s/%s: %s",
            probe.run_id, probe.item_id, exc,
        )


def _quarantine_if_stale(
    store: EventStore,
    probe: _CheckpointProbe,
    summary: ReconciliationSummary,
) -> None:
    """R3: no matching ``done`` dedup record. Quarantine the item once it has
    aged past :data:`_STALE_GAP_THRESHOLD_S`; recent items are left for the next
    reconciler pass."""
    if probe.created_at:
        try:
            age_seconds = (
                datetime.now(timezone.utc) -
                datetime.fromisoformat(probe.created_at)
            ).total_seconds()
        except (ValueError, TypeError):
            age_seconds = 0
    else:
        age_seconds = 0

    if age_seconds < _STALE_GAP_THRESHOLD_S:
        return

    from .reconcile import _quarantine

    source = f"reconciler:{probe.canon}"
    reason = (
        f"checkpoint item {probe.item_id} is {probe.item_status} "
        f"but no done dedup record found (age={age_seconds:.0f}s)"
    )
    _quarantine(
        store,
        source=source,
        reason=reason,
        row_id=probe.item_id,
        run_id=probe.run_id,
        dedup_key=probe.canon,
    )
    summary.quarantined += 1
    _log_recon_event(
        "reconciler_gap",
        item_id=probe.item_id,
        run_id=probe.run_id,
        url=probe.canon,
        status=probe.item_status,
        age_seconds=age_seconds,
        platform=probe.platform,
    )


def _reconcile_checkpoints(
    store: EventStore,
    dedup_store: DedupStore,
) -> ReconciliationSummary:
    """Cross-reference pending/failed checkpoint items against the dedup store.

    This is the core reconciler pass (R2/R3/R8/R10). It runs inside the
    ``EventStore`` context so that ``_quarantine``/``_clear_quarantine``
    writes share the same lock as projection.

    Two passes so every dedup read is one batch instead of one connection per
    item: pass 1 resolves each item to a probe (or drops it), then a single
    ``get_many`` reads all records, then pass 2 cross-references each probe.

    Returns a ``ReconciliationSummary`` with counts of outcomes.
    """
    from .reconcile import _get_reconciler_quarantine_set

    summary = ReconciliationSummary()
    items = list_failed_items()
    summary.total_checkpoints = len(items)

    if not items:
        return summary

    # R10: build skip set from existing reconciler-gap quarantines.
    skip_set = _get_reconciler_quarantine_set(store)

    # Pass 1: resolve items to probes (logging/counting the ones that drop out).
    pending = [
        probe
        for item in items
        if (probe := _resolve_checkpoint_probe(item, skip_set, summary)) is not None
    ]

    # Single batched dedup read for the whole pass. On a read failure, leave
    # every probe for the next pass — matching the original per-item behavior of
    # neither auto-fixing nor quarantining an item whose dedup read failed.
    try:
        records = dedup_store.get_many(probe.key for probe in pending)
    except Exception as exc:
        _log.warning(
            "reconciler: batch dedup read failed; skipping cross-reference: %s", exc
        )
        return summary

    # Pass 2: cross-reference each probe against its (batch-read) dedup record.
    for probe in pending:
        record = records.get(probe.key.as_tuple())
        if record is not None and record.state == "done":
            _auto_fix_checkpoint(store, probe, summary)
        else:
            _quarantine_if_stale(store, probe, summary)

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

    # Pass 1: collect every published URL as a (entry, platform, canon, key)
    # probe and count it as checked. No dedup read here — batched below.
    probes: list[tuple[dict[str, Any], str, str, DedupKey]] = []
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

            probes.append((entry, platform, canon, key))

    # Single batched dedup read. On a read failure, report zero gaps (matching
    # the original per-URL behavior of skipping a URL whose dedup read failed)
    # rather than over-reporting every checked URL as a gap.
    try:
        records = dedup_store.get_many(key for _, _, _, key in probes)
    except Exception as exc:
        _log.warning("reconciler: history batch dedup read failed: %s", exc)
        _log_recon_event("reconciler_history_summary", checked=checked, gaps=0)
        return 0, checked

    # Pass 2: a published URL with no matching done dedup record is a gap (R4).
    for entry, platform, canon, key in probes:
        record = records.get(key.as_tuple())
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
