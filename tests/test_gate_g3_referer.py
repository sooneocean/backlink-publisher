"""Unit 3 — G3 referer render-path audit + GA4 referral intake (plan 005)."""

from __future__ import annotations

from backlink_publisher.gates import g3_referer as g3
from backlink_publisher.gates import verdict as gv


# --- The audit is measured through the real _format_anchor_html, not hard-coded.
def test_strips_referer_measures_real_renderer():
    assert g3._strips_referer("noopener noreferrer") is True
    assert g3._strips_referer("noopener") is False


def test_audit_inventory_grounded():
    facts = {f.name: f for f in g3.audit_render_paths()}
    assert facts["zh_short_default"].strips_referer is True   # default rel
    assert facts["work_themed"].strips_referer is False       # rel="noopener"


# --- Verdict decision order ---------------------------------------------------
def test_majority_strip_kills_on_static_audit_alone():
    # Inventory: 1 of 2 paths strip → 0.5. threshold 0.5 → majority → KILL, with
    # NO credentials and NO referral evidence — the static half terminates alone.
    v = g3.assess_g3(referral=None, credentials_available=False, strip_threshold=0.5)
    assert v.state == gv.KILL
    assert v.tier == 2


def test_credentials_unavailable_blocks_when_not_majority_strip():
    # strip_threshold 0.9 → 0.5 < 0.9 → not majority → fall to creds gate → BLOCKED.
    v = g3.assess_g3(referral=None, credentials_available=False, strip_threshold=0.9)
    assert v.state == gv.BLOCKED


def test_no_referral_evidence_is_inconclusive():
    v = g3.assess_g3(referral=None, credentials_available=True, strip_threshold=0.9)
    assert v.state == gv.INCONCLUSIVE


def test_positive_referral_with_preserving_paths_is_go():
    ev = g3.ReferralEvidence(sessions=42, window="2026-05-01..2026-06-01")
    v = g3.assess_g3(referral=ev, credentials_available=True, strip_threshold=0.9)
    assert v.state == gv.GO


def test_zero_referral_despite_preservable_paths_is_kill():
    ev = g3.ReferralEvidence(sessions=0, window="2026-05-01..2026-06-01")
    v = g3.assess_g3(referral=ev, credentials_available=True, strip_threshold=0.9)
    assert v.state == gv.KILL


def test_first_run_calibration_is_inconclusive():
    # No strip_threshold → calibration: the majority-strip KILL cannot fire and a
    # would-be GO is coerced to INCONCLUSIVE by the verdict contract.
    ev = g3.ReferralEvidence(sessions=42, window="w")
    v = g3.assess_g3(referral=ev, credentials_available=True, strip_threshold=None)
    assert v.state == gv.INCONCLUSIVE


def test_does_not_stall_without_external_evidence():
    # The whole point: G3 reaches a terminal verdict (KILL) from the static audit
    # even when the operator never supplies GA4 evidence.
    v = g3.assess_g3(referral=None, credentials_available=True, strip_threshold=0.5)
    assert v.state in (gv.KILL,)  # majority strip → structurally blind


def test_all_paths_strip_evidence_shows_preserving_none(monkeypatch):
    """When every render path strips referer, evidence must show 'preserving=none'.

    The 'or "preserving=none"' fallback in the evidence tuple was unreachable
    because "preserving=" + "" (an empty join) is truthy, so the `or` short-
    circuits and the fallback string is never used. This test fails before the
    fix (evidence shows "preserving=" instead of "preserving=none").
    """
    monkeypatch.setattr(g3, "_RENDER_PATHS", (
        ("all_strip", "noopener noreferrer"),
    ))
    v = g3.assess_g3(referral=None, credentials_available=False, strip_threshold=0.5)
    assert v.state == gv.KILL
    assert any(e == "preserving=none" for e in v.evidence), (
        f"evidence should contain 'preserving=none' when all paths strip, got {v.evidence!r}"
    )
