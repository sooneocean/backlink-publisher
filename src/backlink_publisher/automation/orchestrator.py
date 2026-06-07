"""auto-publish — unified automation orchestrator CLI (Plan 2026-06-07 R1).

Chains the full pipeline (plan → validate → publish) with health-aware
gating. Supports dry-run previews, force-publish for degraded platforms,
and auto-retry on transient failures.

Contract:
* stdout = JSONL data (per-row results), stderr = config banner + summary
* Exit 0 (success), 3 (dependency), 6 (hard-skip triggered), 8 (config error)
* --dry-run never touches network by default (preview mode)
* --probe enables network operations
* flock guards overlapping runs (same pattern as recheck-backlinks)
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    emit_envelope_and_exit,
    emit_error,
)
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.logger import get_logger
from backlink_publisher.canary.store import is_degraded, is_quarantined

from ._state import AutomationPipelineState, set_current_state

_log = get_logger("auto-publish")

# Exit codes per AGENTS.md contract
EXIT_DEPENDENCY = 3
EXIT_HARD_SKIP = 6
EXIT_CONFIG_ERROR = 8

# Default configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 60
DEFAULT_PER_RUN_CAP = 50

# Module-level cache for seeds across function calls
_loaded_seeds: list[dict] = []


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="auto-publish",
        description=(
            "Unified automation orchestrator that chains plan → validate → publish "
            "with health-aware gating. By default runs in dry-run mode; use --probe "
            "to enable network operations."
        ),
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="enable network operations (default: zero-network dry preview)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="force publish even for degraded platforms",
    )
    parser.add_argument(
        "--retry-on-error", action="store_true",
        help="auto-retry on transient failures",
    )
    parser.add_argument(
        "--max-retries", type=int, default=DEFAULT_MAX_RETRIES, metavar="N",
        help=f"maximum retry attempts (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_PER_RUN_CAP, metavar="N",
        help=f"cap candidates per run (default: {DEFAULT_PER_RUN_CAP})",
    )
    parser.add_argument(
        "--input", "-i", metavar="FILE",
        help="input seed JSONL (default: stdin)",
    )
    parser.add_argument(
        "--mode", choices=["draft", "publish"], default="draft",
        help="publish mode (default: draft)",
    )
    parser.add_argument(
        "--log-level", choices=["debug", "info", "warning", "error"],
        default="info", help="logging level",
    )
    args = parser.parse_args(argv)

    # Validate arguments
    if args.max_retries <= 0:
        emit_error("auto-publish: --max-retries must be positive", exit_code=1)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    state = AutomationPipelineState()
    set_current_state(state)

    try:
        with _single_run_lock() as acquired:
            if not acquired:
                _log.recon("auto_publish_skipped_locked")
                print(
                    "auto-publish: another run holds the lock; skipping",
                    file=sys.stderr,
                )
                return

            # Load seeds early for platform detection
            seeds = _load_seeds(args.input, sys.stdin)
            global _loaded_seeds
            _loaded_seeds = seeds

            # Load config
            from backlink_publisher import config_echo
            from backlink_publisher.config import load_config
            cfg = load_config()
            config_echo.emit_banner(cfg, "auto-publish")

            # Check for degraded platforms before publishing
            degraded_platforms = _get_degraded_platforms()
            hard_skip_platforms = _get_hard_skip_platforms(cfg)

            if hard_skip_platforms and not args.force:
                _log.recon(
                    "auto_publish_hard_skip",
                    platforms=list(hard_skip_platforms),
                )
                print(
                    f"auto-publish: hard-skip platforms detected: {list(hard_skip_platforms)} "
                    f"(use --force to override)",
                    file=sys.stderr,
                )
                # Emit hard-skip advisory
                write_jsonl([
                    {
                        "event": "auto_publish_hard_skip_advisory",
                        "platforms": list(hard_skip_platforms),
                        "ts_utc": datetime.now(timezone.utc).isoformat(),
                        "action_taken": "skipped_publish",
                        "recovery_suggested": True,
                    }
                ], sys.stdout)
                raise SystemExit(EXIT_HARD_SKIP)

            # Dry-run mode: just preview what would happen
            if not args.probe:
                _run_dry_preview(args, cfg, state, seeds)
                return

            # Full execution path
            _run_full_pipeline(args, cfg, state, seeds)

    except DependencyError as exc:
        _log.recon("auto_publish_dependency_error", error=str(exc))
        emit_error(f"auto-publish: dependency error — {exc}", exit_code=EXIT_DEPENDENCY)
    except Exception as exc:
        _log.recon("auto_publish_unexpected_error", error=str(exc))
        emit_error(f"auto-publish: unexpected error — {exc}", exit_code=1)
    finally:
        set_current_state(None)


def _run_dry_preview(
    args: argparse.Namespace,
    cfg: Any,
    state: AutomationPipelineState,
    seeds: list[dict],
) -> None:
    """Run dry preview without network operations."""
    if not seeds:
        _log.recon("auto_publish_dry_preview_empty")
        print("auto-publish: dry preview — no seeds provided", file=sys.stderr)
        return

    # Preview stage: just show what platforms would be used
    platforms_to_run = _identify_platforms(seeds)
    degraded = [p for p in platforms_to_run if is_degraded(p)]
    quarantined = [p for p in platforms_to_run if is_quarantined(p)]

    preview_results = []
    for seed in seeds[: args.limit]:
        platform = seed.get("platform", "unknown")
        can_publish = _can_publish_platform(platform, args.force)
        preview_results.append({
            "seed": seed,
            "would_publish": can_publish,
            "platform_status": _get_platform_status(platform),
        })

    state.planned_count = len(seeds)
    state.degraded_platforms = degraded
    state.quarantined_platforms = quarantined

    write_jsonl(preview_results, sys.stdout)
    _log.recon(
        "auto_publish_dry_preview",
        seeds=len(seeds),
        limit=args.limit,
        platforms=list(platforms_to_run),
        degraded=degraded,
        quarantined=quarantined,
    )
    print(
        f"auto-publish: dry preview — {len(seeds)} seed(s), "
        f"{len(degraded)} degraded, {len(quarantined)} quarantined "
        f"(add --probe to run for real)",
        file=sys.stderr,
    )


def _run_full_pipeline(
    args: argparse.Namespace,
    cfg: Any,
    state: AutomationPipelineState,
    seeds: list[dict],
) -> None:
    """Run the full plan → validate → publish pipeline."""
    seeds = seeds[: args.limit]
    state.planned_count = len(seeds)

    # Stage 1: Plan
    plans = _run_plan_stage(seeds, cfg, state)
    if not plans:
        print("auto-publish: planning produced no valid plans", file=sys.stderr)
        return

    state.validated_count = len(plans)

    # Stage 2: Validate
    validated = _run_validate_stage(plans, cfg, state)
    if not validated:
        print("auto-publish: validation produced no valid payloads", file=sys.stderr)
        return

    # Stage 3: Publish (with retry loop if enabled)
    _run_publish_stage(validated, args, cfg, state)


def _run_plan_stage(
    seeds: list[dict],
    cfg: Any,
    state: AutomationPipelineState,
) -> list[dict]:
    """Run plan-backlinks and return validated plans."""
    import io

    from backlink_publisher._util.jsonl import write_jsonl
    from backlink_publisher.cli.plan_backlinks._engine import PlanOutcome, plan_rows

    try:
        outcome: PlanOutcome = plan_rows(
            seeds, cfg,
            work_count=10,
            fetch_verify_enabled=True,
        )
        state.mark_stage_completed("plan")
        return outcome.outputs
    except Exception as exc:
        state.mark_stage_failed("plan")
        _log.recon("auto_publish_plan_failed", error=str(exc))
        raise


def _run_validate_stage(
    plans: list[dict],
    cfg: Any,
    state: AutomationPipelineState,
) -> list[dict]:
    """Run validate-backlinks and return validated payloads."""
    import io

    from backlink_publisher._util.jsonl import write_jsonl
    from backlink_publisher.validate.engine import (
        load_config_tolerant,
        validate_rows,
    )

    config = load_config_tolerant()
    try:
        outcome = validate_rows(plans, config, check_urls=True)
        state.mark_stage_completed("validate")
        return outcome.outputs
    except Exception as exc:
        state.mark_stage_failed("validate")
        _log.recon("auto_publish_validate_failed", error=str(exc))
        raise


def _run_publish_stage(
    validated: list[dict],
    args: argparse.Namespace,
    cfg: Any,
    state: AutomationPipelineState,
) -> None:
    """Run publish-backlinks with optional retry."""
    from backlink_publisher.cli.publish_backlinks._engine import PublishRunState, run_publish_loop

    # Group by platform for throttling awareness
    platform_groups = _group_by_platform(validated)

    all_results = []
    for platform, payloads in platform_groups.items():
        # Check health gating
        if not _can_publish_platform(platform, args.force):
            _log.recon(
                "auto_publish_platform_skipped",
                platform=platform,
                reason="health_gated",
            )
            continue

        if args.retry_on_error:
            results = _publish_with_retry(payloads, platform, args.mode, cfg, state)
        else:
            results = _publish_single(payloads, platform, args.mode, cfg, state)
        all_results.extend(results)

    state.published_count = len(all_results)
    write_jsonl(all_results, sys.stdout)
    _log.recon(
        "auto_publish_complete",
        planned=state.planned_count,
        validated=state.validated_count,
        published=state.published_count,
        recovery_actions=len(state.recovery_actions),
    )


def _publish_single(
    payloads: list[dict],
    platform: str,
    mode: str,
    cfg: Any,
    state: AutomationPipelineState,
) -> list[dict]:
    """Run single publish attempt (no retry)."""
    import fcntl

    from backlink_publisher._util.jsonl import read_jsonl
    from webui_app.api.pipeline_api import PipelineAPI

    seed_jsonl = _to_jsonl(payloads)
    result = PipelineAPI().publish_seed(seed_jsonl)

    if not result.success:
        state.mark_stage_failed(f"publish:{platform}")

    return result.rows


def _publish_with_retry(
    payloads: list[dict],
    platform: str,
    mode: str,
    cfg: Any,
    state: AutomationPipelineState,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: int = DEFAULT_RETRY_DELAY_SECONDS,
) -> list[dict]:
    """Publish with exponential backoff retry."""
    import time

    results = _publish_single(payloads, platform, mode, cfg, state)

    retry_count = 0
    while retry_count < max_retries:
        if all(r.get("published_url") or r.get("draft_url") for r in results):
            break

        failed = [r for r in results if not (r.get("published_url") or r.get("draft_url"))]
        if not any("429" in str(r.get("error", "")) or "rate" in str(r.get("error", "")).lower() for r in failed):
            break

        retry_count += 1
        state.increment_retry(f"publish:{platform}")
        _log.recon(
            "auto_publish_retry",
            platform=platform,
            attempt=retry_count,
            max=max_retries,
        )
        time.sleep(retry_delay * (2 ** (retry_count - 1)))
        results = _publish_single(payloads, platform, mode, cfg, state)

    return results


def _load_seeds(input_path: str | None, stdin) -> list[dict]:
    """Load seed JSONL from file or stdin."""
    from backlink_publisher._util.jsonl import read_jsonl

    if input_path:
        with open(input_path, "r", encoding="utf-8") as f:
            return list(read_jsonl(f))
    return list(read_jsonl(stdin))


def _identify_platforms(seeds: list[dict]) -> set[str]:
    """Extract platform names from seeds."""
    return {s.get("platform", "") for s in seeds if s.get("platform")}


def _get_degraded_platforms() -> set[str]:
    """Return platforms with degraded status."""
    from backlink_publisher.canary.store import list_all

    degraded = set()
    for platform, rec in (list_all() or {}).items():
        if rec.get("status") == "drift-confirmed" or rec.get("quarantined"):
            degraded.add(platform)
    return degraded


def _get_hard_skip_platforms(cfg: Any) -> set[str]:
    """Return quarantined platforms explicitly configured for hard-skip."""
    from backlink_publisher.canary.store import read_canary_config

    hard_skip = set()
    for platform in _identify_platforms(_loaded_seeds or []):
        canary_cfg = read_canary_config(platform)
        if canary_cfg and canary_cfg.get("hard_skip") and is_quarantined(platform):
            hard_skip.add(platform)
    return hard_skip


def _can_publish_platform(platform: str, force: bool) -> bool:
    """Check if a platform can be published to."""
    if force:
        return True
    if is_quarantined(platform):
        return False
    if is_degraded(platform):
        return False
    return True


def _get_platform_status(platform: str) -> str:
    """Get status string for a platform."""
    if is_quarantined(platform):
        return "quarantined"
    if is_degraded(platform):
        return "degraded"
    return "healthy"


def _group_by_platform(plans: list[dict]) -> dict[str, list[dict]]:
    """Group plan rows by platform."""
    groups: dict[str, list[dict]] = {}
    for plan in plans:
        platform = plan.get("platform", "unknown")
        groups.setdefault(platform, []).append(plan)
    return groups


def _to_jsonl(rows: list[dict]) -> str:
    """Serialize rows to JSONL string."""
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n" if rows else ""


@contextlib.contextmanager
def _single_run_lock():
    """Non-blocking exclusive file lock for cross-run safety."""
    import fcntl

    from backlink_publisher.config.loader import _config_dir

    config_dir = _config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config_dir / ".auto-publish.lock"

    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


if __name__ == "__main__":
    main()
