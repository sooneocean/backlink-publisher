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

import ipaddress
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
    urlopen,
)

from bs4 import BeautifulSoup

from .logger import opencli_logger

#: Wall-clock budget per single GET attempt. Roughly matches ``linkcheck``'s
#: REQUEST_TIMEOUT so a row's combined plan-time HTTP doesn't drift wildly.
FETCH_TIMEOUT: int = 10

#: Retries on transient failures (timeout / 5xx / network). 4xx and
#: ``http_200_no_title`` are not retried — the result is structurally stable.
MAX_RETRIES: int = 2

#: Per-attempt body cap. Larger responses are rejected with ``body_too_large``
#: rather than parsed — protects against accidental binary downloads.
MAX_BODY_BYTES: int = 1_000_000

#: User-Agent identifies this fetcher distinctly from ``linkcheck``'s probe so
#: target sites can rate-limit / allowlist the two independently.
USER_AGENT: str = "backlink-publisher/0.1 content-fetch"

#: Loose TLS context (matches ``linkcheck``'s default — self-signed and
#: expired certs are tolerated because backlink targets historically include
#: rough indie sites).
_SSL_CTX: ssl.SSLContext = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# ── SSRF defence ────────────────────────────────────────────────────────────

#: IPs / IP networks that an outbound content fetch must never reach.
#: Each entry is checked via ``ipaddress.ip_address(ip) in net`` for networks
#: or equality for singleton IPs. Cloud-metadata IPs are listed explicitly so
#: future maintainers see what's covered without re-reading the network masks.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # RFC1918 private — every cloud / corp internal lives here
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local (IPv4 + IPv6)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # CGNAT — RFC6598, used by some carriers but also a corp-internal hideout
    ipaddress.ip_network("100.64.0.0/10"),
    # Multicast (no useful HTTP target)
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("ff00::/8"),
    # Unspecified
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
    # Documentation (TEST-NET) — shouldn't route but defenders block them
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # Carrier-grade reserved / benchmarking
    ipaddress.ip_network("198.18.0.0/15"),
    # Teredo / 6to4 (IPv6 tunnel)
    ipaddress.ip_network("2001::/32"),
    ipaddress.ip_network("2002::/16"),
)


def _is_blocked_ip(ip_text: str) -> Optional[str]:
    """Return a short ``reason`` string if ``ip_text`` falls in any blocked
    network, else ``None``. Used by the SSRF defence to reject metadata /
    internal targets before urlopen attempts a connection.
    """
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return "invalid_ip"
    for net in _BLOCKED_NETWORKS:
        # ipaddress compares mixed IPv4/IPv6 via .version mismatch raising.
        if ip.version != net.version:
            continue
        if ip in net:
            return f"blocked_ip:{net}"
    return None


def _resolve_host_ips(host: str) -> tuple[list[str], Optional[str]]:
    """Return ``(ip_strs, error)``. On DNS failure ``ip_strs`` is empty and
    ``error`` carries a stable reason ('dns_failure' / 'invalid_host').
    """
    if not host:
        return [], "invalid_host"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return [], "dns_failure"
    except Exception:  # noqa: BLE001
        return [], "dns_failure"
    ips: list[str] = []
    for fam, _typ, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0] if sockaddr else None
        if ip and ip not in ips:
            ips.append(ip)
    if not ips:
        return [], "dns_failure"
    return ips, None


