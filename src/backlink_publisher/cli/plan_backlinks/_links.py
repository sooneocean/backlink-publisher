"""Link building, candidate URL collection, and content-gate logic.

Extracted from ``core.py`` in the Unit 3 monolith decomposition.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from backlink_publisher.content import fetch as content_fetch
from backlink_publisher.config import Config
from backlink_publisher._util.logger import plan_logger


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
    language: str = "en",
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
            if language == "ko":
                if "/page/" in path or "?page=" in ex_url:
                    anchor = "페이지"
                elif "/category/" in path or "/tag/" in path:
                    anchor = "카테고리"
                elif "/archive/" in path:
                    anchor = "아카이브"
                else:
                    anchor = "관련"
            else:
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
            cat_anchor = "카테고리" if language == "ko" else "Categories"
            candidates.append({
                "url": cat_url.rstrip("/"),
                "anchor": cat_anchor,
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
            detail_anchor = "상세 페이지" if language == "ko" else "详情页"
            candidates.append({
                "url": detail_url.rstrip("/"),
                "anchor": detail_anchor,
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

    if language == "ko":
        if same_url:
            return (
                f"\n\n더 많은 리소스는 [{a0}]({main_domain})에서 확인하세요 — "
                f"[{a1}]({main_domain})을 방문하면 전체 컬렉션을 살펴볼 수 있습니다."
            )
        return (
            f"\n\n자세한 내용은 [{a1}]({target_url})에서 확인하고 "
            f"전체 컬렉션은 [{a0}]({main_domain})에서 찾아보세요."
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
