"""Path-depth dynamic URL deriver.

Given a raw URL, derive a ``(main, category, work)`` tier triple per the
rules in plan ``docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1``.

Pure function — no network, no filesystem. Failure modes (invalid URL,
parse error, non-http(s) scheme) yield an all-``None`` triple so callers
can treat "not derivable" uniformly.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse, urlunparse


#: Pure-letter category token: 3-15 chars, letters ONLY (no digits, no hyphens).
#: Used at path depth >= 2 to decide whether the trailing segment looks like
#: a category landing page (e.g. ``/about``, ``/comic``, ``/archive``) versus a
#: leaf work URL (e.g. ``/comic/6``, ``/post-slug``, ``/title-abc123``).
#:
#: Hyphens are excluded because hyphenated slugs (``post-slug``, ``my-article``)
#: are almost always article/work URLs in the wild, not category landing pages.
_CATEGORY_TOKEN = re.compile(r"^[a-z]{3,15}$", re.I)


def _normalize_origin(scheme: str, netloc: str) -> str:
    """Return ``https://<netloc>`` — scheme forced, host preserved verbatim."""
    return urlunparse(("https", netloc, "", "", "", ""))


def _normalize_subpath(netloc: str, path: str) -> str:
    """Return ``https://<netloc><path>`` with trailing slash stripped.

    Query string and fragment are dropped (caller has already discarded
    them before invoking). Empty path collapses to origin form upstream.
    """
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    return urlunparse(("https", netloc, path, "", "", ""))


def derive_path_tiers(raw_url: str) -> dict:
    """Derive main/category/work tiers from ``raw_url``.

    Returns ``{"main": Optional[str], "category": Optional[str], "work":
    Optional[str]}``. All-``None`` if URL is invalid or scheme not in
    ``{http, https}``.

    Rules (path-depth dispatch):

    - 0 segs (origin only)       → main only
    - 1 seg                      → main + category (any tail shape)
    - >=2 segs, tail matches
      :data:`_CATEGORY_TOKEN`    → main + category=full path, work=None
    - >=2 segs, tail does NOT
      match                      → main + category=path-without-last-seg,
                                    work=full URL

    Normalization (R2):

    - scheme → https
    - trailing slash dropped on subpaths (root ``/`` kept on origin)
    - query string + fragment dropped
    - host preserved verbatim (incl. ``www.``/subdomain)
    """
    none_result: dict = {"main": None, "category": None, "work": None}
    if not isinstance(raw_url, str) or not raw_url:
        return none_result
    try:
        parsed = urlparse(raw_url)
    except Exception:  # noqa: BLE001 — urlparse is permissive but defend.
        return none_result
    if parsed.scheme not in {"http", "https"}:
        return none_result
    if not parsed.netloc:
        return none_result

    netloc = parsed.netloc
    path = parsed.path or ""
    segments = [seg for seg in path.split("/") if seg]

    main = _normalize_origin("https", netloc)

    if len(segments) == 0:
        return {"main": main, "category": None, "work": None}

    if len(segments) == 1:
        category = _normalize_subpath(netloc, "/" + segments[0])
        return {"main": main, "category": category, "work": None}

    tail = segments[-1]
    if _CATEGORY_TOKEN.match(tail):
        category = _normalize_subpath(netloc, "/" + "/".join(segments))
        return {"main": main, "category": category, "work": None}

    category_path = "/" + "/".join(segments[:-1])
    category = _normalize_subpath(netloc, category_path)
    work = _normalize_subpath(netloc, "/" + "/".join(segments))
    return {"main": main, "category": category, "work": work}
