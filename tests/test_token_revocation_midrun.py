"""Tests for mid-run config drift detection (token revocation)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

import backlink_publisher.config.tokens as tokens_mod
from backlink_publisher.cli._publish_helpers import _check_token_drift
from backlink_publisher.cli.publish_backlinks import _run_resume
from backlink_publisher.config import snapshot_token_revs
from backlink_publisher.config.tokens import save_blogger_token, save_medium_token


def _spy_load_token(monkeypatch):
    """Record every credential filename ``snapshot_token_revs`` actually reads."""
    seen: list[str] = []
    orig = tokens_mod._load_token

    def spy(path, filename):
        seen.append(filename)
        return orig(path, filename)

    monkeypatch.setattr(tokens_mod, "_load_token", spy)
    return seen


def test_snapshot_scans_all_known_files_by_default(tmp_path, monkeypatch):
    """No filter (the run-start baseline snapshot) scans every known file."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    seen = _spy_load_token(monkeypatch)
    snapshot_token_revs()
    # Derive from the source of truth so adding a token platform doesn't break this.
    assert len(seen) == len(tokens_mod._TOKEN_FILES) and "blogger-token.json" in seen


def test_snapshot_honors_platform_filter(tmp_path, monkeypatch):
    """A platform filter limits the scan to exactly those files."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    seen = _spy_load_token(monkeypatch)
    snapshot_token_revs(["blogger", "medium"])
    assert set(seen) == {"blogger-token.json", "medium-token.json"}


def test_snapshot_empty_filter_scans_nothing(tmp_path, monkeypatch):
    """An empty filter (no platform bound at start) reads zero files —
    distinct from None (scan all)."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    seen = _spy_load_token(monkeypatch)
    snapshot_token_revs([])
    assert seen == []


def test_check_token_drift_rescans_only_initial_platforms(tmp_path, monkeypatch):
    """The per-row drift check re-reads only the platforms present at run-start
    (initial_revs), NOT all 10 — that 10xN re-read was the publish hot-loop waste.
    Behavior is unchanged: only an already-bound platform's rotation aborts."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    save_blogger_token({"client_id": "a", "client_secret": "b"})  # rev=1
    initial = snapshot_token_revs()
    assert initial == {"blogger": 1}

    seen = _spy_load_token(monkeypatch)
    _check_token_drift(initial)  # no drift → returns without aborting
    assert seen == ["blogger-token.json"]  # only the bound platform, not 10 files


def test_check_token_drift_detects_rotation_of_any_bound_platform(tmp_path, monkeypatch):
    """With 2+ platforms bound at start, rotating ANY one is still detected after
    the per-row scan was narrowed to initial_revs.keys() — the safety property
    this optimization must preserve."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    save_blogger_token({"client_id": "a", "client_secret": "b"})  # rev=1
    save_medium_token({"token": "m"})  # rev=1
    initial = snapshot_token_revs()
    assert initial == {"blogger": 1, "medium": 1}

    save_medium_token({"token": "m2"})  # rotate medium mid-run → rev=2
    with pytest.raises(SystemExit) as exc:
        _check_token_drift(initial)
    assert exc.value.code == 3  # aborts on the rotated (non-first) platform


def test_check_token_drift_ignores_platform_bound_after_start(tmp_path, monkeypatch):
    """A credential file CREATED mid-run (absent at run-start, so not in
    initial_revs) does NOT abort — only already-bound platforms are tracked."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    save_blogger_token({"client_id": "a", "client_secret": "b"})
    initial = snapshot_token_revs()
    assert initial == {"blogger": 1}

    save_medium_token({"token": "m"})  # newly bound after run-start
    _check_token_drift(initial)  # must NOT raise — medium was never tracked


def test_token_drift_aborts_mid_run(tmp_path, monkeypatch):
    """If a token is updated mid-run, the publisher must abort with exit 3.

    Mid-run credential revocation is a dependency/auth condition → exit 3
    (DependencyError family, per the AGENTS.md 0-6 contract), not the
    undocumented 45 this previously emitted.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    
    # Setup initial token with token_rev=1
    save_blogger_token({"client_id": "a", "client_secret": "b"})
    
    # Mock payload
    ckpt = {
        "platform": "blogger",
        "mode": "draft",
        "items": [
            {
                "id": "r0",
                "status": "pending",
                "payload": {"target_url": "https://x.com/a"},
            },
            {
                "id": "r1",
                "status": "pending",
                "payload": {"target_url": "https://x.com/b"},
            },
        ],
    }

    # Mock adapter_publish to increment the token_rev on the first call
    call_count = {"n": 0}

    def fake_publish(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate WebUI saving a new token mid-run
            save_blogger_token({"client_id": "new", "client_secret": "new"})
        from backlink_publisher.publishing.adapters import AdapterResult
        return AdapterResult(status="drafted", adapter="blogger-api", platform="blogger")

    with patch("backlink_publisher.cli._resume.adapter_publish", side_effect=fake_publish):
        with patch("backlink_publisher.cli._resume.verify_adapter_setup"):
            with patch("backlink_publisher.checkpoint.load_checkpoint", return_value=ckpt):
                with patch("backlink_publisher.cli.publish_backlinks._acquire_publish_leases"):
                    with patch("backlink_publisher.checkpoint.update_item"):
                        class DummyArgs:
                            resume = "20260101T000000Z-deadbeef"
                            dry_run = False
                            skip_publish_time_check = True
                            no_verify = True
                        
                        raised = None
                        try:
                            _run_resume(DummyArgs())
                        except SystemExit as exc:
                            raised = exc

    assert raised is not None, "Expected SystemExit on mid-run token drift"
    assert raised.code == 3, "Should exit 3 (DependencyError) due to config drift"
    assert call_count["n"] == 1, "Should have aborted before processing r1"
