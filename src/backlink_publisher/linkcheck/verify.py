"""Post-publish verification: confirms a published URL actually contains the expected content.

Defends against fake-publish bugs where an adapter returns a fabricated URL
while no real post was created (documented failure class from prior opencli adapter).

Verification steps:
  1. HTTP GET the published/draft URL → must return 200
  2. Title substring present in response body (case-insensitive)
  3. At least one required link URL appears in response body

On failure: returns (False, reason). Caller marks the output as
``published_unverified`` / ``drafted_unverified`` rather than failing the item.

For Medium: indexing lag may delay content availability. The caller should
pass ``max_wait`` (default 30s) to allow polling with backoff.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Sequence
from urllib.request import Request, urlopen
import ssl

from backlink_publisher._util.url import normalize_url_for_fetch

_VERIFY_TIMEOUT = 12  # seconds per individual request
_RETRY_INTERVAL = 6   # seconds between poll attempts
_USER_AGENT = "backlink-publisher/1.0 verify"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


@dataclass
class VerificationResult:
    ok: bool
    reason: str  # empty string when ok=True


def _get_body(url: str) -> tuple[int, str]:
    """Fetch URL body. Returns (status_code, body_text). Never raises."""
    try:
        # Velog Korean @username + CJK url_slug are legal upstream but crash
        # urllib's ASCII request-line encoder. See Plan 2026-05-21-005.
        req = Request(normalize_url_for_fetch(url))
        req.add_header("User-Agent", _USER_AGENT)
        with urlopen(req, timeout=_VERIFY_TIMEOUT, context=_SSL_CTX) as resp:
            code = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
        return code, body
    except Exception as exc:
        return 0, str(exc)


def _title_in_body(title: str, body: str) -> bool:
    if not title:
        return True
    # Use first 40 non-whitespace chars of title for a stable fingerprint
    snippet = title[:40].strip()
    return snippet.lower() in body.lower()


def _link_in_body(link_urls: Sequence[str], body: str) -> bool:
    if not link_urls:
        return True
    # Interstitial-aware match: platforms like LiveJournal rewrite every outbound
    # <a href> through /away?to=<url-encoded-target>, so a verbatim substring scan
    # false-negatives a backlink that is genuinely live (the post and link exist,
    # but the URL only appears wrapped + percent-encoded). Delegate to the shared
    # verifier helper — same unwrap + canonicalize logic as the dofollow canary —
    # so the publish gate and the canary never diverge. Lazy import keeps linkcheck
    # free of a module-load dependency on publishing.adapters.
    from backlink_publisher.publishing.adapters.link_attr_verifier import (
        body_has_required_link,
    )

    return body_has_required_link(body, link_urls)


def verify_published(
    url: str,
    title: str,
    required_link_urls: Sequence[str],
    max_wait: int = 30,
) -> VerificationResult:
    """Verify that a published URL contains the expected content.

    Polls up to ``max_wait`` seconds to accommodate platform indexing lag.
    Returns immediately on first success.
    """
    if not url or not url.startswith(("http://", "https://")):
        return VerificationResult(ok=False, reason=f"no valid URL to verify: {url!r}")

    deadline = time.monotonic() + max_wait
    attempt = 0
    last_reason = "unknown"

    while True:
        attempt += 1
        status_code, body = _get_body(url)

        if status_code == 0:
            last_reason = f"fetch failed: {body}"
        elif status_code != 200:
            last_reason = f"HTTP {status_code}"
        elif not _title_in_body(title, body):
            last_reason = f"title not found in response body (title: {title[:40]!r})"
        elif not _link_in_body(required_link_urls, body):
            last_reason = "required links not found in body"
        else:
            return VerificationResult(ok=True, reason="")

        if time.monotonic() >= deadline:
            return VerificationResult(
                ok=False,
                reason=f"verification failed after {attempt} attempt(s): {last_reason}",
            )

        time.sleep(_RETRY_INTERVAL)
