"""Tests for ``backlink_publisher.publishing.adapters.image_gen``.

Plan: docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md
Unit 2 — OpenAI-compatible ``/images/generations`` adapter that
replaces the 28-line ``frw_image_gen.py`` stub.

Per plan Test scenarios: happy path × url/b64 / large prompt /
size cap (==N, N+1) / MIME sniff PNG/JPEG/WebP / 401 fail-loud /
429 retry / response shape missing / source_url unreachable /
timeout retry.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.publishing.adapters.image_gen.adapter import (
    ImageGenAdapter,
)
from backlink_publisher.publishing.adapters.image_gen.types import (
    BannerArtifact,
)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # 24-byte minimum PNG-ish
_JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 20
_WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 16


def _ok_post(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = "OK"
    resp.raise_for_status = MagicMock()
    return resp


def _err_post(status: int, body: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    resp.json.return_value = {"error": body}
    resp.raise_for_status = MagicMock(
        side_effect=__import__("requests").HTTPError(f"HTTP {status}")
    )
    return resp


def _ok_get(content: bytes, content_type: str = "image/png") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    resp.raise_for_status = MagicMock()
    return resp


def _make_adapter(**overrides) -> ImageGenAdapter:
    defaults = dict(
        base_url="https://gateway.example.com/v1",
        model="banner-1",
        banner_size="1200x630",
        api_key="sk_test",
        timeout_s=5.0,
        max_retries=3,
    )
    defaults.update(overrides)
    return ImageGenAdapter(**defaults)


# ── Happy path × url / b64 ──────────────────────────────────────────────────


def test_generate_url_mode_downloads_and_returns_bytes():
    """``data[].url`` mode → adapter follow-up GETs and returns the
    raw bytes plus the ``source_url`` for downstream auditing."""
    adapter = _make_adapter()

    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/x.png"}]})
    get_resp = _ok_get(_PNG_MAGIC, "image/png")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=get_resp,
    ):
        artifact = adapter.generate("a cat on a sunny porch")

    assert isinstance(artifact, BannerArtifact)
    assert artifact.data == _PNG_MAGIC
    assert artifact.mime == "image/png"
    assert artifact.source_url == "https://cdn.example.com/x.png"
    assert artifact.prompt_sha  # sha256 hex prefix


def test_generate_b64_mode_decodes_inline():
    """``data[].b64_json`` mode → base64-decode in-process, no GET.
    ``source_url`` is None because no external URL is offered."""
    adapter = _make_adapter()
    b64 = base64.b64encode(_PNG_MAGIC).decode("ascii")
    post_resp = _ok_post({"data": [{"b64_json": b64}]})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ):
        artifact = adapter.generate("seed prompt")

    assert artifact.data == _PNG_MAGIC
    assert artifact.mime == "image/png"
    assert artifact.source_url is None


# ── Auth + body shape ───────────────────────────────────────────────────────


def test_post_sends_bearer_authorization():
    """``Authorization: Bearer <key>`` header on every POST (OpenAI
    standard).  The api_key never appears in the body."""
    adapter = _make_adapter(api_key="sk_unique_42")
    post_resp = _ok_post({"data": [{"b64_json": base64.b64encode(_PNG_MAGIC).decode()}]})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ) as mock_post:
        adapter.generate("p")

    assert mock_post.call_count == 1
    kwargs = mock_post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer sk_unique_42"
    # Body MUST NOT contain the api_key
    body_json = kwargs.get("json") or {}
    assert "sk_unique_42" not in str(body_json)
    assert body_json["model"] == "banner-1"
    assert body_json["size"] == "1200x630"
    assert body_json["n"] == 1
    assert body_json["prompt"] == "p"


def test_post_targets_images_generations_path():
    """Endpoint = ``<base_url>/images/generations`` (OpenAI-compatible)."""
    adapter = _make_adapter(base_url="https://gateway.example.com/v1")
    post_resp = _ok_post({"data": [{"b64_json": base64.b64encode(_PNG_MAGIC).decode()}]})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ) as mock_post:
        adapter.generate("p")

    url_arg = mock_post.call_args.args[0]
    assert url_arg == "https://gateway.example.com/v1/images/generations"


# ── MIME sniffing ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "magic, expected_mime",
    [
        (_PNG_MAGIC, "image/png"),
        (_JPEG_MAGIC, "image/jpeg"),
        (_WEBP_MAGIC, "image/webp"),
    ],
)
def test_mime_sniffed_from_magic_bytes(magic, expected_mime):
    """Trust magic bytes over Content-Type — providers sometimes
    mis-report Content-Type but the file body's first bytes are
    authoritative."""
    adapter = _make_adapter()
    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/x"}]})
    # Deliberately bogus Content-Type to prove we don't trust it
    get_resp = _ok_get(magic, content_type="application/octet-stream")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=get_resp,
    ):
        artifact = adapter.generate("p")

    assert artifact.mime == expected_mime


def test_unknown_magic_fails_loud():
    """Unknown magic bytes → fail-loud rather than guess. We must not
    let an HTML 404-page-disguised-as-image slip through silently."""
    adapter = _make_adapter()
    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/x"}]})
    get_resp = _ok_get(b"<html>not an image", "image/png")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=get_resp,
    ), pytest.raises(RuntimeError, match="unrecognized image format"):
        adapter.generate("p")


# ── Size cap ────────────────────────────────────────────────────────────────


def test_response_at_size_cap_succeeds():
    """5 MB exactly → accepted (boundary test)."""
    adapter = _make_adapter()
    big = _PNG_MAGIC + b"\x00" * (5 * 1024 * 1024 - len(_PNG_MAGIC))
    assert len(big) == 5 * 1024 * 1024

    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/x"}]})
    get_resp = _ok_get(big, "image/png")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=get_resp,
    ):
        artifact = adapter.generate("p")

    assert len(artifact.data) == 5 * 1024 * 1024


def test_response_over_size_cap_rejected():
    """5 MB + 1 byte → ``ExternalServiceError`` (DoS guard)."""
    from backlink_publisher._util.errors import ExternalServiceError

    adapter = _make_adapter()
    too_big = _PNG_MAGIC + b"\x00" * (5 * 1024 * 1024 - len(_PNG_MAGIC) + 1)
    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/x"}]})
    get_resp = _ok_get(too_big, "image/png")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=get_resp,
    ), pytest.raises(ExternalServiceError, match=r"exceeds 5 ?MB"):
        adapter.generate("p")


# ── Error paths ─────────────────────────────────────────────────────────────


def test_401_fails_loud_with_rotate_hint():
    """401 → ``RuntimeError`` naming ``frw-login`` so the operator
    knows the fix.  MUST NOT retry."""
    adapter = _make_adapter()
    bad = _err_post(401, "invalid_api_key")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=bad,
    ) as mock_post, pytest.raises(RuntimeError, match="frw-login"):
        adapter.generate("p")

    assert mock_post.call_count == 1, "401 must not retry"


def test_429_retries_then_succeeds():
    """Two 429s followed by 200 → eventual success after retry."""
    adapter = _make_adapter(max_retries=3)
    b64 = base64.b64encode(_PNG_MAGIC).decode("ascii")
    sequence = [
        _err_post(429, "rate limited"),
        _err_post(429, "rate limited"),
        _ok_post({"data": [{"b64_json": b64}]}),
    ]

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        side_effect=sequence,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.time.sleep",
        return_value=None,
    ):
        artifact = adapter.generate("p")

    assert artifact.data == _PNG_MAGIC


def test_5xx_retries_then_gives_up():
    """3 × 500 with ``max_retries=3`` → raise ``ExternalServiceError``
    after exhausting attempts.  No silent ``None``."""
    from backlink_publisher._util.errors import ExternalServiceError

    adapter = _make_adapter(max_retries=3)

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        side_effect=[_err_post(500, "boom")] * 3,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.time.sleep",
        return_value=None,
    ), pytest.raises(ExternalServiceError):
        adapter.generate("p")


def test_4xx_non_401_fails_loud():
    """4xx other than 401 → fail-loud, no retry (bad request from us
    won't succeed on retry; better to surface fast)."""
    from backlink_publisher._util.errors import ExternalServiceError

    adapter = _make_adapter()
    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=_err_post(400, "bad prompt"),
    ) as mock_post, pytest.raises(ExternalServiceError):
        adapter.generate("p")

    assert mock_post.call_count == 1


