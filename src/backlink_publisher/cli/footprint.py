"""CLI: `bp footprint` — offline self-fingerprint audit.

Reads a JSONL stream of payloads (plan-backlinks output) from stdin or
``--input``, extracts every payload's ``content_markdown`` (or
``content_html`` when present), and reports the byte-level patterns that
appear in 100% of links — the project's self-fingerprint.

Pure offline tool. No HTTP, no LLM, no on-disk artifacts beyond stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..footprint import analyze_corpus, format_report_markdown


def _payload_html(payload: dict[str, Any]) -> str:
    """Pick the HTML / markdown field this payload uses."""
    for key in ("content_html", "content_markdown", "body_html", "html"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="footprint",
        description=(
            "Offline self-fingerprint audit. Reads a JSONL stream of plan-"
            "backlinks payloads from stdin (or --input) and reports byte-"
            "level patterns that appear in 100% of links — the project's "
            "self-fingerprint that a Penguin / SpamBrain cluster pass keys "
            "on."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Payload JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output the raw report as JSON instead of Markdown",
    )
    parser.add_argument(
        "--alarm-pct",
        type=float,
        default=95.0,
        help=(
            "Concentration threshold (percent) above which a dimension is "
            "flagged as a CLUSTER KEY in the markdown report (default: 95)"
        ),
    )
    args = parser.parse_args(argv)

    fh = args.input or sys.stdin
    payloads: list[str] = []
    for lineno, raw in enumerate(fh, start=1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"WARN: line {lineno}: malformed JSON — {exc}", file=sys.stderr)
            continue
        payloads.append(_payload_html(payload))

    report = analyze_corpus(payloads)

    if args.json:
        # Coerce Counter -> dict and tuple keys -> string for JSON serializability
        out = {
            "total_links": report.total_links,
            "total_payloads": report.total_payloads,
            "payloads_without_links": report.payloads_without_links,
            "attr_order_counts": {
                " → ".join(k): v for k, v in report.attr_order_counts.items()
            },
            "rel_value_counts": dict(report.rel_value_counts),
            "target_value_counts": dict(report.target_value_counts),
            "preceding_char_counts": dict(report.preceding_char_counts),
            "concentration_pct": {
                "attr_order": report.concentration_pct("attr_order"),
                "rel_value": report.concentration_pct("rel_value"),
                "target_value": report.concentration_pct("target_value"),
                "preceding_char": report.concentration_pct("preceding_char"),
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(format_report_markdown(report, alarm_pct=args.alarm_pct))


if __name__ == "__main__":
    main()
