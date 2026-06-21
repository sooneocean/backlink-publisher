"""auto-recover — survival-rate optimisation closed loop (Plan 2026-06-08-001 U3+U5).

Runs the full recheck → health → route → replan → quality-gate → publish pipeline
in a single invocation, with flock protection against overlapping runs.

Phase flow:
  Phase 1 — Recheck: programmatic probe of published backlinks
  Phase 2 — Health Update: query ChannelHealthRegistry for fresh health state
  Phase 3 — Routing: replan-dead core + HealthRouter + seed routing overrides
  Phase 4 — Replan + Quality Gate: subprocess pipe through plan-backlinks | quality-gate
  Phase 5 — Publish: subprocess pipe through publish-backlinks (skipped in --dry-run)
  Phase 6 — Report: emit JSONL report with routing decisions and results

Contract:
  * stdout = JSONL report data; stderr = diagnostics / summary lines
  * Exit 0 advisory (no --fail-on-* flag in v1)
  * Network is gated behind --probe (same as recheck-backlinks convention)
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time as time_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .._util.errors import emit_error
from .._util.jsonl import read_jsonl, write_jsonl
from .._util.logger import get_logger
from ..config import load_config
from ..events.store import EventStore
from ..health.registry import (
    ChannelHealthRegistry,
    write_published_to_event,
    write_recheck_observed,
    write_routed_event,
)
from ..health.router import HealthRouter, RoutingDecision

_log = get_logger("auto_recover")

#: Per-probe timeout in seconds (bounds redirect-chain accumulation).
_PER_TARGET_TIMEOUT = 10.0
#: Total wall-clock ceiling for the probe batch.
_BATCH_BUDGET_S = 600.0


# ── Phase 1 — Recheck (programmatic) ────────────────────────────────────────


def _probe_batch(candidates: list[dict]) -> list[dict]:
    """Probe each candidate; never-raises. Stops at the batch wall-clock budget."""
    from backlink_publisher.recheck.probe import recheck_link

    deadline = time_mod.monotonic() + _BATCH_BUDGET_S
    results: list[dict] = []
    for index, candidate in enumerate(candidates):
        if time_mod.monotonic() > deadline:
            deferred = len(candidates) - index
            _log.recon("recheck_budget_exhausted", probed=index, deferred=deferred)
            print(
                f"auto-recover: budget exhausted; {deferred} candidate(s) deferred",
                file=sys.stderr,
            )
            break
        results.append(
            recheck_link(candidate, probe=True, timeout=_PER_TARGET_TIMEOUT)
        )
    return results


def _run_recheck_phase(
    store: EventStore,
    since_dt: datetime | None,
    args: Any,
) -> list[dict]:
    """Phase 1 — Programmatic recheck-backlinks core.

    Returns the list of probed result dicts (each has ``verdict``, ``live_url``,
    ``platform``, etc.). Writes ``link.rechecked`` and ``channel.recheck_observed``
    events.
    """
    from backlink_publisher.recheck import selection
    from backlink_publisher.recheck.events_io import emit_recheck

    now = datetime.now(timezone.utc)
    candidates = selection.select_candidates(
        store, now=now, since=since_dt,
    )
    if not candidates:
        if args.emit_stderr:
            print("auto-recover: (phase 1) 0 candidates to recheck", file=sys.stderr)
        return []

    results = _probe_batch(candidates)
    written = emit_recheck(store, results)
    if args.emit_stderr:
        print(
            f"auto-recover: (phase 1) probed {len(results)}, "
            f"{written} link.rechecked event(s) written",
            file=sys.stderr,
        )

    channel_count = 0
    for r in results:
        verdict = r.get("verdict")
        live_url = r.get("live_url", "")
        platform = r.get("platform")
        target_url = r.get("target_url", "")
        if not verdict or not platform:
            continue
        write_recheck_observed(
            store,
            verdict=verdict,
            platform=platform,
            live_url=live_url,
            target_url=target_url,
        )
        channel_count += 1
    if args.emit_stderr and channel_count:
        print(
            f"auto-recover: (phase 1) {channel_count} channel.recheck_observed "
            f"event(s) written",
            file=sys.stderr,
        )
    return results


# ── Phase 2 — Health Update ─────────────────────────────────────────────────


def _run_health_phase(store: EventStore, args: Any) -> dict[str, Any]:
    """Phase 2 — Query fresh channel health after recheck writes.

    Returns the ``get_all_health()`` dict (channel → ChannelHealth).
    """
    registry = ChannelHealthRegistry(store)
    health = registry.get_all_health(window_days=30)
    if args.emit_stderr:
        channels_with_data = [
            ch for ch, h in health.items() if h.has_data
        ]
        print(
            f"auto-recover: (phase 2) {len(channels_with_data)} channel(s) "
            f"with health data",
            file=sys.stderr,
        )
    return health


# ── Phase 3 — Routing (programmatic) ────────────────────────────────────────


def _dead_events_with_checks(
    store: EventStore,
    since_dt: datetime,
    args: Any,
) -> tuple[list[dict], list[dict]]:
    """Query deterministic dead events, skipping resolved and gap-met URLs.

    Returns (dead_events, skipped_reasons) where skipped_reasons is a list of
    dicts with ``url`` and ``reason`` for diagnostics.
    """
    from backlink_publisher.cli.replan_dead import (
        _count_live_dofollow_for_target,
        _deterministic_dead_events,
        _get_resolved_urls,
    )

    dead_events = _deterministic_dead_events(store, since_dt)
    if not dead_events:
        return [], []

    resolved = _get_resolved_urls(store)
    seen_combos: set[tuple[str, str]] = set()
    filtered: list[dict] = []
    skipped: list[dict] = []

    for ev in dead_events:
        live_url = ev["live_url"]
        target_url = ev["target_url"]
        platform = ev.get("platform") or "unknown"

        # Skip resolved.
        if live_url in resolved:
            skipped.append({"live_url": live_url, "reason": "resolved"})
            continue

        # Deduplicate per (target_url, platform).
        combo = (target_url, platform)
        if combo in seen_combos:
            skipped.append({"live_url": live_url, "reason": "dedup"})
            continue
        seen_combos.add(combo)

        # Check min-gap: skip if target already has sufficient live links.
        live_count = _count_live_dofollow_for_target(store, target_url)
        if live_count >= 3:
            skipped.append({"live_url": live_url, "reason": "gap_met"})
            continue

        filtered.append(ev)
    return filtered, skipped


def _build_seeds_with_routing(
    dead_events: list[dict],
    decisions: list[RoutingDecision],
    args: Any,
) -> list[dict]:
    """Build replan seeds with routing decision overrides.

    For each (dead_event, decision) pair, builds a seed via the replan-dead
    ``_build_seed`` helper, then overrides ``platform`` if the router assigned
    a different channel. Adds ``_routing_provenance`` for operator visibility.
    """
    from backlink_publisher.cli.replan_dead import _build_seed

    seeds: list[dict] = []
    for ev, decision in zip(dead_events, decisions):
        seed = _build_seed(
            live_url=ev["live_url"],
            target_url=ev["target_url"],
            host=ev.get("host"),
            platform=decision.assigned_channel,
            language=getattr(args, "language", "en"),
            url_mode=getattr(args, "url_mode", "A"),
            publish_mode=getattr(args, "publish_mode", "draft"),
        )
        # Add routing provenance.
        seed["_routing_provenance"] = {
            "original_platform": decision.original_platform,
            "assigned_channel": decision.assigned_channel,
            "reason": decision.reason,
            "source_survival_rate": decision.source_survival_rate,
            "target_survival_rate": decision.target_survival_rate,
        }
        seeds.append(seed)
    return seeds


def _run_routing_phase(
    store: EventStore,
    registry: ChannelHealthRegistry,
    since_dt: datetime,
    args: Any,
) -> tuple[list[RoutingDecision], list[dict]]:
    """Phase 3 — Route dead backlinks and build seeds with routing overrides.

    Returns (decisions, seeds).
    """
    dead_events, skipped = _dead_events_with_checks(store, since_dt, args)
    if not dead_events:
        if args.emit_stderr:
            print(
                f"auto-recover: (phase 3) 0 dead events to route "
                f"({len(skipped)} skipped)",
                file=sys.stderr,
            )
        return [], []

    # Apply --max-dead cap.
    if getattr(args, "max_dead", None) is not None:
        dead_events = dead_events[: args.max_dead]

    router = HealthRouter(registry, threshold=getattr(args, "routing_threshold", 0.7))
    decisions = router.route(dead_events)

    # Write channel.routed events.
    routed_count = 0
    for decision in decisions:
        write_routed_event(
            store,
            source_channel=decision.original_platform or "unknown",
            target_channel=decision.assigned_channel,
            reason=decision.reason,
            source_survival_rate=decision.source_survival_rate,
            target_survival_rate=decision.target_survival_rate,
            dead_live_url=decision.dead_live_url,
            target_url=decision.target_url,
        )
        routed_count += 1

    seeds = _build_seeds_with_routing(dead_events, decisions, args)

    if args.emit_stderr:
        rerouted = sum(
            1 for d in decisions
            if d.assigned_channel != d.original_platform
        )
        print(
            f"auto-recover: (phase 3) {len(dead_events)} dead event(s), "
            f"{routed_count} routed, {rerouted} rerouted to different channel, "
            f"{len(seeds)} seed(s) built",
            file=sys.stderr,
        )

    return decisions, seeds


# ── Phase 4 — Replan + Quality Gate (subprocess) ────────────────────────────


def _pipe_through_cli(
    seeds: list[dict],
    module_path: str,
    extra_args: list[str] | None = None,
) -> list[dict]:
    """Pipe seeds JSONL through a CLI subprocess and return output rows.

    The CLI must read JSONL on stdin and write JSONL on stdout (the standard
    contract for all backlink-publisher pipeline CLIs).

    Falls back gracefully: if subprocess fails (non-zero exit), logs a warning
    and returns the input seeds unchanged.
    """
    import sys as _sys

    input_jsonl = "\n".join(json.dumps(s) for s in seeds)
    cmd = [_sys.executable, "-m", module_path]
    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = subprocess.run(
            cmd,
            input=input_jsonl,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        _log.recon("subprocess_timeout", module=module_path)
        return seeds
    except FileNotFoundError:
        _log.recon("subprocess_not_found", module=module_path)
        return seeds

    if proc.returncode != 0:
        _log.recon(
            "subprocess_nonzero",
            module=module_path,
            returncode=proc.returncode,
            stderr=proc.stderr[:200],
        )
        # Return original seeds; the CLI may have produced partial output.
        if proc.stdout:
            try:
                return list(read_jsonl(proc.stdout.strip().splitlines()))
            except Exception:
                return seeds
        return seeds

    try:
        return list(read_jsonl(proc.stdout.strip().splitlines()))
    except Exception:
        return seeds


def _run_replan_quality_phase(
    seeds: list[dict],
    args: Any,
) -> list[dict]:
    """Phase 4 — Pipe seeds through plan-backlinks | quality-gate.

    Returns the filtered and quality-gated publishable rows.
    """
    if not seeds:
        return []

    # Apply --max-routing cap.
    if getattr(args, "max_routing", None) is not None:
        seeds = seeds[: args.max_routing]

    # Pipe through plan-backlinks (generates article content).
    planned = _pipe_through_cli(
        seeds, "backlink_publisher.cli.plan_backlinks",
    )
    if not planned:
        if args.emit_stderr:
            print(
                "auto-recover: (phase 4) plan-backlinks returned 0 rows",
                file=sys.stderr,
            )
        return []

    # Pipe through quality-gate (filters low-quality content).
    gated = _pipe_through_cli(
        planned, "backlink_publisher.cli.quality_gate",
    )

    if args.emit_stderr:
        print(
            f"auto-recover: (phase 4) {len(seeds)} input, "
            f"{len(planned)} planned, {len(gated)} quality-gated",
            file=sys.stderr,
        )
    return gated


# ── Phase 5 — Publish (subprocess, skipped in --dry-run) ────────────────────


def _run_publish_phase(
    publishable_rows: list[dict],
    store: EventStore,
    args: Any,
) -> list[dict]:
    """Phase 5 — Pipe rows through publish-backlinks.

    In --dry-run mode, emits a summary to stderr and returns empty results.
    Otherwise pipes through publish-backlinks and writes
    ``channel.published_to`` events for each successful publish.
    """
    if not publishable_rows:
        return []

    if getattr(args, "dry_run", False):
        print(
            f"auto-recover: (phase 5) dry-run — would publish "
            f"{len(publishable_rows)} row(s)",
            file=sys.stderr,
        )
        return []

    publish_args = [f"--mode={args.mode}"]
    results = _pipe_through_cli(
        publishable_rows, "backlink_publisher.cli.publish_backlinks",
        extra_args=publish_args,
    )

    # Write channel.published_to events for successful publishes.
    published_count = 0
    for row in results:
        platform = row.get("platform") or row.get("assigned_channel")
        live_url = row.get("live_url") or row.get("url", "")
        target_url = row.get("target_url", "")
        if platform and live_url:
            write_published_to_event(
                store,
                platform=platform,
                live_url=live_url,
                target_url=target_url,
                status="published",
            )
            published_count += 1

    if args.emit_stderr:
        print(
            f"auto-recover: (phase 5) {len(results)} publish result(s), "
            f"{published_count} channel.published_to event(s) written",
            file=sys.stderr,
        )
    return results


# ── Phase 6 — Report ────────────────────────────────────────────────────────


def _emit_report(
    decisions: list[RoutingDecision],
    publish_results: list[dict],
    health: dict[str, Any],
) -> None:
    """Phase 6 — Emit JSONL report to stdout with routing decisions and results."""
    for decision in decisions:
        row: dict[str, Any] = {
            "phase": "routing",
            "dead_live_url": decision.dead_live_url,
            "target_url": decision.target_url,
            "original_platform": decision.original_platform,
            "assigned_channel": decision.assigned_channel,
            "reason": decision.reason,
            "source_survival_rate": decision.source_survival_rate,
            "target_survival_rate": decision.target_survival_rate,
        }
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")

    for result in publish_results:
        row = dict(result)
        row.setdefault("phase", "publish")
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Summary row.
    summary: dict[str, Any] = {
        "phase": "summary",
        "routing_count": len(decisions),
        "publish_count": len(publish_results),
        "rerouted_count": sum(
            1 for d in decisions
            if d.assigned_channel != d.original_platform
        ),
        "health_snapshot": {
            ch: {
                "survival_rate": h.survival_rate,
                "total_rechecks": h.total_rechecks,
                "dead_count": h.dead_count,
            }
            for ch, h in health.items()
            if hasattr(h, "survival_rate")
        },
    }
    sys.stdout.write(json.dumps(summary, ensure_ascii=False) + "\n")


# ── Flock protection ────────────────────────────────────────────────────────


@contextlib.contextmanager
def _single_run_lock(cache_dir: Path):
    """Non-blocking exclusive flock so overlapping cron runs don't compound."""
    import fcntl

    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / ".auto-recover.lock"
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


