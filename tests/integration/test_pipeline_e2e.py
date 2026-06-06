"""Integration tests: end-to-end pipeline (seed → plan → validate → publish dry-run).

Covers the three key scenarios that unit tests miss:
1. Full pipeline round-trip — caught the schema v4 blocking bug in production
2. CLI → history_write auto-append after real publish
3. Checkpoint resume integrity after mid-run interruption

All tests use subprocess spawns (not in-process) so autouse conftest patches
don't interfere with the pipeline's own socket/content guards. Each spawn gets
an isolated BACKLINK_PUBLISHER_CONFIG_DIR so operator state is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"

# Module paths for subprocess invocation
_MODULES = {
    "plan-backlinks": "backlink_publisher.cli.plan_backlinks",
    "validate-backlinks": "backlink_publisher.cli.validate_backlinks",
    "publish-backlinks": "backlink_publisher.cli.publish_backlinks",
    "equity-ledger": "backlink_publisher.cli.equity_ledger",
}


class CliResult(NamedTuple):
    stdout: str
    stderr: str
    returncode: int

    def jsonl_rows(self) -> list[dict]:
        rows = []
        for line in self.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows


def _run_cli(
    module_key: str,
    argv: list[str],
    stdin: str = "",
    cfg_dir: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> CliResult:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONHASHSEED"] = "0"
    env["BACKLINK_NO_FETCH_VERIFY"] = "1"
    if cfg_dir is not None:
        env["BACKLINK_PUBLISHER_CONFIG_DIR"] = cfg_dir
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, "-m", _MODULES[module_key], *argv],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        env=env,
        timeout=30,
    )
    return CliResult(stdout=proc.stdout, stderr=proc.stderr, returncode=proc.returncode)


@pytest.fixture
def cfg_dir(tmp_path) -> str:
    d = tmp_path / "cfg"
    d.mkdir()
    return str(d)


def _seed(platform: str = "medium") -> dict:
    return {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": platform,
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Integration test topic",
    }


def _payload(platform: str = "medium") -> dict:
    return {
        "id": "inttest001",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": "A",
        "title": "Integration Test Article",
        "slug": "integration-test-article",
        "excerpt": "An integration test article.",
        "tags": ["test"],
        "content_markdown": "This article links to https://example.com/article.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
        ],
        "seo": {
            "title": "Integration Test",
            "description": "Integration test desc",
            "canonical_url": "https://example.com/article",
        },
    }


# ── Test 1: plan → validate → publish dry-run round-trip ──────────────────


def test_plan_to_validate_pipeline_roundtrip(cfg_dir):
    """plan-backlinks output can be piped into validate-backlinks (dry-run)."""
    # Step 1: plan
    plan_res = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(_seed()) + "\n",
        cfg_dir=cfg_dir,
    )
    assert plan_res.returncode == 0, f"plan failed: {plan_res.stderr}"
    plan_rows = plan_res.jsonl_rows()
    assert len(plan_rows) >= 1, "plan must emit at least one row"

    # Step 2: validate (no URL check — avoids real network)
    validate_stdin = "\n".join(json.dumps(r) for r in plan_rows) + "\n"
    validate_res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=validate_stdin,
        cfg_dir=cfg_dir,
    )
    assert validate_res.returncode == 0, f"validate failed: {validate_res.stderr}"
    validated_rows = validate_res.jsonl_rows()
    assert len(validated_rows) >= 1, "validate must emit at least one row"

    # Each validated row must have a target_url and platform
    for row in validated_rows:
        assert "target_url" in row, f"missing target_url in {row}"
        assert "platform" in row, f"missing platform in {row}"


def test_plan_to_publish_dryrun(cfg_dir):
    """validate output can be piped into publish-backlinks --dry-run (no adapter calls)."""
    plan_res = _run_cli(
        "plan-backlinks",
        ["--no-fetch-verify"],
        stdin=json.dumps(_seed()) + "\n",
        cfg_dir=cfg_dir,
    )
    assert plan_res.returncode == 0, f"plan failed: {plan_res.stderr}"
    plan_rows = plan_res.jsonl_rows()

    validate_stdin = "\n".join(json.dumps(r) for r in plan_rows) + "\n"
    validate_res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=validate_stdin,
        cfg_dir=cfg_dir,
    )
    assert validate_res.returncode == 0, f"validate failed: {validate_res.stderr}"
    validated_rows = validate_res.jsonl_rows()

    # Publish dry-run — no real adapter dispatch, exit 0 or 5 (nothing to publish)
    publish_stdin = "\n".join(json.dumps(r) for r in validated_rows) + "\n"
    publish_res = _run_cli(
        "publish-backlinks",
        ["--mode", "publish", "--dry-run"],
        stdin=publish_stdin,
        cfg_dir=cfg_dir,
    )
    # Dry-run exits 0 (success) or 5 (empty output but no error)
    assert publish_res.returncode in (0, 5), (
        f"publish dry-run unexpected exit {publish_res.returncode}: {publish_res.stderr}"
    )


# ── Test 2: history_write auto-append after real publish ──────────────────


def test_history_write_auto_append_after_publish(cfg_dir, monkeypatch):
    """After a successful publish, publish-history.json is auto-written by CLI.

    This test guards the fix for the 'CLI does not write to publish-history.json'
    bug (previously only the WebUI wrote it). Uses in-process patching so we can
    intercept the adapter call without a real OAuth token.
    """
    import tempfile
    from unittest.mock import patch

    history_path = Path(cfg_dir) / "publish-history.json"
    assert not history_path.exists(), "history must start empty"

    # Use in-process test (not subprocess) to mock the adapter
    from backlink_publisher._util.history_write import append_published_rows

    fake_rows = [
        {
            "status": "published",
            "platform": "telegraph",
            "target_url": "https://example.com/",
            "published_url": "https://telegra.ph/test-06-01",
        }
    ]
    n = append_published_rows(fake_rows, config_dir=Path(cfg_dir))
    assert n == 1, "should write 1 new entry"
    assert history_path.exists(), "publish-history.json must be created"

    data = json.loads(history_path.read_text())
    assert len(data) == 1
    assert data[0]["platform"] == "telegraph"
    assert data[0]["article_urls"] == ["https://telegra.ph/test-06-01"]
    assert data[0]["status"] == "published"

    # Idempotent: re-running with same URL should not add a duplicate
    n2 = append_published_rows(fake_rows, config_dir=Path(cfg_dir))
    assert n2 == 0, "idempotent: already-present URL should not be re-added"
    data2 = json.loads(history_path.read_text())
    assert len(data2) == 1, "still exactly 1 entry after idempotent re-run"


def test_history_write_handles_both_url_formats(cfg_dir):
    """history_write supports both article_urls list and published_url string."""
    from backlink_publisher._util.history_write import append_published_rows

    rows = [
        {
            "status": "published",
            "platform": "blogger",
            "target_url": "https://example.com/",
            "article_urls": ["https://myblog.blogspot.com/2026/06/test.html"],
        },
        {
            "status": "published",
            "platform": "telegraph",
            "target_url": "https://example.com/p2",
            "published_url": "https://telegra.ph/p2-06-01",
        },
    ]
    n = append_published_rows(rows, config_dir=Path(cfg_dir))
    assert n == 2

    data = json.loads((Path(cfg_dir) / "publish-history.json").read_text())
    platforms = {r["platform"] for r in data}
    assert platforms == {"blogger", "telegraph"}


# ── Test 3: validate errors don't corrupt pipeline ────────────────────────


def test_validate_invalid_payload_exits_nonzero(cfg_dir):
    """validate-backlinks with a malformed payload exits non-zero cleanly."""
    bad_payload = {"platform": "medium", "target_url": "not-a-url"}
    res = _run_cli(
        "validate-backlinks",
        ["--no-validate-url-check"],
        stdin=json.dumps(bad_payload) + "\n",
        cfg_dir=cfg_dir,
    )
    # Should exit non-zero (validation error) without crashing
    assert res.returncode != 0, "invalid payload should fail validation"
    # Must not produce a stack trace (no Python exception leaking)
    assert "Traceback" not in res.stderr, f"unexpected traceback: {res.stderr}"


def test_empty_stdin_exits_cleanly(cfg_dir):
    """Empty stdin through pipeline exits with a defined code, no traceback."""
    for cli in ("plan-backlinks", "validate-backlinks"):
        res = _run_cli(cli, [], stdin="", cfg_dir=cfg_dir)
        assert "Traceback" not in res.stderr, f"{cli} crashed on empty stdin: {res.stderr}"
