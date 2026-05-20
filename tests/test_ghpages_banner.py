"""Tests for ``GitHubPagesAPIAdapter.embed_banner``.

Plan: docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md
Unit 6 — Commits the banner bytes to the operator's Pages repo at
``assets/banners/<sha16>.<ext>`` via the Contents API and returns
the ``raw.githubusercontent.com`` URL.

Reuses the existing ``_get_existing_sha`` probe + ``_put_binary_contents``
helper.  Distinct from the markdown post-commit path:

  - ``_put_contents`` (markdown) encodes via ``.encode("utf-8")``
  - ``_put_binary_contents`` (this unit) base64s raw bytes directly

The adapter lazily loads ``Config`` (via ``load_config()``) so it
honors ``BACKLINK_PUBLISHER_CONFIG_DIR``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher._util.errors import (
    AuthExpiredError,
    BannerUploadError,
)
from backlink_publisher.publishing.adapters.ghpages import (
    GitHubPagesAPIAdapter,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _seed_token(config_dir: Path, token: str = "ghp_fake_pat") -> Path:
    p = config_dir / "ghpages-token.json"
    p.write_text(json.dumps({"token": token}))
    os.chmod(p, 0o600)
    return p


def _write_config(tmp_path: Path, repo: str = "owner/repo", branch: str = "gh-pages") -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[ghpages]\nrepo = "{repo}"\nbranch = "{branch}"\n'
        'path_template = "_posts/{date}-{slug}.md"\n'
    )
    return cfg


def _isolated_config(tmp_path, monkeypatch) -> Path:
    """Point BACKLINK_PUBLISHER_CONFIG_DIR at tmp_path with a valid
    ghpages config + token.  Returns the directory for the caller to
    inspect."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _write_config(tmp_path)
    _seed_token(tmp_path)
    return tmp_path


def _write_banner(tmp_path: Path, name: str = "banner.png", content: bytes = b"\x89PNG\r\n\x1a\n") -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def _ok_get(sha: str = "abc-existing-sha") -> MagicMock:
    """Return value for ``requests.get`` mock — file exists."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"sha": sha}
    return resp


def _missing_get() -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = 404
    resp.headers = {}
    return resp


def _ok_put(status: int = 201) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = {
        "content": {"sha": "newcontentsha", "html_url": "https://github.com/o/r/blob/.../x.png"},
        "commit": {"sha": "commitsha"},
    }
    return resp


def _err_response(status: int, text: str = "") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.headers = {}
    resp.text = text
    resp.json.return_value = {}
    return resp


# ── Happy paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerHappyPath:
    def test_new_banner_probe_404_then_put_201_returns_raw_url(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        payload = b"\x89PNG\r\n\x1a\n"
        artifact = _write_banner(tmp_path, "fake.png", payload)
        expected_sha16 = hashlib.sha256(payload).hexdigest()[:16]

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ) as mock_get, patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_ok_put(201),
        ) as mock_put:
            url = GitHubPagesAPIAdapter().embed_banner(artifact, "Test Alt")

        assert url == (
            f"https://raw.githubusercontent.com/owner/repo/gh-pages/"
            f"assets/banners/{expected_sha16}.png"
        )

        # Probe hit the right path.
        get_url = mock_get.call_args.args[0]
        assert f"/repos/owner/repo/contents/assets/banners/{expected_sha16}.png" in get_url

        # PUT body is base64 of raw bytes (NOT .encode("utf-8") of any string).
        put_body = mock_put.call_args.kwargs["json"]
        assert put_body["branch"] == "gh-pages"
        assert "sha" not in put_body  # new file, no sha needed
        assert base64.b64decode(put_body["content"]) == payload

        # Commit message references the content sha.
        assert expected_sha16 in put_body["message"]

    def test_existing_banner_probe_200_skips_put_returns_raw_url(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        payload = b"\x89PNG\r\n"
        artifact = _write_banner(tmp_path, "fake.png", payload)
        expected_sha16 = hashlib.sha256(payload).hexdigest()[:16]

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_ok_get("existing-sha-on-server"),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
        ) as mock_put:
            url = GitHubPagesAPIAdapter().embed_banner(artifact, "Test Alt")

        # PUT must NOT be called — idempotent skip.
        mock_put.assert_not_called()

        assert url == (
            f"https://raw.githubusercontent.com/owner/repo/gh-pages/"
            f"assets/banners/{expected_sha16}.png"
        )

    def test_branch_from_config_used_in_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path, branch="main")
        _seed_token(tmp_path)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_ok_put(),
        ):
            url = GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

        # Locks the regression that the returned URL uses the configured
        # branch (the previous draft of this implementation hardcoded
        # gh-pages — caught during the test-writing pass).
        assert "/main/assets/banners/" in url

    def test_webp_extension_preserved(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        payload = b"RIFFwebp"
        artifact = _write_banner(tmp_path, "x.webp", payload)
        expected_sha16 = hashlib.sha256(payload).hexdigest()[:16]

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_ok_put(),
        ):
            url = GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

        assert url.endswith(f"{expected_sha16}.webp")

    def test_extensionless_file_defaults_to_png_suffix(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path, "abcdef0123", b"\x89PNG")

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_ok_put(),
        ):
            url = GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

        assert url.endswith(".png")


# ── Error paths ──────────────────────────────────────────────────────────────


class TestEmbedBannerErrorPaths:
    def test_missing_config_raises_banner_upload_error(self, tmp_path, monkeypatch):
        # Token exists but [ghpages] block is missing.
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        # No config.toml written → empty Config → ghpages=None.
        _seed_token(tmp_path)
        artifact = _write_banner(tmp_path)

        with pytest.raises(BannerUploadError, match="config missing"):
            GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

    def test_missing_token_raises_banner_upload_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
        _write_config(tmp_path)
        # No token file written.
        artifact = _write_banner(tmp_path)

        with pytest.raises(BannerUploadError, match="token unavailable"):
            GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

    def test_unreadable_artifact_raises_banner_upload_error(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        ghost = tmp_path / "never.png"

        with pytest.raises(BannerUploadError, match="banner read failed"):
            GitHubPagesAPIAdapter().embed_banner(ghost, "alt")

    def test_probe_returns_5xx_raises_banner_upload_error(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_err_response(500, "internal"),
        ):
            with pytest.raises(BannerUploadError, match="probe failed.*500"):
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

    def test_put_returns_5xx_raises_banner_upload_error(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_err_response(500, "internal"),
        ):
            with pytest.raises(BannerUploadError, match="PUT returned HTTP 500"):
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

    def test_put_returns_422_raises_eventual_consistency_error(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_err_response(422, "sha required"),
        ):
            with pytest.raises(BannerUploadError, match="eventual-consistency race"):
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

    def test_network_error_on_put_raises_banner_upload_error(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            side_effect=requests.Timeout("timed out"),
        ):
            with pytest.raises(BannerUploadError, match="network"):
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")


# ── Auth-flip negative assertion ─────────────────────────────────────────────


class TestEmbedBannerDoesNotFlipChannelStatus:
    """A 401 from the banner-upload path must NOT raise
    ``AuthExpiredError`` — channel-status ``mark_expired`` is reserved
    for the publish path's 401."""

    def test_probe_401_raises_banner_upload_error_not_auth_expired(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_err_response(401, "Bad credentials"),
        ):
            with pytest.raises(BannerUploadError) as exc_info:
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

        assert not isinstance(exc_info.value, AuthExpiredError)

    def test_put_401_raises_banner_upload_error_not_auth_expired(self, tmp_path, monkeypatch):
        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_err_response(401, "Bad credentials"),
        ):
            with pytest.raises(BannerUploadError) as exc_info:
                GitHubPagesAPIAdapter().embed_banner(artifact, "alt")

        assert not isinstance(exc_info.value, AuthExpiredError)