# ── Main ────────────────────────────────────────────────────────────────────


def _resolve_config() -> Path:
    """Resolve the config dir via the canonical loader helper.

    Delegates to ``config.loader._config_dir()`` — the single source of truth
    for the operator-state root (env override + platform default + test-sandbox
    fail-closed). Avoids a raw ``Path.home()`` primitive (see
    tests/test_no_raw_home_path_primitives.py).
    """
    from backlink_publisher.config.loader import _config_dir
    return _config_dir()


def _resolve_cache() -> Path:
    """Resolve the cache dir via the canonical loader helper.

    Delegates to ``config.loader._cache_dir()`` for the same reasons as
    ``_resolve_config``.
    """
    from backlink_publisher.config.loader import _cache_dir
    return _cache_dir()


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="auto-recover",
        description=(
            "Run the full survival-rate optimisation closed loop: "
            "recheck → health → route → replan → quality-gate → publish. "
            "Without --probe this is a zero-network dry preview with routing report."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report routing decisions without publishing",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="enable network re-verification (default: zero-network dry preview)",
    )
    parser.add_argument(
        "--max-dead", type=int, metavar="N",
        help="cap number of dead links processed per run",
    )
    parser.add_argument(
        "--max-routing", type=int, metavar="N",
        help="cap number of seeds sent to plan/publish per run",
    )
    parser.add_argument(
        "--routing-threshold", type=float, default=0.7, metavar="T",
        help="survival rate threshold for routing (default: 0.7)",
    )
    parser.add_argument(
        "--days", type=int, default=7, metavar="N",
        help="recency window in days for recheck and replan (default: 7)",
    )
    parser.add_argument(
        "--mode", default="draft", choices=("draft", "publish"),
        help="publish mode (default: draft)",
    )
    parser.add_argument(
        "--language", default="en", metavar="LANG",
        help="seed language (default: en)",
    )
    parser.add_argument(
        "--url-mode", default="A", choices=("A", "B", "C"),
        help="seed url_mode (default: A)",
    )
    parser.add_argument(
        "--publish-mode", default="draft", metavar="MODE",
        help="seed publish_mode (default: draft)",
    )
    parser.add_argument(
        "--emit-stderr", action="store_true",
        help="emit diagnostic stderr lines",
    )
    args = parser.parse_args(argv)

    # Validation.
    if args.days <= 0:
        emit_error("auto-recover: --days must be a positive integer", exit_code=1)
    if args.routing_threshold < 0 or args.routing_threshold > 1.0:
        emit_error(
            "auto-recover: --routing-threshold must be between 0 and 1",
            exit_code=1,
        )

    cfg = load_config()
    cache_dir = _resolve_cache()
    store = EventStore()

    # ── Flock ────────────────────────────────────────────────────────────────
    with _single_run_lock(cache_dir) as acquired:
        if not acquired:
            print(
                "auto-recover: another run holds the lock; skipping",
                file=sys.stderr,
            )
            raise SystemExit(0)

        since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)

        if args.emit_stderr:
            print(
                f"auto-recover: started (days={args.days}, "
                f"mode={args.mode}, dry_run={args.dry_run})",
                file=sys.stderr,
            )

        # ── Phase 1: Recheck ─────────────────────────────────────────────────
        if args.emit_stderr:
            print("auto-recover: --- phase 1 (recheck) ---", file=sys.stderr)
        results = _run_recheck_phase(store, since_dt, args)

        # ── Phase 2: Health Update ───────────────────────────────────────────
        if args.emit_stderr:
            print("auto-recover: --- phase 2 (health update) ---", file=sys.stderr)
        health = _run_health_phase(store, args)

        # ── Phase 3: Routing ─────────────────────────────────────────────────
        if args.emit_stderr:
            print("auto-recover: --- phase 3 (routing) ---", file=sys.stderr)
        registry = ChannelHealthRegistry(store)
        decisions, seeds = _run_routing_phase(store, registry, since_dt, args)

        if not seeds:
            if args.emit_stderr:
                print(
                    "auto-recover: no seeds to process; pipeline complete",
                    file=sys.stderr,
                )
            _emit_report(decisions, [], health)
            return

        # ── Phase 4: Replan + Quality Gate ───────────────────────────────────
        if args.emit_stderr:
            print(
                "auto-recover: --- phase 4 (replan + quality) ---",
                file=sys.stderr,
            )
        publishable = _run_replan_quality_phase(seeds, args)

        # ── Phase 5: Publish ─────────────────────────────────────────────────
        if args.emit_stderr:
            print("auto-recover: --- phase 5 (publish) ---", file=sys.stderr)
        publish_results = _run_publish_phase(publishable, store, args)

        # ── Phase 6: Report ──────────────────────────────────────────────────
        _emit_report(decisions, publish_results, health)

        if args.emit_stderr:
            print("auto-recover: pipeline complete", file=sys.stderr)


if __name__ == "__main__":
    main()
