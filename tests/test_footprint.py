"""Tests for the self-fingerprint auditor."""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from backlink_publisher.footprint import (
    LinkSignature,
    analyze_corpus,
    extract_link_signatures,
    format_report_markdown,
)


# ── extract_link_signatures ─────────────────────────────────────────────────


def test_extract_link_signatures_empty_returns_empty():
    assert extract_link_signatures("") == []
    assert extract_link_signatures("plain text no links") == []


def test_extract_link_signatures_captures_attr_order():
    html = '<a href="https://x.com" target="_blank" rel="noopener noreferrer">x</a>'
    sigs = extract_link_signatures(html)
    assert len(sigs) == 1
    s = sigs[0]
    assert s.attr_name_order == ("href", "target", "rel")
    assert s.target_value == "_blank"
    assert s.rel_value == "noopener noreferrer"


def test_extract_link_signatures_detects_different_orders_distinct():
    """Two <a> tags with attributes in different orders produce different signatures."""
    html = """
    <a href="https://x.com" target="_blank" rel="noopener">x</a>
    and
    <a target="_blank" rel="noopener" href="https://x.com">x</a>
    """
    sigs = extract_link_signatures(html)
    assert len(sigs) == 2
    assert sigs[0].attr_name_order != sigs[1].attr_name_order


def test_extract_link_signatures_preceding_char():
    """Char immediately before <a is captured for separator-pattern detection."""
    html = 'word <a href="https://x.com">x</a> ,<a href="https://y.com">y</a>'
    sigs = extract_link_signatures(html)
    assert len(sigs) == 2
    assert sigs[0].preceding_char == " "
    assert sigs[1].preceding_char == ","


# ── analyze_corpus ──────────────────────────────────────────────────────────


def test_analyze_corpus_empty():
    report = analyze_corpus([])
    assert report.total_links == 0
    assert report.total_payloads == 0


def test_analyze_corpus_no_links_in_payloads():
    report = analyze_corpus(["just text", "more text"])
    assert report.total_links == 0
    assert report.total_payloads == 2
    assert report.payloads_without_links == 2


def test_analyze_corpus_byte_uniform_signals_cluster_key():
    """100% concentration on attribute order → that dimension is a cluster key.

    Load-bearing negative-shape: if extract_link_signatures ever returned
    None or skipped attribute capture, this test fails immediately.
    """
    # 5 articles, each emitting the exact same <a> template
    template = '<a href="https://{site}.com/p" target="_blank" rel="noopener noreferrer">x</a>'
    payloads = [template.format(site=f"site{i}") for i in range(5)]
    report = analyze_corpus(payloads)

    assert report.total_links == 5
    # 100% concentration on attribute order
    assert report.concentration_pct("attr_order") == 100.0
    assert report.concentration_pct("rel_value") == 100.0
    assert report.concentration_pct("target_value") == 100.0


def test_analyze_corpus_varied_emission_lower_concentration():
    """When articles vary their attribute orderings, concentration drops below 100%."""
    payloads = [
        '<a href="https://a.com" target="_blank" rel="noopener">x</a>',
        '<a target="_blank" href="https://b.com" rel="noopener">y</a>',
        '<a rel="noopener" href="https://c.com" target="_blank">z</a>',
    ]
    report = analyze_corpus(payloads)
    assert report.total_links == 3
    # 3 distinct orderings → each at 33%
    assert report.concentration_pct("attr_order") < 100.0
    assert len(report.attr_order_counts) == 3


def test_analyze_corpus_top_attr_order():
    """top_attr_order returns the most-common orderings in descending order."""
    payloads = [
        '<a href="https://a.com" target="_blank" rel="noopener">x</a>',
        '<a href="https://b.com" target="_blank" rel="noopener">y</a>',  # same as #1
        '<a target="_blank" href="https://c.com" rel="noopener">z</a>',
    ]
    report = analyze_corpus(payloads)
    top = report.top_attr_order(2)
    assert len(top) == 2
    assert top[0][0] == ("href", "target", "rel")
    assert top[0][1] == 2  # appears twice


