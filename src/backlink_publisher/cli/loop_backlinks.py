"""loop-backlinks — Pipeline Closed-Loop: recheck verdicts → re-plan seeds.

Reads ``link.rechecked`` events from events.db, groups by target_url,
and emits ``plan-backlinks``-compatible seed JSONL for targets whose
backlinks have decayed (host_gone / link_stripped / dofollow_lost).

Designed to be piped into ``plan-backlinks``:

    loop-backlinks | plan-backlinks | validate-backlinks | publish-backlinks

Or read via MCP for AI-agent orchestrated repair cycles.

Exit codes:
  0 — success (may emit zero seeds if no decay detected)
  1 — usage error

Plan: ulw Wave 3 — Pipeline Closed-Loop (recheck → plan-gap feedback).
"""

from __future__ import annotations

import json
import sys
from collections import Counter

from .. import config_echo
from .._util.errors import emit_error
from .._util.jsonl import write_jsonl
from .._util.logger import get_logger
from ..config import load_config

_log = get_logger("loop")

#: Default target per-target live-dofollow count.
_DEFAULT_DESIRED = 3

#: Default language for re-plan seeds.
_DEFAULT_LANGUAGE = "en"


def _query_decayed_targets(
    min_dead: int = 1,
    window_days: int | None = None,
) -> list[dict]:
    """Query events.db for targets with decayed backlinks.

    Reads the latest ``link.rechecked`` verdict per article, groups by
    target_url, and returns targets whose dead/drift count >= min_dead.

    Returns list of dicts with keys: ``target_url``, ``dead_count``,
    ``drift_count``, ``alive_count``, ``total_checked``, ``latest_verdict``.
    """
    from backlink_publisher.events.store import EventStore
    from backlink_publisher.recheck.verdicts import (
        DETERMINISTIC_DEAD,
        DOFOLLOW_LOST,
    )

    store = EventStore()

    # Build WHERE clause for optional recency filter
    where_extra = ""
    params: list[str | int] = []
    if window_days is not None:
        where_extra = "AND e.ts_utc >= datetime('now', ?)"
        params.append(f"-{window_days} days")

    rows = store.query(
        """
        SELECT e.id, e.kind, e.target_url, e.payload_json, e.article_id
        FROM events e
        WHERE e.kind = ?
        {extra}
        ORDER BY e.article_id, e.ts_utc ASC
        """.format(extra=where_extra),
        ("link.rechecked", *params),
    )

    # Group by target_url — keep only the LATEST verdict per article
    from datetime import datetime, timezone

    latest_per_article: dict[int, dict] = {}  # article_id -> row
    row_id_seen: set[int] = set()

    for row in rows:
        rid: int = row[0]
        if rid in row_id_seen:
            continue
        row_id_seen.add(rid)

        article_id: int | None = row[4]
        if article_id is not None:
            # Deduplicate by article_id — later rows win (latest verdict per article)
            latest_per_article[article_id] = {
                "target_url": row[2],
                "payload": json.loads(row[3]) if isinstance(row[3], str) else row[3] or {},
            }

    # Now aggregate by target_url
    target_verdicts: dict[str, Counter] = {}
    for info in latest_per_article.values():
        target = info.get("target_url")
        if not target:
            continue
        payload = info.get("payload", {})
        verdict = payload.get("verdict", "unknown")

        if target not in target_verdicts:
            target_verdicts[target] = Counter()
        target_verdicts[target][verdict] += 1

    # Build result set
    results: list[dict] = []
    for target, counts in target_verdicts.items():
        dead_count = sum(counts.get(v, 0) for v in DETERMINISTIC_DEAD)
        drift_count = counts.get(DOFOLLOW_LOST, 0)
        alive_count = counts.get("alive", 0)
        total = dead_count + drift_count + alive_count

        if total < min_dead and dead_count == 0:
            continue

        # Find the dominant failure verdict
        if dead_count > 0:
            latest_verdict = "dead"
        elif drift_count > 0:
            latest_verdict = "drifting"
        else:
            latest_verdict = "alive"

        results.append({
            "target_url": target,
            "dead_count": dead_count,
            "drift_count": drift_count,
            "alive_count": alive_count,
            "total_checked": total,
            "latest_verdict": latest_verdict,
        })

    # Sort: worst first (most dead → most drift → most alive)
    results.sort(key=lambda r: (-r["dead_count"], -r["drift_count"], r["alive_count"]))
    return results


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="loop-backlinks",
        description=(
            "Pipeline closed-loop: read link.rechecked events from events.db "
            "and emit plan-backlinks-compatible seed JSONL for targets with "
            "decayed backlinks."
        ),
    )
    parser.add_argument(
        "--desired", type=int, default=_DEFAULT_DESIRED, metavar="N",
        help=f"Target per-target live-dofollow count (default: {_DEFAULT_DESIRED}).",
    )
    parser.add_argument(
        "--language", default=_DEFAULT_LANGUAGE, metavar="LANG",
        help=f"Language for re-plan seeds (default: {_DEFAULT_LANGUAGE}).",
    )
    parser.add_argument(
        "--min-dead", type=int, default=1, metavar="N",
        help="Minimum dead links to trigger re-plan (default: 1).",
    )
    parser.add_argument(
        "--window-days", type=int, default=None, metavar="N",
        help="Only consider recheck events within this many days (default: all time).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview decayed targets without emitting seed JSONL.",
    )
    parser.add_argument(
        "--log-level", default="WARN",
        help="Log verbosity (DEBUG/INFO/WARN/ERROR).",
    )
    args = parser.parse_args(argv)

    _log.setLevel(args.log_level.upper())

    cfg = load_config()
    config_echo.emit_banner(cfg, "loop-backlinks")

    decayed = _query_decayed_targets(
        min_dead=args.min_dead,
        window_days=args.window_days,
    )

    if args.dry_run:
        # Print a human-readable table of decayed targets
        if not decayed:
            print("No targets with decayed backlinks found.")
            return
        print(f"Targets requiring re-plan ({len(decayed)}):")
        print(f"  {'TARGET':50s} {'DEAD':>5s} {'DRIFT':>5s} {'ALIVE':>5s} STATUS")
        for t in decayed:
            print(f"  {t['target_url'][:50]:50s} {t['dead_count']:5d} {t['drift_count']:5d} {t['alive_count']:5d} {t['latest_verdict']}")
        return

    # Emit plan-backlinks-compatible seed JSONL
    seeds_emitted = 0
    for t in decayed:
        seed = {
            "target_url": t["target_url"],
            "language": args.language,
            "desired": args.desired,
            "reason": f"recheck: {t['dead_count']} dead, {t['drift_count']} drifted ({t['latest_verdict']})",
        }
        sys.stdout.write(json.dumps(seed, ensure_ascii=False) + "\n")
        seeds_emitted += 1

    # stderr: recon line
    print(
        f"RECON loop_backlinks targets={len(decayed)} seeds_emitted={seeds_emitted}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
