"""Report anchor-text distribution across backlink article payloads."""

from __future__ import annotations

import sys

from .. import config_echo
from backlink_publisher._util.jsonl import read_jsonl
from backlink_publisher.anchor.profile import load_profile
from backlink_publisher.config import load_config
from ._report_format import (  # noqa: F401
    _EXIT_CODE_ALARM,
    _build_profile_report,
    _build_report,
    _build_tier_summary,
    _compute_alarm,
    _format_alarm_markdown,
    _format_profile_report_json,
    _format_profile_report_markdown,
    _json_output,
    _markdown_table,
)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="report-anchors",
        description=(
            "Analyse anchor-text distribution across backlink article payloads. "
            "Reads payload JSONL (plan-backlinks output) from --input or stdin."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Payload JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--from-profile",
        metavar="MAIN_DOMAIN",
        default=None,
        help=(
            "Read from the anchor profile JSON for the given site instead of "
            "JSONL payloads. Reports type distribution vs. target, URL "
            "category × type cross-tab, degradation rate, top repeated "
            "anchor texts, and the per-target distribution alarm "
            "(Shannon entropy + exact-ratio + top-3 concentration over "
            "30d/90d windows). Exits with code 6 when any target's 90d "
            "window breaches the configured thresholds. Only meaningful "
            "for sites using the zh-CN short-form scheduler."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON instead of a Markdown table",
    )
    parser.add_argument(
        "--top-anchors",
        type=int,
        default=5,
        metavar="N",
        help="Number of top anchor keywords to show per target (default: 5)",
    )
    args = parser.parse_args(argv)

    if args.from_profile:
        # ── Profile-based report path ────────────────────────────────────
        # Load config to pull the target proportions; missing config is fine
        # (defaults to Safe SEO) — we want to be useful even before the user
        # has wired up the full scheduler config.
        cfg = load_config()
        # Config Echo Chamber (Round-3 #7): banner so operator sees which
        # config was resolved + env overrides + SHA.
        config_echo.emit_banner(cfg, "report-anchors")
        profile = load_profile(args.from_profile)
        report = _build_profile_report(profile, cfg.anchor_proportions)

        # Layer the anchor distribution alarm on top of the existing report.
        # Computes per-target metrics over 30d / 90d windows, emits a structured
        # alarm block in the JSON output, prints one stderr WARN per breaching
        # target, and exits with code 6 if any target's 90d window breaches.
        alarm_block, breach_lines = _compute_alarm(
            profile, cfg.anchor_alarm, args.from_profile,
        )
        report["alarm"] = alarm_block

        if args.json:
            print(_format_profile_report_json(report))
        else:
            print(_format_profile_report_markdown(report))
            if alarm_block.get("any_breach"):
                print(_format_alarm_markdown(alarm_block))

        for line in breach_lines:
            print(line, file=sys.stderr)
        if alarm_block.get("any_breach"):
            raise SystemExit(_EXIT_CODE_ALARM)
        return

    # ── JSONL-stdin aggregate path ─────────────────────────────────────────
    # Document-review F6: an operator running `cat payloads.jsonl |
    # report-anchors` could see exit 0 and falsely conclude "no anchor
    # breaches". This path is structurally incapable of computing the alarm
    # because the JSONL `links[]` array lacks anchor_type. Emit a one-line
    # hint so the false-safety failure mode does not occur silently.
    print(
        "NOTE: anchor distribution alarm requires --from-profile <main_domain>; "
        "this stdin-aggregate path does not compute distributional metrics.",
        file=sys.stderr,
    )

    fh = args.input or sys.stdin
    rows = list(read_jsonl(fh, strict=False))

    stats = _build_report(rows)
    tier_summary = _build_tier_summary(rows)

    if args.json:
        print(_json_output(stats, tier_summary=tier_summary))
    else:
        print(_markdown_table(stats, top_n=args.top_anchors, tier_summary=tier_summary))


if __name__ == "__main__":
    main()
