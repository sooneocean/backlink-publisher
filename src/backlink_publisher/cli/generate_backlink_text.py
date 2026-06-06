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

import sys
from typing import Any

from backlink_publisher._util.errors import (
    DependencyError,
    PipelineError,
    UsageError,
    handle_error,
)
from backlink_publisher._util.logger import PipelineLogger
from backlink_publisher.cli._candidates import (
    _DEFAULT_MAX_INPUT_BYTES,
    _DEFAULT_MAX_RECORDS,
    _emit_records,
    _make_correction_hint,
    _make_rejected,
    _read_candidates,
    _validate_candidate,
    _validate_generated_text,
)

generate_logger = PipelineLogger("generate-backlink-text")


_OUTPUT_FORMATS = {"jsonl", "json"}


# ── Main entry ────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> None:  # noqa: C901 — argparse top-level dispatcher; real logic lives in helpers
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


# ── Endpoint resolution + guard ───────────────────────────────────────────────


def _resolve_client(args) -> "LLMClientConfig":  # type: ignore[name-defined]
    """Resolve LLM endpoint/key/model from CLI flags → env → config.

    Resolution order for each field:
    - **API key**: env var named by ``--api-key-env`` (e.g. ``BACKLINK_LLM_API_KEY``),
      then ``llm.anchor_provider.api_key`` from config.  Never a CLI flag.
    - **Endpoint**: ``--endpoint`` flag, then ``llm.anchor_provider.base_url`` from
      config (which itself reads ``BACKLINK_LLM_BASE_URL``).
    - **Model**: ``--model`` flag, then ``llm.anchor_provider.model`` from config
      (which itself reads ``BACKLINK_LLM_MODEL``).

    Security guarantees (R15, R16):
    - Rejects endpoints containing URL userinfo (``user:secret@host``) — redaction
      does not cover userinfo and such secrets appear in process listings.
    - Normalises endpoint *before* gating so the guarded host equals the connected
      host (no silent re-normalisation after the check).
    - Calls ``guard_llm_endpoint`` (scheme → allowlist → SSRF) before constructing
      the ``LLMClientConfig`` — the key is never passed to a non-gated host.
    - Malformed endpoints (e.g. ``http://[invalid``) → ``DependencyError``;
      ``ValueError`` from ``urlparse`` is never uncaught.

    Raises:
        DependencyError: missing key/endpoint/model, or endpoint rejected by
            the userinfo / allowlist / SSRF guard.
    """
    import os
    from urllib.parse import urlparse

    from backlink_publisher.llm.client import LLMClientConfig
    from backlink_publisher.llm.http_guard import guard_llm_endpoint

    # ── API key from user-specified env var ────────────────────────────────
    api_key_env_var: str = args.api_key_env  # e.g. "BACKLINK_LLM_API_KEY"
    api_key: str | None = os.environ.get(api_key_env_var) or None

    # ── CLI flags take priority for endpoint + model ───────────────────────
    endpoint: str | None = args.endpoint or None
    model: str | None = args.model or None

    # ── Fall back to config for any missing values ─────────────────────────
    if not api_key or not endpoint or not model:
        from backlink_publisher.config import load_config

        cfg = load_config()
        llm_cfg = cfg.llm_anchor_provider  # LLMProviderConfig | None
        if llm_cfg is not None:
            if not api_key:
                api_key = llm_cfg.api_key or None
            if not endpoint:
                endpoint = llm_cfg.base_url or None
            if not model:
                model = llm_cfg.model or None

    # ── Validate resolved values ───────────────────────────────────────────
    if not api_key:
        raise DependencyError(
            f"generate-backlink-text: LLM not configured — "
            f"no API key found in ${api_key_env_var}. "
            f"Set the env var or add [llm.anchor_provider].api_key to config.toml. "
            f"Use --dry-run to preview prompts without a key."
        )
    if not endpoint:
        raise DependencyError(
            "generate-backlink-text: LLM not configured — "
            "no endpoint (try --endpoint or BACKLINK_LLM_BASE_URL). "
            "Use --dry-run to preview prompts without an endpoint."
        )
    if not model:
        raise DependencyError(
            "generate-backlink-text: LLM not configured — "
            "no model (try --model or BACKLINK_LLM_MODEL). "
            "Use --dry-run to preview prompts without a model."
        )

    # ── Userinfo guard: reject user:secret@host ────────────────────────────
    # URL userinfo bypasses _redact_for_log and exposes credentials in `ps`.
    try:
        parsed_ep = urlparse(endpoint)
    except ValueError as exc:
        raise DependencyError(
            f"generate-backlink-text: malformed --endpoint: {exc}"
        ) from exc
    if parsed_ep.username or parsed_ep.password:
        raise DependencyError(
            "generate-backlink-text: --endpoint must not contain userinfo "
            "(user:password@host leaks credentials in process listings and logs). "
            "Provide the bare base URL, e.g. https://api.openai.com/v1"
        )

    # ── Endpoint normalization ─────────────────────────────────────────────
    # Strip trailing "/chat/completions" (with optional slash) so a full URL
    # supplied by the operator does not double-append the suffix.
    # The string that is *gated* must equal the string the client connects to.
    base = endpoint.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")

    # ── SSRF + allowlist guard (scheme → is_allowlisted → _check_url_for_ssrf)
    try:
        rejection_reason, detail = guard_llm_endpoint(base)
    except ValueError as exc:
        # Guard urlparse ValueError on malformed IPv6 inside guard_llm_endpoint.
        raise DependencyError(
            f"generate-backlink-text: endpoint rejected (malformed): {exc}"
        ) from exc
    if rejection_reason is not None:
        raise DependencyError(
            f"generate-backlink-text: endpoint rejected ({rejection_reason}): {detail}"
        )

    # ── Build client (R2 CLI defaults — not provider defaults) ────────────
    return LLMClientConfig(
        base=base,
        api_key=api_key,
        model=model,
        temperature=args.temperature,   # CLI default 0.4; provider default is 0.7
        timeout=args.timeout,           # CLI default 60;  provider default is 30
        retries=args.retries,
    )


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
    """Resolve config, guard endpoint, generate and validate text per candidate.

    Endpoint resolution + SSRF/allowlist guard run once before any HTTP call
    (Unit 3).  DependencyError from the guard propagates to ``main()`` → exit 3.

    Per-record errors (ExternalServiceError, unsupported mode, validation failure)
    produce a ``rejected`` row — the batch continues (R4b).

    On first-pass validation failure, a corrective re-prompt is attempted once
    (R8).  Only if that also fails does the record become ``rejected``.
    """
    from backlink_publisher._util.errors import ExternalServiceError
    from backlink_publisher.llm.client import SUPPORTED_MODES, generate_link_text
    from backlink_publisher.llm.client import _redact_for_log

    # Short-circuit: if every record is already rejected (e.g. all invalid_record),
    # skip client resolution entirely so no DependencyError fires for an unused LLM.
    if all(rec.get("status") == "rejected" for rec in validated):
        return list(validated)

    # Resolve + guard once; DependencyError (exit 3) surfaces before any HTTP.
    client_cfg = _resolve_client(args)

    output: list[dict] = []
    for rec in validated:
        if rec.get("status") == "rejected":
            output.append(rec)
            continue

        mode = rec.get("mode", "")
        if mode not in SUPPORTED_MODES:
            output.append(_make_rejected(rec, f"unsupported_mode:{mode}"))
            continue

        target_url = rec["target_url"]
        anchor_text = rec["anchor_text"]
        language = rec.get("language", "")

        # ── First generation attempt ──────────────────────────────────────────
        try:
            generated_text = generate_link_text(
                mode=mode,
                target_url=target_url,
                anchor_text=anchor_text,
                language=language,
                cfg=client_cfg,
            )
        except ValueError:
            # generate_link_text raises ValueError for unsupported mode (safety).
            output.append(_make_rejected(rec, f"unsupported_mode:{mode}"))
            continue
        except ExternalServiceError as exc:
            generate_logger.warn(
                "generate_transport_error",
                detail=_redact_for_log(str(exc)),
            )
            output.append(_make_rejected(rec, "transport_error"))
            continue

        # ── First-pass validation ─────────────────────────────────────────────
        vresult = _validate_generated_text(
            generated_text,
            target_url=target_url,
            anchor_text=anchor_text,
            mode=mode,
            language=language,
        )

        if not vresult["ok"]:
            # R8: one corrective re-prompt on structural validation failure.
            hint = _make_correction_hint(vresult["reason"])
            if hint is not None:
                try:
                    generated_text = generate_link_text(
                        mode=mode,
                        target_url=target_url,
                        anchor_text=anchor_text,
                        language=language,
                        cfg=client_cfg,
                        correction_hint=hint,
                    )
                except (ValueError, ExternalServiceError) as exc:
                    if isinstance(exc, ExternalServiceError):
                        generate_logger.warn(
                            "generate_corrective_transport_error",
                            detail=_redact_for_log(str(exc)),
                            validation_reason=vresult["reason"],
                        )
                    output.append(_make_rejected(rec, vresult["reason"]))
                    continue
                # Re-validate the corrected response.
                vresult = _validate_generated_text(
                    generated_text,
                    target_url=target_url,
                    anchor_text=anchor_text,
                    mode=mode,
                    language=language,
                )

            if not vresult["ok"]:
                output.append(_make_rejected(rec, vresult["reason"]))
                continue

        # Assemble ok record.  Only candidate fields are emitted — never
        # endpoint / key / env-var-name (R16, no-credentials-in-output).
        ok_rec: dict[str, Any] = {
            "status": "ok",
            "target_url": target_url,
            "anchor_text": anchor_text,
            "mode": mode,
            "generated_text": vresult["text"],  # extra-link-stripped text
        }
        if vresult["stripped_extra_links"]:
            ok_rec["stripped_extra_links"] = vresult["stripped_extra_links"]
        if vresult["language_flag"] is not None:
            ok_rec["language_flag"] = vresult["language_flag"]
        # Pass through any extra operator-supplied fields (e.g. "language").
        for k, v in rec.items():
            if k not in {"target_url", "anchor_text", "mode", "status"}:
                ok_rec.setdefault(k, v)
        output.append(ok_rec)
    return output


if __name__ == "__main__":
    main()
