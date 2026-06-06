"""Verdict classifier + credit gate for GEO citation probes (Plan 2026-05-29-006 Unit 5).

Turns a :class:`~backlink_publisher.geo.engines.ProbeResult` into a tiered
:class:`VerdictResult` that is hallucination-resistant and asymmetry-aware.

Tier precedence (single reported tier, D3/C5):
  refused       -- transport error outcome OR recognised refusal phrasing in the
                   answer text.  Evaluated before any URL matching so a refused
                   engine never produces a false ``absent``.
  site_cited    -- at least one source URL whose canonical host matches the
                   canonical host of the target URL.
  article_cited -- at least one source URL that path-matches a known published
                   article (host + path, after canonicalization).
  absent        -- non-empty answer with zero creditable URLs (and no refusal).

``brand_mentioned`` is an independent boolean flag, NOT part of the tier
hierarchy: it can be True even when tier == "absent".

A URL matching BOTH target host and a published article is reported as
``site_cited`` (headline) but the matching article URL is also recorded in
``credited_urls`` for downstream analytics (see ``carry_verdict``).

Credit gate (D4) -- pure string matching, zero net I/O:
  * A URL is creditable only if it is a valid http(s) URL whose canonical host
    matches (for site) or whose canonical host+path matches (for article).
  * Garbled / hallucinated URLs that merely string-contain the domain are NOT
    credited -- ``canonicalize_url`` must produce a real host that equals the
    target host.
  * Redirect-wrapper URLs whose host is a known redirector (t.co, bit.ly, ...)
    are moved to ``possibly_cited_unresolved`` rather than silently dropped
    (asymmetry-aware, review A5).  We attempt to extract the destination from
    common redirector parameter patterns; if extraction fails they remain in the
    unresolved bucket.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.geo.engines import ProbeResult

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Refusal-phrasing markers (mirrors perplexity adapter for cross-engine reuse)
# ---------------------------------------------------------------------------

#: Locale-sensitive refusal markers; matched case-insensitively as substrings.
#: Same set as ``perplexity._REFUSAL_MARKERS`` -- kept here so the verdict layer
#: can classify results from any engine without re-importing the adapter.
_REFUSAL_MARKERS: tuple[str, ...] = (
    "i can't help with that",
    "i cannot help with that",
    "i can't assist with",
    "i cannot assist with",
    "i'm unable to help",
    "i am unable to help",
    "i'm not able to provide",
    "i am not able to provide",
    "i won't be able to",
    "against my guidelines",
    "violates the usage policy",
    "i can't provide information",
    "i cannot provide information",
)


def _is_refusal_text(answer: str) -> bool:
    low = answer.lower()
    return any(m in low for m in _REFUSAL_MARKERS)


# ---------------------------------------------------------------------------
# Known redirector hosts (D4 asymmetry-aware bucket, review A5)
# ---------------------------------------------------------------------------

#: URLs whose host matches one of these are placed in
#: ``possibly_cited_unresolved`` rather than ``uncredited_urls`` -- they may
#: resolve to the target but we cannot confirm without a net call.
REDIRECTOR_HOSTS: frozenset[str] = frozenset(
    {
        "t.co",
        "bit.ly",
        "buff.ly",
        "ow.ly",
        "dlvr.it",
        "feedburner.com",
    }
)

#: Known ``?<param>=<destination-url>`` redirector query-param names in order
#: of preference.  If any resolves to a non-empty value, we use it as the
#: extracted destination for further matching.
_REDIRECT_PARAMS: tuple[str, ...] = (
    "url",
    "u",
    "to",
    "dest",
    "destination",
    "href",
    "link",
)


def _extract_redirect_destination(raw_url: str) -> str | None:
    """Try to extract the destination URL from a known redirector wrapper.

    Returns the extracted destination URL string (un-canonicalized) if found,
    or ``None`` if the pattern is not recognised.  Only query-param-style
    redirectors are handled -- we never follow the redirect.
    """
    try:
        parts = urlsplit(raw_url)
    except Exception:
        return None
    qs = parse_qs(parts.query, keep_blank_values=False)
    for param in _REDIRECT_PARAMS:
        values = qs.get(param)
        if values:
            candidate = values[0]
            # Only return if it looks like an http(s) URL.
            if candidate.startswith(("http://", "https://")):
                return candidate
    return None


# ---------------------------------------------------------------------------
# Credit-gate helpers
# ---------------------------------------------------------------------------


def _canonical_host(url: str) -> str | None:
    """Return the canonical lowercase host of ``url``, or None if not http(s)."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except Exception:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = parts.hostname
    if not host:
        return None
    return host.lower()