def test_missing_data_field_fails_loud():
    """200 with no ``data`` key → fail-loud with response excerpt
    in message.  Provider returned junk; retry won't help."""
    adapter = _make_adapter()
    post_resp = _ok_post({"unexpected": "shape"})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), pytest.raises(RuntimeError, match="data"):
        adapter.generate("p")


def test_empty_data_array_fails_loud():
    """200 with ``data: []`` → fail-loud (provider claimed success
    but returned nothing)."""
    adapter = _make_adapter()
    post_resp = _ok_post({"data": []})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), pytest.raises(RuntimeError, match="data"):
        adapter.generate("p")


def test_source_url_unreachable_fails_loud():
    """URL-mode response but the URL itself returns 404 →
    ``ExternalServiceError`` (provider points at vapor).  We must
    NOT silently fall back to the URL string as bytes."""
    from backlink_publisher._util.errors import ExternalServiceError

    adapter = _make_adapter()
    post_resp = _ok_post({"data": [{"url": "https://cdn.example.com/gone"}]})
    bad_get = MagicMock()
    bad_get.status_code = 404
    bad_get.raise_for_status = MagicMock(
        side_effect=__import__("requests").HTTPError("404 Not Found")
    )

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.get",
        return_value=bad_get,
    ), pytest.raises(ExternalServiceError, match="source_url"):
        adapter.generate("p")


def test_timeout_retries():
    """``requests.Timeout`` on POST is treated as transient and
    retried."""
    import requests

    adapter = _make_adapter(max_retries=3)
    b64 = base64.b64encode(_PNG_MAGIC).decode("ascii")

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        side_effect=[
            requests.Timeout("slow"),
            _ok_post({"data": [{"b64_json": b64}]}),
        ],
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.time.sleep",
        return_value=None,
    ):
        artifact = adapter.generate("p")

    assert artifact.data == _PNG_MAGIC


# ── prompt_sha determinism ─────────────────────────────────────────────────


def test_same_prompt_same_sha():
    """Same prompt → identical ``prompt_sha`` across two calls.
    Critical for storage idempotency (Unit 3)."""
    adapter = _make_adapter()
    b64 = base64.b64encode(_PNG_MAGIC).decode("ascii")
    post_resp = _ok_post({"data": [{"b64_json": b64}]})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ):
        a1 = adapter.generate("the same prompt")
        a2 = adapter.generate("the same prompt")

    assert a1.prompt_sha == a2.prompt_sha


def test_different_prompt_different_sha():
    adapter = _make_adapter()
    b64 = base64.b64encode(_PNG_MAGIC).decode("ascii")
    post_resp = _ok_post({"data": [{"b64_json": b64}]})

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
        return_value=post_resp,
    ):
        a1 = adapter.generate("prompt one")
        a2 = adapter.generate("prompt two")

    assert a1.prompt_sha != a2.prompt_sha
