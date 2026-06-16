"""Unit 4 — G5 footprint survival audit (plan 2026-06-01-005)."""

from __future__ import annotations

from backlink_publisher.gates import g5_footprint_survival as g5
from backlink_publisher.gates import verdict as gv

_REL = "noopener noreferrer"


def _links(n):
    return [g5.PublishedLink(f"https://host/{i}", f"https://target/{i}") for i in range(n)]


def _inspect(results):
    """Return an inspect_fn mapping live_url → a canned result dict, by index order."""
    it = iter(results)
    return lambda live, target: next(it)


def _readable(found=True, rel=_REL):
    return {"page_readable": True, "target_anchor_found": found, "target_rel": rel}


def _unreadable(reason="anti_bot"):
    return {"page_readable": False, "target_anchor_found": False, "target_rel": None, "reason": reason}


# --- survival rate + GO/KILL --------------------------------------------------
def test_full_survival_with_threshold_is_go():
    results = [_readable(rel=_REL) for _ in range(4)]
    v = g5.assess_survival(
        _links(4), inspect_fn=_inspect(results), survival_threshold=0.8
    )
    assert v.rate == 1.0
    assert v.state == gv.GO


def test_rel_rewritten_drops_survival_kill():
    # All pages readable + anchor found, but the platform rewrote rel (added ugc)
    # → fingerprint did NOT survive → KILL (dead premise, like entropy-budget).
    results = [_readable(rel="nofollow ugc") for _ in range(4)]
    v = g5.assess_survival(
        _links(4), inspect_fn=_inspect(results), survival_threshold=0.5
    )
    assert v.rate == 0.0
    assert v.state == gv.KILL


def test_anchor_stripped_counts_as_not_survived():
    results = [_readable(found=False) for _ in range(4)]
    v = g5.assess_survival(
        _links(4), inspect_fn=_inspect(results), survival_threshold=0.5
    )
    assert v.rate == 0.0
    assert v.state == gv.KILL


def test_rel_token_order_insensitive():
    results = [_readable(rel="noreferrer noopener")]  # reversed order still matches
    v = g5.assess_survival(
        _links(1), inspect_fn=_inspect(results), survival_threshold=0.5
    )
    assert v.rate == 1.0
    assert v.state == gv.GO


# --- saturation escape --------------------------------------------------------
def test_saturation_below_floor_is_terminal_inconclusive():
    # 3 of 4 pages unfetchable (anti-bot) → readable_fraction 0.25 < 0.5 floor →
    # terminal INCONCLUSIVE-unmeasurable, NOT an endless resample, even with a threshold.
    results = [_unreadable(), _unreadable(), _unreadable(), _readable(rel=_REL)]
    v = g5.assess_survival(
        _links(4), inspect_fn=_inspect(results), survival_threshold=0.5
    )
    assert v.state == gv.INCONCLUSIVE
    assert v.terminal is True


def test_above_floor_is_measured_not_terminal():
    results = [_readable(rel=_REL), _readable(rel=_REL), _readable(rel=_REL), _unreadable()]
    v = g5.assess_survival(
        _links(4), inspect_fn=_inspect(results), survival_threshold=0.5
    )
    assert v.terminal is False
    assert v.state == gv.GO  # 3/3 readable survived


# --- calibration + empties ----------------------------------------------------
def test_first_run_calibration_is_inconclusive():
    results = [_readable(rel=_REL) for _ in range(4)]
    v = g5.assess_survival(_links(4), inspect_fn=_inspect(results))  # no threshold
    assert v.rate == 1.0
    assert v.state == gv.INCONCLUSIVE


def test_empty_sample_is_inconclusive_not_terminal():
    v = g5.assess_survival([], survival_threshold=0.5)
    assert v.state == gv.INCONCLUSIVE
    assert v.sample_n == 0
    assert v.terminal is False


def test_tier_is_one_and_evidence_host_stripped():
    results = [_readable(rel=_REL)]
    v = g5.assess_survival(_links(1), inspect_fn=_inspect(results), survival_threshold=0.5)
    assert v.tier == 1
    joined = " ".join(v.evidence)
    assert "https://host/" not in joined  # no raw URLs in evidence