# ── format_report_markdown ──────────────────────────────────────────────────


def test_format_report_markdown_no_links():
    report = analyze_corpus(["just text"])
    out = format_report_markdown(report)
    assert "No links found" in out


def test_format_report_markdown_flags_cluster_key_at_100pct():
    """Byte-uniform output → markdown report contains ⚠️ CLUSTER KEY."""
    template = '<a href="https://{}.com" target="_blank" rel="noopener noreferrer">x</a>'
    payloads = [template.format(f"site{i}") for i in range(10)]
    report = analyze_corpus(payloads)
    out = format_report_markdown(report)
    assert "⚠️ CLUSTER KEY" in out
    assert "100.0%" in out
    assert "noopener noreferrer" in out


def test_format_report_markdown_below_alarm_pct_is_ok():
    """When concentration drops below alarm_pct, dimension is marked OK."""
    payloads = [
        '<a href="https://a.com" target="_blank" rel="noopener">x</a>',
        '<a target="_blank" href="https://b.com" rel="noopener">y</a>',
        '<a rel="noopener" href="https://c.com" target="_blank">z</a>',
    ]
    report = analyze_corpus(payloads)
    out = format_report_markdown(report, alarm_pct=80.0)
    # Attr-order concentration is 33%, below the 80% alarm
    lines = [line for line in out.splitlines() if "Attribute order" in line]
    assert lines
    # Either no CLUSTER KEY for this row, or the verdict cell says OK
    assert "| OK |" in lines[0] or "CLUSTER KEY" not in lines[0]


# ── CLI integration ─────────────────────────────────────────────────────────


def _run_main(input_data: str, extra_args: list[str] | None = None) -> tuple[str, str, int]:
    from backlink_publisher.cli import footprint as cli_module
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = io.StringIO(input_data)
        out = io.StringIO()
        err = io.StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            cli_module.main(extra_args or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


def test_cli_markdown_output_flags_uniformity():
    payloads = [
        {
            "id": f"id-{i}",
            "content_html": '<a href="https://x.com" target="_blank" rel="noopener noreferrer">x</a>',
        }
        for i in range(5)
    ]
    stdin = "\n".join(json.dumps(p) for p in payloads)
    stdout, stderr, code = _run_main(stdin)
    assert code == 0
    assert "Footprint Audit" in stdout
    assert "⚠️ CLUSTER KEY" in stdout


def test_cli_json_output():
    payloads = [
        {
            "id": "id-1",
            "content_html": '<a href="https://x.com" target="_blank" rel="noopener">x</a>',
        }
    ]
    stdin = json.dumps(payloads[0])
    stdout, _, code = _run_main(stdin, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    assert data["total_links"] == 1
    assert data["concentration_pct"]["attr_order"] == 100.0


def test_cli_picks_content_markdown_when_no_html():
    """Falls back to content_markdown when content_html is absent."""
    payload = {
        "id": "id-1",
        "content_markdown": '<a href="https://x.com" target="_blank" rel="noopener">x</a>',
    }
    stdin = json.dumps(payload)
    stdout, _, code = _run_main(stdin, ["--json"])
    assert code == 0
    data = json.loads(stdout)
    assert data["total_links"] == 1


def test_cli_malformed_json_line_warns_and_skips():
    stdin = "not-json\n" + json.dumps({"id": "id-1", "content_html": "<a href='x.com'>x</a>"})
    stdout, stderr, code = _run_main(stdin)
    assert code == 0
    assert "malformed JSON" in stderr


def test_cli_empty_stdin():
    stdout, _, code = _run_main("")
    assert code == 0
    assert "No links found" in stdout


def test_cli_no_links_in_payloads():
    """Payloads without any <a> tags → 'No links found' message."""
    payloads = [{"id": f"id-{i}", "content_html": "no links here"} for i in range(3)]
    stdin = "\n".join(json.dumps(p) for p in payloads)
    stdout, _, code = _run_main(stdin)
    assert code == 0
    assert "No links found" in stdout
