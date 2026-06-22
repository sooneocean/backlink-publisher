"""Pure-function analysis views over ProfileState — extracted from profile.py.

Deterministic computations over a ``ProfileState`` sliding window:
anchor-type distribution, url-category distribution, anchor-text dedup
window, degradation rate, and article-boundary reconstruction.

All functions are side-effect-free: no I/O, no state mutation, no logging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backlink_publisher.config import ANCHOR_TYPES

# Circular-safe: profile.py defines these constants before reaching the
# ``from ._profile_analysis import ...`` re-export, so the partially
# initialized module object already carries them when this module loads.
from .profile import _DEFAULT_TEXT_WINDOW, _MAX_ENTRIES

#: Look-back window (in ARTICLES) for the secondary-link balance controller.
#: Expressed as an article count, but pinned to ``_MAX_ENTRIES`` (the retained
#: *entry* cap) so it always spans the FULL retained profile: every article has
#: at least one entry, so the article count can never exceed the entry cap, and
#: ``articles[-_SECONDARY_SPLIT_WINDOW:]`` is therefore "all retained articles".
#: Distinct from ``_DEFAULT_TEXT_WINDOW`` (the 20-entry anchor-text dedup window)
#: — the two windows answer different questions and must not be conflated.
_SECONDARY_SPLIT_WINDOW = _MAX_ENTRIES

if TYPE_CHECKING:
    from .profile import ProfileEntry, ProfileState


def recent_type_counts(profile: ProfileState) -> dict[str, int]:
    """Count anchor types across all entries in the window.

    Returns a dict with every key in ``ANCHOR_TYPES`` so callers can do
    ``counts[type]`` without ``.get`` defaults. Unknown types are ignored
    (defensive — schema validation should have caught them at write time).
    """
    counts: dict[str, int] = {t: 0 for t in ANCHOR_TYPES}
    for entry in profile.entries:
        if entry.anchor_type in counts:
            counts[entry.anchor_type] += 1
    return counts


def recent_url_category_counts(profile: ProfileState) -> dict[str, int]:
    """Count entries per url_category. Categories with zero entries are absent
    from the result — callers should treat missing keys as zero."""
    counts: dict[str, int] = {}
    for entry in profile.entries:
        counts[entry.url_category] = counts.get(entry.url_category, 0) + 1
    return counts


def recent_texts(
    profile: ProfileState, n: int = _DEFAULT_TEXT_WINDOW
) -> list[str]:
    """Return the most recent ``n`` anchor texts, newest first.

    The resolver passes this list to its dedup filter so a recently-used
    anchor isn't re-selected in the next few articles. ``n`` defaults to 20,
    matching the brainstorm-confirmed dedup window.
    """
    return [e.anchor_text for e in reversed(profile.entries[-n:])]


def recent_degradation_rate(
    profile: ProfileState, n: int = _MAX_ENTRIES
) -> float:
    """Fraction of the most recent ``n`` entries marked ``degraded``.

    Returns 0.0 when the profile is empty (cold-start) — there is no rate
    when there is no data. Above 0.1 (10%) is the brainstorm-defined alarm
    threshold; this function is the data source for Unit 9's report.
    """
    sample = profile.entries[-n:]
    if not sample:
        return 0.0
    return sum(1 for e in sample if e.degraded) / len(sample)


def _group_into_articles(
    entries: list[ProfileEntry],
) -> list[list[ProfileEntry]]:
    """Reconstruct article boundaries from a flat link list.

    Each "main" entry starts a new article; subsequent entries belong to that
    article until the next "main" appears. Entries before the first "main"
    are dropped — they came from a sliding-window trim that severed an article
    mid-record, so including them would misreport secondary counts.
    """
    articles: list[list[ProfileEntry]] = []
    current: list[ProfileEntry] = []
    for entry in entries:
        if entry.link_role == "main":
            if current:
                articles.append(current)
            current = [entry]
        elif current:
            current.append(entry)
        # else: secondary before any main → trimmed article remnant, drop.
    if current:
        articles.append(current)
    return articles


def recent_secondary_count_split(
    profile: ProfileState,
    n: int = _SECONDARY_SPLIT_WINDOW,
) -> tuple[int, int]:
    """Articles in the most recent ``n`` that had 1 vs 2 secondary links.

    Returned as ``(count_with_1_secondary, count_with_2_secondaries)``. The
    scheduler uses this to keep the (1, 2) split converging on 50/50 — see
    Unit 4 ``pick_secondary_count``.

    ``n`` defaults to :data:`_SECONDARY_SPLIT_WINDOW` so the balance controller
    weighs the FULL retained profile, not just the most recent slice. (It
    previously defaulted to the unrelated 20-entry anchor-text dedup window,
    which silently ignored the older half of the retained articles.)

    Articles with 0 secondaries (a degraded edge case or unusual record) and
    articles with 3+ secondaries (not currently possible by design) are not
    counted in either bucket.
    """
    articles = _group_into_articles(profile.entries)
    sample = articles[-n:]
    count_1 = 0
    count_2 = 0
    for art in sample:
        secondary_count = sum(1 for e in art if e.link_role == "secondary")
        if secondary_count == 1:
            count_1 += 1
        elif secondary_count == 2:
            count_2 += 1
    return count_1, count_2
