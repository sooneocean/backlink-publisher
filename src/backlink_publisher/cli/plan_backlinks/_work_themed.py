"""Work-themed three-URL dispatcher for the backlink pipeline."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.anchor import profile as anchor_profile
from backlink_publisher._util import markdown as markdown_utils
from backlink_publisher.content import scraper as work_scraper, themed_gen as work_themed_generator
from backlink_publisher.config import ThreeUrlConfig
from backlink_publisher._util.errors import ExternalServiceError, InputValidationError, emit_error
from backlink_publisher._util.logger import plan_logger

from .core import (
    _domain_label_of,
    _SUPPORTING_POOL,
    _TARGET_PADDED_LINK_COUNT,
    _ROW_REQUIRED_KINDS,
)


_KIND_REMAP_WORK_THEMED: dict[str, str] = {
    "main_domain": "main_domain",
    "list": "category",
    "work": "target",
}


def _further_reading_paragraph(
    supporting: list[dict[str, Any]], language: str,
) -> str:
    if not supporting:
        return ""
    anchors_md = ", ".join(
        f"[{link['anchor']}]({link['url']})" for link in supporting
    )
    if language == "zh-CN":
        return f"\n\n延伸阅读：{anchors_md}。"
    if language == "ko":
        return f"\n\n추가 읽기: {anchors_md}."
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

    links: list[dict[str, Any]] = []
    existing_urls: set[str] = set()
    for raw in rendered["links"]:
        link = dict(raw)
        link["kind"] = _KIND_REMAP_WORK_THEMED.get(link["kind"], link["kind"])
        link["required"] = link["kind"] in _ROW_REQUIRED_KINDS
        links.append(link)
        existing_urls.add(link["url"])

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


def _plan_work_themed_row(
    row: dict[str, Any],
    three_url_cfg: ThreeUrlConfig,
    *,
    count: int,
) -> Any:
    from typing import Iterator

    main_domain = row["main_domain"].rstrip("/")

    work_urls: list[str] = list(three_url_cfg.work_urls)
    if not work_urls:
        try:
            work_urls = work_scraper.fetch_work_urls_from_list(
                three_url_cfg.list_url,
                main_url=three_url_cfg.main_url,
                max_candidates=max(count * 3, 50),
                list_path_blocklist=three_url_cfg.list_path_blocklist,
                insecure_tls=three_url_cfg.insecure_tls,
            )
        except ExternalServiceError as exc:
            emit_error(
                f"work-themed list_url unreachable for {main_domain}: {exc}",
                exit_code=4,
            )
            return

    work_urls = work_urls[:count]
    if not work_urls:
        plan_logger.warn(
            "work-themed run: 0 candidate work URLs (fail-empty)",
            main_domain=main_domain,
            list_url=three_url_cfg.list_url,
        )
        return

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
