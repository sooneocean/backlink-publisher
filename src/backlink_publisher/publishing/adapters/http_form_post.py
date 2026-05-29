"""Pure-HTTP form-POST publishing helpers (Plan 2026-05-25-001 Unit 4).

**Free functions, NOT a base class.** No-login, credential-less form publishing
(txt.fyi and similar). Each platform adapter is its own ``Publisher`` subclass
that *composes* these helpers and owns its CSRF/field-name/return-URL parsing —
mirroring ``instant_web.py``'s helper-not-base precedent (two no-login adapters
share transport plumbing while each keeps an independent ``publish()``). An
adapter whose archetype turns out to be an account/API path simply does not
import this module, so it is never forced into a form-POST lifecycle.

Transport contract:
- ``fetch_form`` GETs the form page; raises ``AntiBotChallengeError`` on a
  blocking interstitial, ``ExternalServiceError`` on transport/HTTP failure.
- ``extract_hidden_fields`` pulls named input values (CSRF nonce, timestamps)
  out of the fetched HTML so the POST can echo them back.
- ``submit_form`` POSTs form data EXACTLY ONCE — the create-POST is
  non-idempotent, so it is never retried (see the function docstring); same
  challenge / error mapping as ``fetch_form``.
- ``detect_challenge`` is the shared classifier — deliberately ignores the
  Cloudflare ``challenge-platform`` *beacon* script (CF injects it on normally
  served 200 pages, e.g. txt.fyi's live form) so a real publish form is not
  mis-flagged as a challenge.

Anti-bot challenges propagate as ``AntiBotChallengeError`` (an
``ExternalServiceError``), never ``DependencyError`` — credential-less adapters
are a single-entry dispatch chain, so a ``DependencyError`` would be re-raised
verbatim and look like "platform not configured" (see the error class docstring).

No POST body, form-field value, or response HTML is ever placed in an exception
message or log line — only the request host.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

from backlink_publisher._util.errors import AntiBotChallengeError, ExternalServiceError

from .link_attr_verifier import verify_link_attributes

DEFAULT_TIMEOUT: float = 15.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 backlink-publisher"
)

# Strong interstitial markers (lowercased substring match on the response body).
# Deliberately EXCLUDES the bare "challenge-platform" beacon string: Cloudflare
# injects ``/cdn-cgi/challenge-platform/...`` telemetry into normally served
# 200 pages, so matching it would false-positive on a live publish form.
_CHALLENGE_MARKERS: tuple[str, ...] = (
    "just a moment",
    "attention required",
    "checking your browser",
    "verifying you are human",
    "cf-challenge-running",
    "__cf_chl_",
    "cf_chl_opt",
    "turnstile",
    "g-recaptcha",
    "h-captcha",
    "hcaptcha",
)


def _host(url: str) -> str:
    """Best-effort host extraction for safe (body-free) error messages."""
    try:
        return urlparse(url).netloc or "?"
    except Exception:  # pragma: no cover - urlparse is extremely tolerant
        return "?"


def detect_challenge(response: Any) -> bool:
    """Return True iff ``response`` looks like a blocking anti-bot interstitial.

    A challenge is flagged when either:
    - the status is 403/503 AND the response came from an edge proxy
      (``Server: cloudflare``) or carries a strong body marker, or
    - the body carries a strong marker regardless of status (some challenges
      return 200 HTML).

    The Cloudflare beacon script alone (present on legitimate 200 pages) does
    not trigger this.
    """
    status = getattr(response, "status_code", 200)
    body = (getattr(response, "text", "") or "").lower()
    headers = getattr(response, "headers", {}) or {}
    server = str(headers.get("server", "")).lower()

    has_marker = any(marker in body for marker in _CHALLENGE_MARKERS)
    if status in (403, 503) and ("cloudflare" in server or has_marker):
        return True
    return has_marker


def fetch_form(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> requests.Response:
    """GET the form page. Raise on challenge or transport/HTTP failure."""
    try:
        resp = requests.get(
            url, timeout=timeout, headers={"User-Agent": _USER_AGENT}
        )
    except Exception as exc:
        raise ExternalServiceError(
            f"form fetch failed for {_host(url)} ({type(exc).__name__})"
        ) from exc
    if detect_challenge(resp):
        raise AntiBotChallengeError(f"anti-bot challenge on GET {_host(url)}")
    if not (200 <= resp.status_code < 400):
        raise ExternalServiceError(
            f"form fetch {_host(url)} returned HTTP {resp.status_code}"
        )
    return resp


def extract_hidden_fields(html: str, names: Iterable[str]) -> dict[str, str]:
    """Return ``{name: value}`` for each requested ``<input>`` found in ``html``.

    Captures the input's ``value`` attribute (empty string when absent) for any
    input whose ``name`` is in ``names``. Used to echo back anti-CSRF tokens and
    server timestamps (e.g. txt.fyi's ``nonce`` + ``form_time``) on the POST.
    Missing names are simply absent from the result — the caller decides whether
    that is fatal.
    """
    wanted = set(names)
    found: dict[str, str] = {}

    class _InputParser(HTMLParser):
        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag != "input":
                return
            attr_map = dict(attrs)
            name = attr_map.get("name")
            if name in wanted:
                found[name] = attr_map.get("value") or ""

    parser = _InputParser()
    parser.feed(html or "")
    return found


def submit_form(
    url: str,
    data: dict[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> requests.Response:
    """POST ``data`` to ``url`` EXACTLY ONCE — the create-POST is never retried.

    Credential-less form endpoints (txt.fyi and similar) document no idempotency
    key, and a ``Timeout`` / ``ConnectionError`` on a POST is ambiguous: the
    request may already have reached the server and created the post. Retrying
    would publish a DUPLICATE live backlink, so the POST is a single attempt —
    mirroring ``rentry_api``'s "create exactly ONCE" P2 fix and ``retry.py``'s
    5xx-not-retried policy. Any raw network error surfaces as
    ``ExternalServiceError`` (body-free); the resume/dedup machinery then
    decides safely rather than this layer risking a duplicate.

    A challenge raises ``AntiBotChallengeError``; a non-2xx/3xx status raises
    ``ExternalServiceError``.
    """
    try:
        resp = requests.post(
            url,
            data=data,
            timeout=timeout,
            headers={"User-Agent": _USER_AGENT},
            allow_redirects=True,
        )
    except Exception as exc:
        raise ExternalServiceError(
            f"form submit to {_host(url)} failed ({type(exc).__name__})"
        ) from exc
    if detect_challenge(resp):
        raise AntiBotChallengeError(f"anti-bot challenge on POST {_host(url)}")
    if not (200 <= resp.status_code < 400):
        raise ExternalServiceError(
            f"form submit to {_host(url)} returned HTTP {resp.status_code}"
        )
    return resp


def attach_link_verification(
    url: str,
    meta: dict[str, Any] | None = None,
    *,
    target_urls: list[str] | None = None,
) -> dict[str, Any]:
    """Fire-and-forget post-publish link-attribute verification (R4 "measure").

    Runs ``verify_link_attributes`` (which never raises) on the live published
    URL and stores the result under ``meta["link_attr_verification"]``, returning
    the meta dict for attachment to ``AdapterResult._provider_meta``. This is the
    inline pattern medium_api/velog already use, extracted so the new
    credential-less form adapters and the livejournal API adapter reuse one
    call. The verification result is what the R4 two-phase loop later reads to
    amend a platform's ``register(dofollow=...)`` from ``uncertain`` to a measured
    value. Never raises — verification failure must not fail a successful publish.

    ``target_urls`` (Plan 2026-05-27-006 Unit 1): the row's required backlink
    URLs (``required_link_urls(payload)``), threaded to
    :func:`verify_link_attributes` so the verdict gains the target-specific
    forward-path fields for the http_form_post family + livejournal. ``None``
    keeps the page-wide-only shape.
    """
    out = dict(meta) if meta else {}
    if url:
        if target_urls is not None:
            out["link_attr_verification"] = verify_link_attributes(
                url, target_urls=target_urls
            )
        else:
            out["link_attr_verification"] = verify_link_attributes(url)
    return out
