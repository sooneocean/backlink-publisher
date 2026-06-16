"""Validate planned backlink payloads with structured logging.

Thin CLI shell over :func:`backlink_publisher.validate.engine.validate_rows`
(thin-WebUI Phase 2 Unit 6). The shell owns argparse, ``set_log_level`` (H1 —
stays here, NOT the engine), config-load tolerance, the config_echo banner,
stdin/stdout JSONL, the recon line, and the typed-error exit. The pure
validation kernel lives in the engine so the in-process ``PipelineAPI`` bridge
shares it byte-for-byte.
"""

from __future__ import annotations

import sys
from typing import Any

from .. import config_echo
from .._util import errors
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import validate_logger
from backlink_publisher.validate.engine import load_config_tolerant, validate_rows

# Re-export symbols from extracted sub-module so any external callers (tests,
# downstream scripts) can still import them from validate_backlinks directly.
from ._validate_payload import (  # noqa: F401
    _HrefCollector,
    _extract_hrefs_from_html,
    _check_main_domain_in_html,
    _resolve_branded_pool,
    _nfc_normalize_in_place,
    _detect_row_body_language,
    _enhance_payload,
)


def _row_field_text(row: dict[str, Any], field: str) -> str:
    """Read a row field as a string, treating non-strings as empty."""
    value = row.get(field, "")
    return value if isinstance(value, str) else ""


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
        "--zero-auth",
        action="store_true",
        default=False,
        help="Restrict validation to rows targeting zero-auth (no-login) platforms",
    )
    parser.add_argument(
        "--no-validate-url-check",
        action="store_true",
        default=False,
        dest="no_validate_url_check",
        help="Skip URL reachability checks at validate-time",
    )
    parser.add_argument(
        "--no-check-urls",
        action="store_true",
        default=False,
        dest="no_validate_url_check_legacy",
        help=(
            "DEPRECATED alias for --no-validate-url-check. "
            "Will be removed in a future version."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    args = parser.parse_args(argv)

    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    validate_logger.info("validate-backlinks started")

    # R10: --no-check-urls remains as a deprecated alias for back-compat.
    # Either flag set => URL checks disabled.
    if args.no_validate_url_check_legacy and not args.no_validate_url_check:
        validate_logger.warn(
            "--no-check-urls is deprecated; use --no-validate-url-check. "
            "Will be removed in a future version."
        )
    check_urls = not (args.no_validate_url_check or args.no_validate_url_check_legacy)

    # R4 branded-pool fallback source. Failure here is non-fatal — payload-first
    # snapshot from plan-backlinks is the primary source; missing config just
    # disables the live fallback. (Shared with PipelineAPI via the engine.)
    config = load_config_tolerant()

    # Config Echo Chamber (Round-3 #7): emit a 4-line banner so operators
    # see which config was actually resolved + env overrides + SHA.
    if config is not None:
        config_echo.emit_banner(config, "validate-backlinks")

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    if getattr(args, "zero_auth", False):
        from backlink_publisher.publishing.registry import zero_auth_platforms
        zaps = zero_auth_platforms()
        validate_logger.recon("zero_auth_filter", in_count=len(rows), zero_auth_platforms=list(zaps))
        rows = [r for r in rows if r.get("platform", "") in zaps]
        validate_logger.info(f"filtered to {len(rows)} rows after zero-auth filter")

    validate_logger.info(f"validating {len(rows)} payloads")

    try:
        outcome = validate_rows(rows, config, check_urls=check_urls)
    except errors.ExternalServiceError as exc:
        validate_logger.error(f"URL check failed: {exc}")
        errors.emit_envelope_and_exit(
            "ExternalServiceError", 4, f"URL check failed: {exc}"
        )

    # R2/R5: per-row skip semantic — passing rows STILL stream to stdout
    # so downstream consumers see partial success; exit code reflects overall
    # success only when zero rows failed.
    failed_count = outcome.failed_count
    write_jsonl(outcome.outputs)

    # Emit Silent-Drop Tripwire reconciliation BEFORE the exit guard so failed
    # runs still surface a delta summary.
    output_rows = len(outcome.outputs)
    validate_logger.recon(
        "validate_reconciliation",
        input_rows=outcome.input_count,
        output_rows=output_rows,
        delta=outcome.input_count - output_rows,
        dropped={
            "platform": len(outcome.platform_drops),
            "validation": len(outcome.validation_drops),
        },
        dropped_row_indices={
            "platform": outcome.platform_drops,
            "validation": outcome.validation_drops,
        },
    )

    if outcome.errors:
        for err in outcome.errors:
            print(f"validation error: {err}", file=sys.stderr)
        validate_logger.error(
            f"validation failed: {len(outcome.errors)} errors "
            f"({output_rows} passed, {failed_count} failed)"
        )
        errors.emit_envelope_and_exit(
            "InputValidationError",
            2,
            f"validation failed: {len(outcome.errors)} errors "
            f"({output_rows} passed, {failed_count} failed)",
        )

    validate_logger.info(
        f"validated {output_rows} payloads "
        f"({output_rows} passed, {failed_count} failed)"
    )


if __name__ == "__main__":
    main()
