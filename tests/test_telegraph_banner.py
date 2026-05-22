"""Tests for ``TelegraphAPIAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 2 — Telegraph anonymous ``POST /upload`` returning
``https://telegra.ph/file/<sha>.<ext>``.

The adapter's existing publish-path tests live in
``test_adapter_telegraph_api.py``; this file isolates the new
``embed_banner`` method.  ``embed_banner`` does NOT touch the token
file (Telegraph's ``/upload`` is anonymous) so no fixtures around
``BACKLINK_PUBLISHER_CONFIG_DIR`` are needed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher._util.errors import BannerUploadError
from backlink_publisher.publishing.adapters.telegraph_api import (
    TelegraphAPIAdapter,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _resp(status: int = 200, json_body=None, raises_json: Exception | None = None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    if raises_json is not None:
        resp.json.side_effect = raises_json
    else:
        resp.json.return_value = json_body
    return resp


def _write_png(tmp_path: Path, name: str = "banner.png", content: bytes = b"\x89PNG\r\n") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


# ── Happy paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerHappyPath:
    def test_returns_full_telegraph_url(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, [{"src": "/file/abc123.png"}])
            url = TelegraphAPIAdapter().embed_banner(path, "Test Title")

        assert url == "https://telegra.ph/file/abc123.png"
        # Confirm the request shape: multipart 'file' field, anonymous (no token).
        call = mock_post.call_args
        assert call.args[0] == "https://telegra.ph/upload"
        assert "files" in call.kwargs and "file" in call.kwargs["files"]
        filename, data, mime = call.kwargs["files"]["file"]
        assert filename == "banner.png"
        assert data == b"\x89PNG\r\n"
        assert mime == "image/png"
        # No access_token in payload (anonymous endpoint).
        assert "data" not in call.kwargs or "access_token" not in (
            call.kwargs.get("data") or {}
        )

    def test_preserves_webp_extension(self, tmp_path):
        path = _write_png(tmp_path, "banner.webp", b"RIFFwebp")
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, [{"src": "/file/xyz.webp"}])
            url = TelegraphAPIAdapter().embed_banner(path, "alt")

        assert url == "https://telegra.ph/file/xyz.webp"
        # Mime sniffed from filename extension.
        _, _, mime = mock_post.call_args.kwargs["files"]["file"]
        assert mime == "image/webp"

    def test_unknown_extension_falls_back_to_image_png(self, tmp_path):
        # Sha-only filename with no extension → mimetypes can't guess →
        # fall back to image/png so Telegraph's content sniffing decides.
        path = _write_png(tmp_path, "abcdef0123", b"\x89PNG")
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, [{"src": "/file/q.png"}])
            TelegraphAPIAdapter().embed_banner(path, "alt")

        _, _, mime = mock_post.call_args.kwargs["files"]["file"]
        assert mime == "image/png"

    def test_zero_byte_file_still_posted(self, tmp_path):
        # The validation is Telegraph's job — we ship empty bytes; on
        # rejection Telegraph returns 400 which we surface as
        # BannerUploadError (covered in TestEmbedBannerErrorPaths).
        path = _write_png(tmp_path, "empty.png", b"")
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, [{"src": "/file/e.png"}])
            url = TelegraphAPIAdapter().embed_banner(path, "alt")
        assert url == "https://telegra.ph/file/e.png"
        _, data, _ = mock_post.call_args.kwargs["files"]["file"]
        assert data == b""


# ── Error paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerErrorPaths:
    def test_http_413_raises_banner_upload_error(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(413, json_body=None)
            with pytest.raises(BannerUploadError, match="413"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_http_500_raises_banner_upload_error(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(500, json_body=None)
            with pytest.raises(BannerUploadError, match="500"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_network_timeout_raises_banner_upload_error(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.side_effect = requests.Timeout("timed out")
            with pytest.raises(BannerUploadError, match="network"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_connection_error_raises_banner_upload_error(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.side_effect = requests.ConnectionError("dns")
            with pytest.raises(BannerUploadError, match="network"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_malformed_non_json_body_raises(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, raises_json=ValueError("not json"))
            with pytest.raises(BannerUploadError, match="not JSON"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_error_object_response_raises(self, tmp_path):
        # Telegraph's documented error shape for /upload.
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(
                200, json_body={"error": "FILE_TYPE_INVALID"}
            )
            with pytest.raises(BannerUploadError, match="FILE_TYPE_INVALID"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_empty_array_raises(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, json_body=[])
            with pytest.raises(BannerUploadError, match="malformed body shape"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_missing_src_field_raises(self, tmp_path):
        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(200, json_body=[{"other": "key"}])
            with pytest.raises(BannerUploadError, match="empty src"):
                TelegraphAPIAdapter().embed_banner(path, "alt")

    def test_unreadable_local_file_raises(self, tmp_path):
        # File path that does not exist on disk.
        ghost = tmp_path / "never-created.png"
        with pytest.raises(BannerUploadError, match="banner read failed"):
            TelegraphAPIAdapter().embed_banner(ghost, "alt")


# ── Auth-flip negative assertion ─────────────────────────────────────────────


class TestEmbedBannerDoesNotFlipChannelStatus:
    """Regression: banner-upload 401 must NOT mark the Telegraph channel
    as expired.  Banner upload is on the anonymous ``/upload`` endpoint
    and is semantically independent of ``createPage``'s access_token
    lifecycle.  ``BannerUploadError`` is a sibling of
    ``AuthExpiredError``, never a subclass."""

    def test_http_401_raises_banner_upload_error_not_auth_expired(self, tmp_path):
        path = _write_png(tmp_path)
        from backlink_publisher._util.errors import AuthExpiredError

        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(401, json_body=None)
            with pytest.raises(BannerUploadError) as exc_info:
                TelegraphAPIAdapter().embed_banner(path, "alt")

        # MUST be BannerUploadError, NOT AuthExpiredError.
        assert not isinstance(exc_info.value, AuthExpiredError)


# ── Integration with dispatcher ──────────────────────────────────────────────


class TestEmbedBannerThroughDispatcher:
    def test_dispatcher_routes_uploaded_url_into_body(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        path = _write_png(tmp_path)
        emitted: list[tuple[str, dict]] = []

        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(
                200, [{"src": "/file/sha7777.png"}]
            )
            body = banner_dispatcher.apply(
                TelegraphAPIAdapter(),
                banner={
                    "path": str(path),
                    "alt": "Banner Alt",
                    "mime": "image/png",
                    "sha": "deadbeef",
                    "source_url": "https://upstream/x.png",
                },
                body="Body content here.",
                platform="telegraph",
                strict=False,
                emit=lambda k, p: emitted.append((k, p)),
            )

        assert body.startswith(
            "![Banner Alt](https://telegra.ph/file/sha7777.png)\n\n"
        )
        assert body.endswith("Body content here.")
        assert emitted == [("banner.embedded", {"platform": "telegraph"})]

    def test_dispatcher_strict_propagates_banner_upload_error(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        path = _write_png(tmp_path)
        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(500, json_body=None)
            with pytest.raises(BannerUploadError):
                banner_dispatcher.apply(
                    TelegraphAPIAdapter(),
                    banner={
                        "path": str(path),
                        "alt": "Alt",
                        "mime": "image/png",
                        "sha": "x",
                    },
                    body="b",
                    platform="telegraph",
                    strict=True,
                    emit=lambda *_: None,
                )

    def test_dispatcher_non_strict_swallows_and_emits_failed(self, tmp_path):
        from backlink_publisher.publishing import banner_dispatcher

        path = _write_png(tmp_path)
        emitted: list[tuple[str, dict]] = []

        with patch(
            "backlink_publisher.publishing.adapters.telegraph_api.http_post"
        ) as mock_post:
            mock_post.return_value = _resp(500, json_body=None)
            body = banner_dispatcher.apply(
                TelegraphAPIAdapter(),
                banner={
                    "path": str(path),
                    "alt": "Alt",
                    "mime": "image/png",
                    "sha": "x",
                },
                body="b",
                platform="telegraph",
                strict=False,
                emit=lambda k, p: emitted.append((k, p)),
            )

        # Body unchanged (no prepend on failure).
        assert body == "b"
        assert len(emitted) == 1
        kind, payload = emitted[0]
        assert kind == "banner.failed"
        assert payload["platform"] == "telegraph"
        assert "500" in payload["reason"]
