"""Validate planned backlink payloads with structured logging."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .. import errors
from ..errors import emit_error, InputValidationError
from ..jsonl import read_jsonl, write_jsonl
from ..language_check import detect_language, language_matches
from ..linkcheck import check_urls_strict
from ..logger import validate_logger
from ..markdown_utils import validate_markdown_convertible
from ..schema import SUPPORTED_PLATFORMS, validate_output_payload


def _enhance_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Add validation metadata to the payload."""
    row["validation"] = {
        "status": "passed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "warnings": [],
    }

    # Language detection
    text = row.get("content_markdown", "")
    detected = detect_language(text)
    requested = row.get("language", "")

    if not language_matches(detected, requested):
        row["validation"]["warnings"].append(
            f"detected language '{detected}' may not match requested '{requested}'"
        )

    return row


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="validate-backlinks",
        description="Validate planned backlink payloads.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--no-check-urls",
        action="store_true",
        default=False,
        help="Skip URL reachability checks",
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    args = parser.parse_args(argv)

    from ..logger import set_log_level
    set_log_level(args.log_level)

    validate_logger.info("validate-backlinks started")

    check_urls = not args.no_check_urls

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    validate_logger.info(f"validating {len(rows)} payloads")

    if check_urls:
        all_urls = set()
        for row in rows:
            all_urls.add(row.get("target_url", ""))
            all_urls.add(row.get("main_domain", ""))
            for link in row.get("links", []):
                all_urls.add(link.get("url", ""))
        all_urls.discard("")

        if all_urls:
            try:
                check_urls_strict(list(all_urls))
            except errors.ExternalServiceError as exc:
                validate_logger.error(f"URL check failed: {exc}")
                raise SystemExit(4) from None

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []

    for idx, row in enumerate(rows, start=1):
        # Check for unsupported platforms (linkedin)
        platform = row.get("platform", "")
        if platform == "linkedin":
            all_errors.append(
                f"row {idx}: platform 'linkedin' is not supported. "
                f"Supported: {', '.join(sorted(SUPPORTED_PLATFORMS))}"
            )
            continue

        errs = validate_output_payload(row)
        if errs:
            all_errors.extend(f"row {idx}: {e}" for e in errs)
            continue
        outputs.append(_enhance_payload(row))

    if all_errors:
        for err in all_errors:
            print(f"validation error: {err}", file=sys.stderr)
        validate_logger.error(f"validation failed: {len(all_errors)} errors")
        raise SystemExit(2)

    validate_logger.info(f"validated {len(outputs)} payloads")
    write_jsonl(outputs)