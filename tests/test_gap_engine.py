"""Pure gap engine — deficit-driven re-plan (plan-gap).

The active-dofollow universe is injected so tests are registry-agnostic and
deterministic; ``now`` is injected for the freshness floor.
"""

from datetime import datetime

from backlink_publisher.gap.engine import GapOptions, plan_gap

# Injected active-dofollow universe (matches the real 5-platform set today,
# but the test does not depend on the registry).
AD = ["blogger", "ghpages", "medium", "telegraph", "velog"]

NOW = datetime(2026, 5, 29, 12, 0, 0)
FRESH = "2026-05-29T00:00:00"


def _row(
    target="https://t.com/p",
    live_dofollow=0,
    liveness="live",
    live_dofollow_platforms=None,
    liveness_verified_at=FRESH,
):
    return {
        "target_url": target,
        "live_dofollow": live_dofollow,
        "liveness": liveness,
        "live_dofollow_platforms": list(live_dofollow_platforms or []),
        "liveness_verified_at": liveness_verified_at,
    }


def _run(rows, **opts_kw):
    opts = GapOptions(desired=opts_kw.pop("desired", 5), language=opts_kw.pop("language", "zh-CN"), **opts_kw)
    return plan_gap(rows, opts, active_dofollow=AD, now=NOW)


def test_happy_path_fans_out_distinct_platforms():
    seeds, counts, meta = _run([_row(live_dofollow=2, live_dofollow_platforms=["blogger"])], desired=5)
    # deficit = 5 - 2 = 3; candidates = AD - {blogger} = 4; emit 3.
    assert [s["platform"] for s in seeds] == ["ghpages", "medium", "telegraph"]
    assert all(set(s) == {"target_url", "platform", "main_domain", "language", "url_mode", "publish_mode"} for s in seeds)
    assert all(s["target_url"] == "https://t.com/p" for s in seeds)
    assert all(s["main_domain"] == "https://t.com" for s in seeds)
    assert all(s["language"] == "zh-CN" and s["url_mode"] == "A" and s["publish_mode"] == "draft" for s in seeds)
    assert counts.channel_exhausted == 0 and counts.satisfied == 0
    assert meta["as_of"] == FRESH


def test_cap_and_channel_exhausted_when_deficit_exceeds_candidates():
    # deficit 5, already live-dofollow on 3 → 2 candidates → emit 2 + flag exhausted.
    seeds, counts, _ = _run(
        [_row(live_dofollow=3, live_dofollow_platforms=["blogger", "ghpages", "medium"])],
        desired=8,
    )
    assert [s["platform"] for s in seeds] == ["telegraph", "velog"]
    assert counts.channel_exhausted == 1
    assert counts.channel_exhausted_targets == ["https://t.com/p"]


def test_nofollow_or_dead_platform_is_still_a_candidate():
    # Target has a (nofollow/dead) link on blogger — in `platforms` but NOT in
    # live_dofollow_platforms — so blogger MUST remain a candidate (P0 guard).
    seeds, counts, _ = _run([_row(live_dofollow=0, live_dofollow_platforms=[])], desired=1)
    assert [s["platform"] for s in seeds] == ["blogger"]  # blogger reachable
    assert counts.channel_exhausted == 0


def test_zero_deficit_is_satisfied_not_emitted():
    seeds, counts, _ = _run([_row(live_dofollow=5)], desired=5)
    assert seeds == []
    assert counts.satisfied == 1


def test_per_target_override_map():
    rows = [_row(target="https://t.com/money", live_dofollow=5, live_dofollow_platforms=["blogger"])]
    seeds, counts, _ = _run(rows, desired=5, desired_map={"https://t.com/money": 7})
    # override D=7, live=5 → deficit 2 → 2 seeds (non-blogger).
    assert len(seeds) == 2
    assert "blogger" not in [s["platform"] for s in seeds]


def test_none_live_dofollow_coerced_to_zero():
    seeds, _, _ = _run([_row(live_dofollow=None)], desired=1)
    assert len(seeds) == 1  # treated as full deficit, no crash


def test_unknown_liveness_is_failsafe_not_raise():
    seeds, counts, _ = _run([_row(liveness="pending")], desired=3)
    assert seeds == []
    assert counts.unknown_liveness == 1  # suppressed + counted, did NOT raise


def test_stale_and_unverified_suppressed_when_no_live_dofollow():
    seeds, counts, _ = _run(
        [_row(liveness="stale", live_dofollow=0), _row(liveness="unverified", live_dofollow=0, target="https://t.com/u")],
        desired=3,
    )
    assert seeds == []
    assert counts.suppressed_stale == 1 and counts.suppressed_unverified == 1


def test_emit_stale_overrides_suppression():
    seeds, _, _ = _run([_row(liveness="stale", live_dofollow=0)], desired=2, emit_stale=True)
    assert len(seeds) == 2


def test_live_dofollow_positive_stays_eligible_even_if_row_stale():
    # worst-status stale (one old link) but has live-dofollow evidence → eligible.
    seeds, counts, _ = _run([_row(liveness="stale", live_dofollow=1, live_dofollow_platforms=["blogger"])], desired=2)
    assert len(seeds) == 1  # deficit 1
    assert counts.suppressed_stale == 0


def test_failed_skipped_by_default_included_with_flag():
    rows = [_row(liveness="failed", live_dofollow=0)]
    assert _run(rows, desired=2)[1].failed == 1
    assert len(_run([_row(liveness="failed", live_dofollow=0)], desired=2, include_failed=True)[0]) == 2


def test_freshness_floor_suppresses_old_verification():
    old = "2026-04-01T00:00:00"  # ~58 days before NOW
    seeds, counts, _ = _run([_row(live_dofollow=0, liveness_verified_at=old)], desired=2, stale_after_days=14)
    assert seeds == []
    assert counts.suppressed_stale_floor == 1
    # ...but --emit-stale overrides the floor.
    assert len(_run([_row(live_dofollow=0, liveness_verified_at=old)], desired=2, stale_after_days=14, emit_stale=True)[0]) == 2


def test_weakest_first_order_preserved():
    rows = [_row(target="https://t.com/a", live_dofollow=0), _row(target="https://t.com/b", live_dofollow=0)]
    seeds, _, _ = _run(rows, desired=1)
    assert [s["target_url"] for s in seeds] == ["https://t.com/a", "https://t.com/b"]


def test_deterministic_repeat():
    rows = [_row(live_dofollow=1, live_dofollow_platforms=["medium"])]
    assert _run(rows, desired=4)[0] == _run(list(rows), desired=4)[0]


def test_missing_target_url_is_failsafe_not_raise():
    # Valid-JSON row lacking target_url must be skipped + counted, never KeyError.
    seeds, counts, _ = _run(
        [{"liveness": "live", "live_dofollow": 0, "live_dofollow_platforms": [], "liveness_verified_at": FRESH}],
        desired=2,
    )
    assert seeds == []
    assert counts.malformed == 1


def test_empty_or_nonstring_target_url_is_malformed():
    seeds, counts, _ = _run([_row(target=""), {"target_url": 123, "liveness": "live"}], desired=2)
    assert seeds == []
    assert counts.malformed == 2
