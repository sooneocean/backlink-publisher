"""Per-Channel Value Scorecard — read-only per-channel keep/prune view (JSONL).

Emits one JSON object per channel, pairing each channel's *declared* registry
signals (dofollow status, referral_value) with *measured* signals (placements,
liveness) as a signal vector — no composite score. The GA4 referral, GSC
discovery, and AI-retrievability axes are deferred (Wave-0 DESCOPE) and render as
``inert:not-landed``.

Read-only aggregation over the same stores the equity-ledger reads (events.db +
publish history), re-keyed by channel. stdout = data (JSONL), stderr = the
config-echo banner. Exit 0 on success. Advisory only — never gates publishing.
Plan 2026-06-01-005 (Unit 8, Wave-0 MVP).
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before config load
from .. import config_echo
from backlink_publisher._util.errors import emit_error
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher.config import load_config
from backlink_publisher.scorecard import build_channel_scorecard


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="channel-scorecard",
        description=(
            "Per-channel keep/prune scorecard: for each publishing channel, the "
            "declared registry signals (dofollow status, referral_value) beside "
            "the measured signals (total vs live placements, dofollow-live count, "
            "liveness breakdown) as a signal vector — no composite score. "
            "Low-sample channels are flagged 'insufficient-data', not zero. The "
            "GA4 referral / GSC discovery / AI-retrievability axes are deferred "
            "and shown as 'inert:not-landed'. Read-only over events.db + publish "
            "history. Emits one JSON object per channel on stdout. Advisory only."
        ),
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=30,
        metavar="N",
        help=(
            "A link verified longer ago than N days counts as 'stale' rather "
            "than 'live' (display only; default: 30)."
        ),
    )
    parser.add_argument(
        "--small-sample-max",
        type=int,
        default=4,
        metavar="N",
        help=(
            "Channels with N links or fewer are flagged 'insufficient-data' "
            "(low sample is not zero value; default: 4)."
        ),
    )
    args = parser.parse_args(argv)

    # Closed-set/range validation post-parse (repo convention: UsageError-style
    # exit 1, not argparse's exit 2). See [[argparse-choices-vs-usage-error]].
    if args.stale_days <= 0:
        emit_error("channel-scorecard: --stale-days must be a positive integer", exit_code=1)
    if args.small_sample_max < 0:
        emit_error("channel-scorecard: --small-sample-max must be >= 0", exit_code=1)

    # Config Echo Chamber: banner to stderr so the operator sees which config /
    # env / SHA was resolved. Missing config is fine (read-only, safe defaults).
    cfg = load_config()
    config_echo.emit_banner(cfg, "channel-scorecard")

    rows = build_channel_scorecard(
        stale_days=args.stale_days,
        small_sample_max=args.small_sample_max,
    )
    write_jsonl((row.to_jsonl_dict() for row in rows), sys.stdout)


if __name__ == "__main__":
    main()
