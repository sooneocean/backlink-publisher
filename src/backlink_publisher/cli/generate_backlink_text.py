"""generate-backlink-text — LLM-assisted backlink text generation stage.

Reads backlink candidate records (``{target_url, anchor_text, mode}``) from
stdin or a file, optionally calls an OpenAI-compatible LLM to draft
higher-quality backlink text, validates the output deterministically, and emits
a reviewable JSONL/JSON artifact with per-record ``status``.

This is an **opt-in content-drafting tool for human review** — it does not
publish, and it does not wire into the ``seeds → plan → validate → publish``
pipeline.  Authorized exception to the no-runtime-LLM hard policy (owner,
2026-05-27); see ``docs/solutions/best-practices/no-runtime-llm-2026-05-15.md``.

Plan 2026-05-27-006.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from backlink_publisher._util.errors import (
    DependencyError,
    InputValidationError,
    PipelineError,
    UsageError,
    handle_error,
)
from backlink_publisher._util.jsonl import write_jsonl
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher._util.url import validate_https_url

generate_logger = PipelineLogger("generate-backlink-text")

_OUTPUT_FORMATS = {"jsonl", "json"}
_REQUIRED_FIELDS = ("target_url", "anchor_text", "mode")
_DEFAULT_MAX_INPUT_BYTES = 2_000_000
_DEFAULT_MAX_RECORDS = 200


# ── Input parsing ─────────────────────────────────────────────────────────────


def _read_candidates(
    raw_text: str,
    *,
    max_input_bytes: int = _DEFAULT_MAX_INPUT_BYTES,
    max_records: int = _DEFAULT_MAX_RECORDS,
) -> list[dict]:
    """Parse and return a list of candidate dicts from raw input text.

    Accepts JSON object (single record), JSON array, or JSONL.  Enforces
    ``max_input_bytes`` before parsing (fail-closed, R5) and ``max_records``
    after (fail-closed, R5).  Empty input → ``[]`` (R5b).

    Raises:
        InputValidationError: if the raw byte length exceeds ``max_input_bytes``
            or the record count exceeds ``max_records``.
    """
    raw_bytes = raw_text.encode("utf-8") if isinstance(raw_text, str) else raw_text
    if len(raw_bytes) > max_input_bytes:
        raise InputValidationError(
            f"generate-backlink-text: input exceeds --max-input-bytes "
            f"({max_input_bytes:,} bytes); refusing to parse"
        )

    text = raw_text.strip() if isinstance(raw_text, str) else raw_text.decode("utf-8").strip()
    if not text:
        return []

    # Try single JSON object or JSON array first.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            candidates: list[dict] = [parsed]
        elif isinstance(parsed, list):
            candidates = [r for r in parsed if isinstance(r, dict)]
        else:
            candidates = []
    except json.JSONDecodeError:
        # Fall back to JSONL (one record per line, skip malformed — strict=False).
        candidates = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    candidates.append(obj)
            except json.JSONDecodeError:
                pass  # skip malformed lines

    if len(candidates) > max_records:
        raise InputValidationError(
            f"generate-backlink-text: input has {len(candidates)} records, "
            f"exceeds --max-records ({max_records}); refusing to process"
        )

    return candidates


# ── Per-record field validation ───────────────────────────────────────────────


def _validate_candidate(rec: dict) -> dict:
    """Return a validated + normalised candidate or a ``rejected`` marker.

    Never raises — invalid records become ``{"status": "rejected", ...}`` so
    the batch continues (R4b, R13).

    Required fields: ``target_url`` (https-scheme, urlparse-safe), ``anchor_text``
    (non-empty string), ``mode`` (non-empty string; unsupported values produce a
    per-record rejection at generation time, not here).
    """
    # Check all required fields are present and non-empty strings.
    for field in _REQUIRED_FIELDS:
        val = rec.get(field)
        if not isinstance(val, str) or not val.strip():
            return _make_rejected(rec, "invalid_record")

    # Gate target_url: must be https.  Guard urlparse ValueError on malformed IPv6.
    try:
        validated_url = validate_https_url(rec["target_url"])
    except ValueError:
        return _make_rejected(rec, "invalid_record")
    if validated_url is None:
        return _make_rejected(rec, "bad_target_url_scheme")

    return {
        "target_url": validated_url,
        "anchor_text": rec["anchor_text"].strip(),
        "mode": rec["mode"].strip(),
        # Carry any extra fields the operator included (pass-through).
        **{
            k: v
            for k, v in rec.items()
            if k not in _REQUIRED_FIELDS
        },
    }


def _make_rejected(rec: dict, reason: str) -> dict:
    """Build a per-record rejected output row (R13, R14)."""
    out: dict[str, Any] = {"status": "rejected", "rejection_reason": reason}
    # Carry the original fields so the operator can trace the source row.
    for field in _REQUIRED_FIELDS:
        if field in rec:
            out[field] = rec[field]
    return out


# ── Output emission ───────────────────────────────────────────────────────────


def _emit_records(
    records: list[dict], output_format: str, file=None
) -> None:
    """Emit output records in the chosen format (JSONL or JSON array)."""
    dest = file or sys.stdout
    if output_format == "json":
        json.dump(records, dest, ensure_ascii=False, indent=2)
        dest.write("\n")
    else:  # jsonl (default)
        write_jsonl(records, dest)


# ── Main entry ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:  # noqa: C901
    import argparse
    import os

    parser = argparse.ArgumentParser(
        prog="generate-backlink-text",
        description=(
            "Opt-in LLM content-drafting stage: reads backlink candidate records "
            "{target_url, anchor_text, mode} and generates higher-quality backlink "
            "text via an OpenAI-compatible LLM.  Emits JSONL/JSON for human review; "
            "never auto-publishes.  Authorized no-runtime-LLM exception (2026-05-27)."
        ),
    )
    parser.add_argument(
        "--input", "-i",
        metavar="FILE",
        default=None,
        help="Input JSONL / JSON file (default: stdin)",
    )
    parser.add_argument(
        "--endpoint",
        metavar="URL",
        default=None,
        help="OpenAI-compatible base URL (e.g. https://api.openai.com/v1); "
             "overrides LLM_API_BASE env var",
    )
    parser.add_argument(
        "--api-key-env",
        metavar="VAR",
        default="BACKLINK_LLM_API_KEY",
        help="Name of the env var holding the API key (default: BACKLINK_LLM_API_KEY); "
             "the key is never a CLI flag",
    )
    parser.add_argument(
        "--model",
        metavar="NAME",
        default=None,
        help="Model name; overrides LLM_MODEL env var",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.4,
        metavar="FLOAT",
        help="Sampling temperature (default: 0.4)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        metavar="SECS",
        help="HTTP request timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        metavar="N",
        help="Transient transport retries per record (default: 1)",
    )
    parser.add_argument(
        "--output-format",
        metavar="FORMAT",
        default="jsonl",
        help="Output format: jsonl|json (default: jsonl)",
    )
    parser.add_argument(
        "--max-input-bytes",
        type=int,
        default=_DEFAULT_MAX_INPUT_BYTES,
        metavar="N",
        help=f"Maximum raw input size in bytes (default: {_DEFAULT_MAX_INPUT_BYTES:,})",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=_DEFAULT_MAX_RECORDS,
        metavar="N",
        help=f"Maximum number of candidate records (default: {_DEFAULT_MAX_RECORDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit prompts only — no API key or HTTP call required (R3)",
    )

    args = parser.parse_args(argv)

    try:
        # Closed-set validation post-parse (repo convention: UsageError exit 1,
        # not argparse's exit 2).  See [[argparse-choices-vs-usage-error]].
        if args.output_format not in _OUTPUT_FORMATS:
            raise UsageError(
                f"generate-backlink-text: --output-format must be one of "
                f"{sorted(_OUTPUT_FORMATS)}; got {args.output_format!r}"
            )

        # Read raw input ─────────────────────────────────────────────────────
        if args.input is not None:
            try:
                with open(args.input, encoding="utf-8") as fh:
                    raw_text = fh.read()
            except OSError as exc:
                raise PipelineError(
                    f"generate-backlink-text: cannot read --input: {exc}"
                ) from exc
        else:
            raw_text = sys.stdin.read()

        # Parse input and validate per-record fields (Unit 1).
        raw_candidates = _read_candidates(
            raw_text,
            max_input_bytes=args.max_input_bytes,
            max_records=args.max_records,
        )

        if not raw_candidates:
            # R5b: empty input → exit 0, empty output, stderr summary "0".
            generate_logger.recon(
                "generate_summary",
                total=0, ok=0, rejected=0, dry_run=False,
            )
            return

        # Per-record field validation — rejected records continue the batch.
        validated: list[dict] = [_validate_candidate(rec) for rec in raw_candidates]

        # ── Generation (Unit 3+5 will fill this in) ──────────────────────────
        if args.dry_run:
            output_records = _run_dry_run(validated, args)
        else:
            output_records = _run_generate(validated, args)

        # Emit output.
        _emit_records(output_records, args.output_format)

        # Stderr summary.
        ok_count = sum(1 for r in output_records if r.get("status") == "ok")
        rejected_count = sum(
            1 for r in output_records if r.get("status") == "rejected"
        )
        dry_count = sum(
            1 for r in output_records if r.get("status") == "dry_run"
        )
        generate_logger.recon(
            "generate_summary",
            total=len(output_records),
            ok=ok_count,
            rejected=rejected_count,
            dry_run=dry_count,
        )

    except PipelineError as exc:
        handle_error(exc)


def _run_dry_run(validated: list[dict], args) -> list[dict]:
    """Emit prompt previews without making any LLM call (R3).

    Supported modes produce a prompt preview; unsupported modes produce a
    per-record ``rejected`` (R4b).  No API key or HTTP required.
    """
    from backlink_publisher.llm.client import (
        SUPPORTED_MODES,
        _build_article_prompt,
        _build_comment_prompt,
        _sanitize_input,
    )

    _PROMPT_FNS = {
        "article": _build_article_prompt,
        "comment": _build_comment_prompt,
    }

    output = []
    for rec in validated:
        if rec.get("status") == "rejected":
            output.append(rec)
            continue

        mode = rec.get("mode", "")
        if mode not in SUPPORTED_MODES:
            output.append(_make_rejected(rec, f"unsupported_mode:{mode}"))
            continue

        build_prompt = _PROMPT_FNS[mode]
        safe_url = _sanitize_input(rec["target_url"])
        safe_anchor = _sanitize_input(rec["anchor_text"])
        safe_lang = _sanitize_input(rec.get("language", ""))
        system_msg, user_msg = build_prompt(safe_url, safe_anchor, safe_lang)

        output.append(
            {
                "status": "dry_run",
                "target_url": rec["target_url"],
                "anchor_text": rec["anchor_text"],
                "mode": mode,
                "system_prompt": system_msg,
                "user_prompt": user_msg,
            }
        )
    return output


def _run_generate(validated: list[dict], args) -> list[dict]:
    """Resolve LLM config and generate text for each valid candidate.

    Unit 3 will fill in the endpoint resolution and guard.
    Unit 4 will add deterministic validation + record assembly.
    Unit 5 will add the corrective re-prompt loop.

    For now (Unit 1): raises DependencyError so integration tests can see the
    scaffold is wired.  Units 3-5 replace this stub.
    """
    raise DependencyError(
        "generate-backlink-text: LLM generation not yet configured "
        "(use --dry-run to preview prompts, or configure LLM endpoint/key)"
    )


if __name__ == "__main__":
    main()
