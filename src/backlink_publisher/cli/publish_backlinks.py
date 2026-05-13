"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any

_MEDIUM_ADAPTERS = {"medium-api", "medium-browser"}
_HTTP_5XX_RE = re.compile(r"\b5[0-9]{2}\b")

from ..adapters import publish as adapter_publish, verify_adapter_setup
from .. import checkpoint
from ..config import load_config
from ..errors import DependencyError, ExternalServiceError, emit_error
from ..jsonl import read_jsonl, write_jsonl
from ..logger import publish_logger
from ..schema import SUPPORTED_PLATFORMS, validate_publish_payload


def _error_class(exc: Exception) -> str:
    msg = str(exc)
    if _HTTP_5XX_RE.search(msg):
        return "http_5xx"
    if isinstance(exc, ExternalServiceError):
        return "transient"
    return "unexpected"


def item_to_publish_output(item: dict[str, Any]) -> dict[str, Any]:
    """Convert a done checkpoint item to the standard 9-field publish output shape."""
    return {
        "id": item.get("id", ""),
        "platform": item.get("platform", ""),
        "status": item.get("status", ""),
        "title": item.get("title", ""),
        "draft_url": "",
        "published_url": item.get("published_url") or "",
        "created_at": item.get("completed_at", ""),
        "adapter": item.get("adapter") or "",
        "error": None,
    }


