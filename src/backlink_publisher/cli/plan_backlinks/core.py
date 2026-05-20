"""Core payload generation, link building, and CLI entry point."""

from __future__ import annotations

import hashlib
import random
import re
import sys
from typing import Any
from urllib.parse import urlparse

from typing import Iterator

from ... import (
    anchor_profile,
    anchor_resolver,
    anchor_scheduler,
    config_echo,
    content_fetch,
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
from ...schema import (
    validate_input_payload,
)

ARTICLE_LENGTH_WORDS = (100, 200)


class _ContentGateRowFailure(Exception):
    def __init__(self, url: str, reason: str, kind: str) -> None:
        super().__init__(f"row-failing content gate: kind={kind} url={url} reason={reason}")
        self.url = url
        self.reason = reason
        self.kind = kind


_ROW_REQUIRED_KINDS: frozenset[str] = frozenset({"main_domain", "target"})

_SUPPORTING_POOL: tuple[tuple[str, str], ...] = (
    ("https://en.wikipedia.org", "Wikipedia"),
    ("https://developer.mozilla.org", "MDN"),
    ("https://stackoverflow.com", "Stack Overflow"),
    ("https://github.com", "GitHub"),
    ("https://news.ycombinator.com", "Hacker News"),
)

_SUPPORTING_URLS_FOR_PREFETCH: tuple[str, ...] = tuple(
    url for url, _anchor in _SUPPORTING_POOL
)

_TARGET_PADDED_LINK_COUNT: int = 7

_TDK_TITLE_TMPL: dict[str, str] = {
    "zh-CN": "深入了解{tdk}: {domain} 完整指南",
    "ru": "Подробнее о {tdk}: полный гид по {domain}",
    "en": "Deep Dive into {tdk}: The Complete {domain} Guide",
}


def _domain_label_of(main_domain: str) -> str:
    return (
        main_domain.rstrip("/")
        .replace("https://", "")
        .replace("http://", "")
        .replace("www.", "")
    )


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
        "tags": ["\u53cd\u5411\u94fe\u63a5", "\u53c2\u8003", "\u7f51\u7edc\u8d44\u6e90", "{domain_label}", "\u5185\u5bb9\u7b56\u7535"],
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


# ── Link building ──────────────────────────────────────────────────────


def _collect_candidate_urls_for_row(
    row: dict[str, Any], config: Config | None,
) -> list[str]:
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


def _build_links(
    main_domain: str,
    target_url: str,
    url_mode: str,
    extra_urls: list[str] | None = None,
    anchors: list[str] | None = None,
    site_url_categories: dict[str, dict[str, str]] | None = None,
    fetch_verify_enabled: bool = True,
) -> tuple[list[dict[str, Any]], set[str]]:
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
    base = 4
    if target_url != main_domain:
        base += 1
    cats = (site_url_categories or {}).get(main_domain.rstrip("/"), {})
    if url_mode in ("B", "C") and cats.get("category"):
        base += 1
    if url_mode == "C" and cats.get("detail"):
        base += 1
    base += min(extra_url_count, 2)

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
    keywords = get_anchor_keywords(config, main_domain) if config is not None else []
    selected = select_anchor_keywords(keywords, url_mode, 2)
    if selected is None:
        plan_logger.warn(
            f"anchor_keywords missing for {main_domain}, falling back to bare domain label",
            main_domain=main_domain,
        )
        return [fallback_label, fallback_label]
    return selected


# ── Payload generation ──────────────────────────────────────────────────


def _generate_payload(
    row: dict[str, Any],
    config: Config | None = None,
    *,
    fetch_verify_enabled: bool = True,
) -> dict[str, Any]:
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

    domain_label = _domain_label_of(main_domain)

    anchors = _resolve_article_anchors(config, main_domain, url_mode, domain_label)

    tmpl = _TEMPLATES.get(target_language, _TEMPLATES.get(language, _TEMPLATES["en"]))
    title_tmpl = tmpl["title"].get(url_mode, tmpl["title"]["A"])
    topic_val = topic or tmpl.get("topic_fallback", "Resources")

    title = row.get("custom_title", "")
    if not title:
        if tdk_title and url_mode == 'C':
            lang_key = target_language if target_language in _TDK_TITLE_TMPL else "en"
            title = _TDK_TITLE_TMPL[lang_key].format(tdk=tdk_title, domain=domain_label)
        else:
            title = title_tmpl.format(domain=domain_label, topic=topic_val)

    slug = slugify(title)

    if tdk_description and url_mode in ('B', 'C'):
        excerpt = tdk_description[:200]
    else:
        excerpt = tmpl["excerpt"].get(url_mode, tmpl["excerpt"]["A"]).format(
            main_domain=main_domain, domain=domain_label, topic=topic_val,
            anchor=anchors[0],
        )

    tags_raw = tmpl.get("tags", ["backlink"])
    tags = [t.format(domain_label=domain_label) for t in tags_raw]

    if custom_tags:
        custom_tags_list = [t.strip() for t in custom_tags.split(",") if t.strip()]
        tags.extend(custom_tags_list)

    if tdk_keywords:
        kw_list = [k.strip() for k in tdk_keywords.split(",") if k.strip()]
        for kw in kw_list[:3]:
            if kw not in tags:
                tags.append(kw)

    body_tmpl = tmpl["body_paragraphs"].get(url_mode, tmpl["body_paragraphs"]["A"])

    if config and config.llm_anchor_provider and config.llm_anchor_provider.use_article_gen:
        try:
            llm_p = OpenAICompatibleProvider(
                base_url=config.llm_anchor_provider.base_url,
                api_key=config.llm_anchor_provider.api_key,
                model=config.llm_anchor_provider.model,
                temperature=config.llm_anchor_provider.temperature,
                system_prompt=config.llm_anchor_provider.system_prompt,
                article_system_prompt=config.llm_anchor_provider.article_system_prompt,
            )
            body = llm_p.generate_article_body(
                domain_label=domain_label,
                main_domain=main_domain,
                anchors=anchors,
                topic=topic_val,
                language=target_language,
            )
            plan_logger.info(f"LLM article body generated for {main_domain}")
        except Exception as e:
            plan_logger.warn(f"LLM article generation failed, falling back to template: {e}")
            body = body_tmpl(domain=domain_label, main_domain=main_domain, anchors=anchors)
    else:
        body = body_tmpl(domain=domain_label, main_domain=main_domain, anchors=anchors)

    if tdk_title or tdk_description:
        tdk_section = f"\n\n---\n**关于 {domain_label}**\n"
        if tdk_title:
            tdk_section += f"- 标题: {tdk_title}\n"
        if tdk_description:
            tdk_section += f"- 描述: {tdk_description[:150]}...\n"
        body = body + tdk_section

    # Banner image generation moved to `image_gen` adapter + `Config.image_gen`
    # in Plan 2026-05-20-001 Unit 4. The legacy
    # `LLMProviderConfig.{use_image_gen, image_gen_api_key}` branch was
    # retired alongside `frw_image_gen.py` in Unit 2 because the stub
    # endpoint URL (`api.frw.ai`) was never live. The new wiring lands
    # banner artifacts as a separate JSONL field, never as `![](url)` in
    # the body, so older backlinks don't break when an upstream CDN
    # expires the URL.

    if extra_urls:
        extra_intro = f"\n\n除了主要的{domain_label}资源外，我们还整理了以下相关页面供您参考：\n"
        body = body + extra_intro

        for i, ex_url in enumerate(extra_urls[:3]):
            parsed = urlparse(ex_url)
            path = parsed.path

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

        extra_section = "\n## 更多相关资源\n\n"
        for ex_url in extra_urls[:5]:
            parsed = urlparse(ex_url)
            path = parsed.path.split("/")[-1] or parsed.path.split("/")[-2] or "页面"

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

    links, dropped_kinds = _build_links(
        main_domain,
        target_url,
        url_mode,
        extra_urls,
        anchors=anchors,
        site_url_categories=config.site_url_categories if config else None,
        fetch_verify_enabled=fetch_verify_enabled,
    )

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

    content_parts: list[str] = []
    content_parts.append(f"# {title}\n")
    content_parts.append(f"\n{excerpt}\n")
    content_parts.append(f"\n{body}\n")
    content_parts.append("\n## References\n")
    content_parts.append(links_to_markdown(links))
    content_markdown = "\n".join(content_parts)

    seo_title = tmpl.get("seo_title", "{title}").format(title=title)
    seo_desc = tmpl.get("seo_desc", "").format(main_domain=main_domain)

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


# ── Banner image generation seam (Plan 2026-05-20-001 Unit 4) ──────────


def _build_banner_runtime(cfg: Config) -> dict[str, Any] | None:
    """Construct the per-run image-gen state, or return ``None``.

    Returns ``None`` when ``[image_gen]`` is absent, ``use_image_gen``
    is false, or the ``frw-token.json`` file is missing — image-gen
    is fully opt-in and these are graceful skips, not errors.

    On success returns a dict bundling the adapter, auto-disable
    tracker, event store, and a mutable per-run counter so a single
    closure-shaped struct can flow through the per-row loop.
    """
    if cfg.image_gen is None or not cfg.image_gen.use_image_gen:
        return None

    try:
        from backlink_publisher._util.secrets import load_frw_token
        api_key = load_frw_token()
    except RuntimeError as exc:
        plan_logger.warn(
            f"image_gen disabled for this run: {exc}",
        )
        return None

    from backlink_publisher.publishing.adapters.image_gen import ImageGenAdapter
    from backlink_publisher.publishing.adapters.image_gen.caps import (
        AutoDisableTracker,
    )
    from backlink_publisher.events.store import EventStore

    adapter = ImageGenAdapter(
        base_url=cfg.image_gen.base_url,
        model=cfg.image_gen.model,
        banner_size=cfg.image_gen.banner_size,
        api_key=api_key,
        timeout_s=cfg.image_gen.timeout_s,
        max_retries=cfg.image_gen.max_retries,
    )
    tracker = AutoDisableTracker(threshold=cfg.image_gen.auto_disable_threshold)
    store = EventStore()
    return {
        "adapter": adapter,
        "tracker": tracker,
        "store": store,
        "config": cfg.image_gen,
        "run_counter": [0],  # mutable for in-place increment
    }


def _generate_banner_for_payload(
    payload: dict[str, Any],
    *,
    runtime: dict[str, Any],
    llm_provider: "OpenAICompatibleProvider | None",
) -> dict[str, Any]:
    """Generate (or skip) a banner for ``payload``.

    Returns a dict suitable for the JSONL ``banner`` field:

      * ``{path, alt, mime, sha}`` on success
      * ``{path: None, status: "<reason>"}`` on every degraded path

    Body markdown is intentionally NOT touched — per-platform CDN
    upload (Unit 5) happens later and prepends ``![](platform_url)``
    once the platform-hosted URL is known.
    """
    from backlink_publisher.publishing.adapters.image_gen.caps import (
        check_caps,
        record_cap_hit,
        record_invocation,
    )
    from backlink_publisher.publishing.adapters.image_gen.storage import save_banner
    from backlink_publisher._util.errors import ExternalServiceError

    tracker = runtime["tracker"]
    if tracker.disabled:
        return {"path": None, "status": "auto_disabled"}

    decision = check_caps(
        runtime["store"],
        runtime["config"],
        run_counter=runtime["run_counter"][0],
    )
    if not decision.allowed:
        record_cap_hit(runtime["store"], decision.reason or "unknown")
        return {"path": None, "status": f"capped:{decision.reason}"}

    title = payload.get("title", "")
    body = payload.get("content_markdown", "")

    if llm_provider is not None:
        try:
            prompt = llm_provider.generate_image_prompt(title, body)
        except Exception as exc:
            plan_logger.warn(f"image prompt LLM failed, falling back: {exc}")
            prompt = f"Professional article cover for: {title}"
    else:
        prompt = f"Professional article cover for: {title}"

    try:
        artifact = runtime["adapter"].generate(prompt)
    except RuntimeError as exc:
        tracker.record_failure()
        msg = str(exc)
        if "401" in msg or "frw-login" in msg:
            return {"path": None, "status": "auth_failed"}
        return {"path": None, "status": "gen_failed"}
    except ExternalServiceError:
        tracker.record_failure()
        return {"path": None, "status": "gen_failed"}
    except Exception as exc:
        plan_logger.warn(f"image_gen unexpected failure: {exc}")
        tracker.record_failure()
        return {"path": None, "status": "gen_failed"}

    try:
        saved_path = save_banner(artifact)
    except Exception as exc:
        plan_logger.warn(f"banner storage failed: {exc}")
        return {"path": None, "status": "storage_failed"}

    record_invocation(runtime["store"], artifact.prompt_sha)
    runtime["run_counter"][0] += 1
    tracker.record_success()

    return {
        "path": str(saved_path),
        "alt": title,
        "mime": artifact.mime,
        "sha": artifact.prompt_sha,
    }


# ── Dispatch + reconciliation ──────────────────────────────────────────


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
    if three_url_cfg is not None:
        from backlink_publisher.cli.plan_backlinks import _plan_work_themed_row
        for payload in _plan_work_themed_row(row, three_url_cfg, count=work_count):
            _emit_link_count_recon(payload, branch="work_themed")
            yield payload
        return

    payload: dict[str, Any] | None = None
    if row["language"] == "zh-CN" and _scheduler_enabled_for(
        config, row["main_domain"]
    ):
        from backlink_publisher.cli.plan_backlinks import _plan_zh_short_row
        payload = _plan_zh_short_row(row, config, llm_provider, rng=rng)
        if payload is not None:
            _emit_link_count_recon(payload, branch="zh_short")
            yield payload
            return
    if payload is None:
        from backlink_publisher.cli.plan_backlinks import _generate_payload
        payload = _generate_payload(
            row, config=config, fetch_verify_enabled=fetch_verify_enabled,
        )
    _emit_link_count_recon(payload, branch="long_form")
    yield payload


# But _dispatch_row references _scheduler_enabled_for. That's defined in _zh_short.
# We need to import it. Since it's used inside a function, lazy import is fine.

def _scheduler_enabled_for(config: Config, main_domain: str) -> bool:
    from backlink_publisher.cli.plan_backlinks import _scheduler_enabled_for as _inner
    return _inner(config, main_domain)


# ── CLI entry point ────────────────────────────────────────────────────


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

    # Plan 2026-05-20-001 Unit 4 — banner image-gen runtime is
    # opt-in.  None when [image_gen] absent / use_image_gen=false /
    # frw-token.json missing; otherwise a per-run bundle of adapter +
    # tracker + event store + mutable counter.
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
