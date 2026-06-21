"""Deterministic tests for the quality-gate CLI."""

from __future__ import annotations

import io
import json
import sys

from backlink_publisher.cli import quality_gate
from backlink_publisher.events.kinds import PUBLISH_QUALITY_BLOCKED
from backlink_publisher.events.store import EventStore


def _run_quality_gate(monkeypatch, rows: list[dict], argv: list[str] | None = None):
    stdin = io.StringIO("".join(json.dumps(row) + "\n" for row in rows))
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    quality_gate.main(argv or [])

    return stdout.getvalue(), stderr.getvalue()


def _jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_quality_gate_passes_clean_row_to_stdout(monkeypatch):
    row = {
        "target_url": "https://example.com/a",
        "title": "Safe draft",
        "content_markdown": "plain body without backlink stuffing",
    }

    stdout, stderr = _run_quality_gate(monkeypatch, [row])

    assert _jsonl(stdout) == [row]
    assert "quality-gate: 1 passed, 0 blocked" in stderr


def test_quality_gate_blocks_high_anchor_density(monkeypatch):
    row = {
        "target_url": "https://example.com/a",
        "title": "Stuffed draft",
        "content_markdown": "[target](https://example.com/a) tiny body",
    }

    stdout, stderr = _run_quality_gate(monkeypatch, [row])

    assert stdout == ""
    assert "quality-gate: blocked [Stuffed draft]" in stderr
    assert "anchor_density_high" in stderr
    assert "quality-gate: 0 passed, 1 blocked" in stderr


def test_quality_gate_emit_events_records_required_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    row = {
        "target_url": "https://example.com/a",
        "host": "example.com",
        "draft_label": "seed_1",
        "content_markdown": "[target](https://example.com/a) tiny body",
    }

    stdout, stderr = _run_quality_gate(monkeypatch, [row], ["--emit-events"])

    assert stdout == ""
    assert "anchor_density_high" in stderr
    rows = EventStore().query(
        "SELECT kind, target_url, host, payload_json FROM events WHERE kind = ?",
        (PUBLISH_QUALITY_BLOCKED,),
    )
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert rows[0]["target_url"] == "https://example.com/a"
    assert rows[0]["host"] == "example.com"
    assert payload == {
        "draft_label": "seed_1",
        "quality_check": "anchor_density_high",
    }


def test_quality_gate_filters_when_run_as_auto_recover_subprocess(
    monkeypatch, tmp_path
):
    """Regression (PR #8): auto-recover Phase 4 pipes seeds through the REAL
    quality-gate CLI as a subprocess (`python -m ...cli.quality_gate`).

    Why prior code allowed the bug: quality_gate.py shipped referencing an
    undefined ``_MD_LINK_RE`` (and using ``re``/``json`` without importing them),
    so every subprocess invocation crashed. ``_pipe_through_cli`` swallows a
    non-zero exit and returns the seeds UNCHANGED, so the dead stage degraded
    silently — and every auto_recover test mocks ``subprocess``, so nothing
    exercised the real CLI. Broken CI (no ``[dev]`` extra) meant the suite never
    ran to catch the in-process tests either.

    This runs the actual subprocess through auto-recover's own wrapper and proves
    the stage is alive: a high-anchor-density row is filtered out, a clean row
    survives. If quality-gate breaks again the silent fallback returns the seed
    unchanged and this fails — instead of skipping quality control unnoticed.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.cli.auto_recover import _pipe_through_cli

    clean = {
        "target_url": "https://example.com/a",
        "title": "Clean",
        "content_markdown": "plain body without any backlink stuffing here",
    }
    stuffed = {
        "target_url": "https://example.com/b",
        "title": "Stuffed",
        "content_markdown": "[t](https://example.com/b) tiny",
    }

    out = _pipe_through_cli([clean, stuffed], "backlink_publisher.cli.quality_gate")

    labels = {r.get("title") for r in out}
    assert "Clean" in labels, "clean row must pass the quality gate"
    assert "Stuffed" not in labels, (
        "high-anchor-density row must be filtered by the real quality-gate "
        "subprocess; a silent non-zero-exit fallback would let it through"
    )
