"""Tests for backlink_publisher.adapters.llm_anchor_provider."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher.adapters.llm_anchor_provider import (
    LLMAnchorRequest,
    OpenAICompatibleProvider,
    _redact_for_log,
    _sanitize_input,
)
from backlink_publisher.errors import DependencyError


# ── autouse safety net — never hit the real network ──────────────────────────


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Belt-and-suspenders: even if a test forgets to mock, requests.post fails fast."""
    def _never(*args, **kwargs):
        raise AssertionError(
            "Real network call attempted in LLM provider test — every test "
            "must patch requests.post explicitly."
        )
    monkeypatch.setattr("backlink_publisher.adapters.llm_anchor_provider.requests.post", _never)


def _provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test-token-do-not-log",
        model="gpt-4o-mini",
        timeout_s=5.0,
    )


def _ok_response(candidates: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [
            {"message": {"content": json.dumps({"candidates": candidates})}}
        ]
    }
    resp.text = json.dumps(resp.json.return_value)
    return resp


def _make_request(**overrides) -> LLMAnchorRequest:
    base = dict(
        url_category="hot",
        anchor_type="exact",
        keyword="成人漫画",
        target_url="https://51acgs.com/comic/hot",
        url_subject="热门漫画总榜",
        n=5,
    )
    base.update(overrides)
    return LLMAnchorRequest(**base)


# ── happy path ──────────────────────────────────────────────────────────────


