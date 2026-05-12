"""Generate backlink article payloads from seed URLs."""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any
from urllib.parse import urlparse

from .. import errors
from ..errors import InputValidationError, emit_error
from ..jsonl import read_jsonl, write_jsonl
from ..language_check import detect_language
from ..logger import plan_logger
from ..markdown_utils import (
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
    slugify,
)
from ..schema import (
    INPUT_SCHEMA_FIELDS,
    SUPPORTED_LANGUAGES,
    URL_MODES,
    validate_input_payload,
)

ARTICLE_LENGTH_WORDS = (100, 200)

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
            "A": "This article explores the resources and value offered by [{domain}]({main_domain}), "
                  "providing context and curated links for readers.",
            "B": "A curated overview of [{domain}]({main_domain})'s sections and key pages, "
                  "helping you navigate the site effectively.",
            "C": "A detailed look at {topic} as covered by [{domain}]({main_domain}), with "
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
            "A": "\u672c\u6587\u63a2\u8ba8[{domain}]({main_domain})\u63d0\u4f9b\u7684\u8d44\u6e90\u548c\u4ef7\u503c\uff0c\u4e3a\u8bfb\u8005\u63d0\u4f9b\u80cc\u666f\u548c\u7cbe\u9009\u94fe\u63a5\u3002",
            "B": "\u5bf9[{domain}]({main_domain})\u5404\u677f\u5757\u548c\u5173\u952e\u9875\u9762\u7684\u7cbe\u9009\u6982\u89c8\uff0c\u5e2e\u52a9\u60a8\u9ad8\u6548\u6d4f\u89c8\u8be5\u7f51\u7ad9\u3002",
            "C": "\u8be6\u7ec6\u89e3\u8bfb[{domain}]({main_domain})\u4e0a\u7684{topic}\u5185\u5bb9\uff0c\u5e76\u63d0\u4f9b\u5ef6\u4f38\u53c2\u8003\u8d44\u6599\u3002",
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
            "A": "\u042d\u0442\u0430 \u0441\u0442\u0430\u0442\u044c\u044f \u0438\u0441\u0441\u043b\u0435\u0434\u0443\u0435\u0442 \u0440\u0435\u0441\u0443\u0440\u0441\u044b \u0438 \u0446\u0435\u043d\u043d\u043e\u0441\u0442\u044c [{domain}]({main_domain}), "
                  "\u043f\u0440\u0435\u0434\u043e\u0441\u0442\u0430\u0432\u043b\u044f\u044f \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442 \u0438 \u043a\u0443\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043d\u044b\u0435 \u0441\u0441\u044b\u043b\u043a\u0438 \u0434\u043b\u044f \u0447\u0438\u0442\u0430\u0442\u0435\u043b\u0435\u0439.",
            "B": "\u041f\u043e\u0434\u0431\u043e\u0440 \u0440\u0430\u0437\u0434\u0435\u043b\u043e\u0432 \u0438 \u043a\u043b\u044e\u0447\u0435\u0432\u044b\u0445 \u0441\u0442\u0440\u0430\u043d\u0438\u0446 [{domain}]({main_domain}), "
                  "\u043a\u043e\u0442\u043e\u0440\u044b\u0439 \u043f\u043e\u043c\u043e\u0436\u0435\u0442 \u0432\u0430\u043c \u044d\u0444\u0444\u0435\u043a\u0442\u0438\u0432\u043d\u043e \u043e\u0440\u0438\u0435\u043d\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c\u0441\u044f \u043d\u0430 \u0441\u0430\u0439\u0442\u0435.",
            "C": "\u041f\u043e\u0434\u0440\u043e\u0431\u043d\u044b\u0439 \u0430\u043d\u0430\u043b\u0438\u0437 \u0442\u0435\u043c\u044b {topic} \u043d\u0430 [{domain}]({main_domain}) "
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
) -> list[dict[str, Any]]:
    """Construct the list of links for the article (target: 6-8 links)."""
    links: list[dict[str, Any]] = []

    # 1. Main domain link (always present) - 1 link
    domain_label = main_domain.rstrip("/").replace("https://", "").replace("http://", "")
    links.append({
        "url": main_domain.rstrip("/"),
        "anchor": domain_label,
        "kind": "main_domain",
        "required": True,
    })

    # 2. Target URL link - 1 link
    if target_url != main_domain:
        links.append({
            "url": target_url,
            "anchor": target_url.rstrip("/").replace("https://", "").replace("http://", ""),
            "kind": "target",
            "required": True,
        })

    # 3. Add extra URLs first (up to 2)
    if extra_urls:
        for i, ex_url in enumerate(extra_urls[:2]):
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
            
            links.append({
                "url": ex_url.rstrip("/"),
                "anchor": anchor,
                "kind": "extra",
                "required": False,
            })

    # 4. Mode-specific links - B adds 1, C adds 2
    if url_mode == "B":
        cat_url = main_domain.rstrip("/") + "/categories"
        links.append({
            "url": cat_url,
            "anchor": "Categories",
            "kind": "category",
            "required": True,
        })
    elif url_mode == "C":
        cat_url = main_domain.rstrip("/") + "/categories"
        links.append({
            "url": cat_url,
            "anchor": "Categories",
            "kind": "category",
            "required": True,
        })
        detail_url = main_domain.rstrip("/") + "/detail"
        links.append({
            "url": detail_url,
            "anchor": "详情页",
            "kind": "detail",
            "required": True,
        })

    # 5. Pad with supporting links to reach 6-8
    target_min = 6
    target_max = 8
    
    supporting = [
        ("https://en.wikipedia.org", "Wikipedia"),
        ("https://developer.mozilla.org", "MDN"),
        ("https://stackoverflow.com", "Stack Overflow"),
        ("https://github.com", "GitHub"),
        ("https://news.ycombinator.com", "Hacker News"),
    ]
    
    for surl, sanchor in supporting:
        if len(links) >= target_max:
            break
        links.append({
            "url": surl,
            "anchor": sanchor,
            "kind": "supporting",
            "required": False,
        })
    
    # If still below minimum, add more from supporting
    if len(links) < target_min:
        for surl, sanchor in supporting:
            if len(links) >= target_min:
                break
            if any(l["url"] == surl for l in links):
                continue
            links.append({
                "url": surl,
                "anchor": sanchor,
                "kind": "supporting",
                "required": False,
            })

    return links


