"""Tests for ``backlink_publisher.cli.frw_login`` CLI.

Plan: docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md
Unit 1 — interactive 0600 token writer for the FRW image-gen API key.

The CLI is intentionally minimal — there is no browser binding (unlike
``velog-login`` / ``medium-login``), just an interactive prompt that
writes the operator's key into ``~/.config/backlink-publisher/frw-token.json``
with 0600 perms.

Test-first per plan Execution note.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from backlink_publisher.cli import frw_login


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


# ── Banner ──────────────────────────────────────────────────────────────────


def test_main_prints_banner_to_stderr(isolated_config_dir, capsys):
    """Every login alias in the repo prints a one-line banner to stderr
    first (see velog_login.py / medium_login.py).  frw-login mirrors
    that convention for consistency."""
    frw_login.main([], _input_provider=lambda prompt: "sk_x")

    captured = capsys.readouterr()
    assert "frw-login" in captured.err
    # The exact wording is intentionally not asserted — we only require
    # an informational banner on stderr.


# ── Happy path ──────────────────────────────────────────────────────────────


def test_main_writes_token_file(isolated_config_dir):
    """Operator pastes a key → file written at the right path with
    correct 0600 perms and ``{"api_key": "..."}`` schema."""
    frw_login.main([], _input_provider=lambda prompt: "sk_happy_path")

    target = isolated_config_dir / "frw-token.json"
    assert target.exists()

    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode == 0o600

    data = json.loads(target.read_text())
    assert data == {"api_key": "sk_happy_path"}


def test_main_rotates_existing_token(isolated_config_dir):
    """Rerunning ``frw-login`` over an existing token archives the old
    one (key rotation post-suspected-leak workflow)."""
    frw_login.main([], _input_provider=lambda prompt: "sk_first")
    frw_login.main([], _input_provider=lambda prompt: "sk_second")

    target = isolated_config_dir / "frw-token.json"
    assert json.loads(target.read_text())["api_key"] == "sk_second"

    orphans = list(isolated_config_dir.glob("frw-token.json.orphaned-*"))
    assert len(orphans) == 1
    assert json.loads(orphans[0].read_text())["api_key"] == "sk_first"


# ── Error paths ─────────────────────────────────────────────────────────────


def test_empty_input_is_usage_error(isolated_config_dir, capsys):
    """Empty stdin → UsageError exit 1 (NOT silently writing an empty
    api_key).  Argparse ``choices=`` MUST NOT be used here — UsageError
    is exit 1 in this repo, argparse choices= would map to exit 2,
    breaking the contract (feedback_argparse_choices_vs_usage_error)."""
    with pytest.raises(SystemExit) as exc_info:
        frw_login.main([], _input_provider=lambda prompt: "")

    assert exc_info.value.code == 1
    assert not (isolated_config_dir / "frw-token.json").exists()


def test_whitespace_only_input_is_usage_error(isolated_config_dir):
    """``   \n`` after stripping is still empty → reject."""
    with pytest.raises(SystemExit) as exc_info:
        frw_login.main([], _input_provider=lambda prompt: "   \n")

    assert exc_info.value.code == 1


# ── Env var respected ───────────────────────────────────────────────────────


def test_respects_backlink_publisher_config_dir(tmp_path, monkeypatch):
    """``BACKLINK_PUBLISHER_CONFIG_DIR=/x`` → token lands in ``/x``.

    Regression for ``feedback_config_paths_must_respect_env_var`` —
    must never freeze ``Path.home()`` at import time."""
    target_dir = tmp_path / "custom_config"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(target_dir))

    frw_login.main([], _input_provider=lambda prompt: "sk_env_test")

    assert (target_dir / "frw-token.json").exists()


def test_parent_directory_created_with_0700(tmp_path, monkeypatch):
    """Bootstrap: missing parent dir gets ``mkdir -p`` with 0700
    perms.  No clobbered umask leaking a sub-dir to 0755."""
    nested = tmp_path / "fresh" / "config"
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(nested))

    frw_login.main([], _input_provider=lambda prompt: "sk_nested")

    assert nested.exists()
    parent_mode = stat.S_IMODE(os.stat(nested).st_mode)
    assert parent_mode == 0o700
