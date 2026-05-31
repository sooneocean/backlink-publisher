"""Destination-page preflight fetch routine.

Plan: ``docs/plans/2026-05-26-008-feat-preflight-targets-verb-plan.md`` (Unit 1).

The ``preflight-targets`` verb needs richer facts about a destination page than
``content.fetch._check_once`` can provide — its ``(ok, reason, title)`` tuple
carries no HTTP status, final URL, response headers, or ``<h1>``. Widening that
tuple would break the 38+ tests pinning its shape, so this is a *separate*
routine that reuses the same SSRF-guarded urllib opener (``_util.net_safety``)
plus the existing ``extract_title`` / ``is_soft_404_title`` helpers, and adds:

- HTTP status, final URL (after redirects), and host-diff detection
- ``noindex`` via ``<meta name=robots>`` AND the ``X-Robots-Tag`` header
- ``<h1>`` presence via a local byte-regex (NOT ``scraper._extract_h1``, which
  takes a parsed ``BeautifulSoup`` object — incompatible with the byte prefix)
- explicit ``tls_unverified`` / ``ssrf_blocked`` / ``redirect_capped`` facts

Security hardening over ``_check_once`` (which this routine deliberately does
NOT inherit): an explicit http/https scheme gate (``_check_once`` never calls
``_is_valid_http_url``), a pinned redirect cap of 5, and a post-redirect SSRF
re-check of the final URL to narrow the DNS-rebinding window. TLS is already
verifying because ``_make_ssrf_opener`` builds the opener with no custom
context — the work is *classifying* an SSL failure, not changing the context.

The routine NEVER raises: every failure is recorded as a ``reason`` so the verb
can stay exit-0. It returns facts only; the verb applies the verdict ladder.
"""

from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request

from backlink_publisher._util.url import normalize_url_for_fetch, safe_hostname, safe_urlparse
from backlink_publisher._util.net_safety import _check_url_for_ssrf, _make_ssrf_opener
from ._html_utils import extract_title
from ._soft404 import is_soft_404_title

#: Per-attempt wall-clock budget (matches ``content.fetch.FETCH_TIMEOUT``).
FETCH_TIMEOUT: int = 10

#: Redirect cap. 5 (not 3) survives the common legitimate canonicalization
#: chain apex -> https -> www -> trailing-slash (4 hops); 3 would false-flag it.
#: A chain exceeding this surfaces as ``redirect_capped`` rather than collapsing
#: into a generic non-200 verdict.
_MAX_REDIRECTS: int = 5

#: Body-prefix ceiling. Larger than ``HEAD_SCAN_BYTES`` (256 KB) because ``<h1>``
#: lives in ``<body>`` past ``</head>``; we stream and stop at ``</h1>`` or here.
PREFLIGHT_BODY_BYTES: int = 768_000

#: Identifies this fetcher distinctly so targets can rate-limit it separately.
USER_AGENT: str = "backlink-publisher/0.1 preflight-targets"

#: Truncate the stored ``X-Robots-Tag`` value — it is untrusted, display-only.
_X_ROBOTS_MAX_LEN: int = 256

#: Module-level opener so tests can patch ``_PREFLIGHT_OPENER.open``. Pinned to
#: the preflight redirect cap; the SSRF redirect handler re-checks every hop.
_PREFLIGHT_OPENER = _make_ssrf_opener(_MAX_REDIRECTS)

