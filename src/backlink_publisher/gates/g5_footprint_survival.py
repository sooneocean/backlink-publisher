"""G5 — footprint survival audit (Tier-1 offline). Plan 2026-06-01-005 Unit 4.

Premise: footprint's pre-publish fingerprint (the operator's own ``<a>`` bytes —
``rel`` value, attr order, …) is *actually detectable by a crawler*. The
``footprint`` module measures those bytes **before** publish; most platforms
re-serialize the anchor, so the operator fingerprint may never reach the crawled
page (the exact reason ``entropy-budget`` was KILLED). G5 re-fetches a sample of
*published* pages via the SSRF-guarded :func:`inspect_target_anchor` and measures
whether the operator's ``rel`` fingerprint survives into the live DOM.

* **survival_rate** = of the readable pages, the fraction whose live anchor is
  present AND carries the operator's emitted ``rel`` (token-set match).
* **Saturation escape:** many published hosts anti-bot / UA-cloak the verifier,
  so a sample can be dominated by re-fetch failures. Below a re-fetch
  *success-rate* floor the gate returns a **terminal INCONCLUSIVE-unmeasurable**
  (no endless resample) — itself evidence the premise is unverifiable by canary
  re-fetch, which argues against the footprint-gate just like entropy-budget.

Low survival → ``KILL`` (footprint-gate built on a dead premise). High survival
→ ``GO`` (footprint measures a crawler-visible signal). Pure + injectable.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable

from backlink_publisher.publishing.adapters.link_attr_verifier import inspect_target_anchor
from backlink_publisher.gates import verdict as gv

InspectFn = Callable[[str, str], dict]

#: Below this fraction of the sample actually re-fetchable, the premise is
#: unverifiable by canary re-fetch → terminal INCONCLUSIVE-unmeasurable.
DEFAULT_SATURATION_FLOOR = 0.5

#: The operator's default emitted rel fingerprint (``_format_anchor_html`` default).
DEFAULT_EXPECTED_REL = "noopener noreferrer"


@dataclass(frozen=True, slots=True)
class PublishedLink:
    live_url: str
    target_url: str


def _rel_tokens(rel: str | None) -> frozenset[str]:
    """Order-insensitive rel token set (``"a b"`` == ``"b a"``)."""
    return frozenset((rel or "").lower().split())


def assess_survival(
    links: Iterable[PublishedLink],
    *,
    expected_rel: str = DEFAULT_EXPECTED_REL,
    inspect_fn: InspectFn = inspect_target_anchor,
    survival_threshold: float | None = None,
    saturation_floor: float = DEFAULT_SATURATION_FLOOR,
) -> gv.GateVerdict:
    """G5 verdict. ``survival_threshold`` ``None`` = calibration → INCONCLUSIVE."""
    sample = list(links)
    total = len(sample)
    expected = _rel_tokens(expected_rel)
    readable = anchor_found = rel_survived = 0
    reasons: Counter[str] = Counter()

    for link in sample:
        res = inspect_fn(link.live_url, link.target_url)
        if not res.get("page_readable"):
            reasons[res.get("reason") or "unreadable"] += 1
            continue
        readable += 1
        if not res.get("target_anchor_found"):
            reasons["anchor_stripped"] += 1  # operator bytes gone from crawled DOM
            continue
        anchor_found += 1
        if _rel_tokens(res.get("target_rel")) == expected:
            rel_survived += 1
        else:
            reasons["rel_rewritten"] += 1  # platform re-serialized → fingerprint lost

    readable_fraction = (readable / total) if total else 0.0
    survival_rate = (rel_survived / readable) if readable else None
    threshold_set = survival_threshold is not None
    evidence = (
        f"readable={readable}/{total}",
        f"anchor_found={anchor_found}",
        f"rel_survived={rel_survived}",
        "reasons=" + (", ".join(f"{r}={n}" for r, n in sorted(reasons.items())) or "none"),
    )

    # Nothing to sample → INCONCLUSIVE (not terminal; a later sweep may have data).
    if total == 0:
        return gv.build_verdict(
            "g5", gv.INCONCLUSIVE, sample_n=0, confirmed=False, threshold_set=False,
            rate=None, note="no published links to sample", evidence=evidence,
        )
    # Saturation: too few pages re-fetchable → premise unverifiable by re-fetch.
    if readable_fraction < saturation_floor:
        return gv.build_verdict(
            "g5", gv.INCONCLUSIVE, sample_n=total, confirmed=False, threshold_set=threshold_set,
            rate=survival_rate, terminal=True,
            note="premise unverifiable by canary re-fetch (anti-bot saturation)",
            evidence=evidence,
        )

    if not threshold_set:
        requested = gv.INCONCLUSIVE
    elif survival_rate is not None and survival_rate >= survival_threshold:
        requested = gv.GO  # fingerprint survives → footprint-gate measures a real signal
    else:
        requested = gv.KILL  # fingerprint doesn't survive → dead premise (entropy-budget)

    return gv.build_verdict(
        "g5", requested, sample_n=total, confirmed=True, threshold_set=threshold_set,
        rate=survival_rate, note="footprint fingerprint survival in crawled DOM",
        evidence=evidence,
    )
