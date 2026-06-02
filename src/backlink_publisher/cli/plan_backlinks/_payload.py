"""Payload generation for plan-backlinks CLI.

Extracted from core.py — contains _generate_payload() and _resolve_article_anchors().
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from backlink_publisher.config import Config, get_anchor_keywords
from backlink_publisher._util.errors import InputValidationError
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.markdown import (
    links_to_markdown,
    select_anchor_keywords,
    slugify,
)
from backlink_publisher.publishing import registry
from backlink_publisher.publishing.adapters.llm_anchor_provider import OpenAICompatibleProvider

from ._links import _build_link_density_paragraph, _build_links
from ._templates import _TEMPLATES, _TDK_TITLE_TMPL, _domain_label_of

ARTICLE_LENGTH_WORDS = (100, 200)


def dofollow_tier_metadata(platform: str) -> dict[str, Any]:
    """Map a platform to its dofollow-tier observability metadata.

    Reads the registry (single source of truth — never stores a second
    copy) and returns the marking fields injected into each payload's
    ``metadata`` by the plan-backlinks enrichment loop. Observability
    only (Plan 2026-05-25-001 R2 Phase 1): this does NOT change platform
    allocation or row count.

    Tier vocabulary: ``"dofollow"`` for dofollow platforms, otherwise
    ``"nofollow-signal"`` (covering both ``False`` and ``"uncertain"``).
    ``"uncertain"`` additionally carries ``tier_pending=True`` to flag a
    platform whose live dofollow status has not yet been measured (R4).
    ``referral_value`` ("high"/"low"/None) is surfaced as the
    nofollow-signal sub-grade.
    """
    status = registry.dofollow_status(platform)
    tier = "dofollow" if status is True else "nofollow-signal"
    meta: dict[str, Any] = {
        "dofollow_tier": tier,
        "referral_value": registry.referral_value(platform),
    }
    if status == "uncertain":
        meta["tier_pending"] = True
    return meta


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

    # Plan-time URL Validation: verify target_url health
    if fetch_verify_enabled:
        from backlink_publisher.content.fetch import verify_url_has_content
        ok, reason, _ = verify_url_has_content(target_url)
        if not ok:
            # Plan-time URL Validation: ensure we fail early on unreachable target URLs
            raise InputValidationError(f"Target URL {target_url} is unreachable ({reason}).")
    
    extra_urls = row.get("extra_urls", [])
    custom_tags = row.get("custom_tags", "")
    # system_prompt handled via config.llm_anchor_provider.system_prompt
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

    cover_image_url = None
    cover_image_warning = None

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
        language=language,
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

    from ._citability import apply_long_form_levers
    content_markdown, _citability_levers = apply_long_form_levers(
        content_markdown, domain_label, row, language=target_language
    )

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
        "_citability_levers": _citability_levers,
        "links": links,
        "cover_image_url": cover_image_url,
        "cover_image_warning": cover_image_warning,
        "seo": {
            "title": seo_title,
            "description": seo_desc,
            "canonical_url": target_url,
        },
    }
