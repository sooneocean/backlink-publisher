"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from ._resume import _run_resume  # noqa: F401
from ._dedup_gate import (
    enforce_enabled,
    enforce_precondition_or_exit,
    gate_with_force,
    record_done,
    record_failure,
)
from ._dedup_ops import _handle_dedup_ops, load_force_manifest

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
from .. import checkpoint, config_echo
from ..schema import reject_unsupported_platform, supported_platforms, validate_publish_payload

from ._publish_helpers import (
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
)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
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
                print(f"row {idx}: {e}", file=sys.stderr)
            emit_envelope_and_exit(
                "InputValidationError", 2, f"row {idx}: payload validation failed"
            )

    if args.preview_manifest:
        # Read-only dedup preview over the validated planned rows. Emits verdicts
        # and exits 0 before any lease/checkpoint/dispatch side effect (U3).
        from .preview_manifest import emit_manifest
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
            print(f"publish-backlinks: run_id={run_id}", file=sys.stderr, flush=True)
        except Exception as exc:
            print(
                f"[WARN] checkpoint not created — this run cannot be resumed: {exc}",
                file=sys.stderr,
            )

    outputs: list[dict[str, Any]] = []
    ts = datetime.now(timezone.utc).isoformat()
    success_count = 0
    fail_count = 0
    banner_emit = _make_banner_emit()
    skipped_unreachable_count = 0
    skipped_quarantined_count = 0
    publish_path_drift_count = 0
    canary_warned: set[str] = set()  # dedup advisory WARNINGs per platform per run
    dedup_skip_count = 0
    dedup_hold_count = 0
    last_medium_success_idx: int = -1

    throttle_min, throttle_max = _load_throttle_config()

    from backlink_publisher.config import snapshot_token_revs
    initial_token_revs = snapshot_token_revs()

    for row_idx, row in enumerate(rows):
        _medium_throttle_sleep(
            row_idx, last_medium_success_idx,
            args.platform or row.get("platform", ""),
            throttle_min, throttle_max,
            dry_run=args.dry_run,
        )

        platform = args.platform or row.get("platform", "")
        mode = args.mode or row.get("publish_mode", "draft")

        # Canary health gate (Plan 2026-05-27-001 Unit 4): advisory WARNING is
        # the dominant path; opt-in (hard_skip=true) + quarantined → filter the
        # row out of the payload. Fail-open: no canary health → proceeds.
        canary_skip, canary_reason = _canary_gate(
            platform, warned=canary_warned
        )
        if canary_skip:
            # Opt-in hard-skip is a deliberate advisory filter, NOT a publish
            # failure: the row is dropped from the payload (never published) but
            # must not be appended to ``outputs`` with an error — that would let
            # ``_publish_epilogue`` count it as failed and exit 4 on every run
            # for a platform the operator intentionally quarantined. Surface it
            # as a stderr WARNING + recon count instead.
            row_id = row.get("id", "")
            publish_logger.warn(
                f"[publish-backlinks] row_id={row_id} platform={platform} "
                f"status=skipped_quarantined — {canary_reason}"
            )
            skipped_quarantined_count += 1
            continue

        if not args.dry_run and not args.skip_publish_time_check:
            ok, failing_url = _check_row_reachability(row)
            if not ok:
                row_id = row.get("id", "")
                publish_logger.warn(
                    f"[publish-backlinks] row_id={row_id} "
                    f"status=skipped_unreachable url={failing_url}"
                )
                outputs.append(_build_failure_row(
                    "skipped_unreachable", row, platform,
                    f"target unreachable at publish-time: {failing_url}",
                    ts,
                    failing_url=failing_url,
                ))
                skipped_unreachable_count += 1
                continue

        if platform not in supported_platforms():
            outputs.append(_build_failure_row(
                "failed", row, platform,
                f"unsupported platform: {platform}",
                ts, adapter=platform,
            ))
            fail_count += 1
            continue

        if args.dry_run:
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=True,
            )
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": result.status,
                "title": row.get("title", ""),
                "draft_url": result.draft_url,
                "published_url": result.published_url,
                "created_at": ts,
                "adapter": result.adapter,
                "error": None,
                "_dry_run": True,
                "_command": result._command,
            })
            success_count += 1
            publish_logger.debug(
                f"dry-run: {platform} id={row.get('id', '')}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue

        publish_logger.info(
            f"publishing: {platform} id={row.get('id', '')}",
            extra={"id": row.get("id"), "platform": platform, "mode": mode},
        )

        # Dedup gate (U2 observe / U7 enforce). Observe: records intent, always
        # dispatch. Enforce: done->skip, uncertain/live-attempting->hold,
        # absent/failed/stale-attempting->claim+dispatch (fail-closed). A manifest
        # force-flag (U7c) overrides a hold; a force on a done key conflicts (exit 1).
        # Token-drift check BEFORE the gate claim: in enforce mode gate_with_force
        # claims the row (-> attempting), and _check_token_drift raises SystemExit
        # on revocation — running it first avoids stranding a just-claimed row in
        # `attempting` (a BaseException bypasses the per-row except arms). Token
        # revs are a run-level snapshot, so checking before the gate is equivalent.
        _check_token_drift(initial_token_revs)

        verdict, drec = gate_with_force(
            row, platform, run_id=run_id, forced_keys=forced_keys, reason=args.reason
        )
        if verdict == "skip":
            outputs.append(_build_skip_row(
                row, platform, drec.live_url if drec else None, ts
            ))
            dedup_skip_count += 1
            publish_logger.info(
                f"dedup skip (already published): {platform} id={row.get('id', '')}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue
        if verdict == "hold":
            dedup_hold_count += 1
            publish_logger.warn(
                f"dedup hold (uncertain/in-flight): {platform} id={row.get('id', '')}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue

        try:
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
        except AuthExpiredError as exc:
            record_failure(row, platform, error_class="auth_expired", run_id=run_id)
            _handle_auth_expired(exc, run_id, row, publish_logger)
            return
        except BannerUploadError as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                "banner_upload", f"banner upload failed: {exc}",
            )
            continue
        except ContentRejectedError as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                "content_rejected", f"content rejected: {exc}",
            )
            continue
        except DependencyError as exc:
            record_failure(row, platform, error_class="dependency", run_id=run_id)
            emit_error(str(exc), exit_code=3)
            return
        except ExternalServiceError as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                _error_class(exc), f"service error: {exc}",
            )
            continue
        except Exception as exc:
            fail_count += 1
            run_id = _record_publish_failure(
                outputs, row, platform, ts, run_id, exc,
                "unexpected", f"unexpected error: {exc}",
            )
            continue

        outputs.append(result.to_publish_output(row, ts))
        if result.error:
            fail_count += 1
            # In-band adapter failure (returned, not raised): record terminal so the
            # attempting row does not orphan. No exception => not http_5xx => failed.
            record_failure(row, platform, error_class=None, run_id=run_id)
        else:
            success_count += 1
            if result.post_publish_delay_seconds > 0:
                last_medium_success_idx = row_idx

            # U3: advisory forward-path drift recording (Plan 2026-05-27-006).
            # Never gating — only records to canary-health.json + WARN on drift.
            publish_path_drift_count += _record_publish_path(platform, result, row)

            verify_ok, verify_reason = _do_verify(
                args.no_verify, args.dry_run, result, row
            )
            if not verify_ok:
                outputs[-1]["status"] += "_unverified"
                publish_logger.warn(
                    f"verification failed: id={row.get('id', '')} reason={verify_reason}",
                    extra={"id": row.get("id"), "adapter": result.adapter},
                )

            # Observe-only dedup terminal (U2): record done + verify flag. verify_ok is
            # orthogonal to dedup identity — a verify flake leaves the key done.
            record_done(
                row, platform,
                live_url=result.published_url or result.draft_url,
                verify_ok=verify_ok,
                run_id=run_id,
            )

            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "done",
                        published_url=result.published_url,
                        article_urls=[u for u in (result.published_url, result.draft_url) if u],
                        adapter=result.adapter,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                        verified=verify_ok,
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
                    run_id = None
            publish_logger.info(
                f"published: id={row.get('id', '')} status={result.status}",
                extra={"id": row.get("id"), "status": result.status},
            )

    _publish_epilogue(
        outputs,
        rows,
        args,
        run_id,
        success_count,
        fail_count,
        skipped_unreachable_count,
        skipped_quarantined_count,
        publish_path_drift_count,
        dedup_skip_count,
        dedup_hold_count,
    )


if __name__ == "__main__":
    main()
