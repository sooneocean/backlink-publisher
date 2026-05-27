"""Tests for ``comment qualify`` + the conservative scoring ladder (plan Unit 6).

The safety property is the *conservatism* of the ladder, not the precision of the score:
no social target, unknown/closed comment region, or non-indexed page ever reaches
``accept``; every decision carries reasons; the verb stays exit-0.
"""

from __future__ import annotations

import io
import json

import pytest

from backlink_publisher.comment_outreach import schema
from backlink_publisher.comment_outreach.score import (
    ACCEPT_THRESHOLD,
    SOCIAL_PLATFORMS,
    qualify_targets,
    score_target,
)


def _target(**overrides) -> dict:
    row = {
        "id": "t1",
        "source_url": "https://blog.example.com/post",
        "platform": "blog",
        "topic": "python testing tips",
        "target_url": "https://my.example.org/landing",
        "page_title": "Great python testing tips",
        "thread_summary": "a discussion about python testing tips and tools",
        "indexed": True,
        "comment_open": True,
        "link_allowed": True,
        "domain_rank_signal": 70,
    }
    row.update(overrides)
    return row


# --- Happy path: indexed + open + link_allowed blog -> accept --------------
def test_strong_blog_target_accepts():
    r = score_target(_target())
    assert r["decision"] == "accept"
    assert r["action"] == "manual_comment_brief"
    assert r["score"] >= ACCEPT_THRESHOLD


# --- R5: social platform never accepts -------------------------------------
@pytest.mark.parametrize("platform", sorted(SOCIAL_PLATFORMS))
def test_social_platform_never_accepts(platform):
    # Even with otherwise-perfect signals, a social platform caps at review/reject.
    r = score_target(_target(platform=platform, domain_rank_signal=100))
    assert r["decision"] != "accept"
    assert r["action"] == "skip"
    assert any("social" in reason for reason in r["reasons"])


# --- R5: comment_open=null (discovery failure) -> never silent accept ------
def test_comment_open_null_goes_to_review():
    r = score_target(_target(comment_open=None))
    assert r["decision"] == "review"
    assert any("unknown" in reason for reason in r["reasons"])


def test_comment_open_false_rejects():
    r = score_target(_target(comment_open=False))
    assert r["decision"] == "reject"


# --- link_allowed=false -> no-link policy, not accept-with-link ------------
def test_link_not_allowed_yields_no_link_policy():
    r = score_target(_target(link_allowed=False))
    assert r["link_policy"] == "no-link"
    # may still accept (region open + indexed), but never with a link.
    assert r["link_policy"] != "single-link-ok"


def test_link_unknown_defaults_to_no_link():
    r = score_target(_target(link_allowed=None))
    assert r["link_policy"] == "no-link"


# --- reasons[] names the signals that drove the decision -------------------
def test_reasons_name_the_signals():
    r = score_target(_target())
    joined = " ".join(r["reasons"])
    assert "relevance=" in joined and "authority=" in joined and "platform_risk=" in joined


# --- Output is a valid QualificationResult ---------------------------------
def test_output_passes_schema():
    assert schema.validate_qualification_result(score_target(_target())) == []


# --- Driver: invalid input row is surfaced, not silently dropped -----------
def test_qualify_driver_skips_invalid_with_reason(capsys):
    valid = json.dumps(_target()) + "\n"
    invalid = json.dumps({"id": "bad", "platform": "tiktok"}) + "\n"  # bad enum + missing fields
    dest = io.StringIO()
    counts = qualify_targets(io.StringIO(valid + invalid), dest)
    out = [json.loads(l) for l in dest.getvalue().splitlines() if l]
    assert len(out) == 1 and counts == {"qualified": 1, "rejected": 1}
    err = capsys.readouterr().err
    assert "comment_qualify_skip" in err and "platform" in err


def test_qualify_driver_emits_valid_results():
    dest = io.StringIO()
    qualify_targets(io.StringIO(json.dumps(_target()) + "\n"), dest)
    out = [json.loads(l) for l in dest.getvalue().splitlines() if l]
    assert schema.validate_qualification_result(out[0]) == []


# --- indexed=None is deliberately neutral: it does NOT gate accept ---------
def test_indexed_unknown_can_still_accept():
    # Pinned behavior (see score.py docstring): the objective is referral/brand mention,
    # not PageRank, so unknown indexability does not block a strong comment-open target.
    # Only an explicit indexed=False gates. This guards against accidentally tightening it.
    r = score_target(_target(indexed=None))
    assert r["decision"] == "accept"


# --- No conservative-bias case yields accept (the verification gate) -------
def test_no_conservative_case_accepts():
    bias_cases = [
        _target(platform="x"),
        _target(platform="reddit"),
        _target(comment_open=None),
        _target(comment_open=False),
        _target(indexed=False),
    ]
    for case in bias_cases:
        assert score_target(case)["decision"] != "accept", case["platform"]