def _check_url_for_ssrf(url: str) -> Optional[str]:
    """Resolve ``url``'s host to IPs and return a block ``reason`` if any IP
    in the set is in :data:`_BLOCKED_NETWORKS`, else ``None``.

    Conservative: if **any** resolved IP is blocked, refuse the whole URL.
    Operators occasionally serve real content on hostnames that also resolve
    to a local IP (split-horizon DNS) — false positives are preferable to
    SSRF false negatives.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return "invalid_host"
    # Hostname can be a literal IP (operator pastes http://10.0.0.5/) —
    # skip DNS and just check it directly.
    try:
        ipaddress.ip_address(host)
        return _is_blocked_ip(host)
    except ValueError:
        pass
    ips, err = _resolve_host_ips(host)
    if err:
        return err
    for ip in ips:
        blocked = _is_blocked_ip(ip)
        if blocked:
            return blocked
    return None


class _SSRFSafeRedirectHandler(HTTPRedirectHandler):
    """Re-validate every redirect target before allowing the follow.

    urllib's default handler follows 301/302/303/307/308 transparently. An
    attacker who controls a target URL could publish a 302 to
    ``http://169.254.169.254/`` and bypass the initial SSRF check. This
    subclass re-runs :func:`_check_url_for_ssrf` on each redirect target
    and rejects with ``URLError`` (which the caller maps to
    ``ssrf_redirect_blocked``). Also blocks HTTPS→HTTP downgrade redirects
    (a classic credential / TLS-stripping vector).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401, E501
        # HTTPS→HTTP downgrade refused regardless of destination IP.
        old_scheme = urlparse(req.full_url).scheme
        new_scheme = urlparse(newurl).scheme
        if old_scheme == "https" and new_scheme == "http":
            raise URLError("ssrf_https_downgrade")
        blocked = _check_url_for_ssrf(newurl)
        if blocked:
            raise URLError(f"ssrf_redirect:{blocked}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_SSRF_OPENER = build_opener(_SSRFSafeRedirectHandler())
#: Module-level opener with the SSRF-safe redirect handler installed. We
#: don't replace the global ``urllib.request.install_opener`` slot to avoid
#: side-effects on other tooling that imports ``urllib.request`` directly.


CheckResult = tuple[bool, Optional[str], Optional[str]]
#: ``(ok, reason, title)``. ``reason`` is ``None`` on success and one of the
#: stable strings documented in the module docstring otherwise. ``title`` is
#: the extracted text on success (stripped, non-empty) or ``None`` on failure.

#: Cache entry: (result, monotonic timestamp at write). The timestamp lets
#: callers opt into TTL-based expiry without changing the result tuple shape
#: existing callers rely on.
_CacheEntry = tuple[CheckResult, float]
_CACHE: dict[str, _CacheEntry] = {}

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
    return reason in {"timeout", "network_error", "http_5xx"}


def _extract_title(body: bytes) -> Optional[str]:
    """Parse ``body`` as HTML and return the first non-empty title element.

    Looks for ``<meta property="og:title">`` first (typically richer / more
    accurate on modern sites), then falls back to ``<title>``. Returns
    ``None`` if neither element is present or both are empty after strip.
    """
    try:
        soup = BeautifulSoup(body, "html.parser")
    except Exception:  # noqa: BLE001 — bs4 is permissive but a malformed
        # binary payload can still trip the underlying parser.
        return None

    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        content = og.get("content", "")
        if content and content.strip():
            return content.strip()

    title_tag = soup.find("title")
    if title_tag is not None and title_tag.text:
        stripped = title_tag.text.strip()
        if stripped:
            return stripped

    return None


def _check_once(url: str) -> CheckResult:
    """Single GET attempt. Returns the canonical CheckResult; never raises.

    SSRF defence (Plan 005 Unit 1, ported to live inside content_fetch
    rather than the originally-planned standalone ``net_safety.py``):

    1. The URL's hostname is resolved (or interpreted as a literal IP).
       Any address in :data:`_BLOCKED_NETWORKS` aborts the fetch with a
       ``ssrf_<reason>`` reason — RFC1918, loopback, link-local (incl.
       169.254.169.254 cloud-metadata), CGNAT, multicast, IPv6 tunnel.
    2. The request goes through :data:`_SSRF_OPENER` which installs a
       custom redirect handler. Each 30x target is re-checked the same
       way, and HTTPS→HTTP downgrade redirects are refused outright.
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

    req = Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    try:
        resp = _SSRF_OPENER.open(req, timeout=FETCH_TIMEOUT)
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
        body = resp.read(MAX_BODY_BYTES + 1)
    except Exception:  # noqa: BLE001
        return False, "network_error", None
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    if len(body) > MAX_BODY_BYTES:
        return False, "body_too_large", None

    title = _extract_title(body)
    if not title:
        return False, "http_200_no_title", None
    return True, None, title


def _is_valid_http_url(url: str) -> bool:
    """Cheap structural check: scheme is http/https and netloc is non-empty.
    Run before any network attempt so callers get a deterministic
    ``invalid_url`` rather than a flaky network error for malformed input.
    """
    if not isinstance(url, str) or not url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.netloc:
        return False
    return True


def verify_url_has_content(
    url: str,
    max_age_seconds: Optional[float] = None,
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
    effective_ttl = max_age_seconds if max_age_seconds is not None else _DEFAULT_MAX_AGE_S

    cached = _CACHE.get(url)
    if cached is not None:
        result, written_at = cached
        if effective_ttl is None or (time.monotonic() - written_at) < effective_ttl:
            _STATS["cache_hits"] += 1
            return result
        # Expired — fall through to refetch and overwrite the entry.

    _STATS["cache_misses"] += 1

    if not _is_valid_http_url(url):
        result = (False, "invalid_url", None)
        _CACHE[url] = (result, time.monotonic())
        _record_reason("invalid_url", ok=False)
        return result

    started = time.monotonic()
    last_result: CheckResult = (False, "network_error", None)
    for attempt in range(MAX_RETRIES + 1):
        ok, reason, title = _check_once(url)
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
    _CACHE[url] = (last_result, time.monotonic())
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

    distinct = list(dict.fromkeys(urls))
    # Treat URLs whose cached entry has expired (per process default TTL) as
    # misses so the prefetch path re-fetches them too. Bare `not in` ignored
    # TTL and surfaced stale results.
    now = time.monotonic()
    def _fresh(entry: _CacheEntry) -> bool:
        if _DEFAULT_MAX_AGE_S is None:
            return True
        return (now - entry[1]) < _DEFAULT_MAX_AGE_S

    misses = [
        u for u in distinct
        if u not in _CACHE or not _fresh(_CACHE[u])
    ]
    if misses:
        workers = min(max_workers, max(1, len(misses)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(verify_url_has_content, u): u for u in misses}
            for fut in as_completed(futures):
                # verify_url_has_content writes to _CACHE; we just need to
                # drain the future so exceptions (which shouldn't escape)
                # surface in tests rather than swallow.
                try:
                    fut.result()
                except Exception:  # noqa: BLE001
                    url = futures[fut]
                    _CACHE.setdefault(
                        url, ((False, "network_error", None), time.monotonic())
                    )

    return {u: _CACHE[u][0] for u in distinct}
