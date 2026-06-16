"""Unified HTTP session pool for the backlink publisher.

This module provides a shared HTTP connection pool that can be reused across
different parts of the codebase (content fetching, link checking, etc.) to
reduce connection overhead and improve performance.

Usage:
    from backlink_publisher._util.http_session import get_session, close_all_sessions

    # Get a session (creates if doesn't exist)
    session = get_session()
    resp = session.get(url, timeout=10)

    # Close all sessions (cleanup)
    close_all_sessions()
"""

from __future__ import annotations

import ssl
import threading
from typing import Any, Optional
from urllib.error import URLError
from urllib.request import (
    BaseHandler,
    build_opener,
    HTTPCookieProcessor,
    HTTPError,
    HTTPRedirectHandler,
    Request,
)
from http.cookiejar import CookieJar

from backlink_publisher._util.net_safety import _check_url_for_ssrf
from backlink_publisher._util.logger import opencli_logger

# Thread-local storage for sessions
_local = threading.local()

# Global lock for session creation
_session_lock = threading.Lock()

# Shared cookie jar for session persistence
_cookie_jar = CookieJar()

# SSL context (loose, matching linkcheck's default)
_SSL_CTX: ssl.SSLContext = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# User-Agent for identification
USER_AGENT = "backlink-publisher/0.1 http-session"


class _SessionRedirectHandler(HTTPRedirectHandler):
    """Custom redirect handler that respects SSRF constraints."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Optional[Request]:
        """Override to add SSRF checking on redirect targets."""
        # Check SSRF before following redirect
        blocked = _check_url_for_ssrf(newurl)
        if blocked is not None:
            raise URLError(f"ssrf_redirect:blocked_ip:{newurl}")

        # Prevent HTTPS -> HTTP downgrade
        original_scheme = req.get_full_url().split(":")[0]
        new_scheme = newurl.split(":")[0]
        if original_scheme == "https" and new_scheme == "http":
            raise URLError("ssrf_https_downgrade")

        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _build_opener(max_redirects: int = 10) -> Any:
    """Build a urllib opener with custom handlers.

    Args:
        max_redirects: Maximum number of redirects to follow.

    Returns:
        An opener instance with cookie handling and custom redirect logic.
    """
    # Create handlers
    cookie_handler = HTTPCookieProcessor(_cookie_jar)
    redirect_handler = _SessionRedirectHandler()
    setattr(redirect_handler, "max_redirections", max_redirects)

    # Build opener with handlers
    opener = build_opener(cookie_handler, redirect_handler)

    # Add SSL context
    opener.addheaders = [("User-Agent", USER_AGENT)]

    return opener


def get_opener(max_redirects: int = 10) -> Any:
    """Get or create a thread-local opener.

    Args:
        max_redirects: Maximum number of redirects to follow.

    Returns:
        A urllib opener instance.
    """
    # Check thread-local cache
    if not hasattr(_local, "opener"):
        with _session_lock:
            if not hasattr(_local, "opener"):
                _local.opener = _build_opener(max_redirects)
    return _local.opener


def fetch_url(
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    max_redirects: Optional[int] = None,
    headers: Optional[dict[str, str]] = None,
    data: Optional[bytes] = None,
) -> tuple[int, bytes, dict[str, str]]:
    """Fetch a URL using the shared opener.

    Args:
        url: The URL to fetch.
        method: HTTP method (GET, HEAD, POST, etc.).
        timeout: Request timeout in seconds.
        max_redirects: Maximum redirects to follow.
        headers: Additional headers to include.
        data: Request body for POST/PUT methods.

    Returns:
        Tuple of (status_code, response_body, response_headers).

    Raises:
        HTTPError: On HTTP error responses.
        URLError: On network errors or SSRF blocks.
    """
    opener = get_opener(max_redirects) if max_redirects is not None else get_opener()

    # Build request
    req = Request(url, method=method, data=data)
    req.add_header("User-Agent", USER_AGENT)

    # Add custom headers
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    # Execute request
    try:
        resp = opener.open(req, timeout=timeout)
        status = resp.getcode()
        body = resp.read()
        headers_dict = dict(resp.headers)
        resp.close()
        return status, body, headers_dict
    except HTTPError as exc:
        # Re-raise with body available
        body = exc.read() if exc.fp else b""
        raise HTTPError(
            exc.url,
            exc.code,
            exc.msg,
            exc.headers,
            exc.fp,
        ) from exc
    except Exception:
        raise


def close_all_sessions() -> None:
    """Close all thread-local openers and clear cookie jar.

    This is mainly useful for testing or graceful shutdown.
    """
    if hasattr(_local, "opener"):
        del _local.opener
    _cookie_jar.clear()


def reset_cookie_jar() -> None:
    """Clear the shared cookie jar.

    Useful for testing or when credentials need to be refreshed.
    """
    _cookie_jar.clear()


# Convenience function for simple HEAD requests
def head_url(
    url: str,
    timeout: float = 10.0,
    max_redirects: Optional[int] = None,
) -> tuple[int, dict[str, str]]:
    """Perform a HEAD request using the shared opener.

    Args:
        url: The URL to check.
        timeout: Request timeout in seconds.
        max_redirects: Maximum redirects to follow.

    Returns:
        Tuple of (status_code, response_headers).
    """
    status, body, headers = fetch_url(
        url,
        method="HEAD",
        timeout=timeout,
        max_redirects=max_redirects,
    )
    return status, headers


# Convenience function for simple GET requests with streaming
def stream_url(
    url: str,
    timeout: float = 10.0,
    max_redirects: Optional[int] = None,
    chunk_size: int = 8192,
) -> tuple[int, Any, dict[str, str]]:
    """Stream a URL response for memory-efficient large downloads.

    Args:
        url: The URL to fetch.
        timeout: Request timeout in seconds.
        max_redirects: Maximum redirects to follow.
        chunk_size: Size of chunks to read.

    Returns:
        Tuple of (status_code, response_file_object, response_headers).
        The caller is responsible for reading and closing the file object.
    """
    opener = get_opener(max_redirects) if max_redirects is not None else get_opener()

    req = Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)

    try:
        resp = opener.open(req, timeout=timeout)
        return resp.getcode(), resp, dict(resp.headers)
    except Exception:
        raise