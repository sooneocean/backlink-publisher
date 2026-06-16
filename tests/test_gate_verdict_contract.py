"""Unit 1 — the gate verdict contract (plan 2026-06-01-005).

Covers the three discipline rules: no GO without confirmed evidence + a recorded
threshold (incl. first-run calibration), BLOCKED is Tier-2 only, and untrusted
remote strings are capped + escaped before they can reach the committed ledger.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from backlink_publisher.gates import verdict as gv


# --- Closed vocabulary --------------------------------------------------------
def test_unknown_verdict_string_rejected():
    with pytest.raises(ValueError, match="unknown gate verdict"):
        gv.build_verdict(
            "g2", "MAYBE", sample_n=5, confirmed=True, threshold_set=True
        )


# --- No GO without confirmed evidence + recorded threshold --------------------
def test_go_with_confirmed_sample_and_threshold_stays_go():
    v = gv.build_verdict(
        "g2", gv.GO, sample_n=10, confirmed=True, threshold_set=True, rate=0.4
    )
    assert v.state == gv.GO


def test_go_with_empty_sample_coerced_to_inconclusive():
    v = gv.build_verdict(
        "g2", gv.GO, sample_n=0, confirmed=True, threshold_set=True
    )
    assert v.state == gv.INCONCLUSIVE


def test_go_without_confirmation_coerced_to_inconclusive():
    v = gv.build_verdict(
        "g5", gv.GO, sample_n=10, confirmed=False, threshold_set=True
    )
    assert v.state == gv.INCONCLUSIVE


def test_first_run_calibration_coerces_go_and_kill_to_inconclusive():
    # threshold_set=False models the first (calibration) run: a measured rate
    # exists but no rule to classify it yet.
    for requested in (gv.GO, gv.KILL):
        v = gv.build_verdict(
            "g2", requested, sample_n=20, confirmed=True, threshold_set=False, rate=0.5
        )
        assert v.state == gv.INCONCLUSIVE


def test_inconclusive_is_constructible_directly():
    v = gv.build_verdict(
        "g2", gv.INCONCLUSIVE, sample_n=3, confirmed=False, threshold_set=False
    )
    assert v.state == gv.INCONCLUSIVE


# --- BLOCKED is Tier-2 only ---------------------------------------------------
def test_blocked_rejected_for_tier1_gate():
    with pytest.raises(ValueError, match="Tier-2 only"):
        gv.build_verdict(
            "g2", gv.BLOCKED, sample_n=0, confirmed=False, threshold_set=False
        )


def test_blocked_allowed_for_tier2_gate():
    v = gv.build_verdict(
        "g3", gv.BLOCKED, sample_n=0, confirmed=False, threshold_set=False
    )
    assert v.state == gv.BLOCKED
    assert v.tier == 2


def test_tier_classification():
    assert gv.gate_tier("g2") == 1
    assert gv.gate_tier("g5") == 1
    assert gv.gate_tier("g3") == 2
    assert gv.gate_tier("G4") == 2  # case-insensitive


# --- Untrusted-value discipline ----------------------------------------------
def test_evidence_strings_are_capped_and_escaped_on_build():
    hostile = "x" * 400 + "\n| inject | row |\n`code`<script>"
    v = gv.build_verdict(
        "g5", gv.INCONCLUSIVE, sample_n=1, confirmed=False, threshold_set=False,
        evidence=(hostile,),
    )
    (cell,) = v.evidence
    assert len(cell) <= gv.EVIDENCE_MAX_LEN
    assert "\n" not in cell
    assert "|" not in cell.replace("\\|", "")  # raw pipes neutralised
    assert "<script>" not in cell


def test_ledger_row_cannot_be_broken_by_hostile_evidence():
    hostile_premise = "money | page\nrow-break"
    v = gv.build_verdict(
        "g2", gv.KILL, sample_n=8, confirmed=True, threshold_set=True, rate=0.75,
        evidence=("noindex on host",),
    )
    row = v.to_ledger_row(premise=hostile_premise, date="2026-06-01", downstream="parked")
    # A single Markdown table row: exactly one line, leading+trailing pipe, no
    # interior raw newline that would split the table.
    assert "\n" not in row
    assert row.startswith("| ") and row.endswith(" |")
    # 8 columns → 9 pipe delimiters when none are injected.
    assert row.count("|") - row.count("\\|") == 9


def test_cap_untrusted_handles_none_and_control_chars():
    assert gv.cap_untrusted(None) == ""
    assert "\x00" not in gv.cap_untrusted("a\x00b\x07c")


# --- Serialization ------------------------------------------------------------
def test_to_jsonl_dict_shape():
    v = gv.build_verdict(
        "g3", gv.KILL, sample_n=12, confirmed=True, threshold_set=True, rate=0.9,
        note="attribution structurally blind", evidence=("rel=noreferrer",),
    )
    d = v.to_jsonl_dict()
    assert d["gate"] == "g3"
    assert d["tier"] == 2
    assert d["verdict"] == gv.KILL
    assert d["sample_n"] == 12
    assert d["evidence"] == ["rel=noreferrer"]


# --- Property: a gate can never tautologically GO without earning it ----------
@given(
    sample_n=st.integers(min_value=-5, max_value=50),
    confirmed=st.booleans(),
    threshold_set=st.booleans(),
    rate=st.one_of(st.none(), st.floats(min_value=0, max_value=1)),
)
def test_property_go_requires_confirmation_threshold_and_sample(
    sample_n, confirmed, threshold_set, rate
):
    v = gv.build_verdict(
        "g2", gv.GO, sample_n=sample_n, confirmed=confirmed,
        threshold_set=threshold_set, rate=rate,
    )
    if v.state == gv.GO:
        # GO is only reachable when ALL three preconditions hold — never by accident.
        assert sample_n > 0 and confirmed and threshold_set