def _build_link_density_paragraph(
    domain: str,
    main_domain: str,
    target_url: str,
    language: str,
    url_mode: str,
    extra_url_count: int,
) -> str:
    """Return a short paragraph that adds missing target-site links to reach A+B+C ≥ 6.

    Computes the expected link count after body/excerpt/references are assembled,
    and only produces content when the count would be below 6.
    Mode B (categories URL) and C (categories+detail) already reach 6-7 and are skipped.
    """
    # Base count: excerpt(1) + body_template(2) + references_main(1) = 4
    base = 4
    if target_url != main_domain:
        base += 1   # references_target entry
    if url_mode == "B":
        base += 1   # /categories URL
    elif url_mode == "C":
        base += 2   # /categories + /detail URLs
    base += min(extra_url_count, 2)  # up to 2 extra_urls in references

    if base >= 6:
        return ""

    same_url = (target_url == main_domain)

    if language == "zh-CN":
        if same_url:
            return (
                f"\n\n欲了解更多资源，请访问[{domain}]({main_domain})，"
                f"探索[{domain}]({main_domain})为您精心准备的丰富内容。"
            )
        return (
            f"\n\n阅读更多请访问[{domain}]({target_url})，"
            f"并前往[{domain}]({main_domain})获取完整内容。"
        )

    if language == "ru":
        if same_url:
            return (
                f"\n\nБольше материалов доступно на [{domain}]({main_domain}) — "
                f"посетите [{domain}]({main_domain}) для просмотра полного каталога."
            )
        return (
            f"\n\nЧитайте подробнее на [{domain}]({target_url}) и "
            f"посетите [{domain}]({main_domain}) для обзора всех материалов."
        )

    # English (default)
    if same_url:
        return (
            f"\n\nFor more resources, visit [{domain}]({main_domain}) and explore "
            f"the wide range of content available at [{domain}]({main_domain})."
        )
    return (
        f"\n\nRead more at [{domain}]({target_url}) and visit the main hub "
        f"[{domain}]({main_domain}) for the full collection."
    )


def _generate_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Generate a single backlink article payload from a seed row."""
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

    tmpl = _TEMPLATES.get(target_language, _TEMPLATES.get(language, _TEMPLATES["en"]))
    title_tmpl = tmpl["title"].get(url_mode, tmpl["title"]["A"])
    topic_val = topic or tmpl.get("topic_fallback", "Resources")

    # Use TDK title if available, otherwise use custom or auto-generated
    title = row.get("custom_title", "")
    if not title:
        if tdk_title and url_mode == 'C':
            title = f"深入了解{tdk_title}: {domain_label} 完整指南"
        else:
            title = title_tmpl.format(domain=domain_label, topic=topic_val)
    
    slug = slugify(title)
    
    # Use TDK description for excerpt if available
    if tdk_description and url_mode in ('B', 'C'):
        excerpt = tdk_description[:200]
    else:
        excerpt = tmpl["excerpt"].get(url_mode, tmpl["excerpt"]["A"]).format(
            main_domain=main_domain, domain=domain_label, topic=topic_val
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
    body = body_tmpl(domain=domain_label, main_domain=main_domain)
    
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

    # Inject density paragraph if target-site link count would be < 6
    density_para = _build_link_density_paragraph(
        domain=domain_label,
        main_domain=main_domain,
        target_url=target_url,
        language=language,
        url_mode=url_mode,
        extra_url_count=len(extra_urls) if extra_urls else 0,
    )
    if density_para:
        body = body + density_para

    links = _build_links(main_domain, target_url, url_mode, extra_urls)

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
        "--log-level",
        default="WARN",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Log verbosity (default: WARN)",
    )
    args = parser.parse_args(argv)

    from ..logger import set_log_level
    set_log_level(args.log_level)

    plan_logger.info("plan-backlinks started", extra={"mode": "generate"})

    try:
        rows = list(read_jsonl(args.input))
    except SystemExit as exc:
        raise SystemExit(exc.code)

    plan_logger.info(f"read {len(rows)} seed rows")

    outputs: list[dict[str, Any]] = []
    all_errors: list[str] = []

    for line_num, row in enumerate(rows, start=1):
        errs = validate_input_payload(row, line_num)
        if errs:
            all_errors.extend(errs)
            continue
        try:
            payload = _generate_payload(row)
            plan_logger.debug(
                f"generated payload: id={payload['id']} platform={payload['platform']}",
                extra={"id": payload["id"], "platform": payload["platform"]},
            )
            outputs.append(payload)
        except Exception as exc:
            all_errors.append(f"line {line_num}: generation error: {exc}")

    if all_errors:
        for err in all_errors:
            print(err, file=sys.stderr)
        plan_logger.error(f"generation failed: {len(all_errors)} errors")
        raise SystemExit(2)

    plan_logger.info(f"generated {len(outputs)} payloads")
    write_jsonl(outputs)