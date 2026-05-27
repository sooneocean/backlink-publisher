"""Tests for ``comment import`` (plan Unit 3).

``import`` is a lenient validating filter: valid CommentTarget rows flow to stdout,
malformed / schema-invalid rows are skipped with a stderr reason, and the process
always exits 0 (zero valid rows is a legitimate result, not an error).
"""

from __future__ import annotations

import io
import json

from backlink_publisher.cli import comment
from backlink_publisher.comment_outreach.io_import import import_targets


def _target(tid: str, **overrides) -> dict:
    row = {
        "id": tid,
        "source_url": "https://blog.example.com/post",
        "platform": "blog",
        "topic": "python testing",
        "target_url": "https://my.example.org/landing",
        "comment_open": True,
    }
    row.update(overrides)
    return row


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in rows)


def _run(stdin_text: str):
    """Drive import_targets with text stdin; return (out_rows, counts)."""
    dest = io.StringIO()
    counts = import_targets(io.StringIO(stdin_text), dest)
    out_rows = [json.loads(line) for line in dest.getvalue().splitlines() if line]
    return out_rows, counts


# --- Happy path ------------------------------------------------------------
def test_three_valid_records_pass_through():
    out, counts = _run(_jsonl(_target("t1"), _target("t2"), _target("t3")))
    assert [r["id"] for r in out] == ["t1", "t2", "t3"]
    assert counts == {"valid": 3, "rejected": 0}


# --- Mixed file: valid + malformed JSON + schema-invalid -------------------
def test_mixed_file_skips_bad_rows_with_reasons(capsys):
    schema_invalid = _target("bad", platform="tiktok")  # not in PLATFORM_ENUM
    raw = (
        json.dumps(_target("t1")) + "\n"
        + "{not json at all\n"  # malformed JSON — dropped by read_jsonl (WARN)
        + json.dumps(schema_invalid) + "\n"  # schema-invalid — dropped here (RECON)
        + json.dumps(_target("t2")) + "\n"
    )
    out, counts = _run(raw)
    assert [r["id"] for r in out] == ["t1", "t2"]
    assert counts == {"valid": 2, "rejected": 1}

    err = capsys.readouterr().err
    assert "WARN" in err and "malformed JSON" in err  # the malformed-JSON skip reason
    assert '"level": "RECON"' in err and "comment_import_skip" in err  # schema skip
    assert "platform" in err  # the schema reason names the offending field


# --- Social target with comment_open=null passes unchanged -----------------
def test_social_null_comment_open_passes_through():
    row = _target("x1", platform="x", comment_open=None)
    out, counts = _run(_jsonl(row))
    assert counts == {"valid": 1, "rejected": 0}
    assert out[0]["comment_open"] is None
    assert out[0]["platform"] == "x"


# --- Bad URL is rejected before reaching downstream ------------------------
def test_record_with_bad_url_rejected():
    bad = _target("u1", target_url="not-a-url")
    out, counts = _run(_jsonl(bad))
    assert out == []
    assert counts == {"valid": 0, "rejected": 1}


def test_malformed_ipv6_url_does_not_crash_and_is_rejected():
    bad = _target("u2", source_url="http://[invalid")  # urlsplit would raise
    out, counts = _run(_jsonl(bad))  # must not raise
    assert out == []
    assert counts["rejected"] == 1


# --- Empty input is exit-0 with zero output (pipeline filter semantics) -----
def test_empty_input_yields_nothing_no_error():
    out, counts = _run("")
    assert out == []
    assert counts == {"valid": 0, "rejected": 0}


# --- CLI dispatch: `comment import` reads stdin, writes stdout, exit 0 ------
def test_cli_import_end_to_end(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(_jsonl(_target("t1"), _target("t2"))))
    rc = comment.main(["import"])
    assert rc == 0
    out_lines = [l for l in capsys.readouterr().out.splitlines() if l]
    assert [json.loads(l)["id"] for l in out_lines] == ["t1", "t2"]
