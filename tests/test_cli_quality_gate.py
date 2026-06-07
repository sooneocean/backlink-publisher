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
