from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests

from backlink_publisher._util.errors import ExternalServiceError, InputValidationError
from backlink_publisher._util.logger import plan_logger
from backlink_publisher._util.url import validate_https_url
from backlink_publisher.publishing.adapters.retry import retry_transient_call

_USER_AGENT = "backlink-publisher-scraper/0.2.0"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB
class _RetryableHttp(Exception):
    """Marker for 429 responses — bubble up so retry_transient_call retries."""


class _ResponseTooLarge(Exception):
    """Body exceeded ``_MAX_RESPONSE_BYTES``. Fail-continue at caller layer."""


# ── SSRF guard ───────────────────────────────────────────────────────────────


def _resolve_addresses(host: str) -> list[str]:
    """Return all IP literals that ``host`` resolves to. Mockable in tests."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _block_if_private(url: str) -> None:
    """Raise ``InputValidationError`` if any resolved IP is private,
    loopback, or link-local. Treats DNS resolution failure as transient
    (raises ``ExternalServiceError`` so the retry path can decide)."""
    host = urlparse(url).hostname
    if not host:
        raise InputValidationError(f"URL has no resolvable host: {url!r}")
    try:
        addrs = _resolve_addresses(host)
    except OSError as exc:
        raise ExternalServiceError(
            f"DNS resolution failed for {host}: {type(exc).__name__}"
        )
    for ip_str in addrs:
        cleaned = ip_str.split("%", 1)[0]  # strip IPv6 scope id
        try:
            addr = ipaddress.ip_address(cleaned)
        except ValueError:
            continue
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise InputValidationError(
                f"URL resolves to disallowed IP range ({ip_str}): {url}"
            )


# ── Safe HTTP wrapper ────────────────────────────────────────────────────────


def _safe_get(
    url: str, *, timeout: int, insecure_tls: bool = False
) -> tuple[requests.Response, bytes]:
    """Validated, SSRF-checked, size-capped, retry-wrapped GET.

    Returns ``(response, body_bytes)``. Streams the body and aborts when the
    cumulative size exceeds ``_MAX_RESPONSE_BYTES``.

    Retries: ``ConnectionError`` / ``Timeout`` / 429. Everything else
    (5xx, 4xx, oversize) propagates after the first attempt.
    """
    normalized = validate_https_url(url)
    if not normalized:
        raise InputValidationError(
            f"invalid URL (https-only required): {url!r}"
        )
    _block_if_private(normalized)

    def _call() -> tuple[requests.Response, bytes]:
        resp = requests.get(
            normalized,
            timeout=timeout,
            verify=not insecure_tls,
            stream=True,
            allow_redirects=False,
            headers={"User-Agent": _USER_AGENT},
        )

        if resp.status_code == 429:
            resp.close()
            raise _RetryableHttp(f"429 from {normalized}")

        content_length = resp.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_RESPONSE_BYTES:
                    resp.close()
                    raise _ResponseTooLarge(
                        f"Content-Length={content_length} exceeds cap"
                    )
            except ValueError:
                pass  # malformed header — rely on the streaming check

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_RESPONSE_BYTES:
                resp.close()
                raise _ResponseTooLarge(
                    f"streamed body exceeded {_MAX_RESPONSE_BYTES} bytes"
                )
            chunks.append(chunk)
        body = b"".join(chunks)

        # Apparent_encoding inspects the body, so set it now for callers.
        try:
            resp.encoding = resp.apparent_encoding or resp.encoding
        except Exception:  # noqa: BLE001 — defensive: never crash on encoding probe
            pass
        return resp, body

    def _is_retryable(exc: Exception) -> bool:
        return isinstance(
            exc,
            (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                _RetryableHttp,
            ),
        )

    return retry_transient_call(
        _call, is_retryable=_is_retryable, adapter="work_scraper"
    )
