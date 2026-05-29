"""URL content-fetch gate for the backlink pipeline.

Verifies that a URL returns HTTP 200 and parses out a non-empty ``<title>`` or
``og:title`` element before the URL is allowed into a published backlink
article. Catches the failure class generalised by PR #19 / plan
``docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md``: synthesized
or stale URLs that look reachable to the HEAD-only ``linkcheck.check_url`` but
serve a 4xx, soft empty body, CAPTCHA interstitial, or Cloudflare challenge
page on full GET.

Sibling to ``linkcheck`` (reachability only) and ``work_scraper`` (deeper
scrape with SSRF defence). Not a replacement for either — this module is
deliberately the smallest "real GET + title check" surface and stays
process-scope in-memory only.

Public surface
--------------
- :func:`verify_url_has_content` — single URL check with retry, returns
  ``(ok, reason, title)``.
- :func:`verify_urls_batch` — concurrent batch (default 5 workers) with
  in-run cache; same return shape per URL.
- :func:`reset_cache` — test hook; clears the process-scope memoization.

Cache semantics: results (success AND failure) are cached for the lifetime of
the importing process. A 404'd URL does not get re-fetched within the same
plan-backlinks invocation. Operators must either restart the process or call
:func:`reset_cache` (tests) to invalidate.
"""

from __future__ import annotations

import os
import socket  # noqa: F401 — kept for test patch backward compat
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request

from backlink_publisher._util.logger import opencli_logger
from backlink_publisher._util.url import (
    canonicalize_url,
    normalize_url_for_fetch,
    safe_urlparse,
)
from backlink_publisher._util.net_safety import (
    _check_url_for_ssrf,
    _make_ssrf_opener,
    _SSRF_OPENER,
)
from ._soft404 import is_soft_404_title as _is_soft_404_title
from ._html_utils import read_html_head_window, extract_title

#: Wall-clock budget per single GET attempt. Roughly matches ``linkcheck``'s
#: REQUEST_TIMEOUT so a row's combined plan-time HTTP doesn't drift wildly.
FETCH_TIMEOUT: int = 10

#: Retries on transient failures (timeout / 5xx / network). 4xx and
#: ``http_200_no_title`` are not retried — the result is structurally stable.
MAX_RETRIES: int = 2

#: Soft head-window cap. Title extraction only needs ``<head>`` content;
#: we stream the response and stop as soon as ``</head>`` appears (or after
#: this many bytes if it doesn't). 256KB is generous for any reasonable HTML
#: head — typical heads are 5-50KB even with inlined CSS/JS / og: tags.
HEAD_SCAN_BYTES: int = 256_000

#: Retained for backward-import compatibility and as a defensive hard cap
#: passed down to readers in case ``HEAD_SCAN_BYTES`` ever needs to grow.
#: No longer triggers ``body_too_large`` — streaming caps far earlier.
MAX_BODY_BYTES: int = 1_000_000

#: User-Agent identifies this fetcher distinctly from ``linkcheck``'s probe so
#: target sites can rate-limit / allowlist the two independently.
USER_AGENT: str = "backlink-publisher/0.1 content-fetch"

#: Below this byte count, a 200 with neither ``</head>`` parsed nor a
#: ``<title>`` extracted is rejected as ``body_too_small`` — likely a stub /
#: interstitial / placeholder page. Strictly tighter than
#: ``http_200_no_title``: legitimate short pages WITH a title still pass.
BODY_TOO_SMALL_BYTES: int = 2048

#: Loose TLS context (matches ``linkcheck``'s default — self-signed and
#: expired certs are tolerated because backlink targets historically include
#: rough indie sites).
_SSL_CTX: ssl.SSLContext = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# SSRF defence lives in backlink_publisher._util.net_safety.
# _check_url_for_ssrf and _SSRF_OPENER are imported above.


CheckResult = tuple[bool, Optional[str], Optional[str]]
#: ``(ok, reason, title)``. ``reason`` is ``None`` on success and one of the
#: stable strings documented in the module docstring otherwise. ``title`` is
#: the extracted text on success (stripped, non-empty) or ``None`` on failure.

