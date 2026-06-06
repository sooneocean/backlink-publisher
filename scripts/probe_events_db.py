#!/usr/bin/env python3
"""Measurement probe for the events.db read-side projection.

Prototype of R1/R2 from
docs/brainstorms/2026-06-01-events-db-optimization-tripwire-requirements.md
(plan docs/plans/2026-06-01-008-feat-events-db-measurement-probe-and-tripwire-register-plan.md).

Answers, in one run: how big is events.db right now (row counts + file sizes),
and what query plan + coarse wall-time does each recurring read path resolve to?
This is the evidence that anchors the scale-tripwire register and gates the
optional guard index. It is a throwaway diagnostic, not maintained production
code: re-run it and compare against the register's thresholds.

Stdlib only. Read-only. Never raises.

Usage:
  python scripts/probe_events_db.py                 # probe the default events.db
  python scripts/probe_events_db.py --db PATH        # probe a specific DB (e.g. a seeded large one)
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

# A far-past window bound so time-windowed queries include every row. The real
# callers pass a recent `since_utc`; for plan-shape measurement we want the whole
# table to participate, so the EXPLAIN/timing reflects the worst case.
_SINCE_ALL = "2000-01-01T00:00:00+00:00"

_TERMINAL_KINDS = ("publish.confirmed", "publish.unverified", "publish.failed")
_LINK_RECHECKED = "link.rechecked"
_PUBLISH_CONFIRMED = "publish.confirmed"

#: (label, sql, params) for each recurring read path. SQL is copied verbatim from
#: the live callers so EXPLAIN reflects reality:
#:   - success_rate / per_adapter / error_distribution : webui_app/health_metrics.py
#:   - decay_counts                                     : recheck/events_io.py
#:   - recheck_universe                                 : recheck/selection.py
#:   - ledger_article_scan                              : ledger/sources.py
_PLACEHOLDERS = ",".join("?" for _ in _TERMINAL_KINDS)
_QUERIES: list[tuple[str, str, tuple[object, ...]]] = [
    (
        "health.success_rate (build_health)",
        f"""
        WITH latest AS (
            SELECT target_url, kind,
                   ROW_NUMBER() OVER (
                       PARTITION BY target_url
                       ORDER BY ts_utc DESC, id DESC
                   ) AS rn
            FROM events
            WHERE kind IN ({_PLACEHOLDERS})
              AND ts_utc >= ?
              AND target_url IS NOT NULL
        )
        SELECT COUNT(*) AS targets,
               SUM(CASE WHEN kind = 'publish.confirmed' THEN 1 ELSE 0 END) AS confirmed
        FROM latest WHERE rn = 1
        """,
        (*_TERMINAL_KINDS, _SINCE_ALL),
    ),
    (
        "health.per_adapter (json_extract GROUP BY platform)",
        f"""
        SELECT json_extract(payload_json, '$.platform') AS platform,
               SUM(CASE WHEN kind = 'publish.confirmed' THEN 1 ELSE 0 END) AS confirmed,
               COUNT(*) AS total
        FROM events
        WHERE kind IN ({_PLACEHOLDERS}) AND ts_utc >= ?
        GROUP BY platform
        """,
        (*_TERMINAL_KINDS, _SINCE_ALL),
    ),
    (
        "health.error_distribution (json_extract GROUP BY error_class)",
        """
        SELECT json_extract(payload_json, '$.error_class') AS error_class, COUNT(*) AS count
        FROM events
        WHERE kind = 'publish.failed' AND ts_utc >= ?
        GROUP BY error_class
        ORDER BY count DESC, error_class
        """,
        (_SINCE_ALL,),
    ),
    (
        "recheck.derive_decay_counts",
        "SELECT article_id, payload_json, ts_utc FROM events "
        "WHERE kind = ? AND article_id IS NOT NULL",
        (_LINK_RECHECKED,),
    ),
    (
        "recheck.selection_universe",
        "SELECT article_id, payload_json, target_url, host, ts_utc "
        "FROM events WHERE kind = ? AND article_id IS NOT NULL ORDER BY ts_utc",
        (_PUBLISH_CONFIRMED,),
    ),
    (
        "ledger.sources_article_scan",
        "SELECT target_urls_json, live_url FROM articles",
        (),
    ),
]


def _resolve_db_path(argv: list[str]) -> str:
    """`--db PATH` wins; else BACKLINK_PUBLISHER_CONFIG_DIR/events.db; else default."""
    if "--db" in argv:
        i = argv.index("--db")
        if i + 1 < len(argv):
            return os.path.expanduser(argv[i + 1])
    cfg = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR", "~/.config/backlink-publisher")
    return os.path.join(os.path.expanduser(cfg), "events.db")


def _print_counts(con: sqlite3.Connection) -> None:
    print("=== row counts ===")
    for table in ("events", "articles", "quarantine_log"):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608 - fixed names
            print(f"  {table:16s} {n}")
        except sqlite3.Error as exc:
            print(f"  {table:16s} <error: {exc}>", file=sys.stderr)


def _print_sizes(path: str) -> None:
    print("\n=== file sizes ===")
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        try:
            size = os.path.getsize(p)
            print(f"  {os.path.basename(p):24s} {size/1024:.1f}KB ({size}B)")
        except OSError:
            print(f"  {os.path.basename(p):24s} <absent>")


def _print_query_plans(con: sqlite3.Connection) -> None:
    print("\n=== EXPLAIN QUERY PLAN + coarse timing (whole-table window) ===")
    for label, sql, params in _QUERIES:
        print(f"\n-- {label}")
        try:
            plan = con.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
            t0 = time.perf_counter()
            con.execute(sql, params).fetchall()
            dt_ms = (time.perf_counter() - t0) * 1000.0
            for row in plan:
                # rows: (id, parent, notused, detail)
                print(f"     {row[-1]}")
            print(f"     ~{dt_ms:.2f}ms")
        except sqlite3.Error as exc:
            print(f"     <query error: {exc}>", file=sys.stderr)


def main(argv: list[str]) -> int:
    path = _resolve_db_path(argv)
    if not os.path.exists(path):
        print(f"events.db not found at {path}", file=sys.stderr)
        print("set BACKLINK_PUBLISHER_CONFIG_DIR or pass --db PATH", file=sys.stderr)
        return 1
    print(f"# events.db probe — {path}", file=sys.stderr)
    con = None
    try:
        # Read-only by construction: mode=ro cannot create or migrate the file.
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        _print_counts(con)
        _print_sizes(path)
        _print_query_plans(con)
    except sqlite3.Error as exc:
        print(f"could not open events.db read-only: {exc}", file=sys.stderr)
        return 1
    finally:
        if con is not None:
            con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
