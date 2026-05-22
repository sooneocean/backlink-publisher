"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from typing import Any

from ._resume import _run_resume, item_to_publish_output

from backlink_publisher.config import load_config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import publish_logger
from backlink_publisher.publishing.adapters import publish as adapter_publish, verify_adapter_setup
from backlink_publisher.publishing.registry import registered_platforms
from .. import checkpoint, config_echo
from ..schema import reject_unsupported_platform, supported_platforms, validate_publish_payload

from ._publish_helpers import (
    _acquire_publish_leases,
    _build_failure_row,
    _check_row_reachability,
    _check_token_drift,
    _do_verify,
    _error_class,
    _load_throttle_config,
    _make_banner_emit,
    _maybe_emit_gate_banner,
    _medium_throttle_sleep,
    _record_publish_failure,
)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="publish-backlinks",
        description="Publish validated backlink payloads.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--platform",
        choices=registered_platforms(),
        default=None,
        help="Target platform (overrides per-row platform)",
    )
    parser.add_argument(
        "--mode",
        choices=["draft", "publish"],
        default="draft",
        help="Publish mode (default: draft)",
    )
    parser.add_argument(
        "--opencli-profile",
        default=None,
        help="Deprecated. Has no effect (OpenCLI removed).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print command plans without executing",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        default=False,
        help="Deprecated. Has no effect.",
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help="Resume an interrupted batch run from checkpoint",
    )
    parser.add_argument(
        "--list-runs",
        action="store_true",
        default=False,
        help="List incomplete checkpoint runs and exit",
    )
    parser.add_argument(
        "--cleanup",
        default=None,
        metavar="RUN_ID",
        help="Delete a specific checkpoint and exit",
    )
    parser.add_argument(
        "--cleanup-all",
        action="store_true",
        default=False,
        help="Delete all complete checkpoints and exit",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        default=False,
        help="Skip post-publish content verification (default: verify after each publish)",
    )
    parser.add_argument(
        "--skip-publish-time-check",
        action="store_true",
        default=False,
        help=(
            "Skip publish-time URL reachability re-check (default: re-check "
            "each row's target_url and links before dispatch). Per plan "
            "2026-05-14-001 R10: this is independent of validate-time's "
            "--no-validate-url-check; setting one does not affect the other."
        ),
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    exclusive = [args.resume, args.list_runs, args.cleanup, args.cleanup_all]
    if sum(bool(x) for x in exclusive) > 1:
        emit_error(
            "error: --resume, --list-runs, --cleanup, and --cleanup-all are mutually exclusive",
            exit_code=2,
        )

    # ── Housekeeping short-circuits ───────────────────────────────────────

    if args.list_runs:
        runs = checkpoint.list_incomplete()
        if not runs:
            print("No incomplete runs.")
        else:
            print(f"{'RUN_ID':<32}  {'STARTED':<26}  {'PENDING':>7}  {'FAILED':>7}")
            print("-" * 76)
            for run in runs:
                pending = sum(1 for i in run["items"] if i["status"] == "pending")
                failed = sum(1 for i in run["items"] if i["status"] == "failed")
                print(f"{run['run_id']:<32}  {run.get('started_at', ''):<26}  {pending:>7}  {failed:>7}")
        raise SystemExit(0)

    if args.cleanup:
        try:
            checkpoint.delete(args.cleanup)
            print(f"Deleted checkpoint: {args.cleanup}")
        except (ValueError, FileNotFoundError) as exc:
            emit_error(str(exc), exit_code=2)
        raise SystemExit(0)

    if args.cleanup_all:
        count = checkpoint.delete_complete()
        print(f"Deleted {count} complete checkpoint(s).")
        raise SystemExit(0)

    # ── Resume path ───────────────────────────────────────────────────────

    if args.resume:
        from ._resume import _run_resume
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
            raise SystemExit(2)

    if not args.dry_run:
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

        try:
            _check_token_drift(initial_token_revs)
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
                banner_emit=banner_emit,
            )
        except AuthExpiredError as exc:
            try:
                from webui_store.channel_status import mark_expired
                mark_expired(exc.channel)
            except Exception as flip_exc:
                publish_logger.warning(
                    f"mark_expired({exc.channel!r}) failed: {flip_exc}"
                )
            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "failed",
                        error=str(exc),
                        error_class="auth_expired",
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
            publish_logger.error(
                f"auth expired: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            emit_error(str(exc), exit_code=3)
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
        else:
            success_count += 1
            if result.post_publish_delay_seconds > 0:
                last_medium_success_idx = row_idx

            verify_ok, verify_reason = _do_verify(
                args.no_verify, args.dry_run, result, row
            )
            if not verify_ok:
                outputs[-1]["status"] += "_unverified"
                publish_logger.warn(
                    f"verification failed: id={row.get('id', '')} reason={verify_reason}",
                    extra={"id": row.get("id"), "adapter": result.adapter},
                )

            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "done",
                        published_url=result.published_url,
                        article_urls=[u for u in (result.published_url, result.draft_url) if u],
                        adapter=result.adapter,
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
                    run_id = None
            publish_logger.info(
                f"published: id={row.get('id', '')} status={result.status}",
                extra={"id": row.get("id"), "status": result.status},
            )

    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]
    unverified = [r for r in successful if r.get("status", "").endswith("_unverified")]

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
    )

    if successful:
        write_jsonl(successful)

    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        raise SystemExit(4)

    if not args.dry_run and not successful:
        emit_error("no payloads were published", exit_code=5)

    if unverified:
        for u in unverified:
            print(
                f"verification failed: id={u.get('id', '')} status={u.get('status', '')}",
                file=sys.stderr,
            )
        raise SystemExit(5)

    publish_logger.info(
        f"publish complete: {success_count} succeeded, {fail_count} failed, "
        f"{skipped_unreachable_count} skipped_unreachable",
        extra={
            "success": success_count,
            "failed": fail_count,
            "skipped_unreachable": skipped_unreachable_count,
        },
    )


if __name__ == "__main__":
    main()