def _canonical_host_and_path(url: str) -> tuple[str, str] | None:
    """Return ``(host, path)`` from the canonicalized form of ``url``.

    Returns ``None`` for non-http(s) or malformed URLs.
    """
    if not url:
        return None
    try:
        canonical = canonicalize_url(url)
        parts = urlsplit(canonical)
    except Exception:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    host = parts.hostname
    if not host:
        return None
    # Path normalization: strip trailing slash except root.
    path = parts.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return host.lower(), path


# ---------------------------------------------------------------------------
# VerdictResult dataclass
# ---------------------------------------------------------------------------

#: Valid tier strings.
VERDICT_TIERS: frozenset[str] = frozenset(
    {"site_cited", "article_cited", "absent", "refused"}
)


@dataclass
class VerdictResult:
    """Classified verdict for one ``(query, engine)`` probe.

    ``tier`` is the single reported citation tier (highest wins).
    ``brand_mentioned`` is an independent flag -- it does not promote or demote
    the tier.  Both are safe to persist (no raw LLM content, D8).
    """

    tier: str  # "site_cited" | "article_cited" | "absent" | "refused"
    brand_mentioned: bool
    #: Canonical URLs that earned the tier (or both tiers if site+article).
    credited_urls: list[str] = field(default_factory=list)
    #: Valid http(s) URLs that passed parsing but failed host/path matching.
    uncredited_urls: list[str] = field(default_factory=list)
    #: Redirector/aggregator URLs -- may resolve to target but not confirmed.
    possibly_cited_unresolved: list[str] = field(default_factory=list)
    query: str = ""
    engine: str = ""

    def __post_init__(self) -> None:
        if self.tier not in VERDICT_TIERS:
            raise ValueError(
                f"VerdictResult.tier must be one of {sorted(VERDICT_TIERS)}, "
                f"got {self.tier!r}"
            )


# ---------------------------------------------------------------------------
# carry_verdict -- last step of every emit path
# ---------------------------------------------------------------------------


def carry_verdict(
    result: VerdictResult,
    *,
    share: float | None = None,
) -> dict[str, object]:
    """Return the event-payload dict for a ``citation.observed`` event.

    This is the **last step of every emit path**.  ``share`` (if provided) is
    rounded to 6 decimal places before inclusion (D3/D10 float-tiebreak
    invariant).  The payload contains only bounded, safe-to-persist fields
    (no raw LLM content, D8).
    """
    payload: dict[str, object] = {
        "verdict": result.tier,
        "brand_mentioned": result.brand_mentioned,
        "credited_urls": list(result.credited_urls),
        "uncredited_urls": list(result.uncredited_urls),
        "possibly_cited_unresolved": list(result.possibly_cited_unresolved),
        "engine": result.engine,
        "query": result.query,
    }
    if share is not None:
        payload["share"] = round(share, 6)
    return payload


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


def classify_verdict(
    result: ProbeResult,
    *,
    target_url: str,
    published_article_urls: frozenset[str],
    brand_aliases: list[str] | None = None,
    query: str = "",
    engine: str = "",
) -> VerdictResult:
    """Classify a :class:`ProbeResult` into a :class:`VerdictResult`.

    Parameters
    ----------
    result:
        The raw probe outcome from the engine adapter.
    target_url:
        The site being probed (used for host-match credit).
    published_article_urls:
        Canonical article URLs (from ``geo.joins.build_published_article_set``).
        Used for article-level credit.
    brand_aliases:
        List of brand name strings to check for ``brand_mentioned``.  If
        ``None`` or empty, ``brand_mentioned`` is always ``False`` (inert).
        Missing alias list is warned once in the U7 dry-run, not here.
    query:
        The probe query string (carried through to the result payload).
    engine:
        The engine name (e.g. ``"perplexity"``).
    """
    # 1. Refused: engine refused OR outcome == "refused" OR refusal phrasing.
    if result.outcome == "refused" or (
        result.answer_text and _is_refusal_text(result.answer_text)
    ):
        return VerdictResult(
            tier="refused",
            brand_mentioned=False,
            query=query,
            engine=engine,
        )

    # 2. Evaluate creditable URLs via the credit gate.
    target_host = _canonical_host(target_url)

    credited: list[str] = []
    uncredited: list[str] = []
    unresolved: list[str] = []

    for raw_url in result.source_urls:
        _classify_url(
            raw_url=raw_url,
            target_host=target_host,
            published_article_urls=published_article_urls,
            credited=credited,
            uncredited=uncredited,
            unresolved=unresolved,
        )

    # Determine whether we hit site and/or article tiers.
    site_hit = False
    article_hit = False
    for url in credited:
        canon_host = _canonical_host(url)
        if canon_host == target_host:
            site_hit = True
        try:
            canon = canonicalize_url(url)
        except Exception:
            canon = url
        if canon in published_article_urls:
            article_hit = True

    # 3. Determine tier: site_cited > article_cited > absent.
    if site_hit:
        tier = "site_cited"
    elif article_hit:
        tier = "article_cited"
    else:
        # Non-empty answer OR empty answer -- both are "absent" when zero credits.
        tier = "absent"

    # 4. Brand-mention check (independent, locale-aware word-boundary match).
    brand_mentioned = _check_brand_mentioned(result.answer_text, brand_aliases)

    return VerdictResult(
        tier=tier,
        brand_mentioned=brand_mentioned,
        credited_urls=credited,
        uncredited_urls=uncredited,
        possibly_cited_unresolved=unresolved,
        query=query,
        engine=engine,
    )