# ── Integration with dispatcher ──────────────────────────────────────────────


class TestEmbedBannerThroughDispatcher:
    def test_dispatcher_routes_raw_url_into_body(self, tmp_path, monkeypatch):
        from backlink_publisher.publishing import banner_dispatcher

        _isolated_config(tmp_path, monkeypatch)
        payload = b"\x89PNG\r\n"
        artifact = _write_banner(tmp_path, "a.png", payload)
        expected_sha16 = hashlib.sha256(payload).hexdigest()[:16]
        emitted: list[tuple[str, dict]] = []

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_missing_get(),
        ), patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.put",
            return_value=_ok_put(),
        ):
            body = banner_dispatcher.apply(
                GitHubPagesAPIAdapter(),
                banner={
                    "path": str(artifact),
                    "alt": "Banner Alt",
                    "mime": "image/png",
                    "sha": "deadbeef",
                    "source_url": "https://upstream/x.png",
                },
                body="Body content here.",
                platform="ghpages",
                strict=False,
                emit=lambda k, p: emitted.append((k, p)),
            )

        expected_url = (
            f"https://raw.githubusercontent.com/owner/repo/gh-pages/"
            f"assets/banners/{expected_sha16}.png"
        )
        assert body.startswith(f"![Banner Alt]({expected_url})\n\n")
        assert body.endswith("Body content here.")
        assert emitted == [("banner.embedded", {"platform": "ghpages"})]

    def test_dispatcher_strict_propagates_banner_upload_error(self, tmp_path, monkeypatch):
        from backlink_publisher.publishing import banner_dispatcher

        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_err_response(500, "boom"),
        ):
            with pytest.raises(BannerUploadError):
                banner_dispatcher.apply(
                    GitHubPagesAPIAdapter(),
                    banner={
                        "path": str(artifact),
                        "alt": "Alt",
                        "mime": "image/png",
                        "sha": "x",
                    },
                    body="b",
                    platform="ghpages",
                    strict=True,
                    emit=lambda *_: None,
                )

    def test_dispatcher_non_strict_emits_failed_on_500(self, tmp_path, monkeypatch):
        from backlink_publisher.publishing import banner_dispatcher

        _isolated_config(tmp_path, monkeypatch)
        artifact = _write_banner(tmp_path)
        emitted: list[tuple[str, dict]] = []

        with patch(
            "backlink_publisher.publishing.adapters.ghpages.requests.get",
            return_value=_err_response(500, "boom"),
        ):
            body = banner_dispatcher.apply(
                GitHubPagesAPIAdapter(),
                banner={
                    "path": str(artifact),
                    "alt": "Alt",
                    "mime": "image/png",
                    "sha": "x",
                },
                body="b",
                platform="ghpages",
                strict=False,
                emit=lambda k, p: emitted.append((k, p)),
            )

        # Body unchanged on failure.
        assert body == "b"
        assert len(emitted) == 1
        kind, payload = emitted[0]
        assert kind == "banner.failed"
        assert payload["platform"] == "ghpages"
        assert "500" in payload["reason"]
