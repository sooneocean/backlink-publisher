"""HTTP fetcher for work-themed backlinks — Plan 2026-05-13-004 Unit 2.

Public API:
    fetch_work_metadata(url, *, timeout=10, insecure_tls=False) -> WorkMetadata | None
    fetch_work_urls_from_list(list_url, *, main_url, max_candidates=50,
                              timeout=15, list_path_blocklist=None,
                              insecure_tls=False) -> list[str]

Three-state failure semantics (per plan):
- fail-continue: ``fetch_work_metadata`` returns ``None`` on transient errors
  (network exception, 5xx, parse failure, oversized body) — caller skips the URL.
- fail-abort:    ``fetch_work_urls_from_list`` raises ``ExternalServiceError``
  when neither sitemap nor list-page HTML can be retrieved (exit 4).
- fail-empty:    ``fetch_work_urls_from_list`` returns ``[]`` (with WARN log)
  when responses succeed but produce zero candidates after filtering — the
  caller handles the "0 articles" UX explicitly.

Safety guarantees:
- HTTPS-only URLs (validated via :mod:`url_utils`).
- SSRF block: DNS resolution checked against private/loopback/link-local IPs
  *before* any HTTP call. Resolution mismatch with redirect target is sidestepped
  by disabling redirects.
- Body size cap (``_MAX_RESPONSE_BYTES``): Content-Length pre-flight + streamed
  total. Either trigger an early ``response.close()`` and abort.
- Retries only on ``ConnectionError`` / ``Timeout`` / 429. **5xx is not retried**
  (see ``adapters/retry.py`` rationale: no documented idempotency guarantees).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from backlink_publisher.publishing.adapters.retry import retry_transient_call
from backlink_publisher._util.errors import ExternalServiceError, InputValidationError
from backlink_publisher._util.logger import plan_logger
from ._http import _MAX_RESPONSE_BYTES, _resolve_addresses, _ResponseTooLarge, _RetryableHttp, _safe_get
from backlink_publisher._util.url import (
    absolutize,
    is_same_host,
    strip_fragment_query,
    validate_https_url,
)

__all__ = [
    "WorkMetadata",
    "fetch_work_metadata",
    "fetch_work_urls_from_list",
]

# ── Module config ────────────────────────────────────────────────────────────


_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

_DEFAULT_LIST_PATH_BLOCKLIST: tuple[str, ...] = (
    "/tag/",
    "/category/",
    "/page/",
    "/author/",
    "/about",
    "/contact",
    "/search",
    "/feed",
)

_TITLE_MAX = 200
_DESC_MAX = 500
_H1_MAX = 200


@dataclass(frozen=True)
class WorkMetadata:
    """Metadata extracted from a work page. Any field may be ``None``;
    callers receive ``None`` instead of an instance only when **all three**
    fields are missing (no signal at all)."""

    title: str | None
    description: str | None
    h1: str | None




# ── Decoding helpers ─────────────────────────────────────────────────────────


def _decode(body: bytes, resp: requests.Response) -> str:
    encoding = resp.encoding or resp.apparent_encoding or "utf-8"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


# ── Public: fetch_work_metadata ──────────────────────────────────────────────


def fetch_work_metadata(
    url: str, *, timeout: int = 10, insecure_tls: bool = False
) -> WorkMetadata | None:
    """Fetch ``<title>`` / ``<meta name=description>`` / first ``<h1>`` from
    a work URL.

    Fail-continue: network errors, 5xx, oversized bodies, and parse failures
    all return ``None`` with a WARN log so the batch can continue.

    ``InputValidationError`` (SSRF block, non-https URL) propagates — these
    are configuration / contract violations and the caller should surface them.
    """
    try:
        resp, body = _safe_get(url, timeout=timeout, insecure_tls=insecure_tls)
    except InputValidationError:
        raise
    except _ResponseTooLarge as exc:
        plan_logger.warn(
            "work_metadata response too large", url=url, reason=str(exc)
        )
        return None
    except Exception as exc:  # noqa: BLE001 — fail-continue per plan
        plan_logger.warn(
            "work_metadata fetch failed", url=url, error=type(exc).__name__
        )
        return None

    if resp.status_code != 200:
        plan_logger.warn(
            "work_metadata non-200", url=url, status=resp.status_code
        )
        return None

    try:
        soup = BeautifulSoup(_decode(body, resp), "html.parser")
    except Exception as exc:  # noqa: BLE001 — fail-continue per plan
        plan_logger.warn(
            "work_metadata parse failed", url=url, error=type(exc).__name__
        )
        return None

    title = _extract_title(soup)
    description = _extract_description(soup)
    h1 = _extract_h1(soup)

    if not title and not description and not h1:
        return None

    return WorkMetadata(title=title, description=description, h1=h1)


def _extract_title(soup: BeautifulSoup) -> str | None:
    tag = soup.find("title")
    if not tag or not tag.text:
        return None
    cleaned = tag.text.strip()
    return _truncate(cleaned, _TITLE_MAX) or None


def _extract_description(soup: BeautifulSoup) -> str | None:
    tag = soup.find("meta", attrs={"name": "description"})
    if not tag:
        return None
    content = tag.get("content", "")
    cleaned = (content or "").strip()
    return _truncate(cleaned, _DESC_MAX) or None


def _extract_h1(soup: BeautifulSoup) -> str | None:
    tag = soup.find("h1")
    if not tag or not tag.text:
        return None
    cleaned = tag.text.strip()
    return _truncate(cleaned, _H1_MAX) or None


# ── Public: fetch_work_urls_from_list ────────────────────────────────────────


def fetch_work_urls_from_list(
    list_url: str,
    *,
    main_url: str,
    max_candidates: int = 50,
    timeout: int = 15,
    list_path_blocklist: list[str] | None = None,
    insecure_tls: bool = False,
) -> list[str]:
    """Discover candidate work URLs from a list page.

    Strategy: try ``/sitemap.xml`` first, then ``/sitemap_index.xml`` (recurse
    one level on sitemap-index), then fall back to scraping ``<a href>``
    elements from the list page HTML.

    Filters: same host as ``list_url``, drops the main-domain root and the
    list page itself, applies ``list_path_blocklist`` (default excludes
    ``/tag/`` ``/category/`` ``/page/`` ``/author/`` ``/about`` ``/contact``
    ``/search`` ``/feed``; pre-skips ``#fragment`` and ``mailto:`` links),
    de-duplicates, then truncates to ``max_candidates``.

    Three-state semantics (see module docstring).
    """
    blocklist = (
        tuple(list_path_blocklist)
        if list_path_blocklist is not None
        else _DEFAULT_LIST_PATH_BLOCKLIST
    )

    parsed = urlparse(list_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise InputValidationError(f"invalid list_url: {list_url!r}")
    host_root = f"{parsed.scheme}://{parsed.netloc}"

    # Step 1: try the two well-known sitemap locations.
    sitemap_urls: list[str] | None = None
    for sitemap_path in ("/sitemap.xml", "/sitemap_index.xml"):
        sm_url = host_root + sitemap_path
        try:
            sitemap_urls = _try_sitemap(
                sm_url,
                timeout=timeout,
                insecure_tls=insecure_tls,
            )
        except InputValidationError:
            raise
        except Exception:  # noqa: BLE001 — fall through to next variant
            continue
        if sitemap_urls is not None:
            break

    if sitemap_urls:
        filtered = _filter_urls(
            sitemap_urls,
            main_url=main_url,
            list_url=list_url,
            blocklist=blocklist,
        )
        if not filtered:
            plan_logger.warn(
                "sitemap returned 0 work candidates after filtering",
                list_url=list_url,
            )
            return []
        return filtered[:max_candidates]

    # Step 2: HTML fallback.
    try:
        resp, body = _safe_get(
            list_url, timeout=timeout, insecure_tls=insecure_tls
        )
    except InputValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-abort per plan
        raise ExternalServiceError(
            f"list_url fetch failed: {type(exc).__name__}: {exc}"
        )

    if resp.status_code >= 500:
        raise ExternalServiceError(
            f"list_url returned {resp.status_code}: {list_url}"
        )

    if resp.status_code != 200:
        plan_logger.warn(
            "list_url non-200", url=list_url, status=resp.status_code
        )
        return []

    try:
        soup = BeautifulSoup(_decode(body, resp), "html.parser")
    except Exception as exc:  # noqa: BLE001 — fail-abort: list-page is critical
        raise ExternalServiceError(
            f"list_url parse failed: {type(exc).__name__}: {exc}"
        )

    candidates: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = (anchor["href"] or "").strip()
        if not href or href.startswith("#") or href.lower().startswith("mailto:"):
            continue
        candidates.append(absolutize(list_url, href))

    filtered = _filter_urls(
        candidates,
        main_url=main_url,
        list_url=list_url,
        blocklist=blocklist,
    )

    if not filtered:
        plan_logger.warn(
            "list_url returned 0 work candidates after filtering",
            url=list_url,
        )
        return []

    return filtered[:max_candidates]


def _try_sitemap(
    sitemap_url: str, *, timeout: int, insecure_tls: bool
) -> list[str] | None:
    """Fetch + parse a sitemap. Returns ``<loc>`` URLs (empty list if the
    sitemap is well-formed but empty), or ``None`` when the sitemap is 404.

    Recurses one level when the root element is ``<sitemapindex>``. A failing
    sub-sitemap is skipped (one bad shard shouldn't kill the whole batch).
    """
    resp, body = _safe_get(sitemap_url, timeout=timeout, insecure_tls=insecure_tls)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise ExternalServiceError(
            f"sitemap fetch returned {resp.status_code}: {sitemap_url}"
        )

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ExternalServiceError(f"sitemap parse failed: {exc}")

    locs: list[str] = []
    if _local_tag(root.tag) == "urlset":
        for loc in root.findall(f"{_SITEMAP_NS}url/{_SITEMAP_NS}loc"):
            if loc.text:
                locs.append(loc.text.strip())
    elif _local_tag(root.tag) == "sitemapindex":
        sub_sitemaps = [
            loc.text.strip()
            for loc in root.findall(f"{_SITEMAP_NS}sitemap/{_SITEMAP_NS}loc")
            if loc.text
        ]
        for sub in sub_sitemaps:
            try:
                sub_resp, sub_body = _safe_get(
                    sub, timeout=timeout, insecure_tls=insecure_tls
                )
                if sub_resp.status_code != 200:
                    continue
                sub_root = ET.fromstring(sub_body)
                if _local_tag(sub_root.tag) != "urlset":
                    continue  # we only recurse one level
                for loc in sub_root.findall(
                    f"{_SITEMAP_NS}url/{_SITEMAP_NS}loc"
                ):
                    if loc.text:
                        locs.append(loc.text.strip())
            except (ExternalServiceError, InputValidationError):
                raise
            except Exception:  # noqa: BLE001 — skip individual bad shard
                continue
    return locs


def _local_tag(tag: str) -> str:
    """Strip XML namespace from a tag name (``{ns}foo`` → ``foo``)."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _filter_urls(
    urls: list[str],
    *,
    main_url: str,
    list_url: str,
    blocklist: tuple[str, ...],
) -> list[str]:
    """Apply same-host + main/list-root exclusion + blocklist + dedup."""
    main_root_canon = strip_fragment_query(main_url).rstrip("/")
    list_canon = strip_fragment_query(list_url).rstrip("/")
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not url:
            continue
        cleaned = strip_fragment_query(url)
        if not is_same_host(cleaned, list_url):
            continue
        if cleaned.rstrip("/") in (main_root_canon, list_canon):
            continue
        path = urlparse(cleaned).path or "/"
        if any(path.startswith(b) for b in blocklist):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out