# ---------------------------------------------------------------------------
# URL credit-gate internals
# ---------------------------------------------------------------------------


def _classify_url(
    *,
    raw_url: str,
    target_host: str | None,
    published_article_urls: frozenset[str],
    credited: list[str],
    uncredited: list[str],
    unresolved: list[str],
) -> None:
    """Classify a single source URL into credited / uncredited / unresolved.

    Mutates the three output lists in place.  No network I/O (D4).
    """
    if not raw_url:
        return

    # Must be http(s).
    try:
        parts = urlsplit(raw_url)
    except Exception:
        uncredited.append(raw_url)
        return
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        uncredited.append(raw_url)
        return

    host = (parts.hostname or "").lower()
    if not host:
        uncredited.append(raw_url)
        return

    # Redirector: move to unresolved (asymmetry-aware, A5).
    if host in REDIRECTOR_HOSTS:
        # Attempt to extract destination and re-classify recursively once.
        dest = _extract_redirect_destination(raw_url)
        if dest:
            # Recurse once (no loop -- dest is from a trusted param).
            _classify_url(
                raw_url=dest,
                target_host=target_host,
                published_article_urls=published_article_urls,
                credited=credited,
                uncredited=uncredited,
                unresolved=unresolved,
            )
        else:
            # Keep the redirector URL visible -- do NOT drop it silently.
            unresolved.append(raw_url)
        return

    # Canonicalize for matching.
    try:
        canon = canonicalize_url(raw_url)
    except Exception:
        uncredited.append(raw_url)
        return

    canon_host = _canonical_host(canon)
    if canon_host is None:
        uncredited.append(raw_url)
        return

    # Site-host match: canonical host must equal target host exactly.
    # A garbled URL that merely string-contains the domain is NOT credited --
    # canonicalize_url must produce a real host equal to target_host.
    if target_host and canon_host == target_host:
        credited.append(canon)
        return

    # Article match: canonical URL must be in the published article set.
    if canon in published_article_urls:
        credited.append(canon)
        return

    # No match -- uncredited.
    uncredited.append(canon)


# ---------------------------------------------------------------------------
# Brand-mention helpers (locale-aware word-boundary, D4)
# ---------------------------------------------------------------------------

# Word-boundary: split on whitespace and common ASCII punctuation.
# We tokenize and check set-containment rather than using \\b (ASCII-only in
# Python's re module).  Unicode letter/digit runs (Cyrillic, Hangul, CJK)
# are left intact as a single token so multi-script brand names survive.
_TOKEN_SPLIT_RE = re.compile(r"[\s\-.,!?;:\"'()\[\]{}<>/\\|@#$%^&*+=~`]+")


def _tokenize(text: str) -> list[str]:
    """Split ``text`` into lowercase tokens suitable for brand matching.

    Handles ASCII, Cyrillic, Hangul by splitting on whitespace and common
    punctuation code-points.  The raw substring check (``alias in text``) is
    explicitly forbidden -- this function enforces the token-boundary contract.
    """
    # Normalize Unicode to NFC so e.g. e+combining-accent == precomposed form.
    text = unicodedata.normalize("NFC", text)
    tokens = _TOKEN_SPLIT_RE.split(text.lower())
    return [t for t in tokens if t]


def _check_brand_mentioned(answer_text: str, brand_aliases: list[str] | None) -> bool:
    """Return True iff at least one alias appears as a complete token in ``answer_text``.

    Word-boundary / token contract: an alias must appear as a whole token, not
    as a substring of another word (e.g. "Ace" must NOT match "place").

    Missing / empty alias list -- always False (inert, no false positive).
    """
    if not brand_aliases or not answer_text:
        return False
    tokens = set(_tokenize(answer_text))
    for alias in brand_aliases:
        if not alias:
            continue
        alias_lower = alias.lower()
        if alias_lower in tokens:
            return True
    return False
