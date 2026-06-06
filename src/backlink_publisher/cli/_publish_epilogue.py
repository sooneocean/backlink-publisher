"""Publish epilogue for publish-backlinks CLI.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC budget.
Provides the final output emission and exit-code logic after the publish loop.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any


def _publish_epilogue(
    outputs: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    args: Any,
    run_id: str | None,
    success_count: int,
    fail_count: int,
    skipped_unreachable_count: int,
    skipped_quarantined_count: int = 0,
    publish_path_drift_count: int = 0,
    dedup_skip_count: int = 0,
    dedup_hold_count: int = 0,
    checkpoint_disabled: bool = False,
) -> None:
    # Phase 1: projection.
    if run_id is not None:
        from backlink_publisher.events import project_run_safe as _project_run_safe
        _project_run_safe(run_id)

    # Phase 2: reconciler (always runs, RECON.log always written).
    from backlink_publisher.cli._publish_reconciler import (
        _run_reconciler,
        _write_reconciler_report,
    )
    reconciler_summary = _run_reconciler(args)

    # R18/U7 dedup reconciliation line — counts only, no campaign URLs. Always
    # emitted (zeros in observe) so the signal is uniform; RECON level per
    # [[recon-log-level-for-always-on-signals]].
    dispatched = sum(
        1 for r in outputs if r.get("_dedup_verdict") != "skip"
    )
    from backlink_publisher._util.logger import publish_logger
    publish_logger.recon(
        "dedup_reconciliation",
        skipped_already_published=dedup_skip_count,
        held_uncertain=dedup_hold_count,
        dispatched=dispatched,
        skipped_canary=skipped_quarantined_count,
    )

    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]
    unverified = [s for s in successful if s.get("status", "").endswith("_unverified")]

    recon_extra: dict[str, Any] = {}
    if checkpoint_disabled:
        recon_extra["checkpoint_disabled"] = True
    publish_logger.recon(
        "publish_reconciliation",
        input_payloads=len(rows),
        output_rows=len(successful),
        delta=len(rows) - len(successful),
        dropped={
            "failed": len(failed),
            "unverified": len(unverified),
        },
        dropped_ids={
            "failed": [r.get("id", "") for r in failed],
            "unverified": [r.get("id", "") for r in unverified],
        },
        **recon_extra,
    )

    if successful:
        from backlink_publisher._util.jsonl import write_jsonl
        write_jsonl(successful)

    _write_reconciler_report(reconciler_summary)

    from backlink_publisher._util.errors import emit_envelope_and_exit, emit_error

    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        emit_envelope_and_exit(
            "ExternalServiceError", 4, f"{len(failed)} payload(s) failed to publish"
        )

    if not args.dry_run and not successful:
        if dedup_hold_count > 0:
            # Enforce held every row (uncertain/in-flight) — this is operator-action
            # required (adjudicate the holds), not an internal error. Exit 3
            # (DependencyError), not 5.
            emit_error(
                f"all {dedup_hold_count} row(s) held by the dedup gate "
                "(uncertain/in-flight); adjudicate with --list-uncertain / "
                "--adjudicate-uncertain, then re-run",
                exit_code=3,
            )
        emit_error("no payloads were published", exit_code=5)

    if unverified:
        for u in unverified:
            print(
                f"verification failed: id={u.get('id', '')} status={u.get('status', '')}",
                file=sys.stderr,
            )
        emit_envelope_and_exit(
            "InternalError", 5, f"{len(unverified)} payload(s) failed verification"
        )

    publish_logger.info(
        f"publish complete: {success_count} succeeded, {fail_count} failed, "
        f"{skipped_unreachable_count} skipped_unreachable, "
        f"{skipped_quarantined_count} skipped_quarantined, "
        f"{publish_path_drift_count} publish_path_drift",
        extra={
            "success": success_count,
            "failed": fail_count,
            "skipped_unreachable": skipped_unreachable_count,
            "skipped_quarantined": skipped_quarantined_count,
            "publish_path_drift_count": publish_path_drift_count,
        },
    )