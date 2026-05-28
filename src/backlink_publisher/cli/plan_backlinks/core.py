"""CLI shell for plan-backlinks (thin-WebUI Phase 2 Unit 7).

Owns: argparse, ``set_log_level`` (H1), ``config_echo`` banner, stdin/file
parsing, ``content_fetch.reset_stats()`` (H2), ``write_jsonl`` to stdout (H3),
and the preflight / canary nudges.

The generation kernel lives in :mod:`._engine` so both this shell and the
in-process ``PipelineAPI.plan()`` path share identical computation.
"""

from __future__ import annotations

import sys
from typing import Any

from ... import config_echo
from ...content import fetch as content_fetch
import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before argparse
from backlink_publisher.publishing.registry import registered_platforms
from backlink_publisher.config import load_config
from backlink_publisher._util.errors import emit_envelope_and_exit, emit_error
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.url import canonicalize_url

# Re-export sub-module symbols so __init__.py and sibling modules
# (._zh_short, ._work_themed) find them at their old import paths.
from ._links import (                               # noqa: F401
    _ContentGateRowFailure,
    _ROW_REQUIRED_KINDS,
    _SUPPORTING_POOL,
    _SUPPORTING_URLS_FOR_PREFETCH,
    _TARGET_PADDED_LINK_COUNT,
    _build_links,
    _build_link_density_paragraph,
    _collect_candidate_urls_for_row,
)
from ._templates import (                           # noqa: F401
    _TEMPLATES,
    _TDK_TITLE_TMPL,
    _domain_label_of,
)
from ._banners import (                             # noqa: F401
    _build_banner_runtime,
    _generate_banner_for_payload,
)
from ._payload import (                             # noqa: F401
    ARTICLE_LENGTH_WORDS,
    _generate_payload,
    _resolve_article_anchors,
    dofollow_tier_metadata,
)