def test_generate_candidates_returns_list(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _ok_response(["热门漫画", "本周热门", "漫画排行"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )

    candidates = _provider().generate_candidates(_make_request())

    assert candidates == ["热门漫画", "本周热门", "漫画排行"]
    assert captured["url"] == "https://api.example.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-token-do-not-log"
    assert captured["timeout"] == 5.0
    assert captured["json"]["model"] == "gpt-4o-mini"
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_base_url_with_trailing_slash_normalized(monkeypatch):
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        return _ok_response(["x"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )

    OpenAICompatibleProvider(
        base_url="https://api.example.com/v1/",  # trailing slash
        api_key="k",
        model="m",
    ).generate_candidates(_make_request())

    # No "//chat" double slash
    assert captured["url"] == "https://api.example.com/v1/chat/completions"


# ── prompt construction & input sanitization ───────────────────────────────


def test_prompt_wraps_inputs_in_xml_with_data_warning(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, **kwargs):
        captured["json"] = json
        return _ok_response(["x"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )

    _provider().generate_candidates(_make_request(keyword="hot keyword"))

    messages = captured["json"]["messages"]
    system = messages[0]["content"]
    user = messages[1]["content"]

    # System message warns the model that <input> is data
    assert "untrusted data" in system or "data" in system.lower()
    # User message wraps inputs in <input ...>
    assert "<input " in user
    assert 'keyword="hot keyword"' in user


def test_prompt_strips_bidi_overrides_from_keyword(monkeypatch):
    """A keyword containing U+202E (RLO) must not reach the prompt body."""
    captured: dict = {}

    def fake_post(url, json=None, **kwargs):
        captured["json"] = json
        return _ok_response(["x"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )

    evil_keyword = "成人‮漫画"  # contains U+202E in the middle
    _provider().generate_candidates(_make_request(keyword=evil_keyword))

    user = captured["json"]["messages"][1]["content"]
    assert "‮" not in user
    assert "成人漫画" in user


def test_prompt_caps_input_length(monkeypatch):
    captured: dict = {}

    def fake_post(url, json=None, **kwargs):
        captured["json"] = json
        return _ok_response(["x"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )

    huge = "x" * 5000
    _provider().generate_candidates(_make_request(keyword=huge))

    user = captured["json"]["messages"][1]["content"]
    # Total prompt contains the keyword but its block is at most 200 chars
    keyword_block_match = user.split('keyword="')[1].split('"')[0]
    assert len(keyword_block_match) <= 200


def test_sanitize_input_strips_control_chars():
    out = _sanitize_input("a\x00b\x07c")
    assert out == "abc"


def test_sanitize_input_truncates():
    out = _sanitize_input("a" * 300)
    assert len(out) == 200


def test_sanitize_input_non_string_returns_empty():
    assert _sanitize_input(None) == ""  # type: ignore[arg-type]
    assert _sanitize_input(123) == ""  # type: ignore[arg-type]


# ── error paths ─────────────────────────────────────────────────────────────


def test_non_json_response_raises_dependency_error(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    resp.text = "<html>oops</html>"
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: resp,
    )

    with pytest.raises(DependencyError, match="non-JSON"):
        _provider().generate_candidates(_make_request())


def test_missing_candidates_field_raises(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": json.dumps({"foo": "bar"})}}]
    }
    resp.text = json.dumps(resp.json.return_value)
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: resp,
    )

    with pytest.raises(DependencyError, match="candidates"):
        _provider().generate_candidates(_make_request())


def test_non_json_content_raises(monkeypatch):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": "this is not json at all"}}]
    }
    resp.text = json.dumps(resp.json.return_value)
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: resp,
    )

    with pytest.raises(DependencyError, match="non-JSON content"):
        _provider().generate_candidates(_make_request())


def test_http_4xx_raises_dependency_error(monkeypatch):
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "Authorization: Bearer sk-leaked\nbad token"
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: resp,
    )

    with pytest.raises(DependencyError) as exc_info:
        _provider().generate_candidates(_make_request())

    # The leaked token must not appear in the error message
    assert "sk-leaked" not in str(exc_info.value)


def test_http_429_retries_then_succeeds(monkeypatch):
    """A 429 response should be retried by retry_transient_call."""
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            r = MagicMock()
            r.status_code = 429
            r.text = "rate limited"
            return r
        return _ok_response(["热门漫画"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )
    # Speed up retry sleeps
    monkeypatch.setattr(
        "backlink_publisher.adapters.retry.time.sleep", lambda *_: None,
    )

    candidates = _provider().generate_candidates(_make_request())
    assert candidates == ["热门漫画"]
    assert call_count["n"] == 2


def test_http_5xx_retries(monkeypatch):
    """5xx is retried — anchor generation is naturally idempotent."""
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            r = MagicMock()
            r.status_code = 503
            r.text = "service unavailable"
            return r
        return _ok_response(["热门漫画"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )
    monkeypatch.setattr(
        "backlink_publisher.adapters.retry.time.sleep", lambda *_: None,
    )

    candidates = _provider().generate_candidates(_make_request())
    assert candidates == ["热门漫画"]


def test_timeout_retries(monkeypatch):
    """A requests.Timeout should be retried."""
    call_count = {"n": 0}

    def fake_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise requests.exceptions.Timeout("read timeout")
        return _ok_response(["热门漫画"])

    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post", fake_post,
    )
    monkeypatch.setattr(
        "backlink_publisher.adapters.retry.time.sleep", lambda *_: None,
    )

    candidates = _provider().generate_candidates(_make_request())
    assert candidates == ["热门漫画"]


def test_connection_error_retries_then_gives_up(monkeypatch):
    """After max retry attempts, the final exception bubbles as DependencyError."""
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(
            requests.exceptions.ConnectionError("no route")
        ),
    )
    monkeypatch.setattr(
        "backlink_publisher.adapters.retry.time.sleep", lambda *_: None,
    )

    with pytest.raises(DependencyError):
        _provider().generate_candidates(_make_request())


# ── redaction helper ────────────────────────────────────────────────────────


def test_redact_authorization_header():
    out = _redact_for_log("Authorization: Bearer sk-abc123\nfoo")
    assert "sk-abc123" not in out
    assert "Authorization:" in out
    assert "***" in out


def test_redact_bare_bearer_token():
    out = _redact_for_log("ah well, the token was Bearer sk-leaked-key-12345 sorry")
    assert "sk-leaked-key-12345" not in out
    assert "Bearer ***" in out


def test_redact_api_key_in_json():
    out = _redact_for_log('{"api_key": "secret123", "model": "foo"}')
    assert "secret123" not in out


def test_redact_truncates_to_200_chars():
    out = _redact_for_log("a" * 5000)
    assert len(out) <= 201  # 200 + truncation ellipsis


def test_redact_handles_non_string():
    out = _redact_for_log(123)  # type: ignore[arg-type]
    assert out == "123"


def test_provider_exception_string_is_redacted_in_dependency_error(monkeypatch):
    """If the underlying exception text contains a key, the wrapped DependencyError must scrub it."""
    monkeypatch.setattr(
        "backlink_publisher.adapters.llm_anchor_provider.requests.post",
        lambda *a, **kw: (_ for _ in ()).throw(
            # Use a generic Exception so the non-retryable path triggers
            # the DependencyError-wrapping branch, not the bubble-up branch.
            Exception("api_key=secret-leaked-via-exception-message")
        ),
    )
    monkeypatch.setattr(
        "backlink_publisher.adapters.retry.time.sleep", lambda *_: None,
    )

    with pytest.raises(DependencyError) as exc_info:
        _provider().generate_candidates(_make_request())

    assert "secret-leaked-via-exception-message" not in str(exc_info.value)
