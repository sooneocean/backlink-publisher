"""Tests for generate-backlink-text CLI — Plan 2026-05-27-006 Units 1, 3, 4, 5.

Unit 1 test scenarios (this file covers):
- Happy path: a 3-record JSONL stream parses to 3 internal records.
- Happy path: JSON object and JSON array parse the same as equivalent JSONL.
- Edge case: empty stdin / empty file → exit 0, empty stdout, stderr summary "0".
- Edge case: record count = --max-records passes; count+1 → InputValidationError exit 2.
- Edge case: raw input > --max-input-bytes → exit 2 before parse.
- Error path: --output-format=xml → UsageError exit 1 (code 1, not 2).
- Error path: record missing target_url/anchor_text → rejected row, batch continues.
- Integration: python -m --help emits usage banner (covered by test_cli_python_m_entrypoints.py).
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"


# ── _read_candidates unit tests ───────────────────────────────────────────────


def test_read_candidates_jsonl_three_records():
    """3-record JSONL stream parses to 3 dicts."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    jsonl = "\n".join([
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}',
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}',
        '{"target_url": "https://c.com/", "anchor_text": "C", "mode": "comment"}',
    ])
    result = _read_candidates(jsonl)
    assert len(result) == 3
    assert result[0]["anchor_text"] == "A"
    assert result[2]["target_url"] == "https://c.com/"


def test_read_candidates_json_object_wraps_to_list():
    """Single JSON object parses identically to a 1-record JSONL."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    obj = {"target_url": "https://x.com/", "anchor_text": "x", "mode": "comment"}
    result_obj = _read_candidates(json.dumps(obj))
    result_jsonl = _read_candidates(json.dumps(obj))
    assert len(result_obj) == 1
    assert result_obj[0] == result_jsonl[0]


def test_read_candidates_json_array():
    """JSON array parses to same record list as equivalent JSONL."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    records = [
        {"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"},
        {"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"},
    ]
    from_array = _read_candidates(json.dumps(records))
    from_jsonl = _read_candidates(
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}\n'
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}'
    )
    assert from_array == from_jsonl


def test_read_candidates_empty_input_returns_empty_list():
    """Empty input → empty list (R5b)."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    assert _read_candidates("") == []
    assert _read_candidates("   \n  ") == []


def test_read_candidates_max_input_bytes_exceeded_raises():
    """Raw input > max_input_bytes → InputValidationError (exit 2)."""
    from backlink_publisher._util.errors import InputValidationError
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    big_text = "x" * 100
    with pytest.raises(InputValidationError, match="max-input-bytes"):
        _read_candidates(big_text, max_input_bytes=50)


def test_read_candidates_exactly_max_records_passes():
    """Record count == max_records: no error."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    lines = [
        '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    ] * 5
    result = _read_candidates("\n".join(lines), max_records=5)
    assert len(result) == 5


