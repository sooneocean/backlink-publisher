"""URL reachability checker with retry logic for the backlink pipeline."""

from __future__ import annotations

import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .errors import ExternalServiceError
from .logger import opencli_logger

REQUEST_TIMEOUT = 10  # seconds
MAX_CONCURRENT = 10
ACCEPTABLE_CODES = {200, 301, 302}
MAX_RETRIES = 2
RETRY_DELAY = 1  # seconds


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _check_url_once(url: str) -> tuple[bool, str | None]:
    """Single attempt to check a URL. Returns (reachable, error_message)."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False, f"invalid URL: {url}"

    # Try HEAD first
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", "backlink-publisher/0.1 linkcheck")
        resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context())
        code = resp.getcode()
        if code in ACCEPTABLE_CODES:
            return True, None
    except Exception:
        pass

    # Fallback to GET
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "backlink-publisher/0.1 linkcheck")
        resp = urlopen(req, timeout=REQUEST_TIMEOUT, context=_ssl_context())
        code = resp.getcode()
        if code in ACCEPTABLE_CODES:
            return True, None
        return False, f"HTTP {code}"
    except Exception as exc:
        return False, str(exc)


def _check_url_with_retry(url: str) -> tuple[str, bool, str | None]:
    """Check a URL with retry logic. Returns (url, reachable, error_message)."""
    last_error = "unknown error"
    for attempt in range(MAX_RETRIES + 1):
        reachable, error = _check_url_once(url)
        if reachable:
            return url, True, None
        last_error = error or "unknown error"
        if attempt < MAX_RETRIES:
            opencli_logger.debug(
                f"Retry {attempt + 1}/{MAX_RETRIES} for {url}: {last_error}"
            )
            import time
            time.sleep(RETRY_DELAY * (attempt + 1))

    return url, False, last_error


def check_urls(urls: list[str]) -> dict[str, tuple[bool, str | None]]:
    """Check reachability of multiple URLs concurrently with retries.

    Returns a dict mapping URL -> (reachable, error_message).
    """
    results: dict[str, tuple[bool, str | None]] = {}
    deduplicated = list(dict.fromkeys(urls))  # preserve order, deduplicate

    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT, len(deduplicated) or 1)) as pool:
        futures = {pool.submit(_check_url_with_retry, url): url for url in deduplicated}
        for future in as_completed(futures):
            url, reachable, error = future.result()
            results[url] = (reachable, error)
    return results


def check_urls_strict(urls: list[str]) -> None:
    """Check reachability and raise on any unreachable URL.

    Skips obviously invalid URLs (non-HTTP) without failing.
    """
    if not urls:
        return
    # Filter out non-http URLs for checking
    http_urls = [u for u in urls if u.startswith("http://") or u.startswith("https://")]
    if not http_urls:
        return

    results = check_urls(http_urls)
    failures = [(url, err) for url, (ok, err) in results.items() if not ok]
    if failures:
        url, err = failures[0]
        raise ExternalServiceError(f"unreachable URL: {url}" + (f" ({err})" if err else ""))