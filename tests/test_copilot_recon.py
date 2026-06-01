"""Tests for Copilot RECON invocation logging (Plan U7)."""

from __future__ import annotations

import json

from backlink_publisher._util.logger import set_log_level
from webui_app.services.copilot_recon import log_invocation


def _recon_records(captured_err: str) -> list[dict]:
    records = []
    for line in captured_err.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("level") == "RECON":
            records.append(rec)
    return records


def test_emits_one_recon_line_with_allowlisted_fields(capsys):
    log_invocation("advisor", "/copilot/advice", {"findings": 3, "errored": 0})
    recs = _recon_records(capsys.readouterr().err)
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "advisor"
    assert rec["tool_or_route"] == "/copilot/advice"
    assert rec["counts"] == {"findings": 3, "errored": 0}


def test_non_int_counts_values_are_dropped(capsys):
    # A caller cannot smuggle a domain/answer string through the counts map.
    log_invocation("qa", "/copilot/ask", {"tokens": 42, "leak": "realsite.com"})  # type: ignore[dict-item]
    err = capsys.readouterr().err
    assert "realsite.com" not in err
    rec = _recon_records(err)[0]
    assert rec["counts"] == {"tokens": 42}


def test_recon_bypasses_log_level_gate(capsys):
    set_log_level("WARN")
    try:
        log_invocation("advisor", "/copilot/advice", {"findings": 1})
    finally:
        set_log_level("INFO")
    recs = _recon_records(capsys.readouterr().err)
    assert len(recs) == 1
    assert recs[0]["counts"] == {"findings": 1}
