"""Reconciler helpers for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides reconciler summary emission and epilogue logic.
"""

from __future__ import annotations

import sys
from typing import Any


def _run_reconciler(args: Any) -> dict[str, Any] | None:
    if args.dry_run:
        return None
    if not args.reconcile and not args.reconcile_all:
        return None

    try:
        from backlink_publisher.events.reconciler import reconcile_all

        summary = reconcile_all()

        return {
            "event": "reconciler_summary",
            "auto_fixed": summary.auto_fixed,
            "quarantined": summary.quarantined,
            "cleared": summary.cleared,
            "history_gaps": summary.history_gaps,
            "history_checked": summary.history_checked,
            "total_checkpoints": summary.total_checkpoints,
            "skipped_quarantined": summary.skipped_quarantined,
        }
    except Exception as exc:
        from backlink_publisher._util.logger import publish_logger
        publish_logger.warning("reconciler pass failed: %s", exc)
        return None


def _write_reconciler_report(summary: dict[str, Any] | None) -> None:
    if summary is None:
        return
    try:
        import json as _json
        print(_json.dumps(summary, sort_keys=True))
    except Exception as exc:
        from backlink_publisher._util.logger import publish_logger
        publish_logger.warning("reconciler report write failed: %s", exc)