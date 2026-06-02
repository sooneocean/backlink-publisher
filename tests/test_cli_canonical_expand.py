"""Tests for cli/canonical_expand.py — canonical-mode row fan-out."""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

import pytest

import backlink_publisher.cli.canonical_expand as ce
from backlink_publisher.cli.canonical_expand import CANONICAL_PLATFORMS, expand_canonical_row


# ---------------------------------------------------------------------------
# Unit: expand_canonical_row
# ---------------------------------------------------------------------------

def test_passthrough_when_no_canonical_mode():
    row = {"target_url": "https://example.com/", "channel": "velog"}
    result = expand_canonical_row(row)
    assert result == [row]


def test_passthrough_when_canonical_mode_false():
    row = {"target_url": "https://example.com/", "canonical_mode": False}
    result = expand_canonical_row(row)
    assert result == [row]


def test_expands_to_all_canonical_platforms():
    row = {
        "target_url": "https://example.com/",
        "canonical_mode": True,
        "seo": {"canonical_url": "https://mysite.com/article"},
    }
    result = expand_canonical_row(row)
    assert len(result) == len(CANONICAL_PLATFORMS)
    channels = {r["channel"] for r in result}
    assert channels == CANONICAL_PLATFORMS


def test_canonical_url_from_row_seo():
    row = {
        "target_url": "https://example.com/",
        "canonical_mode": True,
        "seo": {"canonical_url": "https://mysite.com/article"},
    }
    for out_row in expand_canonical_row(row):
        assert out_row["seo"]["canonical_url"] == "https://mysite.com/article"


def test_canonical_url_from_default_arg():
    row = {"target_url": "https://example.com/", "canonical_mode": True}
    result = expand_canonical_row(row, default_canonical_url="https://mysite.com/default")
    for out_row in result:
        assert out_row["seo"]["canonical_url"] == "https://mysite.com/default"


def test_row_seo_canonical_takes_priority_over_default():
    row = {
        "target_url": "https://example.com/",
        "canonical_mode": True,
        "seo": {"canonical_url": "https://mysite.com/from-row"},
    }
    result = expand_canonical_row(row, default_canonical_url="https://mysite.com/from-default")
    for out_row in result:
        assert out_row["seo"]["canonical_url"] == "https://mysite.com/from-row"


def test_canonical_mode_key_removed_from_output():
    row = {"target_url": "https://example.com/", "canonical_mode": True}
    for out_row in expand_canonical_row(row):
        assert "canonical_mode" not in out_row


def test_source_row_not_mutated():
    row = {"target_url": "https://example.com/", "canonical_mode": True}
    expand_canonical_row(row)
    assert row.get("canonical_mode") is True  # original untouched


def test_no_canonical_url_when_none_provided():
    row = {"target_url": "https://example.com/", "canonical_mode": True}
    for out_row in expand_canonical_row(row):
        assert out_row.get("seo", {}).get("canonical_url") is None


# ---------------------------------------------------------------------------
# CLI: main() — stdin/stdout plumbing
# ---------------------------------------------------------------------------

def _run_cli(jsonl_input: str, extra_argv: list[str] | None = None) -> tuple[str, str]:
    """Run main() with stdin=jsonl_input; return (stdout, stderr)."""
    import io
    argv = list(extra_argv or [])
    with (
        patch.object(sys, "stdin", io.StringIO(jsonl_input)),
        patch.object(sys, "stdout", io.StringIO()) as mock_stdout,
        patch.object(sys, "stderr", io.StringIO()) as mock_stderr,
    ):
        ce.main(argv)
    return mock_stdout.getvalue(), mock_stderr.getvalue()


def test_cli_passthrough_row_unchanged():
    row = {"target_url": "https://example.com/", "channel": "velog"}
    out, _ = _run_cli(json.dumps(row) + "\n")
    assert json.loads(out.strip()) == row


def test_cli_canonical_row_expanded():
    row = {"target_url": "https://t.com/", "canonical_mode": True,
           "seo": {"canonical_url": "https://mysite.com/a"}}
    out, _ = _run_cli(json.dumps(row) + "\n")
    lines = [l for l in out.strip().splitlines() if l]
    assert len(lines) == len(CANONICAL_PLATFORMS)
    channels = {json.loads(l)["channel"] for l in lines}
    assert channels == CANONICAL_PLATFORMS


def test_cli_default_canonical_url_flag():
    row = {"target_url": "https://t.com/", "canonical_mode": True}
    out, _ = _run_cli(json.dumps(row) + "\n", ["--canonical-url", "https://mysite.com/x"])
    for line in out.strip().splitlines():
        assert json.loads(line)["seo"]["canonical_url"] == "https://mysite.com/x"


def test_cli_empty_lines_skipped():
    row = {"target_url": "https://t.com/"}
    out, _ = _run_cli("\n" + json.dumps(row) + "\n\n")
    lines = [l for l in out.strip().splitlines() if l]
    assert len(lines) == 1


def test_cli_invalid_json_exits_one():
    with pytest.raises(SystemExit) as exc:
        _run_cli("not-json\n")
    assert exc.value.code == 1


def test_cli_non_object_json_exits_one():
    with pytest.raises(SystemExit) as exc:
        _run_cli("[1, 2, 3]\n")
    assert exc.value.code == 1


def test_cli_mixed_rows():
    rows = [
        {"target_url": "https://t1.com/", "canonical_mode": True,
         "seo": {"canonical_url": "https://mysite.com/a"}},
        {"target_url": "https://t2.com/", "channel": "velog"},
    ]
    stdin = "\n".join(json.dumps(r) for r in rows) + "\n"
    out, _ = _run_cli(stdin)
    lines = [l for l in out.strip().splitlines() if l]
    # first row expands to N platforms + second row pass-through
    assert len(lines) == len(CANONICAL_PLATFORMS) + 1


def test_canonical_platforms_does_not_include_velog_or_telegraph():
    assert "velog" not in CANONICAL_PLATFORMS
    assert "telegraph" not in CANONICAL_PLATFORMS
