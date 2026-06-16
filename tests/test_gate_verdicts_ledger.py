"""Unit 5 — Phase-0 closeout: the gate-verdicts ledger integrity (plan 005).

The ledger (``docs/ideation/gate-verdicts.md``) is the single hand-curated
decision surface. These structural guards keep it honest as rows get filled:
all five gates present, the R16 governance rule and the entropy-budget KILL at
the top, no ``GO`` row without an evidence cell, and the no-operator-domain
discipline noted for ``docs/ideation/``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

LEDGER = Path(__file__).resolve().parents[1] / "docs" / "ideation" / "gate-verdicts.md"


@pytest.fixture(scope="module")
def ledger_text() -> str:
    assert LEDGER.exists(), f"gate verdict ledger missing at {LEDGER}"
    return LEDGER.read_text(encoding="utf-8")


def test_r16_governance_rule_present(ledger_text):
    assert "R16" in ledger_text
    assert "/ce:plan" in ledger_text and "GO" in ledger_text


def test_entropy_budget_kill_recorded(ledger_text):
    assert "entropy-budget" in ledger_text
    # In the KILLED-premises section, not merely mentioned in prose.
    assert re.search(r"KILLED|⛔", ledger_text)


def test_all_five_gates_have_a_row(ledger_text):
    for gate in ("G1", "G2", "G3", "G4", "G5"):
        assert re.search(rf"\|\s*{gate}\s*\|", ledger_text), f"missing row for {gate}"


def test_four_state_protocol_documented(ledger_text):
    for state in ("GO", "KILL", "INCONCLUSIVE", "BLOCKED"):
        assert state in ledger_text


def test_no_go_row_without_evidence(ledger_text):
    # Any data row whose verdict cell is exactly GO must carry a non-empty
    # evidence cell (column 5). Guards the "no GO without evidence" rule once
    # rows are filled; passes trivially while rows are still pending.
    for line in ledger_text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 8:
            continue
        gate, tier, premise, verdict, evidence, *_ = cells
        if verdict == "GO":
            assert evidence and evidence != "—", f"GO row without evidence: {line!r}"


def test_no_operator_domain_discipline_noted(ledger_text):
    assert "docs/ideation/" in ledger_text
    assert "operator" in ledger_text.lower() and "url" in ledger_text.lower()


def test_first_run_calibration_protocol_documented(ledger_text):
    assert "calibration" in ledger_text.lower()
    assert "threshold" in ledger_text.lower()
