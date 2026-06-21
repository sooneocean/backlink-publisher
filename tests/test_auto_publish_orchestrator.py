"""Deterministic tests for the auto-publish orchestrator."""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest

from backlink_publisher.automation import orchestrator
from backlink_publisher.automation._state import get_current_state


def _write_config(config_dir: Path, body: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(body, encoding="utf-8")


def _write_canary_health(config_dir: Path, payload: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "canary-health.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _run_auto_publish(
    monkeypatch,
    rows: list[dict],
    argv: list[str] | None = None,
) -> tuple[str, str]:
    stdin = io.StringIO("".join(json.dumps(row) + "\n" for row in rows))
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    import backlink_publisher.config_echo as config_echo

    monkeypatch.setattr(config_echo, "emit_banner", lambda *_args, **_kwargs: None)
    orchestrator.main(argv or [])
    return stdout.getvalue(), stderr.getvalue()


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_auto_publish_dry_run_does_not_call_full_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    def boom(*_args, **_kwargs):
        raise AssertionError("dry-run must not call full publish pipeline")

    monkeypatch.setattr(orchestrator, "_run_full_pipeline", boom)

    stdout, stderr = _run_auto_publish(
        monkeypatch,
        [{"target_url": "https://target.example/a", "platform": "telegraph"}],
    )

    rows = _jsonl(stdout)
    assert rows == [
        {
            "seed": {"target_url": "https://target.example/a", "platform": "telegraph"},
            "would_publish": True,
            "platform_status": "healthy",
        }
    ]
    assert "dry preview" in stderr
    assert get_current_state() is None


def test_auto_publish_hard_skip_config_does_not_block_healthy_platform(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[canary.telegraph]",
                'post_url = "https://publisher.example/canary"',
                'expected_target = "https://target.example/a"',
                "hard_skip = true",
            ]
        )
        + "\n",
    )

    stdout, stderr = _run_auto_publish(
        monkeypatch,
        [{"target_url": "https://target.example/a", "platform": "telegraph"}],
    )

    assert _jsonl(stdout)[0]["would_publish"] is True
    assert "hard-skip platforms detected" not in stderr
    assert get_current_state() is None


def test_auto_publish_hard_skip_blocks_quarantined_platform(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _write_config(
        tmp_path,
        "\n".join(
            [
                "[canary.telegraph]",
                'post_url = "https://publisher.example/canary"',
                'expected_target = "https://target.example/a"',
                "hard_skip = true",
            ]
        )
        + "\n",
    )
    _write_canary_health(
        tmp_path,
        {
            "telegraph": {
                "status": "drift-confirmed",
                "consecutive_failures": 2,
                "consecutive_oks": 0,
                "quarantined": True,
            }
        },
    )

    with pytest.raises(SystemExit) as exc:
        _run_auto_publish(
            monkeypatch,
            [{"target_url": "https://target.example/a", "platform": "telegraph"}],
        )

    assert exc.value.code == orchestrator.EXIT_HARD_SKIP
    assert get_current_state() is None
    rows = _jsonl(sys.stdout.getvalue())
    assert rows[0]["event"] == "auto_publish_hard_skip_advisory"
    assert rows[0]["platforms"] == ["telegraph"]


def test_auto_publish_lock_contention_exits_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    @contextlib.contextmanager
    def locked():
        yield False

    monkeypatch.setattr(orchestrator, "_single_run_lock", locked)

    stdout, stderr = _run_auto_publish(
        monkeypatch,
        [{"target_url": "https://target.example/a", "platform": "telegraph"}],
    )

    assert stdout == ""
    assert "another run holds the lock" in stderr
    assert get_current_state() is None
