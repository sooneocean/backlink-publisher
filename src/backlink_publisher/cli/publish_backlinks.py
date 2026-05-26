"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from ._resume import _run_resume  # noqa: F401

from backlink_publisher.config import load_config
from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
    ContentRejectedError,
    DependencyError,
    ExternalServiceError,
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
    _check_row_reachability,
    _check_token_drift,
    _do_verify,
    _error_class,
    _handle_auth_expired,
    _handle_checkpoint_ops,
    _load_throttle_config,
    _make_banner_emit,
    _maybe_emit_gate_banner,
    _medium_throttle_sleep,
    _publish_epilogue,
    _record_publish_failure,
)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    _handle_checkpoint_ops(args)

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
    )


if __name__ == "__main__":
    main()
