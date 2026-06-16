"""G2 — money-page decay baseline probe (Tier-1 offline). Plan 2026-06-01-005 Unit 2.

Samples the operator's *own* money pages and measures the silent-decay rate —
``noindex`` / 4xx / soft-404 / off-host redirect — using the existing
SSRF-guarded :func:`fetch_target` primitive (no new fetch path). The premise it
falsifies: "do my money pages silently decay after publish at a rate that
justifies a destination-decay machine?"

Pure and injectable: ``assess_decay`` takes a ``fetch_fn`` so the engine is
fully testable offline; the CLI passes the real ``fetch_target``. Evidence is
**aggregate + host-stripped** (reason counts, never raw operator URLs) so the
committed ledger never leaks operator domains.

Classification (from :class:`PreflightFacts`, never raises):

* ``status is None``              → **unmeasurable** (probe error; → INCONCLUSIVE,
                                     not the decay numerator)
* ``noindex``                     → **decayed** (Google won't index → silent death)
* ``soft404`` / ``status >= 400`` → **decayed**
* ``redirected and host_diff``    → **decayed** (off-host redirect = page gone)
* otherwise                       → **healthy**
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Callable

from backlink_publisher.content._preflight_fetch import PreflightFacts, fetch_target
from backlink_publisher.gates import verdict as gv

FetchFn = Callable[[str], PreflightFacts]

_DECAYED = "decayed"
_HEALTHY = "healthy"
_UNMEASURABLE = "unmeasurable"

#: Below this fraction of pages actually readable, the sample is too thin to
#: confirm anything → INCONCLUSIVE regardless of the measured rate.
_MIN_READABLE_FRACTION = 0.5


def classify_page(facts: PreflightFacts) -> tuple[str, str]:
    """Map one page's :class:`PreflightFacts` to ``(bucket, reason)``. Pure, total."""
    if facts.status is None:
        return _UNMEASURABLE, facts.reason or "probe_error"
    if facts.noindex:
        return _DECAYED, "noindex"
    if facts.soft404:
        return _DECAYED, "soft404"
    if facts.status >= 400:
        return _DECAYED, f"http_{facts.status}"
    if facts.redirected and facts.host_diff:
        return _DECAYED, "offhost_redirect"
    return _HEALTHY, "ok"


def assess_decay(
    urls: Iterable[str],
    *,
    fetch_fn: FetchFn = fetch_target,
    decay_threshold: float | None = None,
) -> gv.GateVerdict:
    """Probe each money page once and return G2's :class:`~.verdict.GateVerdict`.

    Args:
        urls: the operator's own money-page URLs (deduped internally).
        fetch_fn: injected fetch (defaults to the SSRF-guarded ``fetch_target``).
        decay_threshold: the calibrated GO/KILL boundary. ``None`` (the first
            run) makes this a **calibration pass** → ``INCONCLUSIVE`` regardless
            of rate; once the operator records a threshold, ``rate >= threshold``
            → GO (machine justified), else KILL (premise too weak to build).
    """
    deduped = list(dict.fromkeys(urls))
    buckets: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for url in deduped:
        bucket, reason = classify_page(fetch_fn(url))
        buckets[bucket] += 1
        if bucket != _HEALTHY:
            reasons[reason] += 1

    total = len(deduped)
    decayed = buckets[_DECAYED]
    measurable = decayed + buckets[_HEALTHY]
    rate = (decayed / measurable) if measurable else None
    readable_fraction = (measurable / total) if total else 0.0
    confirmed = measurable > 0 and readable_fraction >= _MIN_READABLE_FRACTION
    threshold_set = decay_threshold is not None

    if not threshold_set:
        requested = gv.INCONCLUSIVE
    elif rate is not None and rate >= decay_threshold:
        requested = gv.GO
    else:
        requested = gv.KILL

    decayed_detail = (
        "decayed=" + str(decayed)
        + (" (" + ", ".join(f"{r}={n}" for r, n in sorted(reasons.items())) + ")" if reasons else "")
    )
    evidence = (
        f"readable={measurable}/{total}",
        decayed_detail,
        f"unmeasurable={buckets[_UNMEASURABLE]}",
    )
    return gv.build_verdict(
        "g2",
        requested,
        sample_n=total,
        confirmed=confirmed,
        threshold_set=threshold_set,
        rate=rate,
        note="money-page silent-decay probe",
        evidence=evidence,
    )
