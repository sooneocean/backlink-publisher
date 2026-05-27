"""Body-returning, never-raises SSRF-safe fetch for comment-region detection.

``discover`` needs the *full* HTML body of an operator-supplied exact public URL so
:mod:`comment_outreach.detect` can look for comment-region markers (a Disqus thread,
a WordPress ``#respond`` form, a forum reply box). ``content._preflight_fetch`` is the
closest sibling, but it cannot be reused directly:

- its ``_read_body_prefix`` is module-private and stops at the hardcoded ``</h1>``
  sentinel (tuned for *title* extraction) — comment regions live anywhere in ``<body>``,
  often well after the first ``<h1>``, so that early-exit would blind the detector;
- its ``PreflightFacts`` carries title/h1/noindex facts irrelevant here.

So this module **replicates** ``fetch_target``'s security prelude in order — scheme gate
→ initial SSRF check → ``normalize_url_for_fetch`` → request via the SSRF-safe opener →
post-redirect final-URL re-check — and **reimplements** the streamed reader to return the
whole body up to a generous cap. Like the preflight routine it NEVER raises: every
failure is a stable ``reason`` taxonomy string so ``discover`` can stay exit-0 and map a
non-``ok`` fetch to ``comment_open=null``.

Hardening invariants (do not regress — see plan Unit 4):
- **No ``Accept-Encoding: gzip``**: wire bytes are counted at read time, so a compression
  bomb cannot decompress past the cap.
- The fetch path does **not** route through the process-global cached ``http.get_session()``
  (its urllib3 ``Retry`` + lack of per-redirect SSRF re-check would violate this contract).
- SSRF guards use the never-raise wrappers ``_safe_ssrf_check`` / ``_safe_hostname`` at
  every call site, so a malformed IPv6 host or hostile ``Location`` cannot crash the
  never-raises contract.
"""

from __future__ import annotations

import socket
import ssl
from typing import Any, NamedTuple, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request

from backlink_publisher._util.net_safety import _check_url_for_ssrf, _make_ssrf_opener
from backlink_publisher._util.url import normalize_url_for_fetch

#: Per-attempt wall-clock budget (matches ``content.fetch.FETCH_TIMEOUT``).
FETCH_TIMEOUT: int = 10

#: Redirect cap; the SSRF redirect handler re-checks every hop. 5 survives the common
#: apex→https→www→trailing-slash canonicalization chain (see ``_preflight_fetch``).
_MAX_REDIRECTS: int = 5

#: Whole-body ceiling. Generous (vs. preflight's title-only window) because a comment
#: region — esp. a lazy Disqus/Hyvor mount — can sit far down ``<body>``. A page whose
#: *wire* size exceeds this yields ``reason="oversized"`` rather than a truncated, and
#: thus possibly mis-detected, body.
MAX_BODY_BYTES: int = 1_500_000

#: Distinct UA so targets can rate-limit this fetcher separately from publish/preflight.
USER_AGENT: str = "backlink-publisher/0.1 comment-outreach"

#: Module-level opener so tests can patch ``_COMMENT_OPENER.open``. Pinned to the comment
#: redirect cap; the SSRF redirect handler re-checks every hop. Deliberately NOT the
#: cached ``http.get_session()``.
_COMMENT_OPENER = _make_ssrf_opener(_MAX_REDIRECTS)


class FetchResult(NamedTuple):
    """``(html, reason)``. ``html`` is the raw body bytes only when ``reason == "ok"``;
    every other reason carries ``html=None``. ``reason`` is a stable taxonomy string:
    ``ok`` / ``invalid_url`` / ``ssrf_blocked`` / ``non_200`` / ``timeout`` /
    ``oversized`` / ``network_error``.
    """

    html: Optional[bytes]
    reason: str


