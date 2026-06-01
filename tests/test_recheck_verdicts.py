"""Unit 1: recheck verdict taxonomy + link.rechecked kind registration.

Covers the 5-verdict set, the two load-bearing category sets, the two
predicates, and that the new event kind is registered with the {"verdict"}
floor (the R2 gate in test_events_r9_required_fields.py asserts floor coverage
for every KIND; this file adds the recheck-specific assertions).
"""

from __future__ import annotations

import pytest

from backlink_publisher.events import kinds
from backlink_publisher.recheck import verdicts


def test_link_rechecked_kind_registered_with_verdict_floor():
    assert kinds.LINK_RECHECKED == "link.rechecked"
    assert kinds.LINK_RECHECKED in kinds.KINDS
    assert kinds.REQUIRED_FIELDS[kinds.LINK_RECHECKED] == frozenset({"verdict"})


def test_link_rechecked_has_no_seam_b_classification():
    # Written directly by the CLI via EventStore.append, never via the
    # projector — so it must NOT appear in any STATUS_MAP source.
    for per_source in kinds.STATUS_MAP.values():
        assert kinds.LINK_RECHECKED not in per_source.values()


def test_five_verdicts_present_and_distinct():
    assert verdicts.VERDICTS == frozenset(
        {"alive", "host_gone", "link_stripped", "dofollow_lost", "probe_error"}
    )
    assert len(verdicts.VERDICTS) == 5


@pytest.mark.parametrize(
    "verdict,expected",
    [
        (verdicts.HOST_GONE, True),
        (verdicts.LINK_STRIPPED, True),
        (verdicts.DOFOLLOW_LOST, False),
        (verdicts.PROBE_ERROR, False),
        (verdicts.ALIVE, False),
        ("unknown_future_verdict", False),
    ],
)
def test_is_deterministic_dead(verdict, expected):
    assert verdicts.is_deterministic_dead(verdict) is expected


@pytest.mark.parametrize(
    "verdict,expected",
    [
        (verdicts.ALIVE, True),
        (verdicts.HOST_GONE, True),
        (verdicts.LINK_STRIPPED, True),
        (verdicts.DOFOLLOW_LOST, True),
        (verdicts.PROBE_ERROR, False),  # D3: probe_error never advances the cursor
        ("unknown_future_verdict", False),
    ],
)
def test_advances_age_cursor(verdict, expected):
    assert verdicts.advances_age_cursor(verdict) is expected


def test_category_set_relationships():
    # DETERMINISTIC_DEAD is a subset of DEFINITIVE (a dead link is a definitive
    # outcome that advances the cursor).
    assert verdicts.DETERMINISTIC_DEAD <= verdicts.DEFINITIVE
    # probe_error is the only verdict that does NOT advance the cursor.
    assert verdicts.PROBE_ERROR not in verdicts.DEFINITIVE
    assert verdicts.VERDICTS - verdicts.DEFINITIVE == frozenset({verdicts.PROBE_ERROR})
    # dofollow_lost is degradation, not death — never trips --fail-on-dead.
    assert verdicts.DOFOLLOW_LOST not in verdicts.DETERMINISTIC_DEAD
