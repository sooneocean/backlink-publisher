"""Tests for backlink_publisher.anchor_scheduler — pure scheduling logic."""

from __future__ import annotations

from collections import Counter

import pytest

from backlink_publisher.anchor.profile import (
    ProfileEntry,
    ProfileState,
    now_iso,
)
from backlink_publisher.anchor.scheduler import (
    ScheduleDecision,
    SecondaryLink,
    _pick_anchor_type,
    schedule,
)
from backlink_publisher._util.errors import InputValidationError


SAFE_SEO = {"branded": 0.55, "partial": 0.25, "exact": 0.10, "lsi": 0.10}
DEFAULT_CATS = ["home", "hot", "animate", "category", "topic"]


def _entry(
    *,
    role: str = "main",
    cat: str = "home",
    ty: str = "branded",
    text: str = "x",
) -> ProfileEntry:
    return ProfileEntry(
        ts=now_iso(),
        link_role=role,
        url_category=cat,
        anchor_type=ty,
        anchor_text=text,
    )


def _build_profile(types_in_order: list[str]) -> ProfileState:
    """Build a profile with main-only entries of the given types."""
    entries = [_entry(ty=t, text=f"t{i}") for i, t in enumerate(types_in_order)]
    return ProfileState(main_domain="https://example.com", entries=entries)


# ── cold start ──────────────────────────────────────────────────────────────


def test_cold_start_returns_branded_main_with_two_secondaries():
    empty = ProfileState(main_domain="https://example.com")
    decision = schedule(empty, SAFE_SEO, DEFAULT_CATS)

    assert decision.main_link_anchor_type == "branded"
    assert len(decision.secondary_links) == 2
    # Secondaries must be from non-home, and distinct
    cats = [s.url_category for s in decision.secondary_links]
    assert "home" not in cats
    assert len(set(cats)) == 2


def test_cold_start_secondary_types_diversify():
    """At cold start, branded deficit is highest (55) and wins the main link.
    After crediting +1 to working_counts, the proportional deficit flips and
    partial/lsi pick up the secondary slots — within-article diversity works."""
    empty = ProfileState(main_domain="https://example.com")
    decision = schedule(empty, SAFE_SEO, DEFAULT_CATS)
    types = [decision.main_link_anchor_type, *(s.anchor_type for s in decision.secondary_links)]
    # Main is branded; secondaries diversify via within-article credit
    assert types[0] == "branded"
    assert len(set(types)) > 1


# ── perfect distribution ─────────────────────────────────────────────────────


def test_at_target_distribution_branded_wins_tiebreak():
    """When deficits are all ~0, branded wins by the tie-break rule."""
    profile = _build_profile(
        ["branded"] * 55 + ["partial"] * 25 + ["exact"] * 10 + ["lsi"] * 10
    )
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert decision.main_link_anchor_type == "branded"


# ── deficit-driven selection ────────────────────────────────────────────────


def test_partial_deficit_drives_selection():
    """100 entries all Branded → partial has the largest deficit → next main = partial."""
    profile = _build_profile(["branded"] * 100)
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert decision.main_link_anchor_type == "partial"


def test_lsi_wins_when_only_lsi_is_under():
    """branded 55, partial 25, exact 20, lsi 0 → lsi has the biggest deficit."""
    profile = _build_profile(
        ["branded"] * 55 + ["partial"] * 25 + ["exact"] * 20
    )
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert decision.main_link_anchor_type == "lsi"


# ── tie-break order ─────────────────────────────────────────────────────────


