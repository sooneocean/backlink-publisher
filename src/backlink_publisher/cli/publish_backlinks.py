"""Publish validated backlink payloads via adapter dispatcher."""

from __future__ import annotations

import os
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any

_MEDIUM_ADAPTERS = {"medium-api", "medium-browser"}

from ..adapters import publish as adapter_publish, verify_adapter_setup
from ..config import load_config
from ..errors import DependencyError, ExternalServiceError, emit_error
from ..jsonl import read_jsonl, write_jsonl
from ..logger import publish_logger
from ..schema import SUPPORTED_PLATFORMS, validate_publish_payload


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
    args = parser.parse_args(argv)

    from ..logger import set_log_level
    set_log_level(args.log_level)

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
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"service error: {exc}",
            })
            fail_count += 1
            publish_logger.error(
                f"publish failed: {exc}",
                extra={"id": row.get("id"), "platform": platform},
            )
            continue
        except Exception as exc:
            outputs.append({
                "id": row.get("id", ""),
                "platform": platform,
                "status": "failed",
                "title": row.get("title", ""),
                "draft_url": "",
                "published_url": "",
                "created_at": ts,
                "adapter": f"{platform}",
                "error": f"unexpected error: {exc}",
            })
            fail_count += 1
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
