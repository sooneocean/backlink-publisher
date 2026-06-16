"""Tests for backlink_publisher.io_utils."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from backlink_publisher._util.io import atomic_write_json


def test_atomic_write_creates_file_with_default_0600(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"a": 1, "b": "二"})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": "二"}
    if hasattr(target.stat(), "st_mode"):
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_overwrites_existing_file(tmp_path):
    target = tmp_path / "out.json"
    target.write_text(json.dumps({"old": True}), encoding="utf-8")

    atomic_write_json(target, {"new": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}


def test_atomic_write_preserves_permissions_on_overwrite(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"v": 1})

    atomic_write_json(target, {"v": 2})

    if hasattr(target.stat(), "st_mode"):
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_atomic_write_custom_mode(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_json(target, {"v": 1}, mode=0o644)

    if hasattr(target.stat(), "st_mode"):
        assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_atomic_write_uses_temp_then_replace(tmp_path):
    """Confirm the helper writes via a .tmp sibling rather than truncating in place."""
    target = tmp_path / "out.json"
    tmp_path_seen: list[Path] = []

    original_write_text = Path.write_text

    def spy_write_text(self, data, encoding=None, **kwargs):
        if str(self).endswith(".tmp"):
            tmp_path_seen.append(self)
        return original_write_text(self, data, encoding=encoding, **kwargs)

    with patch.object(Path, "write_text", spy_write_text):
        atomic_write_json(target, {"v": 1})

    assert tmp_path_seen, "expected atomic_write_json to write to a .tmp sibling first"
    assert tmp_path_seen[0].name == f"out.json.{os.getpid()}.tmp"
    assert not tmp_path_seen[0].exists(), "temp file should have been renamed away"


def test_atomic_write_unicode_roundtrip(tmp_path):
    target = tmp_path / "zh.json"
    atomic_write_json(target, {"site": "51漫画首页", "list": ["热门漫画", "本周热门"]})

    # ensure_ascii=False is what lets non-ASCII land directly without escapes
    raw = target.read_text(encoding="utf-8")
    assert "51漫画首页" in raw
    assert json.loads(raw)["list"] == ["热门漫画", "本周热门"]


def test_atomic_write_parent_must_exist(tmp_path):
    target = tmp_path / "nope" / "out.json"
    with pytest.raises((OSError, FileNotFoundError)):
        atomic_write_json(target, {"v": 1})


def test_atomic_write_silences_chmod_error(tmp_path):
    """chmod failures must not raise — the rename is the load-bearing step."""
    target = tmp_path / "out.json"

    with patch("backlink_publisher._util.io.os.chmod", side_effect=OSError("nope")):
        atomic_write_json(target, {"v": 1})

    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == {"v": 1}
