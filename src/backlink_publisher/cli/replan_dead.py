"""replan-dead — dead-link auto-replan pipeline (Phase B, Plan 2026-06-07-002).

Reads ``link.rechecked`` events from events.db, selects recent deterministic
dead links (host_gone/link_stripped), extracts the target_url, and emits
``plan-backlinks``-compatible seed JSONL on stdout — one seed per dead link
per platform.

Designed to compose in a shell pipeline:

    recheck-backlinks --probe | replan-dead | plan-backlinks | publish-backlinks

Exit 0 advisory; --fail-on-unresolved exits 6. No automatic pipeline wiring.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

from .._util.errors import emit_error
from .._util.jsonl import write_jsonl
from backlink_publisher.events.store import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import verdicts


def _deterministic_dead_events(
    store: EventStore,
    since_dt: datetime,
) -> list[dict]:
    """Query ``link.rechecked`` events within recency window with deterministic dead verdicts.

    Returns list of dicts: {live_url, target_url, host, platform, verdict}.
    """
    rows = store.query(
        "SELECT ts_utc, target_url, host, payload_json FROM events "
        "WHERE kind = ? AND target_url IS NOT NULL "
        "ORDER BY ts_utc DESC",
        (LINK_RECHECKED,),
    )
    results: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        ts_str = row["ts_utc"]
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < since_dt:
                continue
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        verdict = payload.get("verdict")
        if verdict not in verdicts.DETERMINISTIC_DEAD:
            continue
        live_url = payload.get("live_url")
        if not live_url:
            continue
        # Deduplicate: latest verdict per live_url wins (we queried DESC).
        if live_url in seen:
            continue
        seen.add(live_url)
        results.append({
            "live_url": live_url,
            "target_url": row["target_url"],
            "host": row["host"],
            "platform": payload.get("platform"),
            "verdict": verdict,
        })
    return results


def _get_resolved_urls(store: EventStore) -> set[str]:
    """Return set of live_urls whose latest remediation action is resolve."""
    from backlink_publisher.remediation.events_io import resolved_live_urls
    return resolved_live_urls(store)


def _count_live_dofollow_for_target(
    store: EventStore,
    target_url: str,
) -> int:
    """Count how many publish.confirmed live-dofollow links exist for a target_url."""
    rows = store.query(
        "SELECT payload_json FROM events "
        "WHERE kind = 'publish.confirmed' AND target_url = ?",
        (target_url,),
    )
    count = 0
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            continue
        # Only count if live_url is present (meaning it was actually published)
        if payload.get("live_url"):
            count += 1
    return count


def _build_seed(
    live_url: str,
    target_url: str,
    host: str | None,
    platform: str | None,
    language: str,
    url_mode: str,
    publish_mode: str,
) -> dict:
    """Build a plan-backlinks-compatible seed row."""
    seed: dict = {
        "target_url": target_url,
        "language": language,
        "url_mode": url_mode,
        "publish_mode": publish_mode,
    }
    if platform:
        seed["platform"] = platform
    # Carry dead-link provenance for operator visibility
    seed["_replan_provenance"] = {
        "dead_live_url": live_url,
        "host": host,
        "reason": "dead_link_auto_replan",
    }
    return seed


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="replan-dead",
        description=(
            "Read link.rechecked events from events.db, select recent "
            "deterministic dead links (host_gone/link_stripped), and emit "
            "plan-backlinks-compatible seed JSONL on stdout — one seed per "
            "dead link per platform. Reads events.db directly (no stdin)."
        ),
    )
    parser.add_argument(
        "--days", type=int, default=7, metavar="N",
        help="Recency window in days (default: 7)",
    )
    parser.add_argument(
        "--min-gap", type=int, default=3, metavar="M",
        help="Minimum live-dofollow count before re-planning (default: 3; "
             "skip targets with >= M live links)",
    )
    parser.add_argument(
        "--language", default="en", metavar="LANG",
        help="Seed language (default: en)",
    )
    parser.add_argument(
        "--url-mode", default="A", metavar="MODE",
        help="Seed url_mode (A|B|C; default: A)",
    )
    parser.add_argument(
        "--publish-mode", default="draft", metavar="MODE",
        help="Seed publish_mode (draft|publish; default: draft)",
    )
    parser.add_argument(
        "--emit-stderr", action="store_true",
        help="Emit diagnostic stderr lines (default: minimal)",
    )
    args = parser.parse_args(argv)

    # Validation
    if args.days <= 0:
        emit_error("replan-dead: --days must be a positive integer", exit_code=1)
    if args.min_gap < 0:
        emit_error("replan-dead: --min-gap must be a non-negative integer", exit_code=1)
    if args.url_mode not in ("A", "B", "C"):
        emit_error("replan-dead: --url-mode must be one of A, B, C", exit_code=1)

    since_dt = datetime.now(timezone.utc) - timedelta(days=args.days)
    store = EventStore()

    # Step 1: Get deterministic dead events within recency window
    dead_events = _deterministic_dead_events(store, since_dt)
    if not dead_events:
        if args.emit_stderr:
            print(f"replan-dead: 0 dead links found in last {args.days} days", file=sys.stderr)
        return

    if args.emit_stderr:
        print(
            f"replan-dead: {len(dead_events)} unique dead link(s) found in "
            f"last {args.days} days",
            file=sys.stderr,
        )

    # Step 2: Load resolved URLs to skip
    resolved = _get_resolved_urls(store)
    if resolved and args.emit_stderr:
        print(f"replan-dead: {len(resolved)} resolved link(s) excluded", file=sys.stderr)

    # Step 3: Group dead events by target_url, skip resolved, check min-gap
    # Group first: deduplicate target_url+platform combos
    seen_combos: set[tuple[str, str]] = set()
    seeds: list[dict] = []
    skipped_resolved = 0
    skipped_gap_met = 0

    for ev in dead_events:
        live_url = ev["live_url"]
        target_url = ev["target_url"]
        platform = ev.get("platform") or "unknown"
        host = ev.get("host")

        # Skip if already resolved by operator
        if live_url in resolved:
            skipped_resolved += 1
            continue

        # Deduplicate per (target_url, platform)
        combo = (target_url, platform)
        if combo in seen_combos:
            continue
        seen_combos.add(combo)

        # Check min-gap: count live-dofollow links for this target
        live_count = _count_live_dofollow_for_target(store, target_url)
        if live_count >= args.min_gap:
            skipped_gap_met += 1
            if args.emit_stderr:
                print(
                    f"replan-dead: skip {target_url} — already has {live_count} "
                    f"live link(s) >= --min-gap={args.min_gap}",
                    file=sys.stderr,
                )
            continue

        seed = _build_seed(
            live_url=live_url,
            target_url=target_url,
            host=host,
            platform=platform,
            language=args.language,
            url_mode=args.url_mode,
            publish_mode=args.publish_mode,
        )
        seeds.append(seed)

    # Step 4: Emit seeds on stdout
    write_jsonl(iter(seeds), sys.stdout)

    if args.emit_stderr:
        total_skipped = skipped_resolved + skipped_gap_met
        print(
            f"replan-dead: emitted {len(seeds)} seed(s); "
            f"skipped resolved={skipped_resolved} gap_met={skipped_gap_met}",
            file=sys.stderr,
        )
        if not seeds:
            print(
                "replan-dead: 0 seeds emitted (all dead links are resolved or "
                "already have sufficient live coverage) — downstream "
                "plan-backlinks will exit 2 on the empty stream.",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()