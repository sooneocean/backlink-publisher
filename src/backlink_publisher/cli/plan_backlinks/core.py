"""Core payload generation, link building, and CLI entry point."""

from __future__ import annotations

import random
import sys
from typing import Any

from typing import Iterator

from ... import config_echo
from ...content import (
    fetch as content_fetch,
)
import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before argparse
from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider
from backlink_publisher.publishing.registry import registered_platforms
from backlink_publisher.config import (
    Config,
    get_anchor_pool_v2,
    get_three_url_config,
    load_config,
)
from backlink_publisher._util.errors import (
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.url import canonicalize_url
from ...schema import (
    validate_input_payload,
)

# Re-export symbols from extracted sub-modules so __init__.py and sibling
# modules (._zh_short, ._work_themed) find them at their old import paths.
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

from ._payload import (                            # noqa: F401
    ARTICLE_LENGTH_WORDS,
    _generate_payload,
    _resolve_article_anchors,
    dofollow_tier_metadata,
)


def _emit_link_count_recon(payload: dict[str, Any], *, branch: str) -> None:
    links = payload.get("links") or []
    kinds = sorted({lk.get("kind", "?") for lk in links})
    plan_logger.recon(
        "link_count_at_plan",
        branch=branch,
        count=len(links),
        kinds=kinds,
        main_domain=payload.get("main_domain", ""),
        article_id=payload.get("id", ""),
    )


def _dispatch_row(
    row: dict[str, Any],
    config: Config,
    *,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None,
    work_count: int,
    fetch_verify_enabled: bool = True,
) -> Iterator[dict[str, Any]]:
    three_url_cfg = get_three_url_config(config, row["main_domain"])
    target_language = row.get("target_language", row["language"])
    # work_themed and zh_short only produce content in the site's native language;
    # when the operator explicitly requests a different output language, fall through
    # to long-form _generate_payload which respects target_language.
    use_native_schedulers = target_language == row["language"]
    if three_url_cfg is not None and use_native_schedulers:
        from backlink_publisher.cli.plan_backlinks import _plan_work_themed_row
        for payload in _plan_work_themed_row(row, three_url_cfg, count=work_count):
            _emit_link_count_recon(payload, branch="work_themed")
            yield payload
        return

    payload: dict[str, Any] | None = None
    if (
        row["language"] == "zh-CN"
        and use_native_schedulers
        and _scheduler_enabled_for(config, row["main_domain"])
    ):
        from backlink_publisher.cli.plan_backlinks import _plan_zh_short_row
        payload = _plan_zh_short_row(row, config, llm_provider, rng=rng)
        if payload is not None:
            _emit_link_count_recon(payload, branch="zh_short")
            yield payload
            return
    if payload is None:
        payload = _generate_payload(
            row, config=config, fetch_verify_enabled=fetch_verify_enabled,
        )
    _emit_link_count_recon(payload, branch="long_form")
    yield payload


def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    from backlink_publisher.cli.plan_backlinks import _scheduler_enabled_for as _inner
    return _inner(config, main_domain)


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

        rows = urls_to_seed_rows(
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
    config_sha = config_echo.emit_banner(cfg, "plan-backlinks")

    llm_provider: OpenAICompatibleProvider | None = None
    if cfg.llm_anchor_provider is not None:
        llm_provider = OpenAICompatibleProvider(
            base_url=cfg.llm_anchor_provider.base_url,
            api_key=cfg.llm_anchor_provider.api_key,
            model=cfg.llm_anchor_provider.model,
            timeout_s=cfg.llm_anchor_provider.timeout_s,
            temperature=cfg.llm_anchor_provider.temperature,
            system_prompt=cfg.llm_anchor_provider.system_prompt,
        )

    image_gen_runtime = _build_banner_runtime(cfg)

    rng = random.Random()

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    validation_drops: list[int] = []
    generation_drops: list[int] = []
    content_gate_drops: list[int] = []

    fetch_verify_enabled = not args.no_fetch_verify

    content_fetch.reset_stats()

    if fetch_verify_enabled:
        validated_rows: list[dict[str, Any]] = []
        for row in rows:
            if not validate_input_payload(row, 0):
                validated_rows.append(row)
        prefetch_set: set[str] = set()
        for row in validated_rows:
            prefetch_set.update(_collect_candidate_urls_for_row(row, cfg))
        prefetch_set.update(_SUPPORTING_URLS_FOR_PREFETCH)
        if prefetch_set:
            content_fetch.verify_urls_batch(
                list(prefetch_set), max_workers=10,
            )
            plan_logger.recon(
                "content_fetch_prefetch",
                n_urls_prefetched=len(prefetch_set),
                n_rows=len(validated_rows),
            )

    for line_num, row in enumerate(rows, start=1):
        errs = validate_input_payload(row, line_num)
        if errs:
            all_errors.extend(errs)
            validation_drops.append(line_num)
            continue
        try:
            for payload in _dispatch_row(
                row, cfg,
                llm_provider=llm_provider,
                rng=rng,
                work_count=args.work_count,
                fetch_verify_enabled=fetch_verify_enabled,
            ):
                branded_pool = get_anchor_pool_v2(
                    cfg, payload["main_domain"], "home", "branded"
                )
                metadata = dict(payload.get("metadata") or {})
                metadata["branded_pool"] = list(branded_pool)
                metadata["config_sha"] = config_sha
                metadata.update(dofollow_tier_metadata(payload["platform"]))
                payload["metadata"] = metadata

                if image_gen_runtime is not None:
                    payload["banner"] = _generate_banner_for_payload(
                        payload,
                        runtime=image_gen_runtime,
                        llm_provider=llm_provider,
                    )
                else:
                    payload["banner"] = None

                plan_logger.debug(
                    f"generated payload: id={payload['id']} platform={payload['platform']}",
                    extra={"id": payload["id"], "platform": payload["platform"]},
                )
                outputs.append(payload)
        except _ContentGateRowFailure as exc:
            all_errors.append(
                f"line {line_num}: content-gate failure: kind={exc.kind} "
                f"url={exc.url} reason={exc.reason}"
            )
            content_gate_drops.append(line_num)
        except Exception as exc:
            all_errors.append(f"line {line_num}: generation error: {exc}")
            generation_drops.append(line_num)

    plan_logger.recon(
        "plan_reconciliation",
        input_rows=len(rows),
        output_rows=len(outputs),
        delta=len(rows) - len(outputs),
        dropped={
            "validation": len(validation_drops),
            "generation": len(generation_drops),
            "content_gate": len(content_gate_drops),
        },
        dropped_line_numbers={
            "validation": validation_drops,
            "generation": generation_drops,
            "content_gate": content_gate_drops,
        },
    )

    plan_logger.recon(
        "content_fetch_stats",
        **content_fetch.stats_snapshot(),
    )

    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)
        plan_logger.error(f"generation failed: {len(all_errors)} errors")
        raise SystemExit(2)

    plan_logger.info(f"generated {len(outputs)} payloads")
    write_jsonl(outputs)

    # Forcing function (Plan 2026-05-26-008 R3a): on the success path only, nudge
    # the operator to verify destination pages before publishing. RECON-level so
    # it survives the default WARN gate (and is stripped by tests'
    # _stderr_without_warnings, so existing assertions stay green). stdout stays
    # pure JSONL — the nudge goes to stderr.
    distinct_targets = {
        canonicalize_url(target.strip())
        for row in outputs
        if isinstance((target := row.get("target_url")), str) and target.strip()
    }
    if distinct_targets:
        plan_logger.recon(
            "preflight_nudge",
            distinct_targets=len(distinct_targets),
            hint="run `preflight-targets` to verify destination pages before publishing",
        )