def test_read_candidates_max_records_exceeded_raises():
    """Record count > max_records → InputValidationError (exit 2)."""
    from backlink_publisher._util.errors import InputValidationError
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    lines = [
        '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    ] * 6
    with pytest.raises(InputValidationError, match="max-records"):
        _read_candidates("\n".join(lines), max_records=5)


def test_read_candidates_skips_malformed_jsonl_lines():
    """Malformed JSONL lines are silently skipped (strict=False semantics)."""
    from backlink_publisher.cli.generate_backlink_text import _read_candidates

    text = (
        '{"target_url": "https://a.com/", "anchor_text": "A", "mode": "comment"}\n'
        'NOT JSON !!!\n'
        '{"target_url": "https://b.com/", "anchor_text": "B", "mode": "article"}'
    )
    result = _read_candidates(text)
    assert len(result) == 2


# ── _validate_candidate unit tests ────────────────────────────────────────────


def test_validate_candidate_valid_record_normalises():
    """Valid record with all required fields is normalised (no status key = not rejected)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://example.com/page", "anchor_text": "click me", "mode": "comment"}
    result = _validate_candidate(rec)
    # Valid records carry the normalised fields, no "status" or "rejection_reason".
    assert result.get("status") != "rejected"
    assert "rejection_reason" not in result
    assert result["target_url"] == "https://example.com/page"
    assert result["anchor_text"] == "click me"
    assert result["mode"] == "comment"


def test_validate_candidate_missing_target_url_rejected():
    """Missing target_url → rejected with invalid_record."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"anchor_text": "anchor", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_missing_anchor_text_rejected():
    """Missing anchor_text → rejected with invalid_record."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://example.com/", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_http_url_rejected():
    """Non-https target_url → rejected with bad_target_url_scheme."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "http://example.com/", "anchor_text": "a", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert "scheme" in result["rejection_reason"]


def test_validate_candidate_malformed_ipv6_url_rejected():
    """Malformed IPv6 target_url → rejected (urlparse ValueError guarded)."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {"target_url": "https://[invalid", "anchor_text": "a", "mode": "comment"}
    result = _validate_candidate(rec)
    assert result["status"] == "rejected"
    assert result["rejection_reason"] == "invalid_record"


def test_validate_candidate_extra_fields_preserved():
    """Extra fields in the record are preserved in the normalised output."""
    from backlink_publisher.cli.generate_backlink_text import _validate_candidate

    rec = {
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "comment",
        "language": "zh-CN",
        "extra_field": "foo",
    }
    result = _validate_candidate(rec)
    assert result.get("language") == "zh-CN"
    assert result.get("extra_field") == "foo"


# ── CLI main() integration tests (in-process) ─────────────────────────────────


def _run_main(argv, stdin_text="", capsys=None):
    """Helper: run main(argv) with captured output."""
    from backlink_publisher.cli.generate_backlink_text import main
    import sys
    import io as _io

    old_stdin = sys.stdin
    try:
        sys.stdin = _io.StringIO(stdin_text)
        main(argv)
    except SystemExit as exc:
        return exc.code
    finally:
        sys.stdin = old_stdin
    return 0


def test_cli_empty_stdin_exit_0(capsys):
    """Empty stdin → exit 0, empty stdout (R5b)."""
    from backlink_publisher.cli.generate_backlink_text import main
    import sys, io

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        main([])
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    assert captured.out.strip() == ""


def test_cli_output_format_xml_raises_usage_error(capsys):
    """--output-format=xml → UsageError exit 1 (not argparse exit 2)."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    old_stdin = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--output-format", "xml"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 1


def test_cli_max_records_exceeded_exit_2(capsys):
    """Record count+1 over --max-records → InputValidationError exit 2."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = '{"target_url": "https://x.com/", "anchor_text": "a", "mode": "comment"}'
    stdin_text = "\n".join([record] * 3)

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--max-records", "2"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 2


def test_cli_max_input_bytes_exceeded_exit_2(capsys):
    """Raw input > --max-input-bytes → InputValidationError exit 2."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    big_text = "x" * 200

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(big_text)
    try:
        with pytest.raises(SystemExit) as exc_info:
            main(["--max-input-bytes", "100"])
    finally:
        sys.stdin = old_stdin

    assert exc_info.value.code == 2


def test_cli_dry_run_single_record_jsonl(capsys, tmp_path):
    """--dry-run: single valid comment record → dry_run status row on stdout."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "example anchor",
        "mode": "comment",
    })
    stdin_text = record

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 1
    row = json.loads(output_lines[0])
    assert row["status"] == "dry_run"
    assert "system_prompt" in row
    assert "user_prompt" in row


def test_cli_dry_run_rejected_record_in_batch(capsys):
    """--dry-run: batch with one rejected record continues, rejected shows in output."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    records = [
        json.dumps({"target_url": "https://example.com/", "anchor_text": "a", "mode": "comment"}),
        json.dumps({"anchor_text": "no-url", "mode": "comment"}),   # missing target_url
    ]
    stdin_text = "\n".join(records)

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin_text)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 2
    statuses = {json.loads(line)["status"] for line in output_lines}
    assert "dry_run" in statuses
    assert "rejected" in statuses


def test_cli_dry_run_json_output_format(capsys):
    """--dry-run --output-format=json: stdout is a JSON array."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "article",
    })

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(record)
    try:
        main(["--dry-run", "--output-format", "json"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert isinstance(parsed, list)
    assert parsed[0]["status"] == "dry_run"


def test_cli_dry_run_unsupported_mode_produces_rejected(capsys):
    """--dry-run: unsupported mode → per-record rejected (R4b), batch continues."""
    import sys, io
    from backlink_publisher.cli.generate_backlink_text import main

    record = json.dumps({
        "target_url": "https://example.com/",
        "anchor_text": "anchor",
        "mode": "profile",  # not supported in MVP
    })

    old_stdin = sys.stdin
    sys.stdin = io.StringIO(record)
    try:
        main(["--dry-run"])
    except SystemExit as exc:
        assert exc.code in (None, 0)
    finally:
        sys.stdin = old_stdin

    captured = capsys.readouterr()
    output_lines = [line for line in captured.out.strip().splitlines() if line.strip()]
    assert len(output_lines) == 1
    row = json.loads(output_lines[0])
    assert row["status"] == "rejected"
    assert "unsupported_mode" in row["rejection_reason"]


def test_cli_help_banner_subprocess():
    """python -m backlink_publisher.cli.generate_backlink_text --help emits usage."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_SRC_DIR) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    result = subprocess.run(
        [sys.executable, "-m", "backlink_publisher.cli.generate_backlink_text", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
        timeout=15,
    )
    combined = result.stdout + result.stderr
    assert combined.strip(), "--help produced no output"
    assert "usage:" in combined.lower() or "options:" in combined.lower()
