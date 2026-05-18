"""Generate backlink article payloads from seed URLs."""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from typing import Any
from urllib.parse import urlparse

from typing import Iterator

from .. import (
    anchor_profile,
    anchor_resolver,
    anchor_scheduler,
    config_echo,
    content_fetch,
    errors,
    markdown_utils,
    work_scraper,
    work_themed_generator,
)
import backlink_publisher.publishing.adapters  # noqa: F401  populate registry before argparse
from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider
from backlink_publisher.publishing.registry import registered_platforms
from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.anchor.scheduler import ScheduleDecision, SecondaryLink
from backlink_publisher.config import (
    Config,
    ThreeUrlConfig,
    get_anchor_keywords,
    get_anchor_pool_v2,
    get_three_url_config,
    load_config,
)
from backlink_publisher._util.errors import (
    ExternalServiceError,
    InputValidationError,
    emit_error,
)
from backlink_publisher._util.jsonl import read_jsonl, write_jsonl
from backlink_publisher.linkcheck.language import detect_language
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.markdown import (
    _en_body_a,
    _en_body_b,
    _en_body_c,
    _ru_body_a,
    _ru_body_b,
    _ru_body_c,
    _zh_body_a,
    _zh_body_b,
    _zh_body_c,
    links_to_markdown,
    select_anchor_keywords,
    slugify,
)
from ..schema import (
    INPUT_SCHEMA_FIELDS,
    SUPPORTED_LANGUAGES,
    URL_MODES,
    validate_input_payload,
)

ARTICLE_LENGTH_WORDS = (100, 200)


class _ContentGateRowFailure(Exception):
    """Raised by ``_build_links`` when ``main_domain`` or ``target`` URL fails
    the content gate. The row cannot be published — without its primary
    target the article has no anchor. The main loop catches this and
    increments the ``content_gate`` Silent-Drop Tripwire counter.

    Plan ref: docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md
    Unit 3.
    """

    def __init__(self, url: str, reason: str, kind: str) -> None:
        super().__init__(f"row-failing content gate: kind={kind} url={url} reason={reason}")
        self.url = url
        self.reason = reason
        self.kind = kind


#: URL kinds whose gate failure aborts the row (article unpublishable).
#: All other kinds (extra, supporting, category, detail) just drop the link.
_ROW_REQUIRED_KINDS: frozenset[str] = frozenset({"main_domain", "target"})


#: Canonical (url, anchor) pairs for supporting links. Consumed by both
#: ``_build_links`` (long-form branch) and ``_build_work_themed_payload``
#: (work-themed branch — plan 2026-05-15-003) to pad articles up to the
#: schema's 6-8 link range. Fixed across every row; prefetching once per
#: batch invocation lets CSV runs share a single fetch.
_SUPPORTING_POOL: tuple[tuple[str, str], ...] = (
    ("https://en.wikipedia.org", "Wikipedia"),
    ("https://developer.mozilla.org", "MDN"),
    ("https://stackoverflow.com", "Stack Overflow"),
    ("https://github.com", "GitHub"),
    ("https://news.ycombinator.com", "Hacker News"),
)

#: Backward-compat URL-only view kept for ``_collect_candidate_urls_for_row``'s
#: prefetch path. Always equals the URL column of ``_SUPPORTING_POOL``.
_SUPPORTING_URLS_FOR_PREFETCH: tuple[str, ...] = tuple(
    url for url, _anchor in _SUPPORTING_POOL
)

#: Total link count target for fast-path branches (work-themed,
#: zh-CN short) that historically emitted 2-3 links each and tripped
#: ``schema.py:143``'s 6-8 gate. Midpoint of 6-8 so a single later drop
#: stays in-range.
_TARGET_PADDED_LINK_COUNT: int = 7

#: Remap from ``work_themed_generator``'s emitted kinds to ``schema.LINK_KINDS``
#: members. ``main_domain`` already matches; ``list``/``work`` are translated
#: to their closest semantic neighbours so downstream consumers see the
#: canonical taxonomy.
_KIND_REMAP_WORK_THEMED: dict[str, str] = {
    "main_domain": "main_domain",
    "list": "category",
    "work": "target",
}


def _collect_candidate_urls_for_row(
    row: dict[str, Any], config: Config | None,
) -> list[str]:
    """Return the URLs ``_build_links`` would batch-verify for ``row``.

    Pure-string mirror of the URL emission logic in ``_build_links`` — no
    HTTP, no fetch, no recon side effects. Used by the cross-row prefetch
    optimisation (plan 2026-05-14-008 Unit 2): the main loop unions these
    across all validated rows + the fixed supporting set, then calls
    ``content_fetch.verify_urls_batch`` ONCE up front. Subsequent per-row
    ``_build_links`` calls hit the cache exclusively.

    Skips the work-themed / zh-CN short branches (they don't go through
    ``_build_links`` and build their own link sets — gate doesn't apply
    in this iteration; see scope-deferred note in dispatch_row).
    """
    main_domain = row.get("main_domain", "").rstrip("/")
    target_url = row.get("target_url", "").rstrip("/")
    url_mode = row.get("url_mode", "A")
    extra_urls = row.get("extra_urls") or []

    if not main_domain:
        return []

    urls: list[str] = [main_domain]
    if target_url and target_url != main_domain:
        urls.append(target_url)
    for ex in list(extra_urls)[:2]:
        if ex:
            urls.append(ex.rstrip("/"))

    if config is not None:
        cats = config.site_url_categories.get(main_domain, {})
        if url_mode in ("B", "C"):
            cat = cats.get("category")
            if cat:
                urls.append(cat.rstrip("/"))
        if url_mode == "C":
            det = cats.get("detail")
            if det:
                urls.append(det.rstrip("/"))

    return urls

_TDK_TITLE_TMPL: dict[str, str] = {
    "zh-CN": "深入了解{tdk}: {domain} 完整指南",
    "ru": "Подробнее о {tdk}: полный гид по {domain}",
    "en": "Deep Dive into {tdk}: The Complete {domain} Guide",
}

# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict[str, Any]] = {
    "en": {
        "title": {
            "A": "Exploring {domain}: A Comprehensive Guide",
            "B": "Navigating {domain} \u2014 Categories and Resources",
            "C": "Deep Dive into {domain}: {topic}",
        },
        "excerpt": {
            "A": "This article explores the resources and value offered by [{anchor}]({main_domain}), "
                  "providing context and curated links for readers.",
            "B": "A curated overview of [{anchor}]({main_domain})'s sections and key pages, "
                  "helping you navigate the site effectively.",
            "C": "A detailed look at {topic} as covered by [{anchor}]({main_domain}), with "
                  "additional references for further reading.",
        },
        "seo_title": "{title} | Backlink Article",
        "seo_desc": "A well-researched backlink article referencing {main_domain} "
                    "with curated external links and resources.",
        "topic_fallback": "Latest Resources and Insights",
        "tags": ["backlink", "reference", "web resources", "{domain_label}", "content curation"],
        "body_paragraphs": {
            "A": _en_body_a,
            "B": _en_body_b,
            "C": _en_body_c,
        },
    },
    "zh-CN": {
        "title": {
            "A": "\u6df1\u5165\u63a2\u7d22{domain}\uff1a\u5168\u9762\u6307\u5357",
            "B": "\u6d4f\u89c8{domain}\u2014\u5206\u7c7b\u4e0e\u8d44\u6e90\u6982\u89c8",
            "C": "\u6df1\u5ea6\u89e3\u6790{domain}\uff1a{topic}",
        },
        "excerpt": {
            "A": "\u672c\u6587\u63a2\u8ba8[{anchor}]({main_domain})\u63d0\u4f9b\u7684\u8d44\u6e90\u548c\u4ef7\u503c\uff0c\u4e3a\u8bfb\u8005\u63d0\u4f9b\u80cc\u666f\u548c\u7cbe\u9009\u94fe\u63a5\u3002",
            "B": "\u5bf9[{anchor}]({main_domain})\u5404\u677f\u5757\u548c\u5173\u952e\u9875\u9762\u7684\u7cbe\u9009\u6982\u89c8\uff0c\u5e2e\u52a9\u60a8\u9ad8\u6548\u6d4f\u89c8\u8be5\u7f51\u7ad9\u3002",
            "C": "\u8be6\u7ec6\u89e3\u8bfb[{anchor}]({main_domain})\u4e0a\u7684{topic}\u5185\u5bb9\uff0c\u5e76\u63d0\u4f9b\u5ef6\u4f38\u53c2\u8003\u8d44\u6599\u3002",
        },
        "seo_title": "{title} | \u53cd\u5411\u94fe\u63a5\u6587\u7ae0",
        "seo_desc": "\u4e00\u7bc7\u7cbe\u5fc3\u64b0\u5199\u7684\u53cd\u5411\u94fe\u63a5\u6587\u7ae0\uff0c\u5f15\u7528{main_domain}\u5e76\u63d0\u4f9b\u7cbe\u9009\u5916\u90e8\u94fe\u63a5\u548c\u8d44\u6e90\u3002",
        "topic_fallback": "\u6700\u65b0\u8d44\u6e90\u4e0e\u89c1\u89e3",
        "tags": ["\u53cd\u5411\u94fe\u63a5", "\u53c2\u8003", "\u7f51\u7edc\u8d44\u6e90", "{domain_label}", "\u5185\u5bb9\u7b56\u5c55"],
        "body_paragraphs": {
            "A": _zh_body_a,
            "B": _zh_body_b,
            "C": _zh_body_c,
        },
    },
    "ru": {
        "title": {
            "A": "\u0418\u0437\u0443\u0447\u0435\u043d\u0438\u0435 {domain}: \u041f\u043e\u043b\u043d\u043e\u0435 \u0440\u0443\u043a\u043e\u0432\u043e\u0434\u0441\u0442\u0432\u043e",
            "B": "\u041d\u0430\u0432\u0438\u0433\u0430\u0446\u0438\u044f \u043f\u043e {domain} \u2014 \u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438 \u0438 \u0440\u0435\u0441\u0443\u0440\u0441\u044b",
            "C": "\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0437 {domain}: {topic}",
        },
        "excerpt": {
            "A": "\u042d\u0442\u0430 \u0441\u0442\u0430\u0442\u044c\u044f \u0438\u0441\u0441\u043b\u0435\u0434\u0443\u0435\u0442 \u0440\u0435\u0441\u0443\u0440\u0441\u044b \u0438 \u0446\u0435\u043d\u043d\u043e\u0441\u0442\u044c [{anchor}]({main_domain}), "
                  "\u043f\u0440\u0435\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044f \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0438 \u043a\u0443\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 \u0441\u0441\u044b\u043b\u043a\u0438 \u0434\u043b\u044f \u0447\u0438\u0442\u0430\u0442\u0435\u043b\u0435\u0439.",
            "B": "\u041f\u043e\u0434\u0431\u043e\u0440 \u0440\u0430\u0437\u0434\u0435\u043b\u043e\u0432 \u0438 \u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0445 \u0441\u0442\u0440\u0430\u043d\u0438\u0446 [{anchor}]({main_domain}), "
                  "\u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u043f\u043e\u043c\u043e\u0436\u0435\u0442 \u0432\u0430\u043c \u044d\u0444\u0444\u0435\u043a\u0442\u0438\u0432\u043d\u043e \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c\u0441\u044f \u043d\u0430 \u0441\u0430\u0439\u0442\u0435.",
            "C": "\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0437 \u0442\u0435\u043c\u044b {topic} \u043d\u0430 [{anchor}]({main_domain}) "
                  "\u0441 \u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u043c\u0438 \u0441\u0441\u044b\u043b\u043a\u0430\u043c\u0438 \u0434\u043b\u044f \u0434\u0430\u043b\u044c\u043d\u0435\u0439\u0448\u0435\u0433\u043e \u0447\u0442\u0435\u043d\u0438\u044f.",
        },
        "seo_title": "{title} | \u041e\u0431\u0440\u0430\u0442\u043d\u0430\u044f \u0441\u0441\u044b\u043b\u043a\u0430 \u0441\u0442\u0430\u0442\u044c\u044f",
        "seo_desc": "\u041a\u0430\u0447\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u0430\u044f \u043e\u0431\u0440\u0430\u0442\u043d\u0430\u044f \u0441\u0441\u044b\u043b\u043a\u0430 \u0441\u0442\u0430\u0442\u044c\u044f \u0441\u043e \u0441\u0441\u044b\u043b\u043a\u0430\u043c\u0438 \u043d\u0430 {main_domain} "
                      "\u0438 \u0434\u043e\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c\u043d\u044b\u043c\u0438 \u0440\u0435\u0441\u0443\u0440\u0441\u0430\u043c\u0438.",
        "topic_fallback": "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435 \u0440\u0435\u0441\u0443\u0440\u0441\u044b \u0438 \u0438\u043d\u0441\u0430\u0439\u0442\u044b",
        "tags": ["\u043e\u0431\u0440\u0430\u0442\u043d\u0430\u044f-\u0441\u0441\u044b\u043b\u043a\u0430", "\u0441\u0441\u044b\u043b\u043a\u0430",
                 "\u0432\u0435\u0431-\u0440\u0435\u0441\u0443\u0440\u0441", "{domain_label}", "\u043a\u0443\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435"],
        "body_paragraphs": {
            "A": _ru_body_a,
            "B": _ru_body_b,
            "C": _ru_body_c,
        },
    },
}


