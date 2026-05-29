"""Tests for the Perplexity citation-probe adapter (Plan 2026-05-29-006 Unit 3).

The HTTP seam (``safe_post_json``) is mocked to inject responses — the autouse
conftest socket guard blocks real sockets, so nothing here touches the network.

Coverage (Unit 3 scenarios):
- mocked 200 with citations → ``ProbeResult`` with parsed source URLs (``ok``)
- 200 empty content → ``absent``
- malformed / missing citations → ``absent`` + raw kept (no raise)
- oversize body (``safe_post_json`` ``response_too_large`` ``ValueError``) →
  ``parse_error`` (NOT an unhandled ``ValueError``)
- 4xx auth → ``DependencyError``; 429 / 5xx → transient ``ExternalServiceError``
- refusal text → ``outcome="refused"`` (not an error)
- non-allowlisted base_url AND userinfo base_url rejected BEFORE any HTTP call
  (assert the HTTP mock was NOT called; the Bearer key is never sent)
- ``raw_response`` never leaves memory (no secret-shaped value persists anywhere)

Credential-shaped fixture values are assembled at runtime via concatenation so
the literal ``api_key = "<value>"`` shape never lands in source (leak-check hook).
"""

from __future__ import annotations

import pytest
import requests

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.geo import perplexity as ppx
from backlink_publisher.geo.engines import ProbeResult

# Fake key assembled at runtime (never a source literal). The Bearer header the
# adapter builds will contain this — tests assert it is NOT sent when the guard
# chain rejects the endpoint.
_GEO_KEY = "pk-" + "perplexityfixture"


def _cfg(base_url: str = "https://api.perplexity.ai") -> GeoProbeConfig:
    return GeoProbeConfig(base_url=base_url, api_key=_GEO_KEY, model="sonar")


class _SpyPost:
    """Records calls and returns a canned ``(status, json)`` or raises."""

    def __init__(self, *, status=200, data=None, raises=None):
        self.status = status
        self.data = data if data is not None else {}
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout=10):
        self.calls.append(
            {"url": url, "headers": headers, "payload": payload, "timeout": timeout}
        )
        if self.raises is not None:
            raise self.raises
        return self.status, self.data


def _patch_post(monkeypatch, spy: _SpyPost, *, pass_guard: bool = True) -> _SpyPost:
    """Patch the HTTP seam; by default also stub the endpoint guard to accept.

    The real ``guard_llm_endpoint`` SSRF layer resolves the hostname via DNS,
    which the autouse socket guard blocks. For tests that exercise the
    *post-guard* response handling we stub the guard to accept (``pass_guard``).
    The guard's own reject paths (scheme, userinfo, non-allowlisted) are covered
    by the dedicated rejection tests, which pass ``pass_guard=False`` so the real
    guard runs — and which all reject BEFORE the DNS/SSRF layer is reached.
    """
    monkeypatch.setattr(ppx, "safe_post_json", spy)
    if pass_guard:
        monkeypatch.setattr(ppx, "guard_llm_endpoint", lambda base: (None, None))
    return spy


# ── Happy path: 200 with citations ───────────────────────────────────────────


