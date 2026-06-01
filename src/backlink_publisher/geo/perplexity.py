"""Perplexity citation-probe adapter (Plan 2026-05-29-006 Unit 3).

v1 GEO engine: POST an OpenAI-compatible ``/chat/completions`` request to
Perplexity and parse the answer text + cited source URLs into a
:class:`ProbeResult`. Two contracts the caller depends on:

1. **Credential guard chain BEFORE any network call (D9).** A hostile or
   misconfigured ``base_url`` would exfiltrate the operator's GEO Bearer key.
   We reuse ``generate-backlink-text``'s exact chain: reject endpoint userinfo
   (``user:secret@host``) → normalize the base so the *gated* string equals the
   *connected* string → ``guard_llm_endpoint`` (scheme → allowlist → SSRF).
   Only then is the key sent. A non-allowlisted / userinfo-bearing ``base_url``
   is rejected (``DependencyError``) before the request is built.

2. **Never raises mid-batch on response shape.** Parse failures (missing /
   malformed citations, empty content, oversize body) become structured
   ``absent`` / ``parse_error`` outcomes with ``raw_response`` kept in memory
   for debugging (D8) — they NEVER raise. Only the two error families raise:
   auth (4xx) → :class:`DependencyError` (operator must fix the key); rate-
   limit / server (429 / 5xx) → :class:`ExternalServiceError` (transient,
   retry later). A recognizable refusal answer is ``outcome="refused"`` — not
   an error. All error text is routed through ``_redact_for_log`` so the Bearer
   key is never logged.

**HTTP helper choice (review F3): ``safe_post_json``.** It bundles exactly the
transport hardening a Bearer-carrying probe needs and that ``http_post`` does
NOT give for free: ``allow_redirects=False`` (the SSRF/allowlist gate is
one-shot at input — following a 3xx would re-issue the request, *with the
Authorization header*, to an attacker-chosen Location, defeating the gate),
3xx-reject, content-type enforcement (a CDN/WAF HTML error page is rejected
before ``json.loads``), and a 64 KB streamed-read cap (OOM defense). Its
failure modes are ``ValueError`` (redirect / bad-content-type /
``response_too_large`` — F4) and ``requests.RequestException`` (network). We
map ``ValueError`` → structured ``parse_error`` (never propagate), and a
network ``RequestException`` → transient ``ExternalServiceError``.

**probe-then-pivot (deferred):** the EXACT Perplexity citations field path is
UNVERIFIED against the live endpoint. We parse defensively across the common
shapes (top-level ``citations`` list; ``choices[0].message`` annotations /
``search_results``) and pin the chosen parsing with fixture-backed tests.
*** The field path MUST be confirmed against the live endpoint before this is
trusted in production (probe-then-pivot learning). ***
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import requests

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.llm.client import _redact_for_log
from backlink_publisher.llm.http_guard import guard_llm_endpoint, safe_post_json

from .engines import ProbeResult

_log = logging.getLogger(__name__)

#: Locale-dependent refusal phrasing (probe-then-pivot: tune against real
#: en/ru/ko responses — see the U4 refusal-spike). Matched case-insensitively
#: as a substring of the answer text. This is a heuristic, intentionally
#: conservative: a false negative degrades a refusal to ``absent`` (still not an
#: error), a false positive only mislabels an answered probe as refused.
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


def _normalize_base(base_url: str) -> str:
    """Reject userinfo, then strip a trailing ``/chat/completions`` suffix.

    The string returned here is BOTH the string passed to ``guard_llm_endpoint``
    AND the prefix the client POSTs to — they must be identical so the gate
    cannot be sidestepped by normalization drift (D9).

    Raises :class:`DependencyError` on userinfo or a malformed URL — both are
    "operator must fix the config" conditions (exit 3), and both are caught
    BEFORE any request is built so the Bearer key is never sent.
    """
    try:
        parsed = urlparse(base_url)
    except ValueError as exc:
        raise DependencyError(
            f"geo-probe (perplexity): malformed base_url: {exc}"
        ) from exc
    # Userinfo (user:secret@host) bypasses _redact_for_log and exposes the
    # credential in `ps` / logs; reject it outright.
    if parsed.username or parsed.password:
        raise DependencyError(
            "geo-probe (perplexity): base_url must not contain userinfo "
            "(user:password@host leaks credentials in process listings and "
            "logs). Provide the bare base URL, e.g. https://api.perplexity.ai"
        )
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")].rstrip("/")
    return base


def _guarded_base(cfg: GeoProbeConfig) -> str:
    """Run the full pre-call guard chain; return the gated/connected base.

    Order (D9): normalize (userinfo reject) → ``guard_llm_endpoint``
    (scheme → ``is_allowlisted`` → SSRF). Any rejection here is raised as a
    :class:`DependencyError` BEFORE the POST is constructed, so a non-
    allowlisted or userinfo-bearing ``base_url`` never receives the Bearer key.
    """
    base = _normalize_base(cfg.base_url)
    try:
        rejection_reason, detail = guard_llm_endpoint(base)
    except ValueError as exc:
        # urlparse ValueError on malformed IPv6 inside the guard.
        raise DependencyError(
            f"geo-probe (perplexity): base_url rejected (malformed): {exc}"
        ) from exc
    if rejection_reason is not None:
        raise DependencyError(
            f"geo-probe (perplexity): base_url rejected "
            f"({rejection_reason}): {detail}"
        )
    return base


def probe_perplexity(query: str, cfg: GeoProbeConfig) -> ProbeResult:
    """Probe Perplexity with ``query``; return a structured :class:`ProbeResult`.

    Guard chain runs first (D9) — a bad ``base_url`` raises ``DependencyError``
    before the network. After a successful POST, every response shape maps to a
    structured outcome and never raises; only auth (4xx) and transient
    (429/5xx) HTTP families raise (typed errors), and a refusal answer is
    ``outcome="refused"`` (not an error).
    """
    base = _guarded_base(cfg)

    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg.model,
        "messages": [{"role": "user", "content": query}],
    }

    try:
        status, data = safe_post_json(
            url, headers, payload, timeout=int(cfg.timeout_s)
        )
    except ValueError as exc:
        # safe_post_json transport-hardening rejections: redirect /
        # bad_content_type / response_too_large (F4) / JSON parse error. None of
        # these are auth or service-state signals — they mean "we could not read
        # a usable answer". Map to a structured parse_error, keep the redacted
        # reason in raw_response for debugging (in-memory only, D8), NEVER raise.
        reason = _redact_for_log(str(exc))
        _log.warning("geo-probe (perplexity): unreadable response: %s", reason)
        return ProbeResult(
            answer_text="",
            source_urls=[],
            raw_response={"parse_error": reason},
            outcome="parse_error",
        )
    except requests.RequestException as exc:
        # Network failure (DNS, connect, timeout, read). The probe is read-only,
        # so this is transient — surface as ExternalServiceError (exit 4) so a
        # later run can retry. Redact in case the URL/userinfo echoed back.
        raise ExternalServiceError(
            f"geo-probe (perplexity): network failure: "
            f"{_redact_for_log(str(exc))}"
        ) from exc

    # ── HTTP status families (typed errors) ──────────────────────────────────
    if status == 429 or 500 <= status < 600:
        # Rate-limit / server error: transient. The probe is read-only so a
        # retry is safe (D11 re-probes, never re-appends).
        raise ExternalServiceError(
            f"geo-probe (perplexity): transient HTTP {status}: "
            f"{_redact_for_log(_summarize(data))}"
        )
    if status >= 400:
        # 4xx (auth / bad request): the operator must fix the credential/config.
        raise DependencyError(
            f"geo-probe (perplexity): HTTP {status}: "
            f"{_redact_for_log(_summarize(data))}"
        )

    # ── 2xx: parse defensively, never raise ──────────────────────────────────
    return _parse_response(data)


def _summarize(data: Any) -> str:
    """Stringify a parsed body for an error message (pre-redaction)."""
    try:
        return str(data)
    except Exception:  # pragma: no cover - defensive
        return "<unstringifiable response>"


def _extract_answer_text(data: Any) -> str:
    """Pull the answer text from ``choices[0].message.content``; "" if absent."""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(content, str):
        return ""
    return content


def _extract_source_urls(data: Any) -> list[str]:
    """Extract cited source URLs, parsing defensively across common shapes.

    *** probe-then-pivot: the EXACT field path is UNVERIFIED against the live
    Perplexity endpoint. Confirm before trusting in production. *** We try, in
    order: a top-level ``citations`` list (the most-reported Perplexity shape);
    a top-level ``search_results`` list of objects carrying ``url``; and
    ``choices[0].message`` annotations / ``citations`` (OpenAI-annotation
    style). Anything unrecognized yields ``[]`` (→ ``absent``), never a raise.
    """
    urls: list[str] = []
    if not isinstance(data, dict):
        return urls

    # Shape 1: top-level "citations": ["https://...", ...] or [{"url": ...}].
    urls.extend(_coerce_url_list(data.get("citations")))

    # Shape 2: top-level "search_results": [{"url": ...}, ...].
    urls.extend(_coerce_url_list(data.get("search_results")))

    # Shape 3: per-message annotations / citations on choices[0].message.
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        message = None
    if isinstance(message, dict):
        urls.extend(_coerce_url_list(message.get("citations")))
        urls.extend(_coerce_url_list(message.get("annotations")))

    # De-dup preserving order; the credit gate (U5) does the real validation.
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _coerce_url_list(value: Any) -> list[str]:
    """Coerce a citations-shaped field into a list of URL strings.

    Accepts a list of plain strings or a list of objects carrying a ``url`` /
    ``link`` key (and tolerates an annotation wrapper that nests the URL under
    ``url_citation``). Non-list / unrecognized input → ``[]``.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            if item:
                out.append(item)
        elif isinstance(item, dict):
            url = item.get("url") or item.get("link")
            if not url:
                nested = item.get("url_citation")
                if isinstance(nested, dict):
                    url = nested.get("url")
            if isinstance(url, str) and url:
                out.append(url)
    return out


