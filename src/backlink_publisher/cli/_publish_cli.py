"""Argparse + checkpoint/auth operational handlers for publish-backlinks.

Extracted from ``_publish_helpers.py`` to keep that module within its SLOC
budget. Holds the CLI parser builder (``_build_parser``) and the two
arg-driven operational handlers (``_handle_checkpoint_ops`` for
--list-runs/--cleanup/etc., ``_handle_auth_expired`` for mid-run credential
expiry). Self-contained — no shared module state, only stdlib + lazy imports.
``_publish_helpers`` re-exports these names, so ``publish_backlinks`` and the
test that imports ``_build_parser`` are unaffected.
"""

from __future__ import annotations

import sys
from typing import Any


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
        "--preview-manifest",
        action="store_true",
        default=False,
        help=(
            "Read-only dedup preview: emit per-row NEW/SKIP-DUPLICATE/HOLD-UNCERTAIN "
            "verdicts (JSONL on stdout, HMAC-digest summary on stderr) and exit 0. "
            "No publish, no lease, no checkpoint."
        ),
    )
    from ._dedup_ops import add_dedup_arguments
    add_dedup_arguments(parser)
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
    parser.add_argument(
        "--reconcile",
        default=None,
        metavar="RUN_ID",
        help=(
            "Output reconciliation gap report for a specific run (JSONL "
            "on stdout after publish output)"
        ),
    )
    parser.add_argument(
        "--reconcile-all",
        action="store_true",
        default=False,
        help="Output reconciliation gap report for all runs (JSONL on stdout)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        default=False,
        help="Enable cProfile profiling (saved to ~/.cache/backlink-publisher/profiles/)",
    )
    return parser


def _handle_checkpoint_ops(args: Any) -> None:
    from .. import checkpoint
    from backlink_publisher._util.errors import emit_error

    exclusive = [
        args.resume, args.list_runs, args.cleanup, args.cleanup_all,
        getattr(args, "preview_manifest", False),
        getattr(args, "forget", None), getattr(args, "list_uncertain", False),
        getattr(args, "adjudicate_uncertain", None),
        getattr(args, "adjudicate_bulk", False),
        getattr(args, "backfill_dedup", False),
        getattr(args, "check_enforce_readiness", False),
        # --force-manifest modifies a fresh publish run; it is NOT honored on the
        # resume seam (which calls the plain gate), so reject the combination here
        # rather than silently dropping the operator's force-flags.
        getattr(args, "force_manifest", None),
    ]
    if sum(bool(x) for x in exclusive) > 1:
        emit_error(
            "error: --resume, --list-runs, --cleanup, --cleanup-all, "
            "--preview-manifest, --forget, --list-uncertain, "
            "--adjudicate-uncertain, --adjudicate-bulk, --backfill-dedup, "
            "--check-enforce-readiness, and --force-manifest are mutually exclusive",
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
    # error_class = the real exception type so the operator sees "AuthExpiredError"
    # (re-bind credentials), not the coarse "DependencyError" the exit-3 map yields.
    emit_error(str(exc), exit_code=3, error_class=type(exc).__name__)
