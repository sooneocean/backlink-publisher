"""The tri-state source-page *indexability* axis + its closed-vocab classifier.

Orthogonal to the 5-verdict liveness taxonomy (``recheck.verdicts``): a backlink
can be perfectly ``alive`` and dofollow yet sit on a page Google is told never to
index — in which case it passes **zero** equity. ``probe_liveness`` records this
as advisory metadata (peer to ``anchor_drift``); it never changes the liveness
verdict, and only the opt-in ``--fail-on-unindexable`` gate acts on it.

Three states, **fail-open**:

* ``ok``      — page read cleanly (200, ``</head>`` seen) with no noindex barrier.
* ``blocked`` — a deterministic barrier: ``<meta name=robots|googlebot ...noindex>``
                or an ``X-Robots-Tag: noindex`` header (R3a + R3b, both already
                folded into ``PreflightFacts.noindex`` — the *same* fact
                ``cli/canary_targets.py`` reads, so the two surfaces are a single
                source by construction).
* ``unknown`` — anything indeterminate: page not readable, non-200, fetch error,
                or the body prefix was truncated before ``</head>`` (truncation
                guard). Never a false ``blocked``, never a silent ``ok``.

``indexability_reason`` is a **closed vocabulary** (``meta_noindex`` / ``x_robots``;
``robots_disallow`` is reserved for the gated R3d follow-on) — a fixed token,
never raw fetched bytes, so a multi-KB hostile header can never reach events.db.

This module is the single home for those constants + the pure classifier so the
probe (producer) and ``events_io`` (persistence seam) agree by construction.
"""

from __future__ import annotations

from typing import Any, Final

from backlink_publisher.content._preflight_fetch import _has_noindex_directive

#: Page read cleanly and carries no detectable index barrier.
OK: Final = "ok"
#: A deterministic noindex barrier is present (meta robots / X-Robots-Tag).
BLOCKED: Final = "blocked"
#: Indeterminate — never claimed as either ``ok`` or ``blocked`` (fail-open).
UNKNOWN: Final = "unknown"

#: Every state the axis can take.
STATES: Final[frozenset[str]] = frozenset({OK, BLOCKED, UNKNOWN})

#: Closed reason vocabulary for a ``blocked`` reading. Never raw fetched bytes.
REASON_META_NOINDEX: Final = "meta_noindex"
REASON_X_ROBOTS: Final = "x_robots"
REASON_VOCAB: Final[frozenset[str]] = frozenset({REASON_META_NOINDEX, REASON_X_ROBOTS})


def classify_indexability(facts: Any) -> tuple[str, str | None]:
    """Map a ``content._preflight_fetch.PreflightFacts`` (from ``fetch_target``)
    onto ``(state, reason)``. Pure, total, never raises — reads only ``getattr``
    so a partial/duck-typed facts object can never crash the never-raise probe.

    Fail-open ladder:

    1. Non-200 / transport error (``reason`` set or ``status != 200``) ⇒ ``unknown``
       — an error page is not a clean "indexable/not" reading.
    2. ``facts.noindex`` True ⇒ ``blocked``. Reason is attributed by re-reading the
       (already length-capped) ``x_robots_tag``: if that header carries the noindex
       directive it is ``x_robots`` (header-level), else ``meta_noindex``.
    3. Clean 200 **and** ``</head>`` seen (``head_complete``) with no barrier ⇒ ``ok``.
       Requiring ``head_complete`` is the truncation guard: a prefix cut by a stray
       pre-head ``<h1>`` could hide a later meta, so it must never read as ``ok``.
    """
    if getattr(facts, "reason", None) is not None or getattr(facts, "status", None) != 200:
        return UNKNOWN, None
    if getattr(facts, "noindex", False):
        x_robots = getattr(facts, "x_robots_tag", None)
        if x_robots and _has_noindex_directive(x_robots):
            return BLOCKED, REASON_X_ROBOTS
        return BLOCKED, REASON_META_NOINDEX
    if not getattr(facts, "head_complete", False):
        return UNKNOWN, None
    return OK, None
