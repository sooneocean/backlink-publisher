"""Validation tests for comment_outreach entity schemas (plan Unit 2)."""

from __future__ import annotations

from backlink_publisher.comment_outreach import schema


def _valid_target() -> dict:
    return {
        "id": "t1",
        "source_url": "https://blog.example.com/post",
        "platform": "blog",
        "topic": "python testing",
        "target_url": "https://my.example.org/landing",
        "anchor_text": "my guide",
        "page_title": "A Post",
        "thread_summary": "discussion about testing",
        "indexed": True,
        "comment_open": None,
        "link_allowed": False,
        "domain_rank_signal": 42,
        "discovered_by": "discover",
        "discovered_at": "2026-05-27T10:00:00Z",
        "notes": "",
    }


# --- CommentTarget ---------------------------------------------------------
def test_valid_comment_target_passes():
    assert schema.validate_comment_target(_valid_target()) == []


def test_tristate_fields_accept_null_true_false():
    for value in (None, True, False):
        row = _valid_target()
        row["comment_open"] = value
        assert schema.validate_comment_target(row) == [], value


def test_tristate_field_rejects_non_bool():
    row = _valid_target()
    row["comment_open"] = "yes"
    errors = schema.validate_comment_target(row)
    assert any("comment_open" in e for e in errors)


def test_missing_required_fields_reported():
    for field in ("id", "topic", "source_url", "target_url"):
        row = _valid_target()
        del row[field]
        errors = schema.validate_comment_target(row)
        assert any(field in e for e in errors), f"{field} not reported: {errors}"


def test_bad_platform_enum_reported():
    row = _valid_target()
    row["platform"] = "tiktok"
    errors = schema.validate_comment_target(row)
    assert any("platform" in e for e in errors)


def test_domain_rank_signal_out_of_range_and_bool_rejected():
    row = _valid_target()
    row["domain_rank_signal"] = 101
    assert any("domain_rank_signal" in e for e in schema.validate_comment_target(row))
    row["domain_rank_signal"] = True  # bool must not pass as int
    assert any("domain_rank_signal" in e for e in schema.validate_comment_target(row))


def test_malformed_url_does_not_crash_and_is_reported():
    row = _valid_target()
    row["source_url"] = "http://[invalid"  # malformed IPv6 — urlsplit raises ValueError
    errors = schema.validate_comment_target(row)  # must not raise
    assert any("source_url" in e for e in errors)


def test_non_http_url_rejected():
    row = _valid_target()
    row["target_url"] = "ftp://example.com/x"
    assert any("target_url" in e for e in schema.validate_comment_target(row))


# --- QualificationResult ---------------------------------------------------
def _valid_qual() -> dict:
    return {
        "target_id": "t1",
        "score": 73,
        "decision": "accept",
        "action": "manual_comment_brief",
        "reasons": ["indexed", "comment_open=true"],
        "signals": {
            "relevance_score": 80,
            "authority_score": 50,
            "compliance_score": 70,
            "anchor_risk_score": 10,
            "platform_risk_score": 5,
            "indexed": True,
            "comment_open": True,
            "link_allowed": True,
        },
        "link_policy": "single-link-ok",
        "anchor_policy": "branded-only",
        "created_at": "2026-05-27T10:00:00Z",
    }


def test_valid_qualification_result_passes():
    assert schema.validate_qualification_result(_valid_qual()) == []


def test_qualification_bad_decision_and_action_reported():
    row = _valid_qual()
    row["decision"] = "maybe"
    row["action"] = "post_now"
    errors = schema.validate_qualification_result(row)
    assert any("decision" in e for e in errors)
    assert any("action" in e for e in errors)


def test_qualification_score_out_of_range_reported():
    row = _valid_qual()
    row["score"] = 250
    assert any("score" in e for e in schema.validate_qualification_result(row))


def test_qualification_reasons_must_be_string_list():
    row = _valid_qual()
    row["reasons"] = "indexed"
    assert any("reasons" in e for e in schema.validate_qualification_result(row))


def test_qualification_bad_signal_score_reported():
    row = _valid_qual()
    row["signals"]["relevance_score"] = 200
    assert any("relevance_score" in e for e in schema.validate_qualification_result(row))


# --- CommentBrief ----------------------------------------------------------
def _valid_brief() -> dict:
    return {
        "target_id": "t1",
        "suggested_comment": "Thoughtful, on-topic reply.",
        "suggested_anchor_policy": "branded",
        "suggested_link_policy": "no-link",
        "human_checklist": ["read the article", "personalize"],
        "prohibited_actions": ["no exact-match anchor", "no bulk paste"],
        "created_at": "2026-05-27T10:00:00Z",
    }


def test_valid_comment_brief_passes():
    assert schema.validate_comment_brief(_valid_brief()) == []


def test_brief_requires_checklist_and_prohibited_actions():
    row = _valid_brief()
    del row["human_checklist"]
    del row["prohibited_actions"]
    errors = schema.validate_comment_brief(row)
    assert any("human_checklist" in e for e in errors)
    assert any("prohibited_actions" in e for e in errors)


# --- ReviewStatus ----------------------------------------------------------
def _valid_status() -> dict:
    return {
        "target_id": "t1",
        "status": "posted",
        "reviewer": "alice",
        "comment_url": "https://blog.example.com/post#comment-5",
        "final_comment_text": "the posted text",
        "result_notes": "accepted by mod",
        "updated_at": "2026-05-27T11:00:00Z",
    }


def test_valid_review_status_passes():
    assert schema.validate_review_status(_valid_status()) == []


def test_review_status_bad_status_reported():
    row = _valid_status()
    row["status"] = "published"
    assert any("status" in e for e in schema.validate_review_status(row))


def test_review_status_minimal_required_only():
    assert schema.validate_review_status({"target_id": "t1", "status": "pending"}) == []


def test_review_status_bad_comment_url_reported():
    row = _valid_status()
    row["comment_url"] = "not-a-url"
    assert any("comment_url" in e for e in schema.validate_review_status(row))


# --- Characterized error ordering ------------------------------------------
def test_comment_target_error_order_is_stable():
    # id (missing) then platform (bad) then source_url (bad) — fixed aggregator order.
    row = {
        "topic": "x",
        "platform": "bogus",
        "source_url": "nope",
        "target_url": "https://ok.example/x",
    }
    errors = schema.validate_comment_target(row)
    joined = " | ".join(errors)
    assert joined.index("'id'") < joined.index("'platform'") < joined.index("'source_url'")
