"""Unit 1 + Unit 2: recheck deficit-overlay reader/classifier + row transform.

Pure-function tests for ``recheck.overlay``: the latest-verdict reader
(``build_discount_map``) and the ledger-row transform (``apply_discounts``).
The end-to-end re-plan loop lives in ``test_recheck_overlay_replan_loop.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backlink_publisher._util.errors import DependencyError
from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.gap.engine import GapOptions, plan_gap
from backlink_publisher.recheck import verdicts
from backlink_publisher.recheck.overlay import (
    DiscountResult,
    TargetDiscount,
    apply_discounts,
    build_discount_map,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
TARGET = "https://my.site/"
CANON = canonicalize_url(TARGET)


@pytest.fixture
def store(tmp_path):
    return EventStore(path=tmp_path / "events.db")


def _append(store, aid, verdict, *, target=TARGET, platform="medium", ts=NOW, live_url=None):
    """Append one link.rechecked event with controlled ts/target/platform.

    ``live_url`` defaults to absent (events.db-sourced rechecks keyed via the
    article_id fallback); pass it to exercise the canonical-live_url keying that
    keeps NULL-article_id stdin rechecks (A1).
    """
    payload = {"verdict": verdict, "platform": platform}
    if live_url is not None:
        payload["live_url"] = live_url
    store.append(
        LINK_RECHECKED,
        payload,
        article_id=aid,
        target_url=target,
        ts_utc=ts.isoformat() if ts is not None else None,
    )


# --------------------------------------------------------------------------- #
# Unit 1: build_discount_map (reader + classifier)
# --------------------------------------------------------------------------- #


def test_host_gone_discounts_target_and_records_platform(store):
    _append(store, 1, verdicts.HOST_GONE, platform="medium")
    res = build_discount_map(store)
    td = res.by_target[CANON]
    assert td.dead_count == 1
    assert td.dofollow_lost_count == 0
    assert td.dead_platforms == {"medium"}
    assert res.tally.dead_seen == 1
    assert res.tally.discounted == 1


def test_dofollow_lost_discounts_dofollow_portion(store):
    _append(store, 1, verdicts.DOFOLLOW_LOST, platform="velog")
    res = build_discount_map(store)
    td = res.by_target[CANON]
    assert td.dofollow_lost_count == 1
    assert td.dead_count == 0
    assert td.dead_platforms == {"velog"}
    assert res.tally.dead_seen == 0  # dofollow_lost is degradation, not death
    assert res.tally.discounted == 1


def test_link_stripped_is_dead(store):
    _append(store, 1, verdicts.LINK_STRIPPED)
    res = build_discount_map(store)
    assert res.by_target[CANON].dead_count == 1
    assert res.tally.dead_seen == 1


def test_recency_newer_alive_restores_link(store):
    # host_gone, then a newer alive for the same article_id → no discount (R4).
    _append(store, 1, verdicts.HOST_GONE, ts=NOW - timedelta(days=5))
    _append(store, 1, verdicts.ALIVE, ts=NOW - timedelta(days=1))
    res = build_discount_map(store)
    assert res.by_target == {}
    assert res.tally.discounted == 0


def test_recency_newer_dead_discounts(store):
    # alive, then a newer host_gone → discounted (reverse of the above).
    _append(store, 1, verdicts.ALIVE, ts=NOW - timedelta(days=5))
    _append(store, 1, verdicts.HOST_GONE, ts=NOW - timedelta(days=1))
    res = build_discount_map(store)
    assert res.by_target[CANON].dead_count == 1


def test_same_ts_tiebreak_higher_id_wins(store):
    # Identical ts_utc: the later-appended row (higher events.id) is "latest".
    same = NOW - timedelta(days=2)
    _append(store, 1, verdicts.HOST_GONE, ts=same)  # id 1
    _append(store, 1, verdicts.ALIVE, ts=same)  # id 2 → wins → no discount
    res = build_discount_map(store)
    assert res.by_target == {}

    # Reverse insertion order → dead wins.
    _append(store, 2, verdicts.ALIVE, ts=same)  # id 3
    _append(store, 2, verdicts.HOST_GONE, ts=same)  # id 4 → wins → discounted
    res2 = build_discount_map(store)
    assert res2.by_target[CANON].dead_count == 1


def test_probe_error_only_no_discount(store):
    _append(store, 1, verdicts.PROBE_ERROR)
    res = build_discount_map(store)
    assert res.by_target == {}
    assert res.tally.discounted == 0
    assert res.tally.unknown_verdict == 0  # probe_error is recognized, not unknown


def test_unknown_verdict_is_quarantined_not_alive(store):
    _append(store, 1, "teleported_to_mars")
    res = build_discount_map(store)
    assert res.by_target == {}  # never default-to-alive AND never a silent discount
    assert res.tally.unknown_verdict == 1
    assert res.tally.discounted == 0


def test_null_blank_target_counted_not_dropped(store):
    # target NULL and no articles row to recover from → counted, not silently dropped.
    _append(store, 1, verdicts.HOST_GONE, target=None)
    res = build_discount_map(store)
    assert res.by_target == {}
    assert res.tally.null_or_blank_target == 1


def test_keys_on_live_url_when_article_id_null(store):
    # A1: a stdin-sourced recheck has a real live_url but NULL article_id. Keying on
    # canonical live_url (not article_id, not filtered NOT NULL) keeps it discounted.
    _append(store, None, verdicts.HOST_GONE, live_url="https://medium.com/@me/post")
    res = build_discount_map(store)
    assert res.by_target[CANON].dead_count == 1
    assert res.tally.dead_seen == 1
    assert res.tally.unkeyable == 0


def test_recency_across_null_article_id_keyed_by_live_url(store):
    # Two stdin rechecks for the SAME live_url (both NULL article_id): the newer
    # alive must win over the older host_gone — proving live_url is the link identity.
    lu = "https://medium.com/@me/post"
    _append(store, None, verdicts.HOST_GONE, ts=NOW - timedelta(days=3), live_url=lu)
    _append(store, None, verdicts.ALIVE, ts=NOW - timedelta(days=1), live_url=lu)
    res = build_discount_map(store)
    assert res.by_target == {}  # latest is alive → no discount
    assert res.tally.unkeyable == 0


def test_unkeyable_when_no_live_url_and_no_article_id(store):
    # Neither live_url nor article_id → the link is unidentifiable: loud tally, no
    # silent drop, never treated as alive.
    _append(store, None, verdicts.HOST_GONE)  # no live_url passed
    res = build_discount_map(store)
    assert res.by_target == {}
    assert res.tally.unkeyable == 1
    assert res.tally.dead_seen == 0


def test_canonicalization_matches_ledger_key(store):
    # Event target carries case + utm noise; the discount key is its canonical form.
    _append(store, 1, verdicts.HOST_GONE, target="http://Ex.com/p?utm_source=x")
    res = build_discount_map(store)
    assert canonicalize_url("http://Ex.com/p?utm_source=x") in res.by_target
    assert canonicalize_url("http://Ex.com/p?utm_source=x") == "http://ex.com/p"


def test_empty_store_empty_map(store):
    _append(store, 1, verdicts.ALIVE)  # force-create the db with a non-dead event
    res = build_discount_map(store)
    assert res.by_target == {}
    assert res.tally.discounted == 0


def test_absent_db_returns_empty_without_creating_file(tmp_path):
    missing = tmp_path / "nope.db"
    res = build_discount_map(EventStore(path=missing))
    assert res.by_target == {}
    assert res.tally == build_discount_map(EventStore(path=missing)).tally
    assert not missing.exists()  # read-only: never materializes an empty db


def test_determinism_rerun_converges(store):
    _append(store, 1, verdicts.HOST_GONE, platform="medium")
    _append(store, 2, verdicts.DOFOLLOW_LOST, platform="velog")
    a = build_discount_map(store)
    b = build_discount_map(store)
    assert a.by_target.keys() == b.by_target.keys()
    assert a.by_target[CANON].dead_count == b.by_target[CANON].dead_count
    assert a.by_target[CANON].dead_platforms == b.by_target[CANON].dead_platforms


def test_unreadable_db_raises_dependency_error(store, monkeypatch):
    _append(store, 1, verdicts.HOST_GONE)  # create the file so .exists() is True

    def _boom(*_a, **_k):
        import sqlite3

        raise sqlite3.OperationalError("database disk image is malformed")

    monkeypatch.setattr(store, "query", _boom)
    with pytest.raises(DependencyError):
        build_discount_map(store)


# --------------------------------------------------------------------------- #
# Unit 2: apply_discounts (ledger-row transform)
# --------------------------------------------------------------------------- #


def _row(target=TARGET, live_dofollow=3, platforms=("A", "B", "C"), **extra):
    base = {
        "target_url": target,
        "live_dofollow": live_dofollow,
        "live_dofollow_platforms": list(platforms),
        "liveness": "live",
        "total_links": live_dofollow,
    }
    base.update(extra)
    return base


def _discounts(**by_target):
    res = DiscountResult()
    for canon, td in by_target.items():
        res.by_target[canon] = td
    return res


def test_transform_dead_link_decrements_and_prunes():
    res = _discounts(**{CANON: TargetDiscount(dead_count=1, dead_platforms={"A"})})
    out, tally = apply_discounts([_row()], res)
    assert out[0]["live_dofollow"] == 2
    assert out[0]["live_dofollow_platforms"] == ["B", "C"]
    assert tally.targets_reduced == 1
    assert tally.unmatched_discount == 0


def test_transform_dofollow_lost_decrements_and_prunes():
    res = _discounts(
        **{CANON: TargetDiscount(dofollow_lost_count=1, dead_platforms={"B"})}
    )
    out, _ = apply_discounts([_row()], res)
    assert out[0]["live_dofollow"] == 2
    assert out[0]["live_dofollow_platforms"] == ["A", "C"]


def test_transform_two_dead_links_same_target():
    res = _discounts(
        **{CANON: TargetDiscount(dead_count=2, dead_platforms={"A", "B"})}
    )
    out, _ = apply_discounts([_row()], res)
    assert out[0]["live_dofollow"] == 1
    assert out[0]["live_dofollow_platforms"] == ["C"]


def test_transform_floors_at_zero():
    res = _discounts(**{CANON: TargetDiscount(dead_count=2, dead_platforms={"A"})})
    out, _ = apply_discounts([_row(live_dofollow=1)], res)
    assert out[0]["live_dofollow"] == 0  # never negative


def test_transform_passthrough_identical_when_unmatched():
    other_canon = canonicalize_url("https://other.site/")
    res = _discounts(
        **{other_canon: TargetDiscount(dead_count=1, dead_platforms={"A"})}
    )
    row = _row()
    out, tally = apply_discounts([row], res)
    assert out[0] == row  # byte-for-byte: all keys preserved, none mutated
    assert tally.targets_reduced == 0
    assert tally.unmatched_discount == 1  # the discount matched no ledger row


def test_transform_preserves_all_keys():
    res = _discounts(**{CANON: TargetDiscount(dead_count=1, dead_platforms={"A"})})
    row = _row(exact_match_pct=12.5, has_anchor_data=True, history_item_ids=["h1"])
    out, _ = apply_discounts([row], res)
    assert out[0]["exact_match_pct"] == 12.5
    assert out[0]["has_anchor_data"] is True
    assert out[0]["history_item_ids"] == ["h1"]
    assert out[0]["liveness"] == "live"


def test_transform_output_is_valid_plan_gap_input():
    # Integration: the rewritten row still parses as plan-gap input and the
    # deficit reflects the reduced live_dofollow.
    res = _discounts(**{CANON: TargetDiscount(dead_count=1, dead_platforms={"A"})})
    out, _ = apply_discounts([_row(live_dofollow=2, platforms=("A", "B"))], res)
    opts = GapOptions(desired=2, language="en")
    seeds, counts, _meta = plan_gap(
        out, opts, active_dofollow=["A", "B", "C"], now=NOW.replace(tzinfo=None)
    )
    # live_dofollow 2 → 1 after discount; desired 2 → deficit 1 → exactly one seed,
    # and it avoids B (still live), targeting a freed/other platform.
    assert len(seeds) == 1
    assert seeds[0]["platform"] in {"A", "C"}
    assert counts.satisfied == 0


# --------------------------------------------------------------------------- #
# Multi-placement guard: a dead link must not prune a platform that a surviving
# alive link still occupies for the same target (adversarial finding #1).
# --------------------------------------------------------------------------- #


def test_alive_shields_same_platform_from_pruning(store):
    # Same target, same platform "A", two placements: one dead, one alive.
    _append(store, 1, verdicts.HOST_GONE, platform="A")
    _append(store, 2, verdicts.ALIVE, platform="A")
    res = build_discount_map(store)
    assert res.by_target[CANON].dead_count == 1
    assert res.alive_platforms[CANON] == {"A"}
    # live_dofollow drops by the dead count, but "A" stays (a live link remains).
    out, _ = apply_discounts(
        [_row(live_dofollow=2, platforms=("A", "B"))], res
    )
    assert out[0]["live_dofollow"] == 1
    assert out[0]["live_dofollow_platforms"] == ["A", "B"]  # A NOT pruned


def test_dead_and_alive_different_platforms_prunes_only_dead(store):
    _append(store, 1, verdicts.HOST_GONE, platform="A")
    _append(store, 2, verdicts.ALIVE, platform="B")
    res = build_discount_map(store)
    out, _ = apply_discounts(
        [_row(live_dofollow=2, platforms=("A", "B"))], res
    )
    assert out[0]["live_dofollow"] == 1
    assert out[0]["live_dofollow_platforms"] == ["B"]  # only the dead A pruned


def test_transform_alive_platform_shield_pure():
    # Pure transform: dead on A but A is also in alive_platforms → not pruned.
    res = DiscountResult()
    res.by_target[CANON] = TargetDiscount(dead_count=1, dead_platforms={"A"})
    res.alive_platforms[CANON] = {"A"}
    out, _ = apply_discounts([_row(live_dofollow=2, platforms=("A", "B"))], res)
    assert out[0]["live_dofollow"] == 1
    assert out[0]["live_dofollow_platforms"] == ["A", "B"]