# Re-export engine symbols for backward compat (tests + __init__.py import
# _dispatch_row / _cell_gate_drop from here).
from ._engine import (                              # noqa: F401
    PlanOutcome,
    _cell_gate_drop,
    _dispatch_row,
    _emit_link_count_recon,
    _scheduler_enabled_for,
    plan_rows,
)


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="plan-backlinks",
        description="Generate backlink article payloads from seed URLs.",
    )
    parser.add_argument(
        "--input", "-i",
        type=argparse.FileType("r"),
        default=None,
        help="Input JSONL file (default: stdin)",
    )
    parser.add_argument(
        "--from-csv",
        default=None,
        metavar="FILE",
        help="Read target URLs from a CSV/text file (one URL per line). Use '-' for stdin.",
    )
    parser.add_argument(
        "--from-sitemap",
        default=None,
        metavar="URL",
        help="Fetch target URLs from a sitemap XML URL.",
    )
    parser.add_argument(
        "--default-platform",
        default="blogger",
        choices=registered_platforms(),
        help="Platform for --from-csv / --from-sitemap rows (default: blogger)",
    )
    parser.add_argument(
        "--default-language",
        default="zh-CN",
        choices=["zh-CN", "en", "ru", "ko"],
        help="Language for --from-csv / --from-sitemap rows (default: zh-CN)",
    )
    parser.add_argument(
        "--default-url-mode",
        default="A",
        choices=["A", "B", "C"],
        help="URL mode for --from-csv / --from-sitemap rows (default: A)",
    )
    parser.add_argument(
        "--default-publish-mode",
        default="draft",
        choices=["draft", "publish"],
        help="Publish mode for --from-csv / --from-sitemap rows (default: draft)",
    )
    parser.add_argument(
        "--work-count",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Per-row article count for the work-themed dispatcher path "
            "(default: 10). Ignored for legacy zh-short / long-form rows."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    parser.add_argument(
        "--no-fetch-verify",
        action="store_true",
        default=False,
        help=(
            "Skip the plan-time URL content gate (default: enabled). Each row's "
            "URLs are normally fetched via content_fetch.verify_url_has_content "
            "and required to return HTTP 200 with a non-empty <title> or "
            "og:title before being added to the article. Use this flag in "
            "dev / replay / staging when target sites are intentionally offline. "
            "Plan ref: docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md"
        ),
    )
    args = parser.parse_args(argv)

    # H1: set_log_level stays in the shell — never inside the engine.
    from backlink_publisher._util.logger import set_log_level
    set_log_level(args.log_level)

    if args.no_fetch_verify:
        plan_logger.recon("fetch_verify_disabled", reason="cli_flag")

    bulk_sources = [args.from_csv, args.from_sitemap]
    if sum(bool(x) for x in bulk_sources) > 1:
        emit_error("--from-csv and --from-sitemap are mutually exclusive", exit_code=2)
    if (args.from_csv or args.from_sitemap) and args.input:
        emit_error("--from-csv / --from-sitemap cannot be combined with --input", exit_code=2)

    plan_logger.info("plan-backlinks started", extra={"mode": "generate"})

    if args.from_csv or args.from_sitemap:
        from ...bulk_input import parse_csv, parse_sitemap, urls_to_seed_rows

        if args.from_csv:
            try:
                urls = parse_csv(args.from_csv)
            except Exception as exc:
                emit_error(f"failed to read CSV: {exc}", exit_code=2)
                return
        else:
            try:
                urls = parse_sitemap(args.from_sitemap)
            except RuntimeError as exc:
                emit_error(str(exc), exit_code=2)
                return

        if not urls:
            emit_error("no URLs found in input source", exit_code=2)
            return

        rows: list[dict[str, Any]] = urls_to_seed_rows(
            urls,
            platform=args.default_platform,
            language=args.default_language,
            url_mode=args.default_url_mode,
            publish_mode=args.default_publish_mode,
        )
        plan_logger.info(f"read {len(rows)} seed rows from bulk input")
    else:
        try:
            rows = list(read_jsonl(args.input))
        except SystemExit as exc:
            raise SystemExit(exc.code)

    plan_logger.info(f"read {len(rows)} seed rows")

    cfg = load_config()
    config_echo.emit_banner(cfg, "plan-backlinks")

    # H2: reset fetch stats here (shell responsibility) so plan_rows sees
    # clean per-run counters; the in-process PipelineAPI.plan() path does NOT
    # reset — accepts cumulative stats (documented acceptable, audit surface 2).
    content_fetch.reset_stats()

    outcome = plan_rows(
        rows, cfg,
        work_count=args.work_count,
        fetch_verify_enabled=not args.no_fetch_verify,
    )

    if outcome.errors:
        for err in outcome.errors:
            print(err, file=sys.stderr)
        plan_logger.error(f"generation failed: {len(outcome.errors)} errors")
        emit_envelope_and_exit(
            "InputValidationError", 2, f"generation failed: {len(outcome.errors)} errors"
        )

    plan_logger.info(f"generated {len(outcome.outputs)} payloads")
    # H3: write_jsonl to stdout stays in the shell — engine never touches sys.stdout.
    write_jsonl(outcome.outputs)

    # Preflight nudge (Plan 2026-05-26-008 R3a): advisory on success path only.
    distinct_targets = {
        canonicalize_url(target.strip())
        for row in outcome.outputs
        if isinstance((target := row.get("target_url")), str) and target.strip()
    }
    if distinct_targets:
        plan_logger.recon(
            "preflight_nudge",
            distinct_targets=len(distinct_targets),
            hint="run `preflight-targets` to verify destination pages before publishing",
        )

    # Canary advisory nudge (Plan 2026-05-27-001 Unit 4): surface degraded platforms.
    try:
        from backlink_publisher.canary.store import is_degraded

        planned_platforms = {
            p.strip()
            for row in outcome.outputs
            if isinstance((p := row.get("platform")), str) and p.strip()
        }
        degraded = sorted(p for p in planned_platforms if is_degraded(p))
        if degraded:
            plan_logger.recon(
                "canary_advisory_nudge",
                degraded_platforms=",".join(degraded),
                hint="canary 偵測到上述平台契約漂移;發布前請複查 adapter 或重新 seed canary",
            )
    except Exception:  # noqa: BLE001 — advisory must never break plan generation
        pass