_META_TAG_RE = re.compile(rb"<meta\b[^>]*>", re.IGNORECASE)
_META_NAME_RE = re.compile(rb"""name\s*=\s*["']?([a-zA-Z-]+)""", re.IGNORECASE)
_META_CONTENT_RE = re.compile(rb"""content\s*=\s*["']?([^"'>]*)""", re.IGNORECASE)
_H1_RE = re.compile(rb"<h1\b[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_H1_SENTINEL = b"</h1>"
#: Split robots directives on commas / whitespace / colons (the last covers
#: ``X-Robots-Tag: googlebot: noindex``) so ``noindex`` matches as a token, not
#: a substring (guards against ``noindexing`` false positives).
_DIRECTIVE_SPLIT = re.compile(r"[,\s:]+")


@dataclass(frozen=True)
class PreflightFacts:
    """Raw facts about a fetched destination page. The verb maps these to a
    verdict via the R5b precedence ladder; this routine never decides a verdict.
    ``reason`` is ``None`` on a clean 200 fetch, else a stable taxonomy string.
    """

    status: Optional[int] = None
    final_url: Optional[str] = None
    redirected: bool = False
    host_diff: bool = False
    redirect_capped: bool = False
    noindex: bool = False
    soft404: bool = False
    has_title: bool = False
    has_h1: bool = False
    tls_unverified: bool = False
    reason: Optional[str] = None
    x_robots_tag: Optional[str] = None


def _is_http_url(url: str) -> bool:
    """http/https scheme + non-empty netloc. Run before any network attempt —
    ``_check_once`` skips this, so a reused prelude would have NO scheme gate
    and could hand ``file://`` / ``ftp://`` to ``urlopen``.

    Built on the shared :func:`safe_urlparse` (Plan 2026-05-27-006), which folds
    a malformed-IPv6 ``ValueError`` and non-``str`` input into a ``None`` parse —
    treat any parse failure as "not a valid http url".
    """
    parsed = safe_urlparse(url)
    return parsed is not None and parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _read_body_prefix(resp: Any, max_bytes: int) -> bytes:
    """Stream ``resp`` until ``</h1>``, EOF, or ``max_bytes`` — whichever first.

    Mirrors ``_html_utils.read_html_head_window``'s streaming-cap discipline but
    stops at ``</h1>`` (in ``<body>``) instead of ``</head>``, so the first
    ``<h1>`` is captured. Counts wire bytes; no ``Accept-Encoding: gzip`` is
    sent, so a compression bomb cannot amplify past the cap.
    """
    buf = bytearray()
    chunk_size = 16_384
    probe_window = 32_768
    while len(buf) < max_bytes:
        remaining = max_bytes - len(buf)
        chunk = resp.read(min(chunk_size, remaining))
        if not chunk:
            break
        buf.extend(chunk)
        tail_start = max(0, len(buf) - probe_window)
        if _H1_SENTINEL in bytes(buf[tail_start:]).lower():
            break
    return bytes(buf)


def _has_h1(body: bytes) -> bool:
    m = _H1_RE.search(body)
    return bool(m and m.group(1).strip())


def _has_noindex_directive(text: str) -> bool:
    """True if ``text`` contains ``noindex`` as a distinct directive token, not
    a substring. Tokenizes on commas / whitespace / colons so directive lists
    (``"all, noindex"``) and bot-prefixed headers (``"googlebot: noindex"``)
    match, while ``"noindexing"`` does NOT (false-positive guard).
    """
    return "noindex" in _DIRECTIVE_SPLIT.split(text.lower())


def _meta_noindex(body: bytes) -> bool:
    """True if any ``<meta name=robots|googlebot content=...noindex...>`` is
    present, tolerating either attribute order.
    """
    for tag in _META_TAG_RE.findall(body):
        name_m = _META_NAME_RE.search(tag)
        content_m = _META_CONTENT_RE.search(tag)
        if not name_m or not content_m:
            continue
        if name_m.group(1).lower() in (b"robots", b"googlebot") and _has_noindex_directive(
            content_m.group(1).decode("ascii", "ignore")
        ):
            return True
    return False


def _x_robots_value(headers: Any) -> Optional[str]:
    """Join any ``X-Robots-Tag`` header values (case-insensitive lookup via the
    ``email.message.Message`` container), truncated to a fixed length.
    """
    if headers is None:
        return None
    try:
        values = headers.get_all("X-Robots-Tag")
    except Exception:  # noqa: BLE001
        values = None
    if not values:
        return None
    joined = "; ".join(v for v in values if v)
    return joined[:_X_ROBOTS_MAX_LEN] if joined else None


def _ssrf_reason_to_taxonomy(blocked: str) -> str:
    """Map ``_check_url_for_ssrf``'s reason ladder to the preflight taxonomy,
    matching ``content.fetch._check_once``'s mapping.
    """
    if blocked in {"invalid_host", "invalid_ip"}:
        return "invalid_url"
    if blocked == "dns_failure":
        return "unreachable"
    return "ssrf_blocked"


def _classify_url_error(exc: URLError) -> "PreflightFacts":
    """Translate a :class:`URLError` into a :class:`PreflightFacts` reason."""
    reason_obj = getattr(exc, "reason", None)
    if isinstance(reason_obj, str) and reason_obj.startswith("ssrf_"):
        return PreflightFacts(reason="ssrf_blocked")
    if isinstance(reason_obj, ssl.SSLError):
        return PreflightFacts(tls_unverified=True, reason="tls_unverified")
    if isinstance(reason_obj, socket.timeout):
        return PreflightFacts(reason="timeout")
    return PreflightFacts(reason="network_error")


def _build_facts_from_response(resp: Any, normalized: str) -> "PreflightFacts":
    """Read *resp* metadata + body and return :class:`PreflightFacts`. Never raises."""
    try:
        status = resp.getcode()
        final_url = resp.geturl() or normalized
        headers = resp.info()
        try:
            body = _read_body_prefix(resp, PREFLIGHT_BODY_BYTES)
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return PreflightFacts(reason="network_error")

    # Post-redirect SSRF re-check of the final URL (narrows the DNS-rebinding
    # window; IP-range re-check only — a malformed final host is left to the
    # opener's per-hop guard, which already raised if it mattered).
    if final_url and final_url != normalized:
        final_blocked = _safe_ssrf_check(final_url)
        if final_blocked is not None and final_blocked.startswith("blocked_ip"):
            return PreflightFacts(status=status, final_url=final_url, reason="ssrf_blocked")

    redirected = bool(final_url) and final_url != normalized
    host_diff = redirected and safe_hostname(final_url) != safe_hostname(normalized)

    if status != 200:
        reason = "http_5xx" if 500 <= status < 600 else f"http_{status}"
        return PreflightFacts(
            status=status, final_url=final_url, redirected=redirected,
            host_diff=host_diff, reason=reason,
        )

    title = extract_title(body) or ""
    x_robots = _x_robots_value(headers)
    noindex = _meta_noindex(body) or bool(x_robots and _has_noindex_directive(x_robots))

    return PreflightFacts(
        status=status,
        final_url=final_url,
        redirected=redirected,
        host_diff=host_diff,
        redirect_capped=False,
        noindex=noindex,
        soft404=is_soft_404_title(title),
        has_title=bool(title),
        has_h1=_has_h1(body),
        tls_unverified=False,
        reason=None,
        x_robots_tag=x_robots,
    )


def _safe_ssrf_check(url: str) -> Optional[str]:
    """``_check_url_for_ssrf`` wrapper that never raises.

    ``_check_url_for_ssrf`` calls ``urlparse(url).hostname``, which raises
    ``ValueError`` on a malformed IPv6 literal (e.g. ``http://[invalid``). A
    hostile ``Location`` header or operator typo must not crash this routine —
    the contract is exit-0-safe. Treat any parse failure as ``invalid_host``
    (→ ``invalid_url`` in the taxonomy), never fetched.
    """
    try:
        return _check_url_for_ssrf(url)
    except Exception:  # noqa: BLE001 — malformed URL must not break never-raises
        return "invalid_host"


def fetch_target(url: str, *, timeout: Optional[float] = None) -> PreflightFacts:
    """Fetch ``url`` once and return :class:`PreflightFacts`. Never raises."""
    # 1. Scheme gate — before the SSRF DNS check or any Request.
    if not _is_http_url(url):
        return PreflightFacts(reason="invalid_url")

    # 2. Initial SSRF guard (resolves host; rejects blocked ranges).
    blocked = _safe_ssrf_check(url)
    if blocked is not None:
        return PreflightFacts(reason=_ssrf_reason_to_taxonomy(blocked))

    normalized = normalize_url_for_fetch(url)
    req = Request(normalized, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    effective_timeout = timeout if timeout is not None else FETCH_TIMEOUT

    try:
        resp = _PREFLIGHT_OPENER.open(req, timeout=effective_timeout)
    except HTTPError as exc:
        code = exc.code
        if 300 <= code < 400:
            # Cap exceeded: urllib raises with the last 3xx code. Distinct fact
            # so a legitimate-but-long chain is not mislabeled a dead non-200.
            return PreflightFacts(status=code, redirect_capped=True, reason="redirect_capped")
        if 500 <= code < 600:
            return PreflightFacts(status=code, reason="http_5xx")
        return PreflightFacts(status=code, reason=f"http_{code}")
    except socket.timeout:
        return PreflightFacts(reason="timeout")
    except URLError as exc:
        return _classify_url_error(exc)
    except Exception:  # noqa: BLE001 — routine must never raise out
        return PreflightFacts(reason="network_error")

    return _build_facts_from_response(resp, normalized)
