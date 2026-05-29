"""Post-publish link-attribute verifier.

After an article is published to a platform that may strip HTML attributes,
this helper fetches the live page and checks whether ``target="_blank"`` and
``rel`` survived rendering — and, since Plan 2026-05-13-004 Unit 6, whether
the platform silently injected ``rel="nofollow"`` (which collapses dofollow
weight to zero). Designed to run fire-and-forget after publish succeeds —
failures are logged but never surface as publish failures.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence
from urllib.parse import parse_qsl, unquote, urlparse
from urllib.request import Request

from backlink_publisher import http as _http

_A_TAG_RE = re.compile(r"<a\s[^>]*>", re.IGNORECASE)
# Full <a ...>inner</a> element — used only when a caller opts into anchor-text
# capture (recheck anchor-drift). Kept separate from _A_TAG_RE so the default
# opening-tag scan (and its 6 positional callers) is byte-for-byte unchanged.
_A_ELEMENT_RE = re.compile(r"<a\s([^>]*)>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_INNER_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r'\btarget\s*=\s*["\']?_blank["\']?', re.IGNORECASE)
# Capture the value of the rel attribute on a single <a> tag, single or
# double quoted. Used per-tag to tokenise and look for the "nofollow" keyword.
_REL_VALUE_RE = re.compile(
    r'\brel\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE
)
# Capture the href value on a single <a> tag (single/double quoted or bare).
_HREF_VALUE_RE = re.compile(
    r'\bhref\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', re.IGNORECASE
)
# rel tokens that strip dofollow weight.
_NOFOLLOW_TOKENS = frozenset({"nofollow", "ugc", "sponsored"})
# Common redirect-shim query keys whose value is the effective destination.
_INTERSTITIAL_QUERY_KEYS = ("target", "url", "u", "to", "dest", "redirect")


def verify_link_attributes(
    url: str,
    *,
    timeout: float = 10.0,
    target_urls: Optional[list[str]] = None,
) -> dict:
    """Fetch ``url`` and audit ``<a>`` tags for surviving link attributes.

    Returns a plain dict so callers can stash it in ``_provider_meta`` without
    any import coupling. Never raises — on any network or parse error it
    returns a ``verification: skipped`` sentinel instead.

    Return shape (on success):
        {
            "verification": "ok",
            "total_anchors": int,
            "blank_anchors": int,
            "blank_ratio": float,             # blank_anchors / total_anchors or 0.0
            "nofollow_anchors": int,          # count with rel containing "nofollow"
            "nofollow_detected": bool,        # True iff nofollow_anchors > 0
            "nofollow_reason": str | None,    # human-readable warning when detected
            # target-specific fields, present ONLY when target_urls is given
            # (Plan 2026-05-27-006 Unit 1): isolate the operator's OWN required
            # backlink(s) from the page-wide nofollow noise above.
            "target_found": bool,             # every required target present as an anchor
            "target_nofollow": bool,          # any present required target carries nofollow
            "target_rewritten": bool,         # any present target only via interstitial/rewrite
            "target_nofollow_urls": list[str],
            "target_missing_urls": list[str],
            "target_rewritten_urls": list[str],
        }

    Return shape (on failure):
        {
            "verification": "skipped",
            "reason": str,
        }

    Nofollow detection is a defence against silent platform behaviour
    (Medium and similar): a backlink with rel="nofollow" passes zero SEO
    weight even though it renders identically. ``nofollow_detected=True``
    is a warning signal — callers should record it for trend analysis but
    are NOT expected to fail the publish over it.

    ``target_urls`` (Unit 1, no new fetch): when the caller passes the row's
    required backlink URLs, the same parsed anchors are inspected for the
    operator's *own* links so a per-target drift signal (nofollow / rewritten /
    stripped) can be distinguished from the page-wide ``nofollow_detected``
    (which trips on any footer/nav/share nofollow). Omitting it (``None`` /
    empty) yields the exact pre-Unit-1 return shape (back-compat for the
    page-wide consumers).
    """
    try:
        resp = _http.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "backlink-publisher-verifier/0.1"},
        )
    except Exception as exc:
        return {"verification": "skipped", "reason": str(exc)}

    if not resp.ok:
        return {
            "verification": "skipped",
            "reason": f"HTTP {resp.status_code}",
        }

    html = resp.text
    tags = _A_TAG_RE.findall(html)
    total = len(tags)
    blank = sum(1 for t in tags if _BLANK_RE.search(t))
    nofollow = sum(1 for t in tags if _tag_has_nofollow(t))

    result: dict = {
        "verification": "ok",
        "total_anchors": total,
        "blank_anchors": blank,
        "blank_ratio": blank / total if total else 0.0,
        "nofollow_anchors": nofollow,
        "nofollow_detected": nofollow > 0,
        "nofollow_reason": None,
    }
    if nofollow > 0:
        result["nofollow_reason"] = (
            f"platform injected rel=nofollow on {nofollow}/{total} anchor(s); "
            "dofollow weight transfer is zero — check the publish adapter or "
            "the target platform's link policy"
        )
    if target_urls:
        target_fields = _target_verdicts(tags, target_urls)
        if target_fields is not None:
            result.update(target_fields)
    return result


def _target_verdicts(tags: list[str], target_urls: list[str]) -> Optional[dict]:
    """Classify the operator's OWN required backlink(s) against the page's
    already-parsed ``<a>`` ``tags`` (Unit 1 — no extra fetch).

    For each required target, scan the anchors and decide present / nofollow /
    rewritten by the same canonicalize-and-unwrap-interstitial logic as
    :func:`inspect_target_anchor` (a target reachable only through a redirect
    shim counts as *rewritten*; a target with no matching anchor at all is
    *missing*). A target is ``nofollow`` only when *every* matching anchor
    strips weight (a single surviving dofollow instance ⇒ dofollow), keeping the
    signal false-positive-resistant.

    Returns the aggregate ``target_*`` fields (OR-ed across the required links),
    or ``None`` when no target canonicalizes (nothing checkable → caller adds no
    target fields, identical to the back-compat path)."""
    # (original_url, canonical) for each canonicalizable required target.
    checkable: list[tuple[str, str]] = []
    for raw in target_urls:
        canon = _canonicalize_for_match(raw)
        if canon:
            checkable.append((raw, canon))
    if not checkable:
        return None

    # Pre-parse anchors once into (direct_canonical, effective_canonical, nofollow).
    parsed: list[tuple[Optional[str], Optional[str], bool]] = []
    for tag in tags:
        href = _tag_href(tag)
        if not href:
            continue
        direct = _canonicalize_for_match(href)
        effective = _canonicalize_for_match(_unwrap_interstitial(href))
        parsed.append((direct, effective, _rel_is_nofollow(_tag_rel(tag))))

    nofollow_urls: list[str] = []
    missing_urls: list[str] = []
    rewritten_urls: list[str] = []
    all_found = True

    for raw, canon in checkable:
        direct_match = False
        has_dofollow = False
        has_nofollow = False
        for direct, effective, is_nf in parsed:
            if direct == canon or effective == canon:
                if direct == canon:
                    direct_match = True
                if is_nf:
                    has_nofollow = True
                else:
                    has_dofollow = True
        found = has_dofollow or has_nofollow
        if not found:
            all_found = False
            missing_urls.append(raw)
            continue
        # Present only via an interstitial/redirect shim → rewritten.
        if not direct_match:
            rewritten_urls.append(raw)
        # nofollow only if NO surviving dofollow instance.
        if not has_dofollow:
            nofollow_urls.append(raw)

    return {
        "target_found": all_found,
        "target_nofollow": bool(nofollow_urls),
        "target_rewritten": bool(rewritten_urls),
        "target_nofollow_urls": nofollow_urls,
        "target_missing_urls": missing_urls,
        "target_rewritten_urls": rewritten_urls,
    }


def required_link_urls(payload: dict) -> list[str]:
    """Extract the operator's REQUIRED backlink URLs from a publish ``payload``.

    The publish payload is ``{**row, "platform": ...}`` (see
    ``cli/publish_backlinks.py``), so the row's ``links`` list rides along.
    Mirrors the canonical extraction the CLI verifier uses
    (``cli/_publish_helpers.py`` ``_do_verify``):
    ``[lnk["url"] for lnk in row.get("links", []) if lnk.get("required")]`` —
    deliberately NOT the single top-level ``target_url`` (Plan Scope). Returns
    ``[]`` when absent (the caller then adds no target-specific fields, keeping
    the back-compat page-wide-only shape). Never raises."""
    links = payload.get("links")
    if not isinstance(links, list):
        return []
    urls: list[str] = []
    for lnk in links:
        if isinstance(lnk, dict) and lnk.get("required") and lnk.get("url"):
            urls.append(str(lnk["url"]))
    return urls


def _tag_has_nofollow(tag_html: str) -> bool:
    """True iff the rel attribute on ``tag_html`` contains the literal token
    ``nofollow`` (case-insensitive, whitespace-tokenised — so ``nofollowed``
    or ``not-nofollow`` do NOT trigger)."""
    match = _REL_VALUE_RE.search(tag_html)
    if not match:
        return False
    tokens = match.group(1).lower().split()
    return "nofollow" in tokens


def _tag_href(tag_html: str) -> Optional[str]:
    """Return the ``href`` value on a single ``<a>`` tag, or ``None``."""
    m = _HREF_VALUE_RE.search(tag_html)
    if not m:
        return None
    return m.group(1) or m.group(2) or m.group(3)


def _tag_rel(tag_html: str) -> Optional[str]:
    """Return the raw ``rel`` attribute value on ``tag_html``, or ``None``."""
    m = _REL_VALUE_RE.search(tag_html)
    return m.group(1) if m else None


def _rel_is_nofollow(rel_value: Optional[str]) -> bool:
    """True iff ``rel_value`` carries a weight-stripping token (nofollow / ugc /
    sponsored), whitespace-tokenised (so ``nofollowed`` does NOT trigger)."""
    if not rel_value:
        return False
    return bool(_NOFOLLOW_TOKENS.intersection(rel_value.lower().split()))


def _unwrap_interstitial(href: str) -> str:
    """Decode a redirect-shim href to its effective destination.

    Platforms wrap outbound links through interstitials such as
    ``https://link.example.com/?target=https%3A%2F%2Fexample.com``. We extract the
    first query param (``target``/``url``/...) whose decoded value parses as an
    absolute http(s) URL and treat that as the effective href. If nothing looks
    like a wrapped URL, the original href is returned unchanged.

    Never raises — a malformed href (e.g. malformed IPv6) returns the input.
    """
    if not href:
        return href
    try:
        parsed = urlparse(href)
    except ValueError:
        return href
    if not parsed.query:
        return href
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return href
    lowered = {k.lower(): v for k, v in pairs}
    for key in _INTERSTITIAL_QUERY_KEYS:
        raw = lowered.get(key)
        if not raw:
            continue
        candidate = unquote(raw)
        try:
            cand_parsed = urlparse(candidate)
        except ValueError:
            continue
        if cand_parsed.scheme in {"http", "https"} and cand_parsed.netloc:
            return candidate
    return href


def inspect_target_anchor(
    url: str,
    target_url: str,
    *,
    expected_marker: Optional[str] = None,
    timeout: Optional[float] = None,
    capture_anchor_text: bool = False,
) -> dict:
    """Fetch ``url`` (SSRF-guarded) and inspect the anchor pointing at
    ``target_url``.

    A canary-oriented sibling of :func:`verify_link_attributes`. Unlike that
    function — which is called positionally by 6 post-publish callers and must
    keep its existing ``backlink_publisher.http.get`` fetch / timeout / redirect
    semantics — this routine fetches through the preflight SSRF-guarded opener
    (``content._preflight_fetch._PREFLIGHT_OPENER``), inheriting per-hop and
    post-redirect SSRF re-checks. It does NOT use a page-wide ``nofollow``
    aggregate as a drift signal; it reads the *target anchor's own* ``rel``.

    Never raises. Return shape::

        {
            "page_readable": bool,        # 200 + non-empty parseable body
            "marker_present": bool|None,  # None unless expected_marker given
            "target_anchor_found": bool,
            "target_rel": str|None,       # raw rel of the matched anchor
            "target_is_nofollow": bool,   # matched anchor strips dofollow weight
            "target_anchor_text": str|None,  # inner text of matched anchor; only
                                          # populated when capture_anchor_text=True
            "reason": str|None,           # taxonomy string on any non-OK path
        }

    ``capture_anchor_text`` (default False) opts into returning the matched
    anchor's normalized inner text for anchor-drift comparison (recheck). It
    switches the scan to the full ``<a>...</a>`` element regex; default callers
    keep the unchanged opening-tag scan.

    Honest limitation (R13): the preflight UA is distinct so the target can rate
    limit it separately, but a platform could UA-cloak (serve dofollow to the
    canary, nofollow to real traffic). The verdict is a *contract-drift signal*,
    not a guarantee of what a real visitor/crawler sees.
    """
    # Import lazily so the 6 post-publish callers of verify_link_attributes do
    # not pay the preflight import cost, and to reuse its opener/SSRF helpers.
    from backlink_publisher.content import _preflight_fetch as _pf

    result: dict = {
        "page_readable": False,
        "marker_present": None,
        "target_anchor_found": False,
        "target_rel": None,
        "target_is_nofollow": False,
        "target_anchor_text": None,
        "reason": None,
    }

    body, reason = _fetch_body_via_preflight(url, _pf, timeout)
    if reason is not None:
        result["reason"] = reason
        return result
    if not body:
        result["reason"] = "empty_body"
        return result

    result["page_readable"] = True
    text = body.decode("utf-8", "ignore")

    if expected_marker is not None:
        result["marker_present"] = expected_marker in text

    try:
        canonical_target = _canonicalize_for_match(target_url)
    except Exception:  # noqa: BLE001 — never-raise contract
        canonical_target = None

    # Distinguish "target_url itself is malformed/uncanonicalizable" from
    # "target anchor genuinely absent": a non-empty target that won't canonicalize
    # is indeterminate (the recheck maps it to probe_error), not a stripped link.
    if target_url and not canonical_target:
        result["reason"] = "target_uncanonicalizable"

    if canonical_target:
        if capture_anchor_text:
            tag_iter = (
                ("<a " + m.group(1) + ">", m.group(2))
                for m in _A_ELEMENT_RE.finditer(text)
            )
        else:
            tag_iter = ((tag, None) for tag in _A_TAG_RE.findall(text))
        for tag, inner in tag_iter:
            href = _tag_href(tag)
            if not href:
                continue
            effective = _unwrap_interstitial(href)
            try:
                if _canonicalize_for_match(effective) != canonical_target:
                    continue
            except Exception:  # noqa: BLE001
                continue
            rel = _tag_rel(tag)
            result["target_anchor_found"] = True
            result["target_rel"] = rel
            result["target_is_nofollow"] = _rel_is_nofollow(rel)
            if capture_anchor_text and inner is not None:
                result["target_anchor_text"] = _normalize_anchor_text(inner)
            # First match wins — deterministic and "at least one dofollow exists"
            # when the matched anchor is dofollow.
            break

    return result


def _normalize_anchor_text(inner_html: str) -> str:
    """Strip nested tags and collapse whitespace for anchor-text comparison."""
    return " ".join(_INNER_TAG_RE.sub(" ", inner_html).split())


def _canonicalize_for_match(href: str) -> Optional[str]:
    """Canonicalize ``href`` for target comparison, never raising."""
    from backlink_publisher._util.url import canonicalize_url

    if not href:
        return None
    try:
        return canonicalize_url(href)
    except Exception:  # noqa: BLE001
        return None


def body_has_required_link(body: str, required_urls: Sequence[str]) -> bool:
    """True iff at least one required URL is present in ``body`` as a backlink.

    The publish-gate sibling of a naive ``url in body`` substring scan. Platforms
    such as LiveJournal rewrite every outbound ``<a href>`` through a redirect
    shim (``https://www.livejournal.com/away?to=<url-encoded-target>``), so the
    verbatim target string never appears in the body and a substring scan
    false-negatives a backlink that is genuinely live. This reuses the SAME
    unwrap-interstitial + canonicalize logic as the dofollow canary
    (:func:`_target_verdicts`) so the publish gate and the canary can never
    diverge on what counts as "the backlink is present".

    Matching is OR-across-required (mirrors the gate contract: at least one
    required link present). An empty ``required_urls`` is vacuously satisfied.
    """
    if not required_urls:
        return True
    # Fast path: verbatim substring. Purely additive — anything that passed the
    # old naive substring check still passes here, so no adapter regresses.
    if any(u in body for u in required_urls):
        return True
    wanted = {c for c in (_canonicalize_for_match(u) for u in required_urls) if c}
    if not wanted:
        return False
    for tag in _A_TAG_RE.findall(body):
        href = _tag_href(tag)
        if not href:
            continue
        if _canonicalize_for_match(href) in wanted:
            return True
        if _canonicalize_for_match(_unwrap_interstitial(href)) in wanted:
            return True
    return False


def _fetch_body_via_preflight(url, _pf, timeout) -> tuple[bytes, Optional[str]]:
    """Fetch ``url`` through the preflight SSRF-guarded opener.

    Reuses ``_preflight_fetch``'s scheme gate (``_is_http_url``), never-raising
    SSRF check (``_safe_ssrf_check``), the module-level ``_PREFLIGHT_OPENER``
    (per-hop + post-redirect SSRF re-check), body-prefix streaming cap, and UA —
    so building a fresh opener (which would only check the initial URL) is
    avoided. Returns ``(body, None)`` on a clean 200 or ``(b"", reason)``.
    """
    # Scheme gate — also guards urlparse(malformed IPv6) → ValueError.
    if not _pf._is_http_url(url):
        return b"", "invalid_url"

    blocked = _pf._safe_ssrf_check(url)
    if blocked is not None:
        return b"", _pf._ssrf_reason_to_taxonomy(blocked)

    from backlink_publisher._util.url import normalize_url_for_fetch

    normalized = normalize_url_for_fetch(url)
    req = Request(normalized, method="GET")
    req.add_header("User-Agent", _pf.USER_AGENT)
    effective_timeout = timeout if timeout is not None else _pf.FETCH_TIMEOUT

    try:
        resp = _pf._PREFLIGHT_OPENER.open(req, timeout=effective_timeout)
    except Exception as exc:  # noqa: BLE001 — never-raise; classify generically
        return b"", _classify_fetch_error(exc)

    try:
        status = resp.getcode()
        final_url = resp.geturl() or normalized
        try:
            body = _pf._read_body_prefix(resp, _pf.PREFLIGHT_BODY_BYTES)
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        return b"", "network_error"

    # Post-redirect SSRF re-check of the final URL (narrows DNS-rebinding window).
    if final_url and final_url != normalized:
        final_blocked = _pf._safe_ssrf_check(final_url)
        if final_blocked is not None and final_blocked.startswith("blocked_ip"):
            return b"", "ssrf_blocked"

    if status != 200:
        return b"", f"http_{status}"
    return body, None


def _classify_fetch_error(exc: Exception) -> str:
    """Map an opener exception to a stable reason string. Never raises."""
    from urllib.error import URLError

    reason_obj = getattr(exc, "reason", None)
    if isinstance(reason_obj, str) and reason_obj.startswith("ssrf_"):
        return "ssrf_blocked"
    if isinstance(exc, URLError) and isinstance(getattr(exc, "reason", None), str) \
            and "ssrf" in exc.reason:
        return "ssrf_blocked"
    return "network_error"