def test_200_with_top_level_citations_returns_ok(monkeypatch):
    spy = _patch_post(
        monkeypatch,
        _SpyPost(
            data={
                "choices": [{"message": {"content": "example.com is a great site."}}],
                "citations": [
                    "https://example.com/page",
                    "https://other.com/a",
                ],
            }
        ),
    )
    result = ppx.probe_perplexity("rate example.com", _cfg())

    assert isinstance(result, ProbeResult)
    assert result.outcome == "ok"
    assert result.source_urls == [
        "https://example.com/page",
        "https://other.com/a",
    ]
    assert "example.com" in result.answer_text
    # POST went to {base}/chat/completions with the Bearer key.
    assert spy.calls[0]["url"] == "https://api.perplexity.ai/chat/completions"
    assert spy.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_200_with_search_results_shape_parsed(monkeypatch):
    """Defensive parse: ``search_results`` objects carrying ``url`` are extracted."""
    _patch_post(
        monkeypatch,
        _SpyPost(
            data={
                "choices": [{"message": {"content": "answer text"}}],
                "search_results": [
                    {"title": "t", "url": "https://example.com/r"},
                    {"title": "u", "url": "https://b.com/x"},
                ],
            }
        ),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "ok"
    assert result.source_urls == ["https://example.com/r", "https://b.com/x"]


def test_200_with_message_annotations_shape_parsed(monkeypatch):
    """Defensive parse: OpenAI-annotation style url_citation under the message."""
    _patch_post(
        monkeypatch,
        _SpyPost(
            data={
                "choices": [
                    {
                        "message": {
                            "content": "answer",
                            "annotations": [
                                {"url_citation": {"url": "https://example.com/z"}}
                            ],
                        }
                    }
                ]
            }
        ),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "ok"
    assert result.source_urls == ["https://example.com/z"]


# ── absent / parse_error ──────────────────────────────────────────────────────


def test_200_empty_content_is_absent(monkeypatch):
    _patch_post(
        monkeypatch,
        _SpyPost(data={"choices": [{"message": {"content": ""}}], "citations": []}),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "absent"
    assert result.source_urls == []


def test_200_answered_but_no_citations_is_absent(monkeypatch):
    _patch_post(
        monkeypatch,
        _SpyPost(data={"choices": [{"message": {"content": "a plain answer"}}]}),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "absent"
    assert result.answer_text == "a plain answer"
    assert result.source_urls == []


def test_200_malformed_envelope_is_parse_error_with_raw_kept(monkeypatch):
    """A non-dict body (or wholly unexpected shape) → parse_error, raw kept, no raise."""
    _patch_post(monkeypatch, _SpyPost(data=["not", "a", "dict"]))
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "parse_error"
    assert result.raw_response == ["not", "a", "dict"]


def test_200_missing_choices_is_absent_not_raise(monkeypatch):
    """Missing ``choices`` (malformed) → absent, never an exception."""
    _patch_post(monkeypatch, _SpyPost(data={"unexpected": "shape"}))
    result = ppx.probe_perplexity("q", _cfg())
    # No answer text extractable → absent; the body is kept for debugging.
    assert result.outcome == "absent"
    assert result.raw_response == {"unexpected": "shape"}


def test_oversize_body_maps_to_parse_error(monkeypatch):
    """``safe_post_json`` ``response_too_large`` ValueError (F4) → structured parse_error.

    The 64 KB cap raises a ``ValueError``; the adapter MUST catch it and return a
    structured outcome, NOT let it propagate.
    """
    _patch_post(
        monkeypatch,
        _SpyPost(raises=ValueError("response_too_large: exceeded 65536 bytes")),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "parse_error"
    # The reason is kept in-memory for debugging.
    assert "response_too_large" in str(result.raw_response)


def test_bad_content_type_maps_to_parse_error(monkeypatch):
    _patch_post(
        monkeypatch, _SpyPost(raises=ValueError("bad_content_type: 'text/html'"))
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "parse_error"


def test_redirect_maps_to_parse_error(monkeypatch):
    _patch_post(
        monkeypatch,
        _SpyPost(raises=ValueError("redirect_not_allowed: upstream returned 302")),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "parse_error"


# ── HTTP status families (typed errors) ───────────────────────────────────────


def test_4xx_auth_raises_dependency_error(monkeypatch):
    _patch_post(monkeypatch, _SpyPost(status=401, data={"error": "unauthorized"}))
    with pytest.raises(DependencyError) as exc_info:
        ppx.probe_perplexity("q", _cfg())
    assert exc_info.value.exit_code == 3


def test_429_raises_transient_external_service_error(monkeypatch):
    _patch_post(monkeypatch, _SpyPost(status=429, data={"error": "rate limited"}))
    with pytest.raises(ExternalServiceError) as exc_info:
        ppx.probe_perplexity("q", _cfg())
    assert exc_info.value.exit_code == 4


def test_5xx_raises_transient_external_service_error(monkeypatch):
    _patch_post(monkeypatch, _SpyPost(status=503, data={"error": "down"}))
    with pytest.raises(ExternalServiceError) as exc_info:
        ppx.probe_perplexity("q", _cfg())
    assert exc_info.value.exit_code == 4


def test_network_failure_raises_transient(monkeypatch):
    _patch_post(
        monkeypatch,
        _SpyPost(raises=requests.exceptions.ConnectionError("conn refused")),
    )
    with pytest.raises(ExternalServiceError):
        ppx.probe_perplexity("q", _cfg())


# ── refusal ───────────────────────────────────────────────────────────────────


def test_refusal_text_is_outcome_refused_not_error(monkeypatch):
    _patch_post(
        monkeypatch,
        _SpyPost(
            data={
                "choices": [
                    {"message": {"content": "I can't help with that request."}}
                ]
            }
        ),
    )
    result = ppx.probe_perplexity("q", _cfg())
    assert result.outcome == "refused"
    # A refusal is not an error — no exception, ProbeResult returned.
    assert isinstance(result, ProbeResult)


# ── credential guard chain BEFORE any network call (D9) ───────────────────────


def test_non_allowlisted_base_url_rejected_before_request(monkeypatch):
    """A non-allowlisted host is rejected BEFORE the POST — key never sent."""
    spy = _patch_post(monkeypatch, _SpyPost(), pass_guard=False)
    # Ensure no opt-out env is leaking the allowlist open.
    monkeypatch.delenv("BACKLINK_PUBLISHER_LLM_ALLOW_ANY_HOST", raising=False)

    with pytest.raises(DependencyError) as exc_info:
        ppx.probe_perplexity("q", _cfg(base_url="https://evil.example.com"))

    # The guard chain fired BEFORE any HTTP call — the Bearer key never left.
    assert spy.calls == []
    assert "rejected" in str(exc_info.value).lower()


def test_userinfo_base_url_rejected_before_request(monkeypatch):
    """A userinfo-bearing base_url is rejected BEFORE the POST — key never sent."""
    spy = _patch_post(monkeypatch, _SpyPost(), pass_guard=False)

    with pytest.raises(DependencyError) as exc_info:
        ppx.probe_perplexity(
            "q", _cfg(base_url="https://user:secret@api.perplexity.ai")
        )

    assert spy.calls == []
    assert "userinfo" in str(exc_info.value).lower()


def test_non_https_scheme_rejected_before_request(monkeypatch):
    """A non-http(s) scheme is rejected by guard_llm_endpoint before the POST."""
    spy = _patch_post(monkeypatch, _SpyPost(), pass_guard=False)
    with pytest.raises(DependencyError):
        ppx.probe_perplexity("q", _cfg(base_url="ftp://api.perplexity.ai"))
    assert spy.calls == []


def test_guard_normalized_base_equals_connected_base(monkeypatch):
    """The gated string == the connected string (D9): a full URL with the suffix
    is normalized BEFORE gating, so the POST hits the same host that was gated."""
    spy = _patch_post(
        monkeypatch,
        _SpyPost(data={"choices": [{"message": {"content": "x"}}]}),
    )
    ppx.probe_perplexity(
        "q", _cfg(base_url="https://api.perplexity.ai/chat/completions/")
    )
    # Suffix stripped + re-appended once — no double suffix, same allowlisted host.
    assert spy.calls[0]["url"] == "https://api.perplexity.ai/chat/completions"


# ── raw_response never leaves memory (D8) ─────────────────────────────────────


def test_raw_response_kept_in_memory_not_in_any_persisted_structure(monkeypatch):
    """The adapter returns raw_response on the object but persists nothing here.

    Unit 3 does not write to events.db; this asserts the contract surface: the
    raw body lives only on the in-memory ProbeResult, and the parsed fields
    (answer_text/source_urls/outcome) carry no secret material.
    """
    body = {
        "choices": [{"message": {"content": "answer"}}],
        "citations": ["https://example.com/p"],
    }
    _patch_post(monkeypatch, _SpyPost(data=body))
    result = ppx.probe_perplexity("q", _cfg())

    # raw_response is the in-memory body; the Bearer key is NOT present in it
    # (it was only in the request headers, never echoed into a 2xx body here).
    assert result.raw_response is body
    assert _GEO_KEY not in str(result.source_urls)
    assert _GEO_KEY not in result.answer_text
