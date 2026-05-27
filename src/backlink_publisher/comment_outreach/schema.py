"""Entity validation for the Comment Outreach Queue.

Mirrors the conventions of :mod:`backlink_publisher.schema`: plain dicts
validated by free functions that return a ``list[str]`` of error messages
(empty list = valid), with small ``_check_*`` helpers concatenated in a fixed,
characterized order by each ``validate_*`` aggregator.

Kept registry-free on purpose (see ``tests/test_comment_outreach_isolation.py``):
``platform`` here is a comment-surface category, **not** a publishing adapter, so
its enum is a static set — it must not delegate to ``schema.supported_platforms``
(which would import the publishing registry).

Tri-state fields (``indexed`` / ``comment_open`` / ``link_allowed``) validate as
``bool | None`` where ``None`` means "unknown".
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlsplit

# --- Enumerated value sets -------------------------------------------------
PLATFORM_ENUM = {"x", "facebook", "linkedin", "reddit", "medium", "blog", "forum", "other"}
DECISION_ENUM = {"accept", "review", "reject"}
ACTION_ENUM = {"manual_comment_brief", "skip"}
STATUS_ENUM = {"pending", "approved", "rejected", "posted", "skipped", "hidden", "removed"}

#: Tri-state boolean fields shared across entities.
_TRISTATE_FIELDS = ("indexed", "comment_open", "link_allowed")

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)

__all__ = [
    "PLATFORM_ENUM",
    "DECISION_ENUM",
    "ACTION_ENUM",
    "STATUS_ENUM",
    "validate_comment_target",
    "validate_qualification_result",
    "validate_comment_brief",
    "validate_review_status",
]


# --- Shared field checks ---------------------------------------------------
def _check_required_str(row: dict[str, Any], field: str) -> list[str]:
    val = row.get(field)
    if not isinstance(val, str) or not val.strip():
        return [f"field '{field}' is required and must be a non-empty string"]
    return []


def _check_optional_str_types(row: dict[str, Any], fields: Iterable[str]) -> list[str]:
    errors: list[str] = []
    for field in fields:
        if field in row and row[field] is not None and not isinstance(row[field], str):
            errors.append(f"field '{field}' must be a string")
    return errors


def _check_enum(
    row: dict[str, Any], field: str, allowed: set[str], *, required: bool
) -> list[str]:
    val = row.get(field)
    if val is None or val == "":
        return [f"field '{field}' is required"] if required else []
    if val not in allowed:
        return [f"field '{field}' must be one of {sorted(allowed)} (got {val!r})"]
    return []


def _check_url_field(row: dict[str, Any], field: str, *, required: bool) -> list[str]:
    """Validate an http(s) URL field. Registry-free; never raises on a malformed
    URL (guards the ``urlsplit`` malformed-IPv6 ``ValueError``)."""
    val = row.get(field)
    if val is None or val == "":
        return [f"field '{field}' is required and must be a valid URL"] if required else []
    if not isinstance(val, str) or not _URL_SCHEME_RE.match(val):
        return [f"field '{field}' is not a valid http(s) URL: {val!r}"]
    try:
        urlsplit(val)
    except ValueError as exc:
        return [f"field '{field}' is a malformed URL: {exc}"]
    return []


def _check_tristate_bool(row: dict[str, Any], field: str) -> list[str]:
    val = row.get(field)
    if field in row and val is not None and not isinstance(val, bool):
        return [f"field '{field}' must be true, false, or null (got {type(val).__name__})"]
    return []


def _check_int_range(
    row: dict[str, Any], field: str, lo: int, hi: int, *, required: bool
) -> list[str]:
    if field not in row or row[field] is None:
        return [f"field '{field}' is required"] if required else []
    val = row[field]
    # bool is an int subclass — reject it explicitly.
    if not isinstance(val, int) or isinstance(val, bool) or not (lo <= val <= hi):
        return [f"field '{field}' must be an integer in [{lo}, {hi}] (got {val!r})"]
    return []


def _check_str_list(row: dict[str, Any], field: str, *, required: bool) -> list[str]:
    if field not in row or row[field] is None:
        return [f"field '{field}' is required and must be a list of strings"] if required else []
    val = row[field]
    if not isinstance(val, list) or any(not isinstance(x, str) for x in val):
        return [f"field '{field}' must be a list of strings"]
    return []


def _check_signals(row: dict[str, Any]) -> list[str]:
    """Validate the optional ``signals`` sub-object of a QualificationResult."""
    if "signals" not in row or row["signals"] is None:
        return []
    signals = row["signals"]
    if not isinstance(signals, dict):
        return ["field 'signals' must be an object"]
    errors: list[str] = []
    for score_field in (
        "relevance_score",
        "authority_score",
        "compliance_score",
        "anchor_risk_score",
        "platform_risk_score",
    ):
        errors.extend(_check_int_range(signals, score_field, 0, 100, required=False))
    for tri in _TRISTATE_FIELDS:
        errors.extend(_check_tristate_bool(signals, tri))
    return errors


# --- Entity aggregators (fixed, characterized order) -----------------------
def validate_comment_target(row: dict[str, Any]) -> list[str]:
    """Validate a ``CommentTarget`` record. Returns a list of error messages."""
    errors: list[str] = []
    errors.extend(_check_required_str(row, "id"))
    errors.extend(_check_required_str(row, "topic"))
    errors.extend(_check_enum(row, "platform", PLATFORM_ENUM, required=True))
    errors.extend(_check_url_field(row, "source_url", required=True))
    errors.extend(_check_url_field(row, "target_url", required=True))
    errors.extend(
        _check_optional_str_types(
            row,
            ("anchor_text", "page_title", "thread_summary", "discovered_by", "discovered_at", "notes"),
        )
    )
    for field in _TRISTATE_FIELDS:
        errors.extend(_check_tristate_bool(row, field))
    errors.extend(_check_int_range(row, "domain_rank_signal", 0, 100, required=False))
    return errors


def validate_qualification_result(row: dict[str, Any]) -> list[str]:
    """Validate a ``QualificationResult`` record."""
    errors: list[str] = []
    errors.extend(_check_required_str(row, "target_id"))
    errors.extend(_check_int_range(row, "score", 0, 100, required=True))
    errors.extend(_check_enum(row, "decision", DECISION_ENUM, required=True))
    errors.extend(_check_enum(row, "action", ACTION_ENUM, required=True))
    errors.extend(_check_str_list(row, "reasons", required=True))
    errors.extend(_check_signals(row))
    errors.extend(_check_optional_str_types(row, ("link_policy", "anchor_policy", "created_at")))
    return errors


def validate_comment_brief(row: dict[str, Any]) -> list[str]:
    """Validate a ``CommentBrief`` record."""
    errors: list[str] = []
    errors.extend(_check_required_str(row, "target_id"))
    errors.extend(_check_required_str(row, "suggested_comment"))
    errors.extend(
        _check_optional_str_types(row, ("suggested_anchor_policy", "suggested_link_policy", "created_at"))
    )
    errors.extend(_check_str_list(row, "human_checklist", required=True))
    errors.extend(_check_str_list(row, "prohibited_actions", required=True))
    return errors


def validate_review_status(row: dict[str, Any]) -> list[str]:
    """Validate a ``ReviewStatus`` record."""
    errors: list[str] = []
    errors.extend(_check_required_str(row, "target_id"))
    errors.extend(_check_enum(row, "status", STATUS_ENUM, required=True))
    errors.extend(_check_url_field(row, "comment_url", required=False))
    errors.extend(
        _check_optional_str_types(row, ("reviewer", "final_comment_text", "result_notes", "updated_at"))
    )
    return errors