def _is_http_url(url: str) -> bool:
    """http/https scheme + non-empty netloc, run before any network attempt.
    ``urlparse`` raises ``ValueError`` on a malformed IPv6 literal (``http://[invalid``),
    so this is also the first guard keeping the never-raises contract — any parse failure
    is "not a valid http url" (→ ``invalid_url``)."""
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _safe_hostname(url: str) -> Optional[str]:
    """``urlparse(url).hostname`` that never raises (malformed IPv6 → None)."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def _safe_ssrf_check(url: str) -> Optional[str]:
    """``_check_url_for_ssrf`` wrapper that never raises; a malformed URL (which would
    raise inside ``urlparse(url).hostname``) is treated as ``invalid_host``, never
    fetched. Mirrors ``_preflight_fetch._safe_ssrf_check``."""
    try:
        return _check_url_for_ssrf(url)
    except Exception:  # noqa: BLE001 — malformed URL must not break never-raises
        return "invalid_host"


def _read_body_capped(resp: Any, max_bytes: int) -> tuple[bytes, bool]:
    """Stream ``resp`` to EOF or until the wire-byte cap is exceeded.

    Returns ``(body, oversized)``. Counts **wire** bytes (no gzip is requested), so the
    cap cannot be bypassed by a compression bomb. Unlike the preflight reader there is no
    ``</h1>`` early-exit: comment markers live anywhere in ``<body>``.
    """
    buf = bytearray()
    chunk_size = 32_768
    oversized = False
    while True:
        chunk = resp.read(chunk_size)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            oversized = True
            break
    return bytes(buf[:max_bytes]), oversized


def fetch_comment_page(url: str, *, timeout: Optional[float] = None) -> FetchResult:
    """Fetch ``url`` once and return its body. Never raises."""
    # 1. Scheme gate — before the SSRF DNS check or any Request.
    if not _is_http_url(url):
        return FetchResult(html=None, reason="invalid_url")

    # 2. Initial SSRF guard (resolves host; rejects blocked ranges / malformed host).
    if _safe_ssrf_check(url) is not None:
        return FetchResult(html=None, reason="ssrf_blocked")

    normalized = normalize_url_for_fetch(url)
    req = Request(normalized, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    # No Accept-Encoding header: we never opt into gzip, so wire bytes == decoded bytes
    # and the body cap is a hard ceiling on what a server can make us buffer.
    effective_timeout = timeout if timeout is not None else FETCH_TIMEOUT

    try:
        resp = _COMMENT_OPENER.open(req, timeout=effective_timeout)
    except HTTPError as exc:
        # Any 3xx-over-cap / 4xx / 5xx — detection only cares that it is not a clean 200.
        return FetchResult(html=None, reason="non_200")
    except socket.timeout:
        return FetchResult(html=None, reason="timeout")
    except URLError as exc:
        reason_obj = getattr(exc, "reason", None)
        if isinstance(reason_obj, str) and reason_obj.startswith("ssrf_"):
            return FetchResult(html=None, reason="ssrf_blocked")
        if isinstance(reason_obj, socket.timeout):
            return FetchResult(html=None, reason="timeout")
        if isinstance(reason_obj, ssl.SSLError):
            return FetchResult(html=None, reason="network_error")
        return FetchResult(html=None, reason="network_error")
    except Exception:  # noqa: BLE001 — routine must never raise out
        return FetchResult(html=None, reason="network_error")

    try:
        # `with resp` guarantees the socket is closed even if getcode()/geturl() raise —
        # a plain inner finally around only the body read would leak the fd in that case.
        with resp:
            status = resp.getcode()
            final_url = resp.geturl() or normalized
            body, oversized = _read_body_capped(resp, MAX_BODY_BYTES)
    except Exception:  # noqa: BLE001
        return FetchResult(html=None, reason="network_error")

    # Post-redirect SSRF re-check of the final URL (narrows the DNS-rebinding window;
    # blocked-IP re-check only — a malformed final host would already have raised in the
    # opener's per-hop guard).
    if final_url and final_url != normalized:
        final_blocked = _safe_ssrf_check(final_url)
        if final_blocked is not None and final_blocked.startswith("blocked_ip"):
            return FetchResult(html=None, reason="ssrf_blocked")

    if oversized:
        return FetchResult(html=None, reason="oversized")
    if status != 200:
        return FetchResult(html=None, reason="non_200")
    return FetchResult(html=body, reason="ok")
