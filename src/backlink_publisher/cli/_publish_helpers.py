"""Shared helpers for publish-backlinks CLI.

Extracted from ``publish_backlinks.py`` to keep the main CLI file focused
on ``main()`` and the publish loop.
"""

from __future__ import annotations

import os
import random
import re
import sys
import time
from typing import Any

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from backlink_publisher._util.logger import publish_logger
from backlink_publisher.linkcheck.verify import verify_published
from backlink_publisher.linkcheck.http import MAX_CONCURRENT as _LINKCHECK_MAX_CONCURRENT, check_url

_HTTP_5XX_RE = re.compile(r"\b5[0-9]{2}\b")

_GATE_BANNER_SENTINEL = (
    Path.home() / ".cache" / "backlink-publisher" / "v0.3-gate-banner-seen"
)
_GATE_BANNER_TEXT = (
    "publish-backlinks now performs a publish-time reachability re-check "
    "on every row before dispatch. Use --skip-publish-time-check to "
    "restore prior behavior. This message will not repeat (sentinel: "
    f"{_GATE_BANNER_SENTINEL})."
)


def _release_acquired_leases(store: Any, acquired: list[str], pid: int) -> None:
    for plat in acquired:
        try:
            store.release_lease(plat, pid)
        except Exception as e:
            publish_logger.warning(f"Failed to release lease on {plat!r}: {e}")


def _acquire_publish_leases(platforms: set[str], dry_run: bool) -> None:
    if dry_run or not platforms:
        return

    import atexit
    from backlink_publisher.events.store import EventStore
    from backlink_publisher._util.errors import emit_error

    store = EventStore()
    pid = os.getpid()
    acquired = []

    for plat in sorted(platforms):
        if store.acquire_lease(plat, pid, ttl_seconds=3600):
            acquired.append(plat)
        else:
            _release_acquired_leases(store, acquired, pid)
            lease_details = store.get_lease(plat)
            owner_info = f"PID {lease_details['owner_pid']}" if lease_details else "unknown"
            emit_error(
                f"error: another publish process ({owner_info}) is currently active for platform {plat!r}. "
                "Aborting to prevent concurrent publishing conflicts.",
                exit_code=3,
            )

    atexit.register(_release_acquired_leases, store, acquired, pid)


def _maybe_emit_gate_banner(skip_flag: bool) -> None:
    if skip_flag or _GATE_BANNER_SENTINEL.exists():
        return
    publish_logger.warn(_GATE_BANNER_TEXT)
    try:
        _GATE_BANNER_SENTINEL.parent.mkdir(parents=True, exist_ok=True)
        _GATE_BANNER_SENTINEL.touch(exist_ok=True)
    except OSError:
        pass


def _check_row_reachability(row: dict[str, Any]) -> tuple[bool, str | None]:

    urls = [row.get("target_url", "")]
    for link in row.get("links", []):
        if isinstance(link, dict):
            url = link.get("url")
            if url:
                urls.append(url)
    urls = [u for u in urls if u]
    if not urls:
        return True, None

    workers = min(_LINKCHECK_MAX_CONCURRENT, len(urls))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(check_url, u): u for u in urls}
        first_failure: str | None = None
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                ok, _err = fut.result()
            except Exception:
                ok = False
            if not ok and first_failure is None:
                first_failure = url
                for other in futures:
                    if not other.done():
                        other.cancel()
                break
    if first_failure is not None:
        return False, first_failure
    return True, None


def _make_banner_emit() -> Any:
    store_holder: dict[str, Any] = {}

    def _emit(kind: str, payload: dict[str, Any]) -> None:
        publish_logger.info(
            f"banner-embed: {kind} {payload}",
            extra={"banner_event": kind, **payload},
        )
        if "store" not in store_holder:
            from backlink_publisher.events.store import EventStore
            store_holder["store"] = EventStore()
        try:
            store_holder["store"].append(kind, payload)
        except Exception as exc:
            publish_logger.warning(
                f"banner-event EventStore.append({kind!r}) failed: {exc}"
            )

    return _emit


def _error_class(exc: Exception) -> str:
    from backlink_publisher.publishing.adapters.retry import classify_exception
    return classify_exception(exc).value


def _check_token_drift(initial_revs: dict[str, int]) -> None:
    from backlink_publisher.config import snapshot_token_revs
    from backlink_publisher._util.errors import emit_error

    current = snapshot_token_revs()
    for plat, init_rev in initial_revs.items():
        if current.get(plat, 0) != init_rev:
            emit_error(
                f"error: configuration for platform {plat!r} was updated mid-run. "
                "Aborting to prevent using revoked credentials.",
                exit_code=3,
            )


