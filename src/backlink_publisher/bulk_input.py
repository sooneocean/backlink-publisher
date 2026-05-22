"""Bulk URL input helpers: CSV parsing, sitemap fetching, seed row construction."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def derive_main_domain(url: str) -> str:
    """Return the scheme + host (e.g. 'https://example.com') from a URL."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return url


def parse_csv(source: str | Path) -> list[str]:
    """Parse URLs from a CSV/text file (one URL per line, blank lines skipped).

    ``source`` may be a file path string, a Path, or '-' / '' to read from stdin.
    Returns a list of URL strings (stripped, non-empty).
    """
    import sys

    if str(source) in ("-", ""):
        lines = sys.stdin.read().splitlines()
    else:
        lines = Path(source).read_text(encoding="utf-8").splitlines()

    urls = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Strip optional CSV quoting or trailing comma (order: comma first, then quotes)
        line = line.rstrip(",").strip().strip('"').strip("'").strip()
        if line:
            urls.append(line)
    return urls


def parse_sitemap(url: str) -> list[str]:
    """Fetch a sitemap URL and extract all <loc> elements.

    Handles both regular sitemaps and sitemap index files (recursively fetches
    up to one level of nested sitemaps). Returns a deduplicated list of URLs.
    """
    from backlink_publisher.http import get as http_get

    seen: set[str] = set()
    result: list[str] = []

    def _fetch_and_parse(sitemap_url: str, depth: int = 0) -> None:
        if depth > 1:
            return
        try:
            resp = http_get(sitemap_url, headers={"User-Agent": "backlink-publisher/1.0"})
            resp.raise_for_status()
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch sitemap {sitemap_url}: {exc}") from exc

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            raise RuntimeError(f"Failed to parse sitemap XML from {sitemap_url}: {exc}") from exc

        ns_uri = _guess_namespace_uri(root)
        ns_map = {"sm": ns_uri} if ns_uri else {}
        prefix = "sm:" if ns_uri else ""

        # Sitemap index: root tag ends with "sitemapindex" and has <sitemap> children
        sitemap_children = root.findall(f"{prefix}sitemap", ns_map)
        if sitemap_children and depth == 0:
            for sitemap_el in sitemap_children:
                loc_el = sitemap_el.find(f"{prefix}loc", ns_map)
                if loc_el is not None and loc_el.text:
                    _fetch_and_parse(loc_el.text.strip(), depth=depth + 1)
            return

        # Regular sitemap: contains <url><loc>...</loc></url>
        for loc_el in root.findall(f".//{prefix}loc", ns_map):
            loc = loc_el.text.strip() if loc_el.text else ""
            if loc and loc not in seen:
                seen.add(loc)
                result.append(loc)

    _fetch_and_parse(url)
    return result


def _guess_namespace_uri(root: ET.Element) -> str:
    """Extract the namespace URI from the root element's Clark-notation tag."""
    tag = root.tag
    if tag.startswith("{"):
        return tag[1 : tag.index("}")]
    return ""


def urls_to_seed_rows(
    urls: list[str],
    platform: str = "blogger",
    language: str = "zh-CN",
    url_mode: str = "A",
    publish_mode: str = "draft",
) -> list[dict[str, Any]]:
    """Convert a list of URLs into seed row dicts ready for plan-backlinks.

    ``main_domain`` is derived from each URL's scheme + host.
    Each URL that doesn't start with http is auto-prefixed with https://.
    """
    rows = []
    for raw_url in urls:
        url = raw_url.strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        main_domain = derive_main_domain(url)
        rows.append({
            "target_url": url,
            "main_domain": main_domain,
            "platform": platform,
            "language": language,
            "url_mode": url_mode,
            "publish_mode": publish_mode,
        })
    return rows
