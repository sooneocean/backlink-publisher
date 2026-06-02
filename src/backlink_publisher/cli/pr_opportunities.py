"""``pr-opportunities`` — ingest and list PR / earned-link opportunities.

Sub-commands:

  ingest   Read a JSONL digest from stdin; score each entry; upsert into the
           opportunity store.  Each input row must be a JSON object with at
           least ``id`` and ``headline`` fields.

  list     Print all stored opportunities as JSONL, optionally filtered by
           status.  stdout = JSONL; stderr = summary.

Typical pipeline usage::

    cat haro-digest.jsonl | pr-opportunities ingest
    pr-opportunities list --status pending

Drafts are composed separately (via ``generate-backlink-text``) and updated
with ``pr-opportunities update-status --id <id> --status draft --draft <text>``.
No automated submission is performed.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from backlink_publisher.pr_outreach.store import STATUS_ENUM, load_opportunities, upsert_opportunity, update_status


def _load_config_targets() -> dict[str, Any]:
    """Return the [targets.*] section from config, or {} on any error."""
    try:
        from backlink_publisher.config.loader import load_config

        cfg = load_config()
        return cfg.get("targets") or {}
    except Exception:
        return {}


def _cmd_ingest(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="pr-opportunities ingest")
    parser.add_argument(
        "--source",
        default=None,
        metavar="NAME",
        help="Source tag injected into every entry (e.g. 'featured', 'sos'). "
             "Per-row 'source' field takes priority.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        metavar="N",
        help="Skip entries with relevance score below N (default: 0 — ingest all).",
    )
    args = parser.parse_args(argv)

    from backlink_publisher.pr_outreach.scorer import build_topic_tokens, score_opportunity

    topic_tokens = build_topic_tokens(_load_config_targets())

    ingested = 0
    skipped = 0
    for lineno, raw in enumerate(sys.stdin, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"pr-opportunities ingest: line {lineno}: invalid JSON — {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(row, dict):
            print(f"pr-opportunities ingest: line {lineno}: expected JSON object", file=sys.stderr)
            sys.exit(1)
        if not row.get("id"):
            print(f"pr-opportunities ingest: line {lineno}: missing 'id' field — skipping", file=sys.stderr)
            skipped += 1
            continue

        score = score_opportunity(row, topic_tokens)
        if score < args.min_score:
            skipped += 1
            continue

        entry: dict[str, Any] = {**row, "relevance_score": score}
        if args.source and not entry.get("source"):
            entry["source"] = args.source
        entry.setdefault("status", "pending")
        upsert_opportunity(entry)
        ingested += 1

    print(
        f"pr-opportunities ingest: {ingested} ingested, {skipped} skipped",
        file=sys.stderr, flush=True,
    )


def _cmd_list(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="pr-opportunities list")
    parser.add_argument(
        "--status",
        default=None,
        choices=sorted(STATUS_ENUM),
        help="Filter by status (default: all).",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=None,
        metavar="N",
        help="Only show entries with relevance_score >= N.",
    )
    args = parser.parse_args(argv)

    rows = load_opportunities()

    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    if args.min_score is not None:
        rows = [r for r in rows if (r.get("relevance_score") or 0) >= args.min_score]

    rows.sort(key=lambda r: r.get("relevance_score") or 0, reverse=True)

    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)

    print(f"pr-opportunities list: {len(rows)} entries", file=sys.stderr, flush=True)


def _cmd_update_status(argv: list[str]) -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="pr-opportunities update-status")
    parser.add_argument("--id", required=True, dest="opp_id", metavar="ID")
    parser.add_argument(
        "--status", required=True, choices=sorted(STATUS_ENUM), metavar="STATUS"
    )
    parser.add_argument(
        "--draft", default=None, metavar="TEXT", help="Draft response text."
    )
    args = parser.parse_args(argv)

    saved = update_status(args.opp_id, args.status, draft=args.draft)
    print(json.dumps(saved, ensure_ascii=False), flush=True)
    print(
        f"pr-opportunities update-status: {args.opp_id} → {args.status}",
        file=sys.stderr, flush=True,
    )


_CMDS = {
    "ingest": _cmd_ingest,
    "list": _cmd_list,
    "update-status": _cmd_update_status,
}


def main(argv: list[str] | None = None) -> None:
    args = list(argv) if argv is not None else sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(
            "usage: pr-opportunities <command> [options]\n"
            "commands: ingest | list | update-status\n"
            "run 'pr-opportunities <command> --help' for per-command help",
            file=sys.stderr,
        )
        sys.exit(0 if not args else 1)

    cmd_name, *rest = args
    cmd = _CMDS.get(cmd_name)
    if cmd is None:
        print(f"pr-opportunities: unknown command {cmd_name!r}", file=sys.stderr)
        sys.exit(1)
    cmd(rest)


if __name__ == "__main__":  # pragma: no cover
    main()