def test_lsi_wins_over_exact_when_tied():
    """branded surplus, partial at target, exact and lsi both deficit by same
    amount → tie-break says LSI > Exact."""
    profile = _build_profile(
        ["branded"] * 60 + ["partial"] * 25 + ["exact"] * 7 + ["lsi"] * 8
    )
    # actual: br 60/100=0.60 (surplus 0.05), pa 0.25 (target), ex 0.07 (def 0.03), lsi 0.08 (def 0.02)
    # exact deficit larger here, so this is just a setup; assert via direct fn:
    deficit_test_counts = {"branded": 50, "partial": 25, "exact": 12, "lsi": 13}
    # br 50/100 → def 0.05; pa 25/100 → def 0; ex 12/100 → def -0.02; lsi 13/100 → def -0.03
    # Branded wins. Now force a real tie:
    tied_counts = {"branded": 55, "partial": 25, "exact": 15, "lsi": 5}
    # br def 0; pa def 0; ex def -0.05; lsi def 0.05 → lsi wins outright (not tied)
    # Force exact + lsi tie:
    really_tied = {"branded": 55, "partial": 35, "exact": 5, "lsi": 5}
    # br def 0; pa def -0.10; ex def 0.05; lsi def 0.05 → ex/lsi tied at 0.05 → LSI wins
    chosen = _pick_anchor_type(really_tied, SAFE_SEO)
    assert chosen == "lsi"


def test_branded_wins_against_partial_when_tied():
    """branded and partial both have deficit 0.05 → branded wins (highest rank)."""
    tied = {"branded": 50, "partial": 20, "exact": 15, "lsi": 15}
    # totals 100 → actual br 0.50 (def 0.05), pa 0.20 (def 0.05), ex 0.15 (def -0.05), lsi 0.15 (def -0.05)
    chosen = _pick_anchor_type(tied, SAFE_SEO)
    assert chosen == "branded"


def test_partial_wins_against_lsi_when_tied():
    """If partial and lsi are tied at the top, partial wins."""
    tied = {"branded": 60, "partial": 20, "exact": 10, "lsi": 10}
    # br def -0.05; pa def 0.05; ex def 0; lsi def 0 → partial wins outright (not tied with lsi)
    # Force tie: pa and lsi both at 0.05
    really_tied = {"branded": 60, "partial": 20, "exact": 15, "lsi": 5}
    # br def -0.05; pa def 0.05; ex def -0.05; lsi def 0.05 → pa/lsi tied → partial wins
    chosen = _pick_anchor_type(really_tied, SAFE_SEO)
    assert chosen == "partial"


# ── secondary count balance ─────────────────────────────────────────────────


def test_secondary_count_recovers_balance():
    """If recent history shows more 2-secondary articles than 1, next article
    should get 1 secondary."""
    # 5 articles with 2 secondaries, 0 with 1 → next should be 1
    entries: list[ProfileEntry] = []
    for i in range(5):
        entries.append(_entry(ty="branded"))
        entries.append(_entry(role="secondary", cat="hot", ty="partial"))
        entries.append(_entry(role="secondary", cat="animate", ty="lsi"))
    profile = ProfileState(main_domain="https://example.com", entries=entries)

    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert len(decision.secondary_links) == 1


def test_secondary_count_cold_start_returns_two():
    empty = ProfileState(main_domain="https://example.com")
    decision = schedule(empty, SAFE_SEO, DEFAULT_CATS)
    assert len(decision.secondary_links) == 2


# ── url_category fan-out ────────────────────────────────────────────────────


def test_secondary_url_categories_never_home():
    profile = ProfileState(main_domain="https://example.com")
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    for sec in decision.secondary_links:
        assert sec.url_category != "home"


def test_secondary_url_categories_distinct_within_article():
    """If we ask for 2 secondaries, they must be from different categories."""
    empty = ProfileState(main_domain="https://example.com")
    decision = schedule(empty, SAFE_SEO, DEFAULT_CATS)
    assert len(decision.secondary_links) == 2
    cats = [s.url_category for s in decision.secondary_links]
    assert len(set(cats)) == 2


def test_secondary_url_picks_least_used():
    """When 'hot' and 'animate' have been used and the secondary-count
    balance calls for 2 secondaries, scheduler picks the unused categories."""
    entries: list[ProfileEntry] = []
    # 10 articles with main + 1 secondary (so count_1 dominates → next gets 2 secondaries)
    # Half use 'hot', half use 'animate' so 'category' and 'topic' stay zero.
    for i in range(5):
        entries.append(_entry(ty="branded"))
        entries.append(_entry(role="secondary", cat="hot", ty="partial"))
    for i in range(5):
        entries.append(_entry(ty="branded"))
        entries.append(_entry(role="secondary", cat="animate", ty="lsi"))
    profile = ProfileState(main_domain="https://example.com", entries=entries)

    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert len(decision.secondary_links) == 2
    cats = {s.url_category for s in decision.secondary_links}
    # 'category' and 'topic' have zero recent use → both should be selected
    assert "category" in cats
    assert "topic" in cats