#: Cache entry: (result, monotonic timestamp at write). The timestamp lets
#: callers opt into TTL-based expiry without changing the result tuple shape
#: existing callers rely on.
_CacheEntry = tuple[CheckResult, float]
_CACHE: dict[str, _CacheEntry] = {}

#: Canonical "unreachable / unexpected failure" verdict used as a fail-closed
#: fallback in the batch path.
_NETWORK_ERROR: CheckResult = (False, "network_error", None)

#: Thread lock guarding the ``_CACHE`` dict structure and ``_evict_lru``.
#: It protects dict integrity only — the network fetch runs OUTSIDE the lock,
#: so two threads missing the same key may both fetch (last write wins). This
#: is intentional: holding the lock across a blocking GET would serialize all
#: fetches. ``_STATS`` counters are advisory and updated outside the lock.
_CACHE_LOCK = threading.Lock()

#: Maximum cache entries (LRU eviction). Prevents unbounded growth in
#: long-running processes like the WebUI daemon. Set via
#: ``BACKLINK_FETCH_CACHE_MAX_ENTRIES`` (default 256).
_MAX_CACHE_ENTRIES: int = 256


def _evict_lru() -> None:
    """Evict oldest cache entries when size limit is exceeded."""
    if len(_CACHE) <= _MAX_CACHE_ENTRIES:
        return
    # Remove oldest 25% of entries (simple FIFO, not true LRU)
    to_remove = len(_CACHE) - (_MAX_CACHE_ENTRIES - _MAX_CACHE_ENTRIES // 4)
    for _ in range(to_remove):
        _CACHE.pop(next(iter(_CACHE)), None)


def _cache_key(url: object) -> str:
    """Canonical cache key for ``url`` (collapses utm params, default ports,
    trailing slash, fragment — see :func:`canonicalize_url`).

    Declared ``-> str`` for the happy path, but defensively accepts ``object``
    and passes non-canonicalizable input through unchanged so the fail-closed
    contract of :func:`verify_url_has_content` holds: a malformed or non-string
    URL must still reach ``_is_valid_http_url`` and resolve as ``invalid_url``
    rather than crashing the fetch gate. Two cases:

    - non-``str`` scalar input (``int``, ``bool`` …) — ``urlsplit`` would raise
      ``AttributeError``/``TypeError``; we short-circuit on the type.
    - malformed strings such as ``http://[invalid`` — ``urlsplit`` raises
      ``ValueError`` (``Invalid IPv6 URL``); we catch and fall back.
    """
    if not isinstance(url, str):
        return url  # type: ignore[return-value]  # fail-closed passthrough
    try:
        return canonicalize_url(url)
    except ValueError:
        return url


# Initialize max cache entries from environment (allows tuning without code change)
try:
    _MAX_CACHE_ENTRIES = int(os.environ.get("BACKLINK_FETCH_CACHE_MAX_ENTRIES", "256"))
except (ValueError, TypeError):
    pass  # Keep default if env var is malformed

#: Process-wide default TTL for cache entries (seconds). ``None`` means "never
#: expire" (CLI default — process is short-lived). Webui startup sets this to
#: ``BACKLINK_GATE_CACHE_TTL_SECONDS`` (default 900) so a long-running daemon
#: re-fetches stale results.
_DEFAULT_MAX_AGE_S: Optional[float] = None

#: Process-wide statistics counters. Updated on every
#: :func:`verify_url_has_content` call. ``stats_snapshot()`` returns a
#: shallow copy; ``reset_stats()`` clears for tests / per-invocation resets.
_STATS: dict[str, Any] = {
    "cache_hits": 0,
    "cache_misses": 0,
    "fetches": 0,
    "total_latency_ms": 0,
    "reason_counts": {},
}


def reset_cache() -> None:
    """Clear the in-run cache. Tests call this between scenarios; production
    code should not need it (process restart clears the cache naturally).
    """
    _CACHE.clear()


def set_default_max_age(seconds: Optional[float]) -> None:
    """Set the process-wide TTL for cache entries.

    Passing ``None`` disables expiry (CLI default). Webui startup wires this
    to ``BACKLINK_GATE_CACHE_TTL_SECONDS`` (default 900s = 15 min) so a daemon
    that has been running for hours doesn't serve stale gate results.
    Idempotent — multiple calls just replace the value.
    """
    global _DEFAULT_MAX_AGE_S
    _DEFAULT_MAX_AGE_S = seconds


def reset_stats() -> None:
    """Reset the per-process stats counters.

    Called by plan-backlinks ``main()`` so each invocation reports its own
    cache hit rate / fetch count. Also by the autouse test fixture so
    cross-test bleed doesn't corrupt assertions.
    """
    _STATS["cache_hits"] = 0
    _STATS["cache_misses"] = 0
    _STATS["fetches"] = 0
    _STATS["total_latency_ms"] = 0
    _STATS["reason_counts"] = {}


def stats_snapshot() -> dict[str, Any]:
    """Return a snapshot of the stats counters.

    Shallow copy of the top-level dict, with ``reason_counts`` deep-copied
    so callers can mutate without affecting the live counters. Use
    :func:`reset_stats` to clear.
    """
    return {
        "cache_hits": _STATS["cache_hits"],
        "cache_misses": _STATS["cache_misses"],
        "fetches": _STATS["fetches"],
        "total_latency_ms": _STATS["total_latency_ms"],
        "reason_counts": dict(_STATS["reason_counts"]),
    }


def _record_reason(reason: Optional[str], ok: bool) -> None:
    """Increment the per-reason counter. ``ok=True`` records 'ok'."""
    key = "ok" if ok else (reason or "unknown")
    counts = _STATS["reason_counts"]
    counts[key] = counts.get(key, 0) + 1


def _is_transient(reason: str) -> bool:
    """Return True for failure reasons safe to retry. 4xx and 200-no-title
    are not transient — the page state is structurally stable.
    """
    from backlink_publisher.publishing.adapters.retry import is_transient_reason
    return is_transient_reason(reason)








def _check_once(
    url: str,
    timeout_seconds: Optional[float] = None,
    max_redirects: Optional[int] = None,
) -> CheckResult:
    """Single GET attempt. Returns the canonical CheckResult; never raises.

    ``timeout_seconds`` overrides :data:`FETCH_TIMEOUT` when set; ``None`` =
    default. ``max_redirects`` builds a fresh SSRF opener with a custom
    redirect cap; ``None`` = reuse the shared :data:`_SSRF_OPENER`
    (default 10 redirects).

    SSRF defence lives in ``backlink_publisher._util.net_safety``:

    1. :func:`_check_url_for_ssrf` resolves the URL's host and rejects
       any address in ``_BLOCKED_NETWORKS`` (RFC1918, loopback,
       link-local, cloud-metadata, CGNAT, multicast, IPv6 tunnel).
    2. :data:`_SSRF_OPENER` installs a custom redirect handler that
       re-checks each 30x target and refuses HTTPS→HTTP downgrade.
    """
    blocked = _check_url_for_ssrf(url)
    if blocked is not None:
        # Map the precise reason ladder to a stable taxonomy:
        # - 'invalid_host' / 'invalid_ip' → invalid_url
        # - 'dns_failure' → network_error (operator may be offline; retry)
        # - 'blocked_ip:<net>' → ssrf_blocked (no retry; structural)
        if blocked in {"invalid_host", "invalid_ip"}:
            return False, "invalid_url", None
        if blocked == "dns_failure":
            return False, "network_error", None
        return False, "ssrf_blocked", None

    req = Request(normalize_url_for_fetch(url), method="GET")
    req.add_header("User-Agent", USER_AGENT)
    opener = _make_ssrf_opener(max_redirects) if max_redirects is not None else _SSRF_OPENER
    effective_timeout = timeout_seconds if timeout_seconds is not None else FETCH_TIMEOUT
    try:
        resp = opener.open(req, timeout=effective_timeout)
    except HTTPError as exc:
        code = exc.code
        if 400 <= code < 500:
            return False, f"http_{code}", None
        if 500 <= code < 600:
            return False, "http_5xx", None
        return False, f"http_{code}", None
    except socket.timeout:
        return False, "timeout", None
    except URLError as exc:
        reason_obj = getattr(exc, "reason", None)
        # Our custom redirect handler raises URLError with reason strings
        # like "ssrf_redirect:blocked_ip:10.0.0.0/8" or
        # "ssrf_https_downgrade". Surface those as their own category so
        # operators don't confuse them with network failures.
        if isinstance(reason_obj, str) and reason_obj.startswith("ssrf_"):
            return False, "ssrf_blocked", None
        if isinstance(reason_obj, socket.timeout):
            return False, "timeout", None
        return False, "network_error", None
    except Exception:  # noqa: BLE001
        return False, "network_error", None

    code = resp.getcode()
    if code != 200:
        if 400 <= code < 500:
            return False, f"http_{code}", None
        if 500 <= code < 600:
            return False, "http_5xx", None
        return False, f"http_{code}", None

    try:
        body = read_html_head_window(resp, HEAD_SCAN_BYTES)
    except Exception:  # noqa: BLE001
        return False, "network_error", None
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    title = extract_title(body)
    if not title:
        has_head_close = b"</head>" in body.lower()
        if not has_head_close and len(body) < BODY_TOO_SMALL_BYTES:
            return False, "body_too_small", None
        return False, "http_200_no_title", None
    if _is_soft_404_title(title):
        # Page returned HTTP 200 but its title advertises a 404 state.
        # Distinct reason so operators can filter soft-404s separately
        # from hard 404s / empty-title pages.
        return False, "soft_404_title", None
    return True, None, title


def _is_valid_http_url(url: str) -> bool:
    """Cheap structural check: scheme is http/https and netloc is non-empty.
    Run before any network attempt so callers get a deterministic
    ``invalid_url`` rather than a flaky network error for malformed input.
    """
    parsed = safe_urlparse(url)
    if parsed is None:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def verify_url_has_content(
    url: str,
    max_age_seconds: Optional[float] = None,
    timeout_seconds: Optional[float] = None,
    max_redirects: Optional[int] = None,
) -> CheckResult:
    """Verify ``url`` returns HTTP 200 and a parseable non-empty title.

    Cached: subsequent calls with the same URL return the cached result
    (positive or negative) without re-fetching, *subject to TTL*. Use
    :func:`reset_cache` to invalidate during tests.

    ``max_age_seconds`` (call-site override) > :data:`_DEFAULT_MAX_AGE_S`
    (process-wide, set by :func:`set_default_max_age`) > ``None`` (never
    expire). When a cached entry is older than the effective TTL, the entry
    is treated as a miss and re-fetched. ``max_age_seconds=0`` forces a
    fresh fetch every call.

    Stats: every call updates :data:`_STATS` (cache hits / misses /
    fetches / latency / reason_counts). Inspect via :func:`stats_snapshot`.

    Returns
    -------
    (ok, reason, title)
        ``ok`` is ``True`` only when HTTP status is 200, the body parses, and
        either ``<meta property="og:title">`` or ``<title>`` resolves to a
        non-empty stripped string. ``reason`` carries the failure category
        on ``ok=False`` and is ``None`` on success. ``title`` is the
        extracted string on success and ``None`` otherwise.
    """
    # Normalize URL for cache key to collapse equivalent representations.
    canonical_url = _cache_key(url)
    effective_ttl = max_age_seconds if max_age_seconds is not None else _DEFAULT_MAX_AGE_S

    # Fast path: check cache under lock to avoid duplicate fetches.
    with _CACHE_LOCK:
        cached = _CACHE.get(canonical_url)
        if cached is not None:
            result, written_at = cached
            if effective_ttl is None or (time.monotonic() - written_at) < effective_ttl:
                _STATS["cache_hits"] += 1
                return result
            # Expired — fall through to refetch (will overwrite under lock later).

    _STATS["cache_misses"] += 1

    if not _is_valid_http_url(url):
        result = (False, "invalid_url", None)
        with _CACHE_LOCK:
            _CACHE[canonical_url] = (result, time.monotonic())
            _evict_lru()
        _record_reason("invalid_url", ok=False)
        return result

    started = time.monotonic()
    last_result: CheckResult = (False, "network_error", None)
    for attempt in range(MAX_RETRIES + 1):
        ok, reason, title = _check_once(url, timeout_seconds, max_redirects)
        if ok:
            last_result = (True, None, title)
            break
        last_result = (False, reason, None)
        if reason is None or not _is_transient(reason):
            break
        if attempt < MAX_RETRIES:
            opencli_logger.debug(
                f"content_fetch retry {attempt + 1}/{MAX_RETRIES} for {url}: {reason}"
            )
    elapsed_ms = int((time.monotonic() - started) * 1000)

    _STATS["fetches"] += 1
    _STATS["total_latency_ms"] += elapsed_ms
    _record_reason(last_result[1], ok=last_result[0])
    with _CACHE_LOCK:
        _CACHE[canonical_url] = (last_result, time.monotonic())
        _evict_lru()
    return last_result


def verify_urls_batch(
    urls: list[str], max_workers: int = 5,
) -> dict[str, CheckResult]:
    """Verify a batch of URLs concurrently and return a per-URL result dict.

    Deduplicates the input, consults the cache, submits cache-miss URLs to a
    bounded ``ThreadPoolExecutor``, and merges the results. Each call to
    :func:`verify_url_has_content` inside the workers updates the shared cache,
    so a subsequent batch (or single-URL call) seeing the same URL hits the
    cached result.

    Parameters
    ----------
    urls : list[str]
        Candidate URLs. Order is not preserved in the dict; callers map
        results back to their positions via the URL keys.
    max_workers : int, default 5
        Concurrency cap. The default matches ``linkcheck.check_urls`` and is
        gentle enough that batches of 6–10 URLs overlap without overwhelming
        target sites.

    Returns
    -------
    dict[str, CheckResult]
        One entry per distinct URL. Caller-side duplicates collapse to a
        single entry.
    """
    if not urls:
        return {}

    # Build mapping: canonical URL -> list of original URLs that map to it
    canonical_to_originals: dict[str, list[str]] = {}
    for u in urls:
        c = _cache_key(u)
        canonical_to_originals.setdefault(c, []).append(u)

    distinct_canonical = list(canonical_to_originals.keys())

    # Determine cache misses (respect TTL)
    now = time.monotonic()
    def _fresh(entry: _CacheEntry) -> bool:
        if _DEFAULT_MAX_AGE_S is None:
            return True
        return (now - entry[1]) < _DEFAULT_MAX_AGE_S

    # Snapshot fresh cache hits and collect misses atomically under the lock.
    # We capture the hit *value* now rather than re-reading _CACHE after the
    # fetch phase: a large batch (more distinct URLs than _MAX_CACHE_ENTRIES)
    # would otherwise let _evict_lru drop an early result before we read it,
    # turning a genuine success into a spurious "network_error".
    results_by_canonical: dict[str, CheckResult] = {}
    misses_canonical: list[str] = []
    with _CACHE_LOCK:
        for c in distinct_canonical:
            entry = _CACHE.get(c)
            if entry is not None and _fresh(entry):
                results_by_canonical[c] = entry[0]
            else:
                misses_canonical.append(c)

    if misses_canonical:
        workers = min(max_workers, max(1, len(misses_canonical)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for c in misses_canonical:
                # Pick any original URL that maps to this canonical to preserve logging context
                representative_url = canonical_to_originals[c][0]
                fut = pool.submit(verify_url_has_content, representative_url)
                futures[fut] = c
            for fut in as_completed(futures):
                c = futures[fut]
                try:
                    # verify_url_has_content caches internally; we keep the
                    # returned value too so eviction can't lose it before the
                    # result dict is built.
                    results_by_canonical[c] = fut.result()
                except Exception:  # noqa: BLE001
                    results_by_canonical[c] = _NETWORK_ERROR
                    # Cache the failure so a repeat call doesn't re-raise.
                    with _CACHE_LOCK:
                        _CACHE.setdefault(c, (_NETWORK_ERROR, time.monotonic()))

    # Build result dict for every original URL from the captured values.
    result: dict[str, CheckResult] = {}
    for c, originals in canonical_to_originals.items():
        res = results_by_canonical.get(c, _NETWORK_ERROR)
        for orig in originals:
            result[orig] = res
    return result
