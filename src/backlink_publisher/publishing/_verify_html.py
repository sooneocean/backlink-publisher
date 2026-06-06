from __future__ import annotations

import re
import ssl
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

__all__ = ["RenderedLinkResult", "verify_rendered_link"]

REQUEST_TIMEOUT = 30
USER_AGENT = "backlink-publisher/0.1 rendered-link-verifier"


@dataclass
class RenderedLinkResult:
    """Result of verifying whether a published page contains a dofollow backlink."""

    effective: bool
    failure_reason: str | None = None


_STRIP_RE = re.compile(r"(?i)^(https?://)?(www\d?\.)?")
_FRAGMENT_RE = re.compile(r"#.*$")


def _normalize_url(url: str) -> str:
    url = url.lower().strip()
    url = _FRAGMENT_RE.sub("", url)
    url = url.rstrip("/")
    url = _STRIP_RE.sub("", url, count=1)
    return url


class _LinkExtractor(HTMLParser):
    """Extract all <a href=... rel=...> elements from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href: str | None = None
        rel: str | None = None
        for name, value in attrs:
            if name == "href" and value is not None:
                href = value.strip()
            elif name == "rel" and value is not None:
                rel = value.strip().lower()
        if href:
            self.links.append({"href": href, "rel": rel or ""})


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


_REL_BLOCKED = frozenset({"nofollow", "ugc", "sponsored"})


def _check_link_attrs(entry: dict[str, str]) -> RenderedLinkResult | None:
    """Check a single link entry. Return None if href doesn't match target."""
    href = entry["href"]
    rel = entry["rel"]
    rel_tokens = set(re.split(r"\s+", rel))
    blocked = rel_tokens & _REL_BLOCKED
    if blocked:
        blocked_str = " ".join(sorted(blocked))
        return RenderedLinkResult(
            effective=False,
            failure_reason=_REL_TO_REASON.get(blocked_str, "nofollow"),
        )
    return RenderedLinkResult(effective=True, failure_reason=None)


_REL_TO_REASON: dict[str, str] = {
    "nofollow": "nofollow",
    "ugc": "ugc",
    "sponsored": "sponsored",
}


def _normalize_href(href: str, base_url: str) -> str:
    """Resolve a potentially relative href against base_url."""
    if href.startswith("http://") or href.startswith("https://"):
        return _normalize_url(href)
    # Strip leading / for path-relative URLs, then join with base domain
    base = _normalize_url(base_url)
    href_clean = href.lstrip("/")
    if not href_clean:
        return base
    return f"{base}/{href_clean}" if not href.startswith("/") else f"{base}/{href_clean}"


def verify_rendered_link(
    published_url: str,
    target_url: str,
    timeout: int = REQUEST_TIMEOUT,
) -> RenderedLinkResult:
    """Fetch the page at ``published_url`` and check it contains a dofollow
    ``<a>`` linking to ``target_url``.

    Returns ``RenderedLinkResult`` with:
    - ``effective=True`` if the target URL is found as a hyperlink without
      ``rel=nofollow/ugc/sponsored``.
    - ``effective=False`` with a ``failure_reason`` describing the problem.
    """
    try:
        req = Request(published_url)
        req.add_header("User-Agent", USER_AGENT)
        resp = urlopen(req, timeout=timeout, context=_ssl_context())
        html_bytes = resp.read()
    except HTTPError as exc:
        return RenderedLinkResult(
            effective=False,
            failure_reason=f"fetch_failed:HTTP {exc.code}",
        )
    except URLError as exc:
        reason = str(exc.reason) if exc.reason else "unknown"
        return RenderedLinkResult(
            effective=False,
            failure_reason=f"fetch_failed:network:{reason}",
        )
    except Exception as exc:
        return RenderedLinkResult(
            effective=False,
            failure_reason=f"fetch_failed:{type(exc).__name__}",
        )

    try:
        html_text = html_bytes.decode("utf-8", errors="replace")
    except Exception as exc:
        return RenderedLinkResult(
            effective=False,
            failure_reason=f"parse_failed:decode:{type(exc).__name__}",
        )

    try:
        extractor = _LinkExtractor()
        extractor.feed(html_text)
    except Exception as exc:
        return RenderedLinkResult(
            effective=False,
            failure_reason=f"parse_failed:html:{type(exc).__name__}",
        )

    if not extractor.links:
        return RenderedLinkResult(
            effective=False,
            failure_reason="link_not_found:no_links_in_page",
        )

    target_norm = _normalize_url(target_url)

    for entry in extractor.links:
        href_norm = _normalize_href(entry["href"], published_url)
        if target_norm in href_norm or href_norm in target_norm:
            result = _check_link_attrs(entry)
            if result is not None:
                return result

    # If we get here, href matched but it was blocked, or no href matched
    # Check for plain-text URL (txt.fyi style — URL mentioned but not as <a>)
    if target_norm in _normalize_url(html_text):
        return RenderedLinkResult(
            effective=False,
            failure_reason="link_not_found:plain_text_only",
        )

    return RenderedLinkResult(
        effective=False,
        failure_reason="link_not_found:target_not_found",
    )