# ── error paths ─────────────────────────────────────────────────────────────


def test_only_home_category_raises():
    profile = ProfileState(main_domain="https://example.com")
    with pytest.raises(InputValidationError, match="non-home url_category"):
        schedule(profile, SAFE_SEO, ["home"])


def test_no_categories_at_all_raises():
    profile = ProfileState(main_domain="https://example.com")
    with pytest.raises(InputValidationError, match="non-home url_category"):
        schedule(profile, SAFE_SEO, [])


def test_single_non_home_caps_secondary_count_to_one():
    profile = ProfileState(main_domain="https://example.com")
    decision = schedule(profile, SAFE_SEO, ["home", "hot"])
    # Cold start wants 2 secondaries but only one non-home category exists
    assert len(decision.secondary_links) == 1
    assert decision.secondary_links[0].url_category == "hot"


# ── _pick_anchor_type defensive cases ───────────────────────────────────────


def test_pick_anchor_type_ignores_unknown_count_keys():
    counts = {"branded": 50, "partial": 25, "exact": 10, "lsi": 10, "garbage": 999}
    chosen = _pick_anchor_type(counts, SAFE_SEO)
    # garbage ignored → branded deficit = 55-50=5, partial=0, exact=0, lsi=0 → branded wins
    assert chosen == "branded"


def test_pick_anchor_type_missing_target_key_treats_as_zero():
    """If the proportion table doesn't mention 'lsi', it's effectively a zero target."""
    counts = {"branded": 50, "partial": 25, "exact": 10, "lsi": 10}
    partial_target = {"branded": 0.6, "partial": 0.4}  # exact + lsi unspecified
    chosen = _pick_anchor_type(counts, partial_target)
    # branded deficit = 60-50=10, partial deficit = 40-25=15 → partial wins
    assert chosen == "partial"


# ── convergence simulation ──────────────────────────────────────────────────


def test_long_simulation_converges_to_target_distribution():
    """Run schedule + record many times; final distribution should match target."""
    entries: list[ProfileEntry] = []

    def current_profile() -> ProfileState:
        # Keep only the last 100 entries to simulate the real sliding window
        window = entries[-100:]
        return ProfileState(main_domain="https://example.com", entries=window)

    n_articles = 400
    for _ in range(n_articles):
        decision = schedule(current_profile(), SAFE_SEO, DEFAULT_CATS)
        # Append the decisions to our running tape
        entries.append(_entry(ty=decision.main_link_anchor_type))
        for sec in decision.secondary_links:
            entries.append(_entry(role="secondary", cat=sec.url_category, ty=sec.anchor_type))

    # Tally over the last 100 link records (matches what the scheduler "sees")
    sample = entries[-100:]
    counts = Counter(e.anchor_type for e in sample)
    total = sum(counts.values())
    actual = {t: counts.get(t, 0) / total for t in ("branded", "partial", "exact", "lsi")}

    # Each type within 5 percentage points of its target — a strong but
    # reachable bar given a 100-entry window and 4 categories.
    for t, target in SAFE_SEO.items():
        assert abs(actual[t] - target) <= 0.05, f"{t}: actual={actual[t]:.3f} target={target}"


# ── ScheduleDecision is hashable + immutable ───────────────────────────────


def test_schedule_decision_is_frozen_dataclass():
    profile = ProfileState(main_domain="https://example.com")
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    with pytest.raises((AttributeError, Exception)):
        decision.main_link_anchor_type = "other"  # type: ignore[misc]


def test_secondary_links_are_distinct_objects():
    profile = ProfileState(main_domain="https://example.com")
    decision = schedule(profile, SAFE_SEO, DEFAULT_CATS)
    assert all(isinstance(s, SecondaryLink) for s in decision.secondary_links)
    assert isinstance(decision, ScheduleDecision)
