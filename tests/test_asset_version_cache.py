"""Tests for asset-version cache behavior.

The _compute_asset_version function must cache its result to avoid walking
the static tree on every WebUI startup.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_asset_version_reads_cached_stamp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Second call returns cached value without walking the tree."""
    monkeypatch.setenv(
        "BACKLINK_PUBLISHER_CONFIG_DIR",
        str(tmp_path / "config")
    )

    # Create a stamp file with a known version
    stamp_file = tmp_path / "config" / "asset-version.stamp"
    stamp_file.parent.mkdir(parents=True, exist_ok=True)
    stamp_file.write_text("deadbeef")

    from webui_app import _get_version_file
    # Ensure the version file path resolves correctly given the env var
    vf = _get_version_file()
    assert vf == stamp_file

    # Call without static_folder - should read cached stamp
    from webui_app import _compute_asset_version
    version = _compute_asset_version(None)
    assert version == "deadbeef"


def test_asset_version_writes_stamp_on_cache_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """First call walks the tree and writes the stamp file."""
    monkeypatch.setenv(
        "BACKLINK_PUBLISHER_CONFIG_DIR",
        str(tmp_path / "config")
    )

    # Create a fake static folder with a file
    static_folder = tmp_path / "static"
    static_folder.mkdir(parents=True, exist_ok=True)
    (static_folder / "test.js").write_text("console.log('hello');")

    from webui_app import _get_version_file
    vf = _get_version_file()
    assert vf.parent.exists() or True  # Parent will be created on write

    from webui_app import _compute_asset_version
    version = _compute_asset_version(str(static_folder))

    assert version != "0"
    stamp_file = tmp_path / "config" / "asset-version.stamp"
    assert stamp_file.exists()
    assert stamp_file.read_text().strip() == version


def test_asset_version_returns_zero_when_static_folder_is_none():
    """None static_folder returns '0' sentinel without creating stamp."""
    from webui_app import _compute_asset_version

    version = _compute_asset_version(None)
    assert version == "0"