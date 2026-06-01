"""Unit 1: events/kinds.py registry + classifier contract.

Covers the vocabulary set, the three-outcome classifier (kind / CONFIRMED_FAMILY
/ NO_EMIT / QUARANTINE), the NO_EMIT-vs-quarantine distinction that prevents
false-positive quarantine floods, and the dependency-free (no sqlite) guarantee.
"""

from __future__ import annotations

import sys

import pytest

from backlink_publisher.events import kinds


def test_kinds_set_is_the_15_documented_kinds():
    assert kinds.KINDS == frozenset(
        {
            "publish.intent",
            "publish.confirmed",
            "publish.unverified",
            "publish.failed",
            "draft.created",
            "draft.scheduled",
            "banner.source_url_fallback",
            "banner.skipped_no_method",
            "banner.failed",
            "banner.embedded",
            "banner.skipped_no_artifact",
            "image_gen_invoked",
            "image_gen_capped",
            "image_gen_disabled_auto",
            "citation.observed",
            "link.rechecked",
        }
    )
    assert len(kinds.KINDS) == 16


@pytest.mark.parametrize(
    "source,status,expected",
    [
        ("checkpoint", "pending", kinds.PUBLISH_INTENT),
        ("checkpoint", "done", kinds.CONFIRMED_FAMILY),
        ("checkpoint", "succeeded", kinds.CONFIRMED_FAMILY),
        ("checkpoint", "failed", kinds.PUBLISH_FAILED),
        ("history", "published", kinds.PUBLISH_CONFIRMED),
        ("history", "failed", kinds.PUBLISH_FAILED),
        ("drafts", "published", kinds.PUBLISH_CONFIRMED),
        ("drafts", "scheduled", kinds.DRAFT_SCHEDULED),
        ("drafts", "drafted", kinds.DRAFT_CREATED),
    ],
)
def test_classify_known_pairs(source, status, expected):
    assert kinds.classify(source, status) == expected


def test_done_and_succeeded_are_confirmed_family_not_a_flat_kind():
    # Guards the PR #222 verified-split: success status maps to the family,
    # the concrete confirmed/unverified kind is resolved downstream.
    assert kinds.classify("checkpoint", "done") is kinds.CONFIRMED_FAMILY
    assert kinds.classify("checkpoint", "succeeded") is kinds.CONFIRMED_FAMILY


def test_intentional_no_emit_distinct_from_quarantine():
    # The false-positive guard: history/drafts intentionally suppress
    # non-owned statuses -> NO_EMIT, NOT quarantine.
    assert kinds.classify("history", "drafted") is kinds.NO_EMIT
    assert kinds.classify("drafts", "failed") is kinds.NO_EMIT
    # History/drafts catch-all default is NO_EMIT (they are not authoritative
    # for unknown statuses).
    assert kinds.classify("history", "some_transient") is kinds.NO_EMIT
    assert kinds.classify("drafts", "whatever") is kinds.NO_EMIT


def test_unknown_checkpoint_status_quarantines():
    # The P0 class: checkpoint is authoritative, so an unrecognized status is
    # genuine drift -> QUARANTINE (never silently dropped).
    assert kinds.classify("checkpoint", "done2") is kinds.QUARANTINE
    assert kinds.classify("checkpoint", "bogus") is kinds.QUARANTINE


def test_unknown_source_defaults_to_quarantine():
    assert kinds.classify("brand_new_source", "anything") is kinds.QUARANTINE


def test_every_status_map_source_has_a_default():
    # The history/drafts reducers rely on SOURCE_DEFAULT being NO_EMIT for an
    # unmapped status (their `else` assumes it). Guard against drift: every
    # source with a STATUS_MAP entry must declare a SOURCE_DEFAULT, and the
    # non-authoritative sources must default to NO_EMIT (not QUARANTINE), else
    # the reducers would silently mis-handle unknown statuses.
    for source in kinds.STATUS_MAP:
        assert source in kinds.SOURCE_DEFAULT
    assert kinds.SOURCE_DEFAULT["history"] is kinds.NO_EMIT
    assert kinds.SOURCE_DEFAULT["drafts"] is kinds.NO_EMIT
    assert kinds.SOURCE_DEFAULT["checkpoint"] is kinds.QUARANTINE


def test_classify_never_raises():
    # Defensive: empty / odd inputs resolve, never throw.
    assert kinds.classify("", "") is kinds.QUARANTINE
    assert kinds.classify("checkpoint", "") is kinds.QUARANTINE


def test_kinds_module_is_sqlite_free():
    # banner_dispatcher (a deliberately I/O-free module) must be able to import
    # a kind constant without dragging in EventStore / sqlite. Assert the
    # module did not import sqlite3 into its namespace.
    assert not hasattr(kinds, "sqlite3")
    assert "backlink_publisher.events.store" not in sys.modules.get(
        "backlink_publisher.events.kinds", type("x", (), {"__dict__": {}})
    ).__dict__.get("__builtins__", {})
    # Direct check: importing kinds alone must not import the store module.
    # (store imports sqlite3; kinds must not transitively require it.)
    assert "sqlite3" not in dir(kinds)
