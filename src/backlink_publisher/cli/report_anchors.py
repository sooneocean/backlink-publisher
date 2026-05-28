"""Report anchor-text distribution across backlink article payloads.

CLI shell over :func:`._report_engine.report_from_profile` and
:func:`~.report_from_rows` (thin-WebUI Phase 2 Unit 7). The shell owns argparse,
config-load + config_echo banner, stdin/stdout I/O (H3), stderr breach lines,
and the typed-error exit. The pure reporting kernel lives in ``_report_engine``
so the in-process ``PipelineAPI.report_anchors()`` bridge shares it.
"""

from __future__ import annotations

import sys

import backlink_publisher.publishing.adapters  # noqa: F401  populate registry
from .. import config_echo
from backlink_publisher._util.errors import emit_envelope_and_exit
from backlink_publisher._util.jsonl import read_jsonl
from backlink_publisher.config import load_config

# Re-export format helpers so callers that import them from here still find them.
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
        # ── Profile-based report path ────────────────────────────────────────
        from ._report_engine import report_from_profile

        cfg = load_config()
        config_echo.emit_banner(cfg, "report-anchors")
        outcome = report_from_profile(args.from_profile, cfg, as_json=args.json)

        # H3: stdout write stays in the shell.
        print(outcome.document)
        for line in outcome.breach_lines:
            print(line, file=sys.stderr)

        if outcome.alarm_breach:
            emit_envelope_and_exit(
                "AnchorDistributionAlarm",
                outcome.exit_code,
                f"anchor distribution alarm: {outcome.breach_count} target(s) breached",
            )
        return

    # ── JSONL-stdin aggregate path ──────────────────────────────────────────
    # Document-review F6: this path is structurally incapable of computing the
    # alarm because the JSONL `links[]` array lacks anchor_type.
    print(
        "NOTE: anchor distribution alarm requires --from-profile <main_domain>; "
        "this stdin-aggregate path does not compute distributional metrics.",
        file=sys.stderr,
    )

    from ._report_engine import report_from_rows

    fh = args.input or sys.stdin
    rows = list(read_jsonl(fh, strict=False))
    outcome = report_from_rows(rows, as_json=args.json, top_anchors=args.top_anchors)
    print(outcome.document)


if __name__ == "__main__":
    main()