def _do_verify(
    no_verify: bool,
    dry_run: bool,
    result: Any,
    row: dict[str, Any],
) -> tuple[bool, str]:

    if no_verify or dry_run:
        return True, ""
    verify_url = result.published_url or result.draft_url
    if not verify_url:
        return True, ""
    needs_extended_wait = getattr(result, "post_publish_delay_seconds", 0) > 0
    max_wait = 30 if needs_extended_wait else 10
    required_links = [lnk["url"] for lnk in row.get("links", []) if lnk.get("required")]
    vr = verify_published(
        verify_url,
        title=row.get("title", ""),
        required_link_urls=required_links,
        max_wait=max_wait,
    )
    return vr.ok, vr.reason


def _build_failure_row(
    status: str,
    row: dict[str, Any],
    platform: str,
    error: str,
    ts: str,
    *,
    adapter: str = "",
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.get("id", ""),
        "platform": platform,
        "status": status,
        "title": row.get("title", ""),
        "draft_url": "",
        "published_url": "",
        "created_at": ts,
        "adapter": adapter,
        "error": error,
    }
    out.update(extra)
    return out


def _try_update_ckpt_failed(
    run_id: str | None,
    row_id: str,
    error: str,
    error_class: str,
) -> str | None:
    from .. import checkpoint

    if run_id is None:
        return None
    try:
        checkpoint.update_item(run_id, row_id, "failed", error=error, error_class=error_class)
    except Exception as ckpt_exc:
        print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
        return None
    return run_id


def _load_throttle_config() -> tuple[int, int]:
    return (
        int(os.environ.get("MEDIUM_THROTTLE_MIN", "60")),
        int(os.environ.get("MEDIUM_THROTTLE_MAX", "300")),
    )


def _do_sleep(seconds: float) -> None:
    """Sleep for the specified number of seconds. (Mockable for tests)"""
    time.sleep(seconds)


def _sleep_with_throttle(throttle_min: int, throttle_max: int, context: str = "") -> None:
    sleep_secs = random.uniform(throttle_min, throttle_max)
    label = f" ({context})" if context else ""
    publish_logger.info(f"throttle: sleeping {sleep_secs:.0f}s{label}")
    _do_sleep(sleep_secs)


def _record_publish_failure(
    outputs: list[dict[str, Any]],
    row: dict[str, Any],
    platform: str,
    ts: str,
    run_id: str | None,
    exc: Exception,
    err_class: str,
    err_msg: str,
) -> str | None:
    outputs.append(_build_failure_row("failed", row, platform, err_msg, ts, adapter=platform))
    new_run_id = _try_update_ckpt_failed(run_id, row.get("id", ""), err_msg, err_class)
    publish_logger.error(
        f"publish failed: {exc}",
        extra={"id": row.get("id"), "platform": platform},
    )
    return new_run_id


def _build_parser() -> Any:
    import argparse
    from backlink_publisher.publishing.registry import registered_platforms

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
    return parser


def _handle_checkpoint_ops(args: Any) -> None:
    from .. import checkpoint
    from backlink_publisher._util.errors import emit_error

    exclusive = [args.resume, args.list_runs, args.cleanup, args.cleanup_all]
    if sum(bool(x) for x in exclusive) > 1:
        emit_error(
            "error: --resume, --list-runs, --cleanup, and --cleanup-all are mutually exclusive",
            exit_code=2,
        )

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


def _handle_auth_expired(
    exc: Any,
    run_id: str | None,
    row: dict[str, Any],
    logger: Any,
) -> None:
    from backlink_publisher._util.errors import emit_error

    try:
        from webui_store.channel_status import mark_expired
        mark_expired(exc.channel)
    except Exception as flip_exc:
        logger.warning(f"mark_expired({exc.channel!r}) failed: {flip_exc}")
    if run_id is not None:
        from .. import checkpoint
        try:
            checkpoint.update_item(
                run_id, row.get("id", ""), "failed",
                error=str(exc),
                error_class="auth_expired",
            )
        except Exception as ckpt_exc:
            print(f"[WARN] checkpoint update failed: {ckpt_exc}", file=sys.stderr)
    logger.error(
        f"auth expired: {exc}",
        extra={"id": row.get("id"), "platform": row.get("platform", "")},
    )
    emit_error(str(exc), exit_code=3)


def _medium_throttle_sleep(
    row_idx: int,
    last_success_idx: int,
    platform: str,
    throttle_min: int,
    throttle_max: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run or row_idx == 0:
        return
    if last_success_idx != row_idx - 1 or platform != "medium":
        return
    _sleep_with_throttle(throttle_min, throttle_max, "next Medium post")
