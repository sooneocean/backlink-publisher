"""Stateless helper predicates for the content-fetch gate.

Extracted from :mod:`backlink_publisher.content.fetch` (monolith-budget
headroom, 2026-06-01). Pure functions only — no process state, no network,
nothing tests patch by name on the ``fetch`` module — so they are safe to
import from anywhere and safe to relocate out of the gate's hot module.
"""

from __future__ import annotations

from backlink_publisher._util.url import canonicalize_url, safe_urlparse


def _cache_key(url: object) -> str:
    """Canonical cache key for ``url`` (collapses utm params, default ports,
    trailing slash, fragment — see :func:`canonicalize_url`).

    Declared ``-> str`` for the happy path, but defensively accepts ``object``
    and passes non-canonicalizable input through unchanged so the fail-closed
    contract of ``verify_url_has_content`` holds: a malformed or non-string
    URL must still reach :func:`_is_valid_http_url` and resolve as
    ``invalid_url`` rather than crashing the fetch gate. Two cases:

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


def _is_transient(reason: str) -> bool:
    """Return True for failure reasons safe to retry. 4xx and 200-no-title
    are not transient — the page state is structurally stable.
    """
    from backlink_publisher.publishing.adapters.retry import is_transient_reason

    return is_transient_reason(reason)
