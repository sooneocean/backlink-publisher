"""Content-addressed banner storage — Plan 2026-05-20-001 Unit 3.

Persists ``BannerArtifact.data`` under
``<config_dir>/banners/<YYYY-MM>/<sha>.<ext>``.  Content-addressed
so a re-run of ``plan-backlinks`` for an unchanged article never
re-pays the image-gen bill — the same prompt → same sha → same
file → save is a no-op.

Banner files are NOT secrets (they get embedded in public backlink
articles); we don't tighten perms below the default umask.  The
parent ``banners/`` directory does inherit the 0700 perms of
``config_dir`` for consistency with the credential family.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from .types import BannerArtifact

_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def _current_month_bucket() -> str:
    """Return ``YYYY-MM`` for today in UTC.

    Indirected through a module-level function so tests can pin the
    bucket without having to monkeypatch ``datetime``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _banner_root() -> Path:
    """Return ``<config_dir>/banners`` re-resolving the env var
    every call."""
    from backlink_publisher import config as _cfg
    return _cfg._config_dir() / "banners"


def _path_for(prompt_sha: str, mime: str, month: str) -> Path:
    ext = _MIME_TO_EXT.get(mime)
    if ext is None:
        raise ValueError(
            f"unsupported mime for banner storage: {mime!r}. "
            "Expected one of: image/png, image/jpeg, image/webp."
        )
    return _banner_root() / month / f"{prompt_sha}.{ext}"


def path_for_artifact(artifact: BannerArtifact) -> Path | None:
    """Pure query — no I/O writes.

    Returns the path where ``artifact`` is (or would be) stored if it
    exists on disk, else ``None``.  Useful for plan-backlinks to
    decide whether to skip the adapter call when an artifact already
    exists from a previous run (storage cache hit).
    """
    target = _path_for(artifact.prompt_sha, artifact.mime, _current_month_bucket())
    return target if target.exists() else None


def save_banner(artifact: BannerArtifact) -> Path:
    """Persist ``artifact.data`` and return the file path.

    Idempotent: if a file with the matching ``prompt_sha.ext`` already
    exists in this month's bucket AND has the same byte length, the
    save is a no-op (mtime is preserved).  Length mismatch triggers a
    rewrite to recover from a previously corrupted / interrupted save.

    Write is atomic: ``<path>.tmp`` is created then ``os.replace``-d
    into the destination, so a crash mid-write can never leave the
    canonical path in a half-written state.
    """
    target = _path_for(artifact.prompt_sha, artifact.mime, _current_month_bucket())
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists() and target.stat().st_size == len(artifact.data):
        # Idempotent: same content already on disk.  Preserve mtime so
        # downstream caches (rsync, build systems) don't see spurious
        # updates.
        return target

    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(artifact.data)
    os.replace(tmp, target)
    return target
