"""plan-gap — deficit-driven re-plan verb (thin I/O shell over gap.engine).

Reads ``equity-ledger`` JSONL on stdin, emits ``plan-backlinks``-compatible seed
JSONL on stdout. stdout = data (JSONL); stderr = config banner + RECON. Exit 0
on success (advisory diagnostic), exit 1 on usage error, exit 2 on malformed
stdin. Composes as ``equity-ledger | plan-gap | plan-backlinks``.

Plan 2026-05-29-007.
"""

from __future__ import annotations

import json
import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before lookups
from .. import config_echo
from backlink_publisher._util.errors import emit_error
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.config import load_config
from backlink_publisher.gap.engine import GapOptions, plan_gap
from backlink_publisher.linkcheck.language import SUPPORTED_LANGUAGES
from backlink_publisher.schema import PUBLISH_MODES, URL_MODES


def _load_desired_map(path: str) -> dict[str, int]:
    """Read a JSON file mapping ``target_url`` -> desired dofollow count (int)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        emit_error(f"plan-gap: --desired-map could not be read: {exc}", exit_code=1)
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0
        for k, v in data.items()
    ):
        emit_error(
            "plan-gap: --desired-map must be a JSON object of target_url -> "
            "non-negative integer",
            exit_code=1,
        )
    return data


def main(argv: list[str] | None = None) -> None:
    import argparse

    langs = ", ".join(sorted(SUPPORTED_LANGUAGES))
    parser = argparse.ArgumentParser(
        prog="plan-gap",
        description=(
            "Deficit-driven re-plan: read equity-ledger JSONL on stdin and emit "
            "plan-backlinks seed JSONL on stdout, fanning each under-linked target "
            "out across the active dofollow platforms it lacks a live-dofollow link "
            "on. Pure transform; pipe into plan-backlinks."
        ),
    )
    parser.add_argument("--desired", type=int, default=None, metavar="D",
                        help="REQUIRED. Target per-target live-dofollow count.")
    parser.add_argument("--language", default=None, metavar="LANG",
                        help=f"REQUIRED. Seed language; one of: {langs}.")
    parser.add_argument("--desired-map", default=None, metavar="FILE",
                        help="JSON file: target_url -> desired int (per-target override of --desired).")
    parser.add_argument("--stale-after", type=int, default=None, metavar="N",
                        help="Freshness floor: suppress targets whose liveness was verified > N days ago "
                             "(unless --emit-stale). Default: no floor.")
    parser.add_argument("--emit-stale", action="store_true",
                        help="Include stale/unverified/old targets instead of suppressing them.")
    parser.add_argument("--include-failed", action="store_true",
                        help="Include targets whose liveness is 'failed' (default: skip).")
    parser.add_argument("--url-mode", default="A", metavar="MODE",
                        help="Seed url_mode (A|B|C; default: A).")
    parser.add_argument("--publish-mode", default="draft", metavar="MODE",
                        help="Seed publish_mode (draft|publish; default: draft).")
    args = parser.parse_args(argv)

    # Post-parse validation: UsageError-style exit 1 (no argparse choices=/required=,
    # which exit 2 and clash with the repo's exit-code contract).
    if args.desired is None:
        emit_error("plan-gap: --desired is required (per-target live-dofollow target)", exit_code=1)
    if args.desired < 0:
        emit_error("plan-gap: --desired must be a non-negative integer", exit_code=1)
    if args.language is None:
        emit_error("plan-gap: --language is required (no silent default)", exit_code=1)
    if args.language not in SUPPORTED_LANGUAGES:
        emit_error(f"plan-gap: --language must be one of: {langs}", exit_code=1)
    if args.url_mode not in URL_MODES:
        emit_error("plan-gap: --url-mode must be one of A, B, C", exit_code=1)
    if args.publish_mode not in PUBLISH_MODES:
        emit_error("plan-gap: --publish-mode must be one of draft, publish", exit_code=1)
    if args.stale_after is not None and args.stale_after < 0:
        emit_error("plan-gap: --stale-after must be a non-negative integer", exit_code=1)
    desired_map = _load_desired_map(args.desired_map) if args.desired_map else {}

    cfg = load_config()
    config_echo.emit_banner(cfg, "plan-gap")
    print(
        f"plan-gap: seed defaults language={args.language} url_mode={args.url_mode} "
        f"publish_mode={args.publish_mode} desired={args.desired} overrides={len(desired_map)}",
        file=sys.stderr,
    )

    # Buffer stdin so an empty ledger is a success (advisory + exit 0), not the
    # strict read_jsonl exit-2. Malformed JSON still exits 2 (strict). Split on
    # "\n" only (not str.splitlines) to match read_jsonl's line semantics and
    # avoid splitting a row on embedded U+2028/U+2029 (json.dumps leaves them raw).
    lines = sys.stdin.read().split("\n")
    if not any(line.strip() for line in lines):
        print("plan-gap: 0 targets to re-plan — empty ledger input.", file=sys.stderr)
        return
    rows = list(read_jsonl(lines, strict=True))

    opts = GapOptions(
        desired=args.desired,
        language=args.language,
        url_mode=args.url_mode,
        publish_mode=args.publish_mode,
        desired_map=desired_map,
        emit_stale=args.emit_stale,
        include_failed=args.include_failed,
        stale_after_days=args.stale_after,
    )
    seeds, counts, meta = plan_gap(rows, opts)
    write_jsonl(seeds, sys.stdout)

    as_of = meta.get("as_of") or "unknown"
    print(
        f"plan-gap: liveness as-of {as_of}; emitted {len(seeds)} seed(s); "
        f"suppressed satisfied={counts.satisfied} stale={counts.suppressed_stale} "
        f"unverified={counts.suppressed_unverified} stale_floor={counts.suppressed_stale_floor} "
        f"failed={counts.failed} unknown_liveness={counts.unknown_liveness} "
        f"malformed={counts.malformed} channel_exhausted={counts.channel_exhausted}",
        file=sys.stderr,
    )
    if counts.channel_exhausted_targets:
        print(
            "plan-gap: channel_exhausted (cannot reach D under current dofollow roster): "
            + ", ".join(counts.channel_exhausted_targets),
            file=sys.stderr,
        )
    if not seeds:
        print(
            "plan-gap: 0 seeds emitted (all satisfied/suppressed) — downstream "
            "plan-backlinks will exit 2 on the empty stream; this is the normal "
            "end of a satisfied pipe, not a failure.",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