def _build_links(
    main_domain: str,
    target_url: str,
    url_mode: str,
    extra_urls: list[str] | None = None,
    anchors: list[str] | None = None,
    site_url_categories: dict[str, dict[str, str]] | None = None,
    fetch_verify_enabled: bool = True,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Construct the list of links for the article (target: 6-8 links).

    ``anchors`` (when provided) supplies SEO-friendly keyword anchors for the
    main_domain and target links — anchors[0] for main_domain, anchors[1] for
    target. When omitted or shorter than needed, falls back to the bare-domain
    label (legacy behaviour).

    ``site_url_categories`` (when provided) sources mode-specific category /
    detail URLs from ``[sites."<main_domain>".url_categories]`` config table.
    The historical pre-2026-05-14 behaviour was to synthesise
    ``<main_domain>/categories`` and ``<main_domain>/detail`` regardless of
    whether those paths existed on the target site — which the PR #16
    publish-time reachability gate then rejected with HTTP 404. New behaviour:
    pull the URLs from config; if absent, omit the mode-specific link entirely
    rather than emit a known-broken URL.

    ``fetch_verify_enabled`` (default True) gates every candidate URL through
    :func:`content_fetch.verify_urls_batch` before appending. Links whose URL
    returns non-200 or has no parseable ``<title>`` are dropped with a
    ``link_dropped_no_content`` recon event. ``main_domain`` and ``target``
    failures raise :class:`_ContentGateRowFailure` to abort the entire row;
    other kinds (extra, supporting, category, detail) just thin the article
    and rely on the density paragraph to compensate. Set False to bypass
    (``plan-backlinks --no-fetch-verify``).

    Returns
    -------
    (links, dropped_kinds)
        ``dropped_kinds`` is the set of kinds that the gate dropped for this
        row. ``_build_link_density_paragraph`` uses it to subtract the
        dropped contributions when computing whether to compensate.
    """
    # Phase 1: collect every candidate link record. The gate runs once over
    # the merged URL set so concurrent HTTP overlaps a single row's links.
    candidates: list[dict[str, Any]] = []

    domain_label = main_domain.rstrip("/").replace("https://", "").replace("http://", "")
    main_anchor = anchors[0] if anchors and len(anchors) >= 1 else domain_label
    candidates.append({
        "url": main_domain.rstrip("/"),
        "anchor": main_anchor,
        "kind": "main_domain",
        "required": True,
    })

    if target_url != main_domain:
        target_label = target_url.rstrip("/").replace("https://", "").replace("http://", "")
        target_anchor = anchors[1] if anchors and len(anchors) >= 2 else target_label
        candidates.append({
            "url": target_url,
            "anchor": target_anchor,
            "kind": "target",
            "required": True,
        })

    if extra_urls:
        for ex_url in extra_urls[:2]:
            parsed = urlparse(ex_url)
            path = parsed.path
            if "/page/" in path or "?page=" in ex_url:
                anchor = "分页"
            elif "/category/" in path or "/tag/" in path:
                anchor = "分类"
            elif "/archive/" in path:
                anchor = "归档"
            else:
                anchor = "相关"
            candidates.append({
                "url": ex_url.rstrip("/"),
                "anchor": anchor,
                "kind": "extra",
                "required": False,
            })

    domain_key = main_domain.rstrip("/")
    cats = (site_url_categories or {}).get(domain_key, {})
    if url_mode in ("B", "C"):
        cat_url = cats.get("category")
        if cat_url:
            candidates.append({
                "url": cat_url.rstrip("/"),
                "anchor": "Categories",
                "kind": "category",
                "required": True,
            })
        else:
            plan_logger.recon(
                "category_link_skipped_no_config",
                main_domain=domain_key,
                url_mode=url_mode,
                reason="no_url_categories.category_in_config",
            )
    if url_mode == "C":
        detail_url = cats.get("detail")
        if detail_url:
            candidates.append({
                "url": detail_url.rstrip("/"),
                "anchor": "详情页",
                "kind": "detail",
                "required": True,
            })
        else:
            plan_logger.recon(
                "detail_link_skipped_no_config",
                main_domain=domain_key,
                url_mode=url_mode,
                reason="no_url_categories.detail_in_config",
            )

    # Pad with supporting links up to 8 total (gate may drop some so we keep
    # all 5 candidates and let the gate decide how many survive).
    target_max = 8
    for surl, sanchor in _SUPPORTING_POOL:
        if len(candidates) >= target_max:
            break
        candidates.append({
            "url": surl,
            "anchor": sanchor,
            "kind": "supporting",
            "required": False,
        })

    # Phase 2: content-fetch gate. Batch-verify all candidate URLs, then
    # filter the record list. Row-required failures raise; other failures
    # drop the link and record the kind so density math knows.
    dropped_kinds: set[str] = set()
    if not fetch_verify_enabled:
        return candidates, dropped_kinds

    urls = [c["url"] for c in candidates]
    results = content_fetch.verify_urls_batch(urls)

    links: list[dict[str, Any]] = []
    for cand in candidates:
        ok, reason, _ = results.get(cand["url"], (False, "missing_result", None))
        if ok:
            links.append(cand)
            continue
        if cand["kind"] in _ROW_REQUIRED_KINDS:
            # Row cannot be published without main_domain / target. Caller
            # (main loop) catches and counts against the Silent-Drop Tripwire.
            plan_logger.recon(
                "row_dropped_content_gate",
                url=cand["url"],
                kind=cand["kind"],
                reason=reason or "unknown",
            )
            raise _ContentGateRowFailure(cand["url"], reason or "unknown", cand["kind"])
        dropped_kinds.add(cand["kind"])
        plan_logger.recon(
            "link_dropped_no_content",
            url=cand["url"],
            kind=cand["kind"],
            reason=reason or "unknown",
        )

    return links, dropped_kinds


def _build_link_density_paragraph(
    domain: str,
    main_domain: str,
    target_url: str,
    language: str,
    url_mode: str,
    extra_url_count: int,
    anchors: list[str] | None = None,
    site_url_categories: dict[str, dict[str, str]] | None = None,
    dropped_kinds: set[str] | None = None,
) -> str:
    """Return a short paragraph that adds missing target-site links to reach A+B+C ≥ 6.

    Computes the expected link count after body/excerpt/references are assembled,
    and only produces content when the count would be below 6.

    Mode B's category link and Mode C's category+detail links are only counted
    when ``site_url_categories`` provides real URLs for them. Synthesised
    ``/categories`` / ``/detail`` URLs were removed in 2026-05-14 after the
    publish-time reachability gate (PR #16) caught them as HTTP 404 on sites
    that don't actually serve those paths — see _build_links.

    ``dropped_kinds`` carries the set of link kinds that the content-fetch gate
    (plan 2026-05-14-007) just removed from the row, so this paragraph's
    base-count math doesn't over-credit links that ``_build_links`` no longer
    emits. Without this hook a dropped ``extra`` or ``category`` link would
    silently push the article below the 6-link density floor.

    ``anchors`` (when provided) supplies SEO keywords for the two link slots in
    the paragraph; falls back to ``domain`` (bare label) otherwise.
    """
    # Base count: excerpt(1) + body_template(2) + references_main(1) = 4
    base = 4
    if target_url != main_domain:
        base += 1   # references_target entry
    cats = (site_url_categories or {}).get(main_domain.rstrip("/"), {})
    if url_mode in ("B", "C") and cats.get("category"):
        base += 1
    if url_mode == "C" and cats.get("detail"):
        base += 1
    base += min(extra_url_count, 2)  # up to 2 extra_urls in references

    # Subtract anything the content-fetch gate already dropped from this row.
    # `extra` / `category` / `detail` were counted above but won't render.
    # `supporting` / `main_domain` / `target` aren't part of base, so we
    # ignore them here (main_domain / target failures raised earlier and the
    # row never reaches this function).
    if dropped_kinds:
        if "extra" in dropped_kinds:
            base -= min(extra_url_count, 2)
        if "category" in dropped_kinds:
            base -= 1
        if "detail" in dropped_kinds:
            base -= 1

    if base >= 6:
        return ""

    same_url = (target_url == main_domain)
    a0 = anchors[0] if anchors and len(anchors) >= 1 else domain
    a1 = anchors[1] if anchors and len(anchors) >= 2 else domain

    if language == "zh-CN":
        if same_url:
            return (
                f"\n\n欲了解更多资源，请访问[{a0}]({main_domain})，"
                f"探索[{a1}]({main_domain})为您精心准备的丰富内容。"
            )
        return (
            f"\n\n阅读更多请访问[{a1}]({target_url})，"
            f"并前往[{a0}]({main_domain})获取完整内容。"
        )

    if language == "ru":
        if same_url:
            return (
                f"\n\nБольше материалов доступно на [{a0}]({main_domain}) — "
                f"посетите [{a1}]({main_domain}) для просмотра полного каталога."
            )
        return (
            f"\n\nЧитайте подробнее на [{a1}]({target_url}) и "
            f"посетите [{a0}]({main_domain}) для обзора всех материалов."
        )

    # English (default)
    if same_url:
        return (
            f"\n\nFor more resources, visit [{a0}]({main_domain}) and explore "
            f"the wide range of content available at [{a1}]({main_domain})."
        )
    return (
        f"\n\nRead more at [{a1}]({target_url}) and visit the main hub "
        f"[{a0}]({main_domain}) for the full collection."
    )


def _resolve_article_anchors(
    config: Config | None,
    main_domain: str,
    url_mode: str,
    fallback_label: str,
) -> list[str]:
    """Pick the two SEO anchor keywords for an article's main_domain links.

    When the target site has no configured ``anchor_keywords`` (or the entry is
    empty), fall back to the bare-domain label and emit a single WARN per
    article so the operator notices the missed SEO opportunity.
    """
    keywords = get_anchor_keywords(config, main_domain) if config is not None else []
    selected = select_anchor_keywords(keywords, url_mode, 2)
    if selected is None:
        plan_logger.warn(
            f"anchor_keywords missing for {main_domain}, falling back to bare domain label",
            main_domain=main_domain,
        )
        return [fallback_label, fallback_label]
    return selected


def _generate_payload(
    row: dict[str, Any],
    config: Config | None = None,
    *,
    fetch_verify_enabled: bool = True,
) -> dict[str, Any]:
    """Generate a single backlink article payload from a seed row.

    ``fetch_verify_enabled`` (default True) gates every URL emitted into the
    payload's ``links`` list through :mod:`content_fetch`. Raises
    :class:`_ContentGateRowFailure` when ``main_domain`` or ``target_url`` fails
    the gate; the caller (main loop) catches and counts the drop against the
    Silent-Drop Tripwire ``content_gate`` bucket.
    """
    main_domain = row["main_domain"].rstrip("/")
    target_url = row["target_url"].rstrip("/")
    url_mode = row.get("url_mode", "A")
    platform = row["platform"]
    language = row["language"]
    target_language = row.get("target_language", language)
    publish_mode = row.get("publish_mode", "draft")
    topic = row.get("topic", "")
    extra_urls = row.get("extra_urls", [])
    custom_tags = row.get("custom_tags", "")
    system_prompt = row.get("system_prompt", "")
    tdk_title = row.get('tdk_title', '')
    tdk_description = row.get('tdk_description', '')
    tdk_keywords = row.get('tdk_keywords', '')

    domain_label = main_domain.replace("https://", "").replace("http://", "").replace("www.", "")

    # Resolve the two SEO anchor keywords for this article (or fall back to the
    # bare domain label with a WARN if no pool is configured).
    anchors = _resolve_article_anchors(config, main_domain, url_mode, domain_label)

    tmpl = _TEMPLATES.get(target_language, _TEMPLATES.get(language, _TEMPLATES["en"]))
    title_tmpl = tmpl["title"].get(url_mode, tmpl["title"]["A"])
    topic_val = topic or tmpl.get("topic_fallback", "Resources")

    # Use TDK title if available, otherwise use custom or auto-generated
    title = row.get("custom_title", "")
    if not title:
        if tdk_title and url_mode == 'C':
            lang_key = target_language if target_language in _TDK_TITLE_TMPL else "en"
            title = _TDK_TITLE_TMPL[lang_key].format(tdk=tdk_title, domain=domain_label)
        else:
            title = title_tmpl.format(domain=domain_label, topic=topic_val)
    
    slug = slugify(title)
    
    # Use TDK description for excerpt if available
    if tdk_description and url_mode in ('B', 'C'):
        excerpt = tdk_description[:200]
    else:
        excerpt = tmpl["excerpt"].get(url_mode, tmpl["excerpt"]["A"]).format(
            main_domain=main_domain, domain=domain_label, topic=topic_val,
            anchor=anchors[0],
        )

    tags_raw = tmpl.get("tags", ["backlink"])
    tags = [t.format(domain_label=domain_label) for t in tags_raw]
    
    # Add custom tags and TDK keywords
    if custom_tags:
        custom_tags_list = [t.strip() for t in custom_tags.split(",") if t.strip()]
        tags.extend(custom_tags_list)
    
    if tdk_keywords:
        kw_list = [k.strip() for k in tdk_keywords.split(",") if k.strip()]
        for kw in kw_list[:3]:
            if kw not in tags:
                tags.append(kw)

    body_tmpl = tmpl["body_paragraphs"].get(url_mode, tmpl["body_paragraphs"]["A"])
    body = body_tmpl(domain=domain_label, main_domain=main_domain, anchors=anchors)
    
    # Add TDK info section if available
    if tdk_title or tdk_description:
        tdk_section = f"\n\n---\n**关于 {domain_label}**\n"
        if tdk_title:
            tdk_section += f"- 标题: {tdk_title}\n"
        if tdk_description:
            tdk_section += f"- 描述: {tdk_description[:150]}...\n"
        body = body + tdk_section

    # Add extra URLs content naturally into the article body
    if extra_urls:
        # Add intro paragraph referencing the extra pages
        extra_intro = f"\n\n除了主要的{domain_label}资源外，我们还整理了以下相关页面供您参考：\n"
        body = body + extra_intro
        
        # Add inline links to body content based on URL type
        for i, ex_url in enumerate(extra_urls[:3]):
            parsed = urlparse(ex_url)
            path = parsed.path
            
            # Determine context based on URL path
            if "/page/" in path or "?page=" in ex_url:
                anchor = f"第{path.split('/page/')[-1] if '/page/' in path else '其他'}页"
                context = f"更多内容请查看{anchor}。"
            elif "/category/" in path or "/tag/" in path:
                cat_name = path.split("/")[-2] if len(path.split("/")) > 2 else path.split("/")[-1]
                anchor = cat_name
                context = f"探索{anchor}分类了解更多相关内容。"
            elif "/archive/" in path:
                anchor = "历史归档"
                context = "查看历史文章归档。"
            else:
                anchor = f"相关页面 {i+1}"
                context = "这些相关页面也值得一读。"
            
            body = body + f"- [{anchor}]({ex_url}) - {context}\n"
        
        # Add detailed reference section at the end
        extra_section = "\n## 更多相关资源\n\n"
        for ex_url in extra_urls[:5]:
            parsed = urlparse(ex_url)
            path = parsed.path.split("/")[-1] or parsed.path.split("/")[-2] or "页面"
            
            # Generate more descriptive anchor text
            if "/category/" in path:
                anchor = f"分类: {path.split('/')[-1]}"
            elif "/tag/" in path:
                anchor = f"标签: {path.split('/')[-1]}"
            elif "/page/" in path:
                anchor = f"分页 {path.split('/page/')[-1]}"
            elif "/archive/" in path:
                anchor = "归档页面"
            else:
                anchor = path if path else "相关链接"
            
            extra_section += f"- [{anchor}]({ex_url})\n"
        
        body = body + extra_section

    # Build links FIRST so the density paragraph can subtract anything the
    # content-fetch gate just dropped. May raise _ContentGateRowFailure when
    # main_domain or target fails the gate — propagates to the main loop.
    links, dropped_kinds = _build_links(
        main_domain,
        target_url,
        url_mode,
        extra_urls,
        anchors=anchors,
        site_url_categories=config.site_url_categories if config else None,
        fetch_verify_enabled=fetch_verify_enabled,
    )

    # Inject density paragraph if target-site link count would be < 6,
    # accounting for any links the gate just removed.
    density_para = _build_link_density_paragraph(
        domain=domain_label,
        main_domain=main_domain,
        target_url=target_url,
        language=language,
        url_mode=url_mode,
        extra_url_count=len(extra_urls) if extra_urls else 0,
        anchors=anchors,
        site_url_categories=config.site_url_categories if config else None,
        dropped_kinds=dropped_kinds,
    )
    if density_para:
        body = body + density_para

    # Build content_markdown
    content_parts: list[str] = []
    content_parts.append(f"# {title}\n")
    content_parts.append(f"\n{excerpt}\n")
    content_parts.append(f"\n{body}\n")
    content_parts.append(f"\n## References\n")
    content_parts.append(links_to_markdown(links))
    content_markdown = "\n".join(content_parts)

    seo_title = tmpl.get("seo_title", "{title}").format(title=title)
    seo_desc = tmpl.get("seo_desc", "").format(main_domain=main_domain)

    # Deterministic ID from seed data
    seed_str = f"{target_url}:{main_domain}:{url_mode}:{platform}"
    article_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

    return {
        "id": article_id,
        "platform": platform,
        "language": target_language,
        "source_language": language,
        "publish_mode": publish_mode,
        "target_url": target_url + ("/" if not target_url.endswith("/") else ""),
        "main_domain": main_domain + ("/" if not main_domain.endswith("/") else ""),
        "url_mode": url_mode,
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "tags": tags,
        "content_markdown": content_markdown,
        "links": links,
        "seo": {
            "title": seo_title,
            "description": seo_desc,
            "canonical_url": target_url,
        },
    }


# ─── zh-CN short-form scheduler integration ─────────────────────────────────
#
# The scheduler engages only when (a) the seed row is zh-CN AND (b) the site
# config carries the v2 typed pool + url_categories ≥ home + 1 non-home. Any
# other combination falls back to the legacy long-form ``_generate_payload``,
# so existing en/ru rows and any zh-CN row from a site that hasn't been
# migrated to v2 config are bit-for-bit unchanged.


def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    """Return True iff the zh-CN scheduler can engage for ``main_domain``."""
    key = main_domain.rstrip("/")
    cats = config.site_url_categories.get(key, {})
    has_home = "home" in cats
    has_non_home = any(c != "home" for c in cats)
    has_pools = bool(config.target_anchor_pools_v2.get(key))
    return has_home and has_non_home and has_pools


def _domain_label_of(main_domain: str) -> str:
    """Bare-domain string used as the last-resort branded anchor."""
    return (
        main_domain.rstrip("/")
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
    )


def _extract_zh_keyword(row: dict[str, Any], main_domain: str) -> str:
    """Pick a keyword for the resolver prompt: seed_keywords[0] → topic → domain."""
    seeds = row.get("seed_keywords")
    if isinstance(seeds, list) and seeds and isinstance(seeds[0], str) and seeds[0]:
        return seeds[0]
    topic = row.get("topic", "")
    if isinstance(topic, str) and topic:
        return topic
    return _domain_label_of(main_domain)


def _build_profile_entries(
    decision: ScheduleDecision,
    main_anchor: str,
    main_target_url: str,
    sec_records: list[tuple[str, str, str, str]],
    *,
    degraded: bool,
) -> list[ProfileEntry]:
    """Pack the article's link decisions into ProfileEntry rows.

    ``sec_records`` is ``[(url_category, anchor_type, anchor_text, target_url), ...]``
    — one tuple per secondary, ordered as rendered. ``main_target_url`` is the
    URL the main anchor points at (home_url for both happy and degrade paths).
    Each entry's ``target_url`` is populated so report-anchors can compute
    per-destination distribution metrics (anchor over-optimization is a
    per-URL signal, not per-domain).
    """
    ts = anchor_profile.now_iso()
    entries = [
        ProfileEntry(
            ts=ts,
            link_role="main",
            url_category="home",
            anchor_type=decision.main_link_anchor_type,
            anchor_text=main_anchor,
            degraded=degraded,
            target_url=main_target_url,
        )
    ]
    for url_cat, anchor_type, anchor_text, target_url in sec_records:
        entries.append(
            ProfileEntry(
                ts=ts,
                link_role="secondary",
                url_category=url_cat,
                anchor_type=anchor_type,
                anchor_text=anchor_text,
                degraded=degraded,
                target_url=target_url,
            )
        )
    return entries


def _build_zh_short_payload(
    row: dict[str, Any],
    html: str,
    main_domain: str,
    main_anchor: str,
    sec_pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    """Shape a zh-CN short-form payload to the same schema as ``_generate_payload``.

    ``content_markdown`` holds the rendered HTML directly — markdown-it is
    idempotent on plain HTML (see Unit 6 round-trip test), so downstream
    ``publish_backlinks`` works without changes.

    Per plan 2026-05-15-003 (extended in commit dfa44e8 + zh-CN follow-up):
    - Every link record carries the ``required`` field that schema demands.
    - The bare 1+len(sec_pairs) link set is padded up to
      ``_TARGET_PADDED_LINK_COUNT`` with entries from ``_SUPPORTING_POOL`` so
      the row clears ``schema.py:143``'s 6-8 gate. A matching "延伸阅读"
      paragraph is appended to the body so the URL strings appear in
      ``content_markdown`` (R3 — verify_publish's link-presence check).
    """
    target_url = row["target_url"].rstrip("/")
    platform = row["platform"]
    publish_mode = row.get("publish_mode", "draft")
    language = row["language"]
    url_mode = row.get("url_mode", "A")

    domain_label = _domain_label_of(main_domain)
    home_url = main_domain.rstrip("/") + "/"

    links: list[dict[str, Any]] = [
        {
            "url": home_url,
            "anchor": main_anchor,
            "kind": "main_domain",
            "required": True,
        },
    ]
    existing_urls: set[str] = {home_url}
    for sec_url, sec_anchor in sec_pairs:
        links.append({
            "url": sec_url,
            "anchor": sec_anchor,
            "kind": "supporting",
            "required": False,
        })
        existing_urls.add(sec_url)

    # Pad to _TARGET_PADDED_LINK_COUNT with the shared supporting pool;
    # dedupe by URL so an operator-configured secondary URL that happens
    # to coincide with the pool doesn't double-list.
    pad_count = _TARGET_PADDED_LINK_COUNT - len(links)
    added_supporting: list[dict[str, Any]] = []
    if pad_count > 0:
        for surl, sanchor in _SUPPORTING_POOL:
            if len(added_supporting) >= pad_count:
                break
            if surl in existing_urls:
                continue
            sup = {
                "url": surl,
                "anchor": sanchor,
                "kind": "supporting",
                "required": False,
            }
            added_supporting.append(sup)
            links.append(sup)
            existing_urls.add(surl)

    # NOTE: zh-CN short articles have a 150-200 plain-char length budget
    # (validated in markdown_utils.validate_zh_short_payload). Padded
    # supporting URLs live in ``links[]`` metadata only — we deliberately
    # do NOT append a "延伸阅读" paragraph here. The schema check
    # (validate_output_payload) only requires ``main_domain`` to appear
    # in body; verify_publish only checks URLs with ``required=True``.
    # Both are already satisfied by the rendered short-article HTML.

    custom_title = row.get("custom_title", "")
    title = custom_title or f"{domain_label} 内容推荐"
    slug = slugify(title) or hashlib.sha256(title.encode()).hexdigest()[:12]
    excerpt = re.sub(r"<[^>]+>", "", html)[:100]

    custom_tags = row.get("custom_tags", "")
    tags = ["backlink", domain_label]
    if custom_tags:
        tags.extend(t.strip() for t in custom_tags.split(",") if t.strip())

    seed_str = f"{target_url}:{main_domain}:zh-short:{platform}"
    article_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

    return {
        "id": article_id,
        "platform": platform,
        "language": language,
        "source_language": language,
        "publish_mode": publish_mode,
        "target_url": target_url + "/",
        "main_domain": home_url,
        "url_mode": url_mode,
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "tags": tags,
        "content_markdown": html,
        "links": links,
        "seo": {
            "title": title,
            "description": excerpt,
            "canonical_url": target_url,
        },
    }


def _plan_zh_short_row(
    row: dict[str, Any],
    config: Config,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None = None,
) -> dict[str, Any] | None:
    """Generate one zh-CN short article via scheduler + resolver + validator.

    Flow per Unit 7+8 spec:
    1. Schedule the anchor types and url_categories for this article
    2. Resolve each slot's anchor text (config pool → LLM fallback)
    3. Render the short HTML body
    4. Validate; on failure, retry one full pass with a new schedule
    5. After two failures, degrade to 1 main + 1 secondary, all Branded,
       both pointing at the home URL — accept the temporary URL repetition
       in exchange for never failing the row.
    6. Record the resulting link types in the per-site profile (with
       ``degraded=True`` flagged honestly so observability stays accurate).

    Returns ``None`` when the site config doesn't meet the scheduler's
    minimum requirements (no non-home category, or no v2 pool) — caller
    routes to the legacy long-form path.
    """
    main_domain = row["main_domain"].rstrip("/")
    cats_map = config.site_url_categories.get(main_domain, {})
    available_cats = list(cats_map.keys())
    if "home" not in cats_map or not any(c != "home" for c in cats_map):
        return None

    rng = rng or random.Random()
    style_seed = abs(hash(row.get("target_url", main_domain))) % 10_000
    keyword = _extract_zh_keyword(row, main_domain)
    home_url = cats_map["home"]
    topic = row.get("topic")

    last_errors: list[str] = []

    for attempt in range(2):
        profile = anchor_profile.load_profile(main_domain)
        recent = anchor_profile.recent_texts(profile, n=20)
        try:
            decision = anchor_scheduler.schedule(
                profile, config.anchor_proportions, available_cats,
            )
        except InputValidationError:
            # Site genuinely lacks a non-home category — caller falls back.
            return None

        # Resolve main link. Plan 2026-05-18-006 Unit 4 R13: plumb
        # row["language"] through to the language-aware filter dispatch.
        # In v1 this is always "zh-CN" (scheduler ko activation reverted
        # per pass-2 P0); the plumbing is preparatory for the follow-up
        # that ships ko-localized short-form templates.
        main_anchor = anchor_resolver.resolve_anchor(
            url_category="home",
            anchor_type=decision.main_link_anchor_type,
            keyword=keyword,
            target_url=home_url,
            url_subject=topic,
            config=config,
            main_domain=main_domain,
            recent_texts=recent,
            provider=llm_provider,
            rng=rng,
            language=row["language"],
        )
        if main_anchor is None:
            last_errors = ["main_anchor_resolution_failed"]
            continue

        # Resolve each secondary, tracking already-picked anchor texts for dedup.
        running_recent = list(recent) + [main_anchor]
        sec_pairs: list[tuple[str, str]] = []
        sec_records: list[tuple[str, str, str, str]] = []
        for sec in decision.secondary_links:
            sec_url = cats_map.get(sec.url_category)
            if not sec_url:
                last_errors = [f"missing_url_for_category:{sec.url_category}"]
                break
            sec_anchor = anchor_resolver.resolve_anchor(
                url_category=sec.url_category,
                anchor_type=sec.anchor_type,
                keyword=keyword,
                target_url=sec_url,
                url_subject=topic,
                config=config,
                main_domain=main_domain,
                recent_texts=running_recent,
                provider=llm_provider,
                rng=rng,
                language=row["language"],
            )
            if sec_anchor is None:
                last_errors = ["secondary_anchor_resolution_failed"]
                break
            sec_pairs.append((sec_url, sec_anchor))
            sec_records.append((sec.url_category, sec.anchor_type, sec_anchor, sec_url))
            running_recent.append(sec_anchor)

        if len(sec_pairs) != len(decision.secondary_links):
            continue

        html = markdown_utils.render_zh_short_article(
            keyword=keyword,
            main_domain=home_url,
            main_anchor=main_anchor,
            secondary_links=sec_pairs,
            style_seed=style_seed + attempt,
        )
        expected = [main_anchor] + [a for _, a in sec_pairs]
        ok, errors_out = markdown_utils.validate_zh_short_payload(html, expected)
        if ok:
            entries = _build_profile_entries(
                decision, main_anchor, home_url, sec_records, degraded=False,
            )
            anchor_profile.record_article(main_domain, entries)
            return _build_zh_short_payload(
                row, html, main_domain, main_anchor, sec_pairs,
            )
        last_errors = errors_out

    # ── Degrade path ────────────────────────────────────────────────────────
    # Both attempts failed. Produce a 2-link payload using only branded text
    # from the home pool. Two safety nets are layered here:
    #
    # 1. Apply the same recent_texts dedup the normal resolver uses. The
    #    20-entry text-dedup window is the scheduler's defence against
    #    anchor repetition; without re-applying it on the degrade path, a
    #    burst of degrades could surface an anchor that just shipped 2-3
    #    articles ago, breaking the dedup invariant the rest of the
    #    pipeline relies on.
    #
    # 2. If recent-aware filtering empties the pool, fall back to the
    #    raw branded pool (allowing repetition is still better than
    #    failing the row). Last resort is the bare domain label.
    #
    # Then guarantee main_anchor != sec_anchor so the article never
    # publishes with two identical anchors pointing at the home URL —
    # an obvious SEO-spam signal that the validator's set-based check
    # wouldn't catch.
    recent_for_dedup = anchor_profile.recent_texts(
        anchor_profile.load_profile(main_domain), n=20,
    )
    branded_pool = get_anchor_pool_v2(config, main_domain, "home", "branded")
    # Plan 2026-05-18-006 Unit 4 R13: plumb row["language"] to the
    # language-aware filter. v1 always "zh-CN" (scheduler ko activation
    # reverted); preparatory for the follow-up that adds ko templates.
    branded_clean_all = [
        w for w in branded_pool
        if anchor_resolver._passes_filters(w, row["language"])
    ]
    branded_clean = [w for w in branded_clean_all if w not in recent_for_dedup]
    if not branded_clean:
        # Recent-aware filtering exhausted the pool — relax dedup before
        # giving up entirely.
        branded_clean = branded_clean_all or [_domain_label_of(main_domain)]

    main_anchor = rng.choice(branded_clean)
    sec_candidates = [w for w in branded_clean if w != main_anchor]
    if not sec_candidates:
        # Same pool, just relax the recent_texts filter for the secondary
        # slot. Pulling from the unfiltered branded list is preferable to
        # publishing two identical anchors.
        sec_candidates = [w for w in branded_clean_all if w != main_anchor]
    sec_anchor = (
        rng.choice(sec_candidates) if sec_candidates else _domain_label_of(main_domain)
    )
    sec_pairs = [(home_url, sec_anchor)]

    html = markdown_utils.render_zh_short_article(
        keyword=keyword,
        main_domain=home_url,
        main_anchor=main_anchor,
        secondary_links=sec_pairs,
        style_seed=style_seed + 999,
    )

    degrade_decision = ScheduleDecision(
        main_link_anchor_type="branded",
        secondary_links=(
            SecondaryLink(url_category="home", anchor_type="branded"),
        ),
    )

    plan_logger.warn(
        "anchor_resolver_degraded",
        main_domain=main_domain,
        errors=last_errors,
    )

    entries = _build_profile_entries(
        degrade_decision,
        main_anchor,
        home_url,
        [("home", "branded", sec_anchor, home_url)],
        degraded=True,
    )
    anchor_profile.record_article(main_domain, entries)
    return _build_zh_short_payload(row, html, main_domain, main_anchor, sec_pairs)


# ─── Work-themed three-URL dispatcher (Plan 2026-05-13-004 Unit 5a) ─────────


def _plan_work_themed_row(
    row: dict[str, Any],
    three_url_cfg: ThreeUrlConfig,
    *,
    count: int,
) -> Iterator[dict[str, Any]]:
    """Yield one payload per discovered work URL for a work-themed target.

    Three-state failure semantics (matches ``work_scraper`` contract):
    - **fail-abort**: list_url unreachable when ``cfg.work_urls`` is empty
      → :func:`emit_error` exits the whole batch with code 4.
    - **fail-empty**: list_url returns 200 + zero candidates after filtering
      → emit a WARN summary and yield nothing (caller treats as "0 articles").
    - **fail-continue**: a single ``work_url``'s metadata fetch returns ``None``
      or raises a transient error → log WARN + skip that URL.

    Anchor dedup uses the per-site profile: ``recent_texts(profile, n=20)``
    is loaded once at start and grown in-memory after each successful
    rendering so the dedup window updates within a single batch run.
    """
    main_domain = row["main_domain"].rstrip("/")

    # 1. Resolve work_urls — config-pinned first, scraper discovery second.
    work_urls: list[str] = list(three_url_cfg.work_urls)
    if not work_urls:
        try:
            work_urls = work_scraper.fetch_work_urls_from_list(
                three_url_cfg.list_url,
                main_url=three_url_cfg.main_url,
                max_candidates=max(count * 3, 50),  # buffer for fail-continue
                list_path_blocklist=three_url_cfg.list_path_blocklist,
                insecure_tls=three_url_cfg.insecure_tls,
            )
        except ExternalServiceError as exc:
            # fail-abort: kill the batch so the operator sees the broken target
            emit_error(
                f"work-themed list_url unreachable for {main_domain}: {exc}",
                exit_code=4,
            )
            return  # unreachable — emit_error raises SystemExit

    work_urls = work_urls[:count]
    if not work_urls:
        plan_logger.warn(
            "work-themed run: 0 candidate work URLs (fail-empty)",
            main_domain=main_domain,
            list_url=three_url_cfg.list_url,
        )
        return

    # 2. Initialise the per-site dedup window from disk.
    profile = anchor_profile.load_profile(main_domain)
    recent: list[str] = list(anchor_profile.recent_texts(profile, n=20))

    generated = 0
    skipped = 0
    for idx, work_url in enumerate(work_urls):
        try:
            meta = work_scraper.fetch_work_metadata(
                work_url, insecure_tls=three_url_cfg.insecure_tls,
            )
        except InputValidationError as exc:
            # SSRF / non-https — surface but keep the batch alive
            plan_logger.warn(
                "work-themed: invalid work_url, skipping",
                main_domain=main_domain, url=work_url, reason=str(exc),
            )
            skipped += 1
            continue

        if meta is None:
            plan_logger.warn(
                "work-themed: metadata fetch failed (fail-continue), skipping",
                main_domain=main_domain, url=work_url,
            )
            skipped += 1
            continue

        # Stable seed per (main_domain, work_url, idx) so re-runs of the same
        # batch produce byte-identical articles.
        seed = abs(
            int(hashlib.sha256(
                f"{main_domain}:{work_url}:{idx}".encode()
            ).hexdigest()[:8], 16)
        )
        anchors = work_themed_generator.select_anchors(
            three_url_cfg, meta, seed=seed, recent_texts=recent,
        )
        rendered = work_themed_generator.render_work_themed_article(
            three_url_cfg, work_url, anchors, seed=seed,
        )

        anchor_profile.record_article(main_domain, [
            ProfileEntry(
                ts=anchor_profile.now_iso(),
                link_role="main",
                url_category="work_themed",
                anchor_type="work",
                anchor_text=anchors.work_anchor,
                degraded=False,
            )
        ])
        recent.append(anchors.work_anchor)

        yield _build_work_themed_payload(
            row, three_url_cfg, work_url, anchors, rendered,
        )
        generated += 1

    plan_logger.info(
        "work-themed run summary",
        main_domain=main_domain,
        generated=generated,
        skipped=skipped,
    )


def _further_reading_paragraph(
    supporting: list[dict[str, Any]], language: str,
) -> str:
    """Render a natural-prose "Further reading" appendix containing every
    URL in ``supporting`` as a markdown anchor ``[anchor](url)``.

    Returns ``""`` when ``supporting`` is empty. Each language template
    weaves the anchor list into a single short paragraph so the article
    body remains readable rather than degenerating into a link dump.
    Per plan 2026-05-15-003 R3: every URL in ``links[]`` must appear in
    ``content_markdown`` so downstream ``verify_publish`` can confirm
    link presence in the published post body.
    """
    if not supporting:
        return ""
    anchors_md = ", ".join(
        f"[{link['anchor']}]({link['url']})" for link in supporting
    )
    if language == "zh-CN":
        return f"\n\n延伸阅读：{anchors_md}。"
    if language == "ru":
        return f"\n\nДополнительные материалы: {anchors_md}."
    return f"\n\nFurther reading: {anchors_md}."


def _build_work_themed_payload(
    row: dict[str, Any],
    three_url_cfg: ThreeUrlConfig,
    work_url: str,
    anchors: work_themed_generator.Anchors,
    rendered: dict[str, Any],
) -> dict[str, Any]:
    """Wrap the generator output in the same payload schema as zh-short.

    Downstream ``validate-backlinks`` / ``publish-backlinks`` then need no
    branch — they see the same OUTPUT_REQUIRED_FIELDS contract regardless
    of which planner produced the article.

    Per plan 2026-05-15-003 Unit 2 + Unit 3:
    - Normalize ``work_themed_generator``'s emitted kinds (``main_domain`` /
      ``list`` / ``work``) into ``schema.LINK_KINDS`` (``main_domain`` /
      ``category`` / ``target``) before the payload leaves this boundary.
    - Pad ``links`` up to ``_TARGET_PADDED_LINK_COUNT`` (= 7) with
      entries from ``_SUPPORTING_POOL``; append a matching "Further reading"
      paragraph to ``content_markdown`` so every padded URL is also present
      in the body (R3).
    """
    target_url = row["target_url"].rstrip("/")
    platform = row["platform"]
    publish_mode = row.get("publish_mode", "draft")
    language = row["language"]
    url_mode = row.get("url_mode", "A")

    main_domain = three_url_cfg.main_url
    domain_label = _domain_label_of(main_domain)

    custom_title = row.get("custom_title", "")
    title = custom_title or anchors.work_anchor or domain_label
    slug = (
        markdown_utils.slugify(title)
        or hashlib.sha256(title.encode()).hexdigest()[:12]
    )

    custom_tags = row.get("custom_tags", "")
    tags = ["backlink", domain_label]
    if custom_tags:
        tags.extend(t.strip() for t in custom_tags.split(",") if t.strip())

    seed_str = f"{work_url}:{main_domain}:work-themed:{platform}"
    article_id = hashlib.sha256(seed_str.encode()).hexdigest()[:16]

    # Unit 2: remap kinds to schema.LINK_KINDS taxonomy AND ensure the
    # ``required`` field is set on every link (schema.py validator demands
    # it on each record). ``work_themed_generator`` omits ``required``;
    # populate it here based on whether the remapped kind is row-required
    # (main_domain / target → row cannot publish without it).
    links: list[dict[str, Any]] = []
    existing_urls: set[str] = set()
    for raw in rendered["links"]:
        link = dict(raw)
        link["kind"] = _KIND_REMAP_WORK_THEMED.get(link["kind"], link["kind"])
        link["required"] = link["kind"] in _ROW_REQUIRED_KINDS
        links.append(link)
        existing_urls.add(link["url"])

    # Unit 3: pad to _TARGET_PADDED_LINK_COUNT with supporting URLs,
    # then append a "Further reading" paragraph so the body contains every
    # padded URL (R3).
    pad_count = _TARGET_PADDED_LINK_COUNT - len(links)
    added_supporting: list[dict[str, Any]] = []
    if pad_count > 0:
        for surl, sanchor in _SUPPORTING_POOL:
            if len(added_supporting) >= pad_count:
                break
            if surl in existing_urls:
                continue
            sup = {
                "url": surl,
                "anchor": sanchor,
                "kind": "supporting",
                "required": False,
            }
            added_supporting.append(sup)
            links.append(sup)
            existing_urls.add(surl)

    content_markdown = rendered["content_markdown"]
    if added_supporting:
        content_markdown += _further_reading_paragraph(
            added_supporting, language
        )

    excerpt = re.sub(r"<[^>]+>", "", content_markdown)[:100]

    return {
        "id": article_id,
        "platform": platform,
        "language": language,
        "source_language": language,
        "publish_mode": publish_mode,
        "target_url": target_url + "/",
        "main_domain": main_domain,
        "url_mode": url_mode,
        "title": title,
        "slug": slug,
        "excerpt": excerpt,
        "tags": tags,
        "content_markdown": content_markdown,
        "links": links,
        "seo": {
            "title": title,
            "description": excerpt,
            "canonical_url": work_url,
        },
    }


def _dispatch_row(
    row: dict[str, Any],
    config: Config,
    *,
    llm_provider: OpenAICompatibleProvider | None,
    rng: random.Random | None,
    work_count: int,
    fetch_verify_enabled: bool = True,
) -> Iterator[dict[str, Any]]:
    """Three-path dispatch (Plan 2026-05-13-004 Unit 5a).

    Priority: ``[targets."<domain>"]`` three-URL → zh-CN scheduler →
    long-form. Each branch yields its own payload(s); the caller appends.
    The middle branch returns ``None`` when the scheduler refuses (no v2
    pool, missing non-home category) — falls through to long-form.

    ``fetch_verify_enabled`` propagates into the long-form branch's
    ``_generate_payload`` so the content-fetch gate (plan 2026-05-14-007)
    can be bypassed via ``plan-backlinks --no-fetch-verify``. The
    work-themed and zh-CN short branches build their own link sets via
    ``work_themed_generator`` / ``_build_zh_short_payload`` and currently
    do not pass through ``_build_links``; the gate does not apply to those
    branches in this iteration — tracked as deferred follow-up.
    """
    three_url_cfg = get_three_url_config(config, row["main_domain"])
    if three_url_cfg is not None:
        for payload in _plan_work_themed_row(row, three_url_cfg, count=work_count):
            _emit_link_count_recon(payload, branch="work_themed")
            yield payload
        return

    payload: dict[str, Any] | None = None
    if row["language"] == "zh-CN" and _scheduler_enabled_for(
        config, row["main_domain"]
    ):
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


def _emit_link_count_recon(payload: dict[str, Any], *, branch: str) -> None:
    """Emit a RECON event capturing which dispatch branch yielded this
    payload and how many links / which kinds ended up in it.

    Per plan 2026-05-15-003 Unit 4: surfaces link-count regressions in
    cron logs so the next time a branch yields a payload outside the
    schema's 6-8 gate (or with an unexpected kind taxonomy), operators
    see it immediately rather than only at validate-backlinks-reject time.
    Bypasses ``--log-level`` per ``recon-log-level-for-always-on-signals``.
    """
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
        plan_logger.recon(
            "fetch_verify_disabled",
            reason="cli_flag",
        )

    # Mutual exclusion: --from-csv / --from-sitemap are exclusive with --input
    bulk_sources = [args.from_csv, args.from_sitemap]
    if sum(bool(x) for x in bulk_sources) > 1:
        emit_error("--from-csv and --from-sitemap are mutually exclusive", exit_code=2)
    if (args.from_csv or args.from_sitemap) and args.input:
        emit_error("--from-csv / --from-sitemap cannot be combined with --input", exit_code=2)

    plan_logger.info("plan-backlinks started", extra={"mode": "generate"})

    # ── Bulk input paths ──────────────────────────────────────────────────────
    if args.from_csv or args.from_sitemap:
        from ..bulk_input import parse_csv, parse_sitemap, urls_to_seed_rows

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
        # ── Standard JSONL input path ─────────────────────────────────────────
        try:
            rows = list(read_jsonl(args.input))
        except SystemExit as exc:
            raise SystemExit(exc.code)

    plan_logger.info(f"read {len(rows)} seed rows")

    # Load user config so SEO anchor_keywords are available to payload generation.
    # Missing config file returns an empty Config (no error).
    # Malformed TOML is a DependencyError and is surfaced to the operator — a syntax
    # mistake in config.toml should not silently degrade SEO across the whole batch.
    cfg = load_config()

    # Config Echo Chamber (Round-3 #7): emit a 4-line banner so operators
    # see which config was actually resolved + which env vars override it
    # + the SHA of the effective config dict. Same SHA stamped into every
    # payload's metadata below for artifact-to-config reverse lookup.
    config_sha = config_echo.emit_banner(cfg, "plan-backlinks")

    # Build the LLM provider once at startup if config supplies one — the
    # zh-CN scheduler's resolver uses it for typed-pool fallback. None is a
    # valid state (config-pinned pools only).
    llm_provider: OpenAICompatibleProvider | None = None
    if cfg.llm_anchor_provider is not None:
        llm_provider = OpenAICompatibleProvider(
            base_url=cfg.llm_anchor_provider.base_url,
            api_key=cfg.llm_anchor_provider.api_key,
            model=cfg.llm_anchor_provider.model,
            timeout_s=cfg.llm_anchor_provider.timeout_s,
        )

    # Shared RNG so identical input batches stay deterministic across runs.
    # Tests can preempt this by passing their own ``random.Random``.
    rng = random.Random()

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []
    # Silent-Drop Tripwire: track which line each drop happened at, partitioned
    # by which gate ate it. The reconciliation log line lets the operator see
    # "I had 20 input rows but only got 5 payloads — 12 validation, 3 generation,
    # 5 content_gate".
    validation_drops: list[int] = []
    generation_drops: list[int] = []
    content_gate_drops: list[int] = []

    fetch_verify_enabled = not args.no_fetch_verify

    # Plan 008 Unit 1: reset stats so this invocation's content_fetch_stats
    # recon at end-of-run reports only THIS run's counters, not whatever
    # bled in from the importing context (pytest, REPL, etc.).
    content_fetch.reset_stats()

    # Plan 008 Unit 2: cross-row URL prefetch. Walk validated rows, union
    # their candidate URLs with the fixed supporting set, single-shot batch
    # fetch with concurrency 10. Subsequent per-row _build_links calls hit
    # the in-run cache exclusively — turns N sequential row-batches into
    # 1 union batch.
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
                # Snapshot the branded_pool so validate-backlinks can apply
                # the R4 exemption without re-loading config (closes the
                # validate→publish TOCTOU window per plan 2026-05-14-001 R4).
                # validate-backlinks falls back to config-load when this
                # metadata field is absent (older JSONL).
                branded_pool = get_anchor_pool_v2(
                    cfg, payload["main_domain"], "home", "branded"
                )
                metadata = dict(payload.get("metadata") or {})
                metadata["branded_pool"] = list(branded_pool)
                # Stamp the effective config SHA so artifacts (checkpoints,
                # publish logs, oldest in .cache) can be reverse-mapped to
                # the config that produced them — Config Echo Chamber #7.
                metadata["config_sha"] = config_sha
                payload["metadata"] = metadata
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

    # Emit the Silent-Drop Tripwire reconciliation BEFORE the exit guard so
    # failed runs still surface a delta summary. Operator grep target:
    # `RECON plan_reconciliation`.
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

    # Plan 008 Unit 3: emit content-fetch stats snapshot at end-of-run so
    # operators see cache-hit rate / fetch count / reason distribution
    # without scraping per-link log lines. Operator grep target:
    # `RECON content_fetch_stats`.
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