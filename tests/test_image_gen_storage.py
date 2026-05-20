"""Tests for ``image_gen.storage`` — Plan 2026-05-20-001 Unit 3.

Content-addressed banner storage under
``<config_dir>/banners/<YYYY-MM>/<sha>.<ext>``.  Idempotent on same
prompt_sha so a re-run of plan-backlinks never re-pays the
image-gen bill for an unchanged article.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from backlink_publisher.publishing.adapters.image_gen.storage import (
    save_banner,
    path_for_artifact,
)
from backlink_publisher.publishing.adapters.image_gen.types import BannerArtifact


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 30
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 30


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _artifact(data: bytes, mime: str, sha: str = "abc1234567890def") -> BannerArtifact:
    return BannerArtifact(data=data, mime=mime, source_url=None, prompt_sha=sha)


def test_save_banner_writes_file_under_banners_dir(isolated_config_dir, monkeypatch):
    """Path = ``<config_dir>/banners/<YYYY-MM>/<sha>.<ext>``."""
    # Pin the month so the test isn't time-sensitive.
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="cafebabe1234567")
    path = save_banner(art)

    expected = isolated_config_dir / "banners" / "2026-05" / "cafebabe1234567.png"
    assert path == expected
    assert expected.exists()
    assert expected.read_bytes() == _PNG


@pytest.mark.parametrize(
    "mime, ext",
    [
        ("image/png", "png"),
        ("image/jpeg", "jpg"),
        ("image/webp", "webp"),
    ],
)
def test_save_banner_picks_extension_from_mime(isolated_config_dir, mime, ext, monkeypatch):
    """``image/jpeg`` → ``.jpg`` (operator-conventional, not ``.jpeg``)."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    data = {"image/png": _PNG, "image/jpeg": _JPEG, "image/webp": _WEBP}[mime]
    art = _artifact(data, mime, sha="aaaa1111bbbb2222")
    path = save_banner(art)

    assert path.suffix == f".{ext}"
    assert path.exists()


def test_save_banner_unknown_mime_fails_loud(isolated_config_dir):
    """Unknown MIME → ``ValueError`` rather than guessing the
    extension (would create a file no platform can serve)."""
    art = _artifact(_PNG, "image/svg+xml", sha="a" * 16)
    with pytest.raises(ValueError, match="unsupported mime"):
        save_banner(art)


def test_save_banner_is_idempotent_on_same_sha(isolated_config_dir, monkeypatch):
    """Calling twice with the same prompt_sha → second call does NOT
    rewrite the file (mtime preserved).  Idempotency is load-bearing:
    operator re-runs of plan-backlinks must not re-pay the image-gen
    bill for unchanged articles."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="idemp0001")
    p1 = save_banner(art)
    mtime1 = p1.stat().st_mtime_ns

    import time
    time.sleep(0.01)
    p2 = save_banner(art)
    mtime2 = p2.stat().st_mtime_ns

    assert p1 == p2
    assert mtime1 == mtime2, "expected idempotent save to skip rewrite"


def test_save_banner_overwrites_size_mismatch(isolated_config_dir, monkeypatch):
    """If a file with the right sha exists but its bytes differ in
    length (corruption / interrupted prior write), overwrite it."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    # Seed a corrupted file
    art_path = isolated_config_dir / "banners" / "2026-05" / "corrupt01.png"
    art_path.parent.mkdir(parents=True)
    art_path.write_bytes(b"\x00")  # 1 byte — clearly wrong

    art = _artifact(_PNG, "image/png", sha="corrupt01")
    p = save_banner(art)

    assert p.read_bytes() == _PNG


def test_save_banner_creates_month_bucket(isolated_config_dir, monkeypatch):
    """First save of the month creates ``banners/YYYY-MM/`` (and the
    ``banners/`` parent if needed)."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-07")

    art = _artifact(_PNG, "image/png", sha="newmonth1")
    save_banner(art)

    assert (isolated_config_dir / "banners" / "2026-07").is_dir()


def test_save_banner_respects_env_var(tmp_path, monkeypatch):
    """``BACKLINK_PUBLISHER_CONFIG_DIR=/x`` → banner lands in ``/x/banners/``."""
    target = tmp_path / "custom"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(target))
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="envtest1")
    p = save_banner(art)

    assert p.is_relative_to(target / "banners")


def test_save_banner_uses_atomic_write(isolated_config_dir, monkeypatch):
    """Banner is written via tmp + ``os.replace`` (no .tmp residue).

    Regression: a previous storage impl used direct ``write_bytes``
    which leaves a half-written file on crash — readers see partial
    PNGs that fail to decode."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="atomic01")
    save_banner(art)

    tmp_residue = list((isolated_config_dir / "banners" / "2026-05").glob("*.tmp"))
    assert tmp_residue == [], f"unexpected .tmp residue: {tmp_residue}"


def test_path_for_artifact_returns_none_when_absent(isolated_config_dir, monkeypatch):
    """Pure query (no I/O): ``None`` when not previously saved."""
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="absent01")
    assert path_for_artifact(art) is None


def test_path_for_artifact_returns_path_when_present(isolated_config_dir, monkeypatch):
    import backlink_publisher.publishing.adapters.image_gen.storage as mod
    monkeypatch.setattr(mod, "_current_month_bucket", lambda: "2026-05")

    art = _artifact(_PNG, "image/png", sha="present01")
    saved = save_banner(art)
    assert path_for_artifact(art) == saved