def _parse_response(data: Any) -> ProbeResult:
    """Map a 2xx parsed body to a structured :class:`ProbeResult` (never raises).

    - refusal phrasing in the answer → ``refused`` (not an error)
    - answer + parsed source URLs → ``ok``
    - empty content / answered with no creditable URLs → ``absent``
    - body present but not the expected dict envelope → ``parse_error``
    """
    if not isinstance(data, dict):
        return ProbeResult(
            answer_text="",
            source_urls=[],
            raw_response=data,
            outcome="parse_error",
        )

    answer = _extract_answer_text(data)
    urls = _extract_source_urls(data)

    if answer and _is_refusal(answer):
        return ProbeResult(
            answer_text=answer,
            source_urls=urls,
            raw_response=data,
            outcome="refused",
        )

    if not answer:
        # Empty content: the engine returned nothing usable.
        return ProbeResult(
            answer_text="",
            source_urls=urls,
            raw_response=data,
            outcome="absent",
        )

    if urls:
        return ProbeResult(
            answer_text=answer,
            source_urls=urls,
            raw_response=data,
            outcome="ok",
        )

    # Answered but cited nothing creditable — absent for the north star (D3);
    # the credit gate (U5) decides tiers, this just records "no citations".
    return ProbeResult(
        answer_text=answer,
        source_urls=[],
        raw_response=data,
        outcome="absent",
    )


def _is_refusal(answer: str) -> bool:
    low = answer.lower()
    return any(marker in low for marker in _REFUSAL_MARKERS)
