"""Pure-function scheduler — picks anchor types + secondary URL categories.

Given a per-site sliding-window profile and a target distribution, this module
returns a ``ScheduleDecision`` describing what types of anchors the next
article should use. The scheduler is intentionally side-effect-free: it does
no I/O, holds no state, and never decides anchor *text*. The resolver (Unit 5)
turns these typed slots into concrete anchor strings; the profile store (Unit
3) records what actually landed.

The scheduler is the only place that *steers* the distribution. Two knobs:

1. **Anchor type per link.** Compute (target - actual) deficits across the
   four anchor types; pick the largest. Ties break by ``_TYPE_TIEBREAK_ORDER``
   (Branded > Partial > LSI > Exact — conservative on the "high-risk" Exact
   side). Within one article, after picking a type for link N we temporarily
   credit the count so link N+1 doesn't pile onto the same type unless the
   global picture really demands it. That keeps single articles from looking
   like "all Branded" or "all Exact" while the multi-article average still
   converges on the target.

2. **Secondary URL category.** Pick the least-recently-used non-home category,
   never repeating one inside a single article. The brainstorm leaves the
   exact target unspecified, so we treat the non-home categories as having an
   implicit uniform target — the least-used wins.

Cold start: with an empty profile, all anchor deficits equal the target
proportions, so the main link gets Branded (largest target → largest deficit).
The subsequent secondaries then naturally rotate to Partial and LSI through
the within-article diversity mechanism described above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from backlink_publisher.anchor.profile import (
    ProfileState,
    _MAX_ENTRIES,
    recent_secondary_count_split,
    recent_type_counts,
    recent_url_category_counts,
)
from backlink_publisher.config import ANCHOR_TYPES
from backlink_publisher._util.errors import InputValidationError

# Tie-break order when multiple anchor types share the maximum deficit.
# Brainstorm v2 R20 + Plan v2 Key Decisions: Branded > Partial > LSI > Exact.
# Note this is intentionally NOT the same as ``ANCHOR_TYPES`` ordering — the
# tie-break prefers the lower-risk types when the math is indifferent.
_TYPE_TIEBREAK_ORDER: tuple[str, ...] = ("branded", "partial", "lsi", "exact")
_TYPE_TIEBREAK_RANK: dict[str, int] = {
    t: rank for rank, t in enumerate(_TYPE_TIEBREAK_ORDER)
}

# The "home" category is reserved for the main link only; secondaries must
# point elsewhere so a single article never has two anchors to the same URL.
_HOME_CATEGORY = "home"


@dataclass(frozen=True)
class SecondaryLink:
    url_category: str
    anchor_type: str


@dataclass(frozen=True)
class ScheduleDecision:
    main_link_anchor_type: str
    secondary_links: tuple[SecondaryLink, ...]


def schedule(
    profile: ProfileState,
    target_proportions: dict[str, float],
    available_url_categories: Iterable[str],
) -> ScheduleDecision:
    """Return the type + url-category decisions for the next article.

    ``available_url_categories`` is the set of category names the site
    declared in config (including ``home``). The scheduler requires at least
    one non-home category to engage — otherwise it has nowhere to point the
    secondary link(s) without duplicating the main URL, and raises
    ``InputValidationError`` so the caller falls back to the legacy path.
    """
    categories = list(available_url_categories)
    non_home = sorted(c for c in categories if c != _HOME_CATEGORY)
    if not non_home:
        raise InputValidationError(
            f"anchor scheduler requires at least one non-home url_category, "
            f"got {categories!r}"
        )

    secondary_count = _pick_secondary_count(profile, max_available=len(non_home))

    # Counts we'll mutate as we hand out types within this article. Starting
    # from the real recent counts means single decisions still see the global
    # picture; incrementing as we go drives within-article diversity.
    working_counts = recent_type_counts(profile)

    main_type = _pick_anchor_type(working_counts, target_proportions, window_size=_MAX_ENTRIES)
    working_counts[main_type] = working_counts.get(main_type, 0) + 1

    # For url_category selection across the article, similarly maintain a
    # working count snapshot of recent category use.
    working_url_counts = recent_url_category_counts(profile)
    used_in_this_article: set[str] = set()

    secondaries: list[SecondaryLink] = []
    for _ in range(secondary_count):
        sec_type = _pick_anchor_type(working_counts, target_proportions, window_size=_MAX_ENTRIES)
        working_counts[sec_type] = working_counts.get(sec_type, 0) + 1

        sec_cat = _pick_secondary_url_category(
            non_home, used_in_this_article, working_url_counts,
        )
        used_in_this_article.add(sec_cat)
        working_url_counts[sec_cat] = working_url_counts.get(sec_cat, 0) + 1

        secondaries.append(SecondaryLink(url_category=sec_cat, anchor_type=sec_type))

    return ScheduleDecision(
        main_link_anchor_type=main_type,
        secondary_links=tuple(secondaries),
    )


def _pick_secondary_count(profile: ProfileState, *, max_available: int) -> int:
    """Pick 1 or 2 secondary links, keeping the rolling (1, 2) split near 50/50.

    Behavioral target: roughly half the articles ship with 2 secondaries
    (total 3 links) and half with 1 (total 2 links), so the average article
    has ~2.5 links — a brainstorm-defined target that smooths the per-article
    link density without making every article look identical.

    If the site only declared one non-home category, we cap at 1 secondary
    because a second secondary would have nowhere to point that didn't repeat.
    """
    if max_available <= 1:
        return 1
    count_1, count_2 = recent_secondary_count_split(profile)
    # If we've seen at least as many 1-link articles as 2-link articles
    # recently, the next one should be 2. (And vice versa, including the
    # cold-start case where both counts are 0 → returns 2, matching the
    # plan's cold-start test scenario.)
    return 2 if count_1 >= count_2 else 1


def _pick_anchor_type(
    current_counts: dict[str, int],
    target_proportions: dict[str, float],
    *,
    window_size: int = 100,
) -> str:
    """Return the anchor type with the largest (target − actual) deficit.

    At cold start (``total == 0``), deficits use the full ``window_size`` as
    reference — giving count-based absolute deficits (e.g. branded deficit =
    55). Once entries exist (``total > 0``), deficits normalize by the current
    total, enabling within-article diversity via the working-count credit.

    On cold start every type's deficit equals its target count, so Branded
    (largest target) wins. No special case needed; the math handles it.

    Unknown keys in ``current_counts`` are ignored — that defensive handling
    means callers can pass a dict from a different source without first
    filtering. Unknown keys in ``target_proportions`` similarly default to 0.
    """
    total = sum(current_counts.get(t, 0) for t in ANCHOR_TYPES)
    deficits: dict[str, float] = {}
    for t in ANCHOR_TYPES:
        target = target_proportions.get(t, 0.0)
        actual = (current_counts.get(t, 0) / total) if total > 0 else 0.0
        ref = window_size if total == 0 else 1.0
        # Round to absorb floating-point noise — without this, deficits that
        # are "conceptually equal" compare as different at the 1e-17 level and
        # the tie-break order never fires.
        deficits[t] = round(target * ref - actual, 6)
    return max(
        ANCHOR_TYPES,
        key=lambda t: (deficits[t], -_TYPE_TIEBREAK_RANK[t]),
    )


def _pick_secondary_url_category(
    non_home_categories: list[str],
    used_in_this_article: set[str],
    recent_url_counts: dict[str, int],
) -> str:
    """Pick the least-used non-home category not already used in this article.

    ``non_home_categories`` is expected to be alphabetically sorted by the
    caller; we re-sort defensively. Ties on recent-count are broken by that
    alphabetical order, which keeps the choice deterministic across runs.

    Raises ``InputValidationError`` only if the caller asked for more
    secondaries than there are distinct non-home categories — the public
    ``schedule`` entrypoint guards against that by capping the secondary
    count via ``_pick_secondary_count``.
    """
    candidates = sorted(c for c in non_home_categories if c not in used_in_this_article)
    if not candidates:
        raise InputValidationError(
            "no available url_category — caller exhausted distinct non-home "
            "categories within a single article"
        )
    return min(candidates, key=lambda c: (recent_url_counts.get(c, 0), c))
