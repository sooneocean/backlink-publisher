"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from backlink_publisher.cli._resume import _run_resume  # noqa: F401
from backlink_publisher.cli._dedup_gate import (
    enforce_enabled,
    enforce_precondition_or_exit,
    gate_with_force,
    record_done,
    record_failure,
)
from backlink_publisher.cli._dedup_ops import _handle_dedup_ops, load_force_manifest

from backlink_publisher.config import load_config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
    emit_envelope_and_exit,
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl
from backlink_publisher._util.logger import publish_logger
from backlink_publisher.publishing.adapters import publish as adapter_publish, verify_adapter_setup
from backlink_publisher.publishing.reliability.policy import policy_enabled, publish_with_policy
from ... import checkpoint, config_echo
from ...schema import reject_unsupported_platform, supported_platforms, validate_publish_payload

from backlink_publisher.cli.publish_backlinks._engine import PublishRunState, run_publish_loop
from backlink_publisher.cli._publish_helpers import (
    _acquire_publish_leases,
    _build_failure_row,
    _build_parser,
    _canary_gate,
    _check_row_reachability,
    _check_token_drift,
    _do_verify,
    _error_class,
    _handle_auth_expired,
    _build_skip_row,
    _handle_checkpoint_ops,
    _load_throttle_config,
    _make_banner_emit,
    _maybe_emit_gate_banner,
    _medium_throttle_sleep,
    _publish_epilogue,
    _record_publish_failure,
    _record_publish_path,
    _try_update_ckpt_failed,
)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    from backlink_publisher._util.profiling import profile_if_enabled
    set_log_level(args.log_level)

    _handle_checkpoint_ops(args)
    _handle_dedup_ops(args)

    if args.resume:
        _run_resume(args)
        return

    publish_logger.info("publish-backlinks started", extra={
        "platform": args.platform,
        "mode": args.mode,
        "dry_run": args.dry_run,
    })

    if not args.dry_run:
        _maybe_emit_gate_banner(args.skip_publish_time_check)

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    publish_logger.info(f"processing {len(rows)} payloads")

    config = load_config()
    config_echo.emit_banner(config, "publish-backlinks")

    for idx, row in enumerate(rows, start=1):
        platform = args.platform or row.get("platform", "")
        platform_msg = reject_unsupported_platform(platform)
        if platform_msg is not None:
            emit_error(f"row {idx}: {platform_msg}", exit_code=2)
        errs = validate_publish_payload(row)
        if errs:
            for e in errs:
                publish_logger.warning(f"row {idx}: {e}")
            emit_envelope_and_exit(
                "InputValidationError", 2, f"row {idx}: payload validation failed"
            )

    if args.preview_manifest:
        # Read-only dedup preview over the validated planned rows. Emits verdicts
        # and exits 0 before any lease/checkpoint/dispatch side effect (U3).
        from backlink_publisher.cli.preview_manifest import emit_manifest
        emit_manifest(rows, args.platform)
        raise SystemExit(0)

    forced_keys: set = set()
    if not args.dry_run:
        # R19b: enforce refuses to run until the dedup store covers the
        # back-catalogue (no-op in observe). Checked before acquiring leases so a
        # not-ready run fails fast without holding a platform lease.
        enforce_precondition_or_exit()
        if args.force_manifest:
            # U7c: honor force-flags from a preview manifest (enforce only).
            if not enforce_enabled():
                emit_error(
                    "error: --force-manifest requires "
                    "BACKLINK_PUBLISHER_DEDUP_ENFORCE=1",
                    exit_code=1,
                )
            forced_keys = load_force_manifest(
                args.force_manifest, confirm=args.confirm, reason=args.reason
            )
        platforms_in_use = {
            args.platform or row.get("platform", "") for row in rows
        }
        _acquire_publish_leases(platforms_in_use, False)
        for plat in platforms_in_use:
            if plat not in supported_platforms():
                continue
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    run_id: str | None = None
    checkpoint_disabled = False
    if not args.dry_run:
        try:
            run_id, _ = checkpoint.create_checkpoint(
                rows,
                platform=args.platform,
                mode=args.mode,
                flags={
                    "skip_publish_time_check": args.skip_publish_time_check,
                },
            )
            publish_logger.info(f"publish-backlinks: run_id={run_id}")
        except Exception as exc:
            checkpoint_disabled = True
            publish_logger.warning(
                f"[WARN] checkpoint not created — this run cannot be resumed: {exc}"
            )

    state = PublishRunState(run_id=run_id)
    ts = datetime.now(timezone.utc).isoformat()
    banner_emit = _make_banner_emit()

    throttle_min, throttle_max = _load_throttle_config()

    from backlink_publisher.config import snapshot_token_revs
    initial_token_revs = snapshot_token_revs()

    run_publish_loop(
        rows, args, config, state, ts, banner_emit,
        forced_keys, throttle_min, throttle_max, initial_token_revs,
    )

    if not state.auth_aborted:
        _publish_epilogue(
                state.outputs,
            rows,
            args,
                state.run_id,
                state.success_count,
                state.fail_count,
                state.skipped_unreachable_count,
                state.skipped_quarantined_count,
                state.publish_path_drift_count,
                state.dedup_skip_count,
                state.dedup_hold_count,
                checkpoint_disabled=checkpoint_disabled,
        )