def _run_resume(args: Any) -> None:
    """Handle --resume <run_id>: load checkpoint, process pending/failed items, emit union output."""
    run_id = args.resume

    try:
        ckpt = checkpoint.load_checkpoint(run_id)
    except (ValueError, FileNotFoundError) as exc:
        emit_error(str(exc), exit_code=2)
        return

    config = load_config()

    # verify adapter setup for platforms in checkpoint
    platforms_in_ckpt = {item["platform"] for item in ckpt["items"] if item.get("platform")}
    for plat in platforms_in_ckpt:
        if plat in SUPPORTED_PLATFORMS:
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    to_process = [item for item in ckpt["items"] if item["status"] in ("pending", "failed")]

    # warn on http_5xx items
    for item in to_process:
        if item.get("error_class") == "http_5xx":
            print(
                f"WARNING: item {item['id']} failed with HTTP 5xx — "
                f"post may already be live on {item['platform']}. Verify before resuming.",
                file=sys.stderr,
            )

    # all done: emit union and mark complete
    if not to_process:
        all_done = [item_to_publish_output(i) for i in ckpt["items"] if i["status"] == "done"]
        write_jsonl(all_done)
        sys.stdout.flush()
        checkpoint.mark_complete(run_id)
        raise SystemExit(0)

    # R8: find last done Medium item and compute elapsed
    throttle_min = int(os.environ.get("MEDIUM_THROTTLE_MIN", "60"))
    throttle_max = int(os.environ.get("MEDIUM_THROTTLE_MAX", "300"))
    resume_elapsed_skip_throttle = False
    for item in ckpt["items"]:
        if item["status"] == "done" and item.get("adapter") in _MEDIUM_ADAPTERS:
            try:
                last_ts = datetime.fromisoformat(item["completed_at"])
                elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
                if elapsed >= 300:
                    resume_elapsed_skip_throttle = True
                else:
                    resume_elapsed_skip_throttle = False
            except (ValueError, TypeError):
                pass

    first_medium_in_resume = True
    last_medium_success_idx = -1

    for item_idx, item in enumerate(to_process):
        row = item["payload"]
        platform = ckpt.get("platform") or row.get("platform", "")
        mode = ckpt.get("mode") or row.get("publish_mode", "draft")

        if platform == "medium":
            if first_medium_in_resume:
                if not resume_elapsed_skip_throttle:
                    sleep_secs = random.uniform(throttle_min, throttle_max)
                    publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s before resume Medium post")
                    time.sleep(sleep_secs)
                first_medium_in_resume = False
            elif last_medium_success_idx == item_idx - 1:
                sleep_secs = random.uniform(throttle_min, throttle_max)
                publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s before next Medium post")
                time.sleep(sleep_secs)

        publish_logger.info(
            f"resume publishing: {platform} id={item['id']}",
            extra={"id": item["id"], "platform": platform},
        )

        try:
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
            )
        except DependencyError as exc:
            emit_error(str(exc), exit_code=3)
            return
        except ExternalServiceError as exc:
            err_msg = f"service error: {exc}"
            checkpoint.update_item(
                run_id, item["id"], "failed",
                error=err_msg,
                error_class=_error_class(exc),
            )
            publish_logger.error(f"publish failed: {exc}", extra={"id": item["id"], "platform": platform})
            continue
        except Exception as exc:
            err_msg = f"unexpected error: {exc}"
            checkpoint.update_item(
                run_id, item["id"], "failed",
                error=err_msg,
                error_class="unexpected",
            )
            publish_logger.error(f"publish failed: {exc}", extra={"id": item["id"], "platform": platform})
            continue

        completed_at = datetime.now(timezone.utc).isoformat()
        checkpoint.update_item(
            run_id, item["id"], "done",
            published_url=result.published_url,
            adapter=result.adapter,
            completed_at=completed_at,
        )
        if result.adapter in _MEDIUM_ADAPTERS:
            last_medium_success_idx = item_idx
        publish_logger.info(
            f"published: id={item['id']} status={result.status}",
            extra={"id": item["id"], "status": result.status},
        )

    # build union output in original checkpoint order
    updated_ckpt = checkpoint.load_checkpoint(run_id)
    all_done = [item_to_publish_output(i) for i in updated_ckpt["items"] if i["status"] == "done"]
    write_jsonl(all_done)
    sys.stdout.flush()

    still_unfinished = [i for i in updated_ckpt["items"] if i["status"] in ("pending", "failed")]
    if not still_unfinished:
        checkpoint.mark_complete(run_id)
        raise SystemExit(0)
    else:
        for f in still_unfinished:
            print(f"publish failed: {f.get('error', 'unknown error')}", file=sys.stderr)
        raise SystemExit(4)


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
        choices=["blogger", "medium"],
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
    args = parser.parse_args(argv)

    from ..logger import set_log_level
    set_log_level(args.log_level)

    # Mutual exclusion: only one of --resume, --list-runs, --cleanup, --cleanup-all
    exclusive = [args.resume, args.list_runs, args.cleanup, args.cleanup_all]
    if sum(bool(x) for x in exclusive) > 1:
        emit_error(
            "error: --resume, --list-runs, --cleanup, and --cleanup-all are mutually exclusive",
            exit_code=2,
        )

    # ── Housekeeping short-circuits ───────────────────────────────────────────

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

    # ── Resume path ───────────────────────────────────────────────────────────

    if args.resume:
        _run_resume(args)
        return  # _run_resume raises SystemExit; this is unreachable

    publish_logger.info("publish-backlinks started", extra={
        "platform": args.platform,
        "mode": args.mode,
        "dry_run": args.dry_run,
    })

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    publish_logger.info(f"processing {len(rows)} payloads")

    config = load_config()

    # Pre-flight: validate all payloads and check for unsupported platforms
    for idx, row in enumerate(rows, start=1):
        platform = args.platform or row.get("platform", "")
        if platform == "linkedin":
            emit_error(
                f"row {idx}: platform 'linkedin' is not supported. "
                f"Supported platforms: blogger, medium",
                exit_code=2,
            )
        errs = validate_publish_payload(row)
        if errs:
            for e in errs:
                print(f"row {idx}: {e}", file=sys.stderr)
            raise SystemExit(2)

    # Verify adapter setup (unless dry-run)
    if not args.dry_run:
        platforms_in_use = {
            args.platform or row.get("platform", "") for row in rows
        }
        for plat in platforms_in_use:
            if plat not in SUPPORTED_PLATFORMS:
                continue
            try:
                verify_adapter_setup(plat, config)
            except DependencyError as exc:
                emit_error(str(exc), exit_code=3)

    # Create checkpoint (after pre-flight passes, skipped for dry-run)
    run_id: str | None = None
    if not args.dry_run:
        try:
            run_id, _ = checkpoint.create_checkpoint(
                rows,
                platform=args.platform,
                mode=args.mode,
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
    last_medium_success_idx: int = -1

    throttle_min = int(os.environ.get("MEDIUM_THROTTLE_MIN", "60"))
    throttle_max = int(os.environ.get("MEDIUM_THROTTLE_MAX", "300"))

    for row_idx, row in enumerate(rows):
        # Throttle: sleep between Medium rows when previous was a successful Medium publish
        if (
            not args.dry_run
            and row_idx > 0
            and last_medium_success_idx == row_idx - 1
        ):
            platform_next = args.platform or row.get("platform", "")
            if platform_next == "medium":
                sleep_secs = random.uniform(throttle_min, throttle_max)
                publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s before next Medium post")
                time.sleep(sleep_secs)

        platform = args.platform or row.get("platform", "")
        mode = args.mode or row.get("publish_mode", "draft")

        if platform not in SUPPORTED_PLATFORMS:
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"unsupported platform: {platform}",
            })
            fail_count += 1
            continue

        # Dry run
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
            result = adapter_publish(
                payload={**row, "platform": platform},
                mode=mode,
                config=config,
                dry_run=False,
            )
        except DependencyError as exc:
            emit_error(str(exc), exit_code=3)
            return  # unreachable but satisfies type checker
        except ExternalServiceError as exc:
            err_class = _error_class(exc)
            err_msg = f"service error: {exc}"
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": err_msg,
            })
            fail_count += 1
            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "failed",
                        error=err_msg,
                        error_class=err_class,
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
                    run_id = None
            publish_logger.error(
                f"publish failed: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue
        except Exception as exc:
            err_msg = f"unexpected error: {exc}"
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": err_msg,
            })
            fail_count += 1
            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "failed",
                        error=err_msg,
                        error_class="unexpected",
                    )
                except Exception as ckpt_exc:
                    print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
                    run_id = None
            publish_logger.error(
                f"publish failed: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue

        outputs.append(result.to_publish_output(row, ts))
        if result.error:
            fail_count += 1
        else:
            success_count += 1
            if result.adapter in _MEDIUM_ADAPTERS:
                last_medium_success_idx = row_idx
            if run_id is not None:
                try:
                    checkpoint.update_item(
                        run_id, row.get("id", ""), "done",
                        published_url=result.published_url,
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

    # Only output successful results to stdout
    successful = [r for r in outputs if r.get("error") is None]
    failed = [r for r in outputs if r.get("error") is not None]

    if successful:
        write_jsonl(successful)

    if failed:
        for f in failed:
            print(f"publish failed: {f['error']}", file=sys.stderr)
        raise SystemExit(4)

    if not args.dry_run and not successful:
        emit_error("no payloads were published", exit_code=5)

    publish_logger.info(
        f"publish complete: {success_count} succeeded, {fail_count} failed",
        extra={"success": success_count, "failed": fail_count},
    )
