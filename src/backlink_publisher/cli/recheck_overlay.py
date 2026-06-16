"""recheck-overlay — read-only deficit overlay between equity-ledger and plan-gap.

Reads ``equity-ledger`` JSONL on stdin, discounts each target's ``live_dofollow``
by the latest ``link.rechecked`` dead / ``dofollow_lost`` verdicts in events.db
(read-only), prunes the dead platform from ``live_dofollow_platforms``, and emits
the discounted ledger JSONL on stdout. ``plan-gap``'s existing deficit math then
proposes replacements that avoid the dead platform.

    equity-ledger | recheck-overlay | plan-gap --emit-stale --desired N --language L | plan-backlinks

``--emit-stale`` on plan-gap is REQUIRED in this recipe: a target discounted to
``live_dofollow == 0`` is usually ``liveness=stale``/``unverified`` (publish-time
clock, never re-verified), which plan-gap suppresses by default — so without it the
re-plan emits zero seeds for exactly the aged links recheck targets. The overlay
itself does not rewrite ``liveness`` (rewriting to ``failed`` would trip plan-gap's
failed-target suppression; to ``live`` would be dishonest).

stdout = data (discounted ledger JSONL); stderr = config banner + discount tally.
Exit 0 advisory (default); absent events.db → 0 (ledger passed through); unreadable
store → 3 (DependencyError); usage → 1; opt-in ``--fail-on-dead`` → 6 when a
deterministic-dead verdict was seen. Mutates nothing — throwaway interim path
until the ledger-writeback fix (R6-proper) lands.

Plan 2026-06-01-006.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from backlink_publisher._util.errors import (
    PipelineError,
    emit_envelope_and_exit,
    handle_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.config import load_config
from backlink_publisher.events import EventStore
from backlink_publisher.recheck.overlay import apply_discounts, build_discount_map

#: ``--fail-on-dead`` exit code — mirrors ``recheck-backlinks``: 6 is the project's
#: "advisory domain alarm fired" code, outside the 1–5 error taxonomy.
FAIL_ON_DEAD_EXIT_CODE = 6


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="recheck-overlay",
        description=(
            "Discount dead / dofollow-lost backlinks (latest link.rechecked "
            "verdict in events.db) from the equity-ledger JSONL on stdin, so a "
            "cron-detected dead link raises plan-gap's deficit instead of "
            "counting as live equity. Read-only; pipe between equity-ledger and "
            "plan-gap."
        ),
    )
    parser.add_argument(
        "--fail-on-dead",
        action="store_true",
        help=(
            "After emitting the discounted ledger, exit 6 if any deterministic-"
            "dead verdict (host_gone / link_stripped) was seen (CI/cron alarm; "
            "dofollow_lost is advisory and never trips this)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
        config_echo.emit_banner(cfg, "recheck-overlay")

        # Buffer stdin so an empty ledger is a success (advisory + exit 0), not the
        # strict read_jsonl exit-2. Malformed JSON still exits 2 (strict). Split on
        # "\n" only (matches read_jsonl line semantics; avoids splitting a row on a
        # raw U+2028/U+2029 left by json.dumps). Mirrors cli/plan_gap.py.
        lines = sys.stdin.read().split("\n")
        if not any(line.strip() for line in lines):
            print(
                "recheck-overlay: empty ledger input — nothing to discount.",
                file=sys.stderr,
            )
            return
        rows = list(read_jsonl(lines, strict=True))

        discounts = build_discount_map(EventStore())
        out_rows, transform = apply_discounts(rows, discounts)

        write_jsonl(out_rows, sys.stdout)

        t = discounts.tally
        print(
            f"recheck-overlay: discounted {t.discounted} link(s) across "
            f"{transform.targets_reduced} target(s); dead_seen={t.dead_seen} "
            f"dofollow_lost={t.discounted - t.dead_seen} "
            f"unmatched_discount={transform.unmatched_discount} "
            f"null_target={t.null_or_blank_target} "
            f"unkeyable={t.unkeyable} "
            f"unknown_verdict={t.unknown_verdict}",
            file=sys.stderr,
        )
        if t.unknown_verdict:
            print(
                f"recheck-overlay: WARNING {t.unknown_verdict} unrecognized "
                "verdict(s) quarantined (counted, not treated as alive).",
                file=sys.stderr,
            )
        if transform.unmatched_discount:
            print(
                f"recheck-overlay: WARNING {transform.unmatched_discount} discount(s) "
                "matched no ledger row — a dead/dofollow-lost link may still be counting "
                "as live equity (canonicalization mismatch or ledger drift).",
                file=sys.stderr,
            )

        if args.fail_on_dead and t.dead_seen > 0:
            emit_envelope_and_exit(
                "DeadBacklinksDetected",
                FAIL_ON_DEAD_EXIT_CODE,
                f"recheck-overlay: {t.dead_seen} deterministic dead backlink(s) discounted",
            )
    except PipelineError as exc:
        handle_error(exc)


if __name__ == "__main__":
    main()
