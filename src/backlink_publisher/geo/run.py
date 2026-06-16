"""Pure probe kernel for GEO citation probes (Plan 2026-05-29-006 Unit 7).

Provides ``probe_one`` and ``probe_many`` with an injectable ``probe_fn``
seam so the CLI shell can swap in a real dispatch or a test double without
patching deep internals.

Design notes
------------
- **D5** — durability via events.db append-before-advance: a mid-batch
  abort leaves already-appended pairs cursored (oldest-first picks them up
  last on the next run), un-probed pairs remain un-cursored.
- **D8** — ``ProbeResult.raw_response`` is never persisted.  Only the
  bounded ``VerdictResult`` fields (verdict, engine, query, URLs) are
  written via ``carry_verdict``.
- **D11** — at-least-once delivery: ``probe_many`` never double-appends
  on retry; the read-time dedup in ``geo.share`` handles any surviving
  duplicates (e.g. process crash after append but before cursor advance).
- Never-raises contract: a per-pair exception is caught, logged, and
  counted as ``probe_error``; the batch continues unless cost cap or
  wall-clock budget fires (both exit cleanly with exit 0).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from backlink_publisher._util.logger import get_logger
from backlink_publisher.config.types import GeoProbeConfig
from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED
from backlink_publisher.geo.engines import ProbeResult
from backlink_publisher.geo.selection import ProbeCandidate
from backlink_publisher.geo.verdict import VerdictResult, carry_verdict, classify_verdict

_log = get_logger("probe-citations")

ProbeCallable = Callable[[str, GeoProbeConfig], ProbeResult]


@dataclass
class ProbeSummary:
    """Aggregate result of a ``probe_many`` run."""

    probed: int = 0
    site_cited: int = 0
    article_cited: int = 0
    absent: int = 0
    refused: int = 0
    probe_error: int = 0
    #: Pairs whose cost cap fired mid-batch (not probed this run).
    deferred: int = 0
    #: True when the run was cut short by the wall-clock budget.
    budget_exhausted: bool = False
    #: True when the run was cut short by the per-run cost cap.
    cost_cap_hit: bool = False

    def to_jsonl_dict(self) -> dict:
        return {
            "type": "summary",
            "probed": self.probed,
            "site_cited": self.site_cited,
            "article_cited": self.article_cited,
            "absent": self.absent,
            "refused": self.refused,
            "probe_error": self.probe_error,
            "deferred": self.deferred,
            "budget_exhausted": self.budget_exhausted,
            "cost_cap_hit": self.cost_cap_hit,
        }


def probe_one(
    target_url: str,
    query: str,
    *,
    probe_fn: ProbeCallable,
    cfg: GeoProbeConfig,
    article_urls: frozenset[str],
    brand_aliases: list[str],
    engine: str = "perplexity",
) -> VerdictResult:
    """Probe one (target_url, query) pair, classify, and return the verdict.

    Parameters
    ----------
    target_url:
        The target site URL.
    query:
        The probe query string.
    probe_fn:
        Injectable probe callable — takes ``(query, cfg)`` → :class:`ProbeResult`.
    cfg:
        The GEO probe config (API key, endpoint, model, timeout).
    article_urls:
        Canonical published article URLs for article-tier credit matching.
    brand_aliases:
        Brand name aliases for ``brand_mentioned`` detection.
    engine:
        Engine name to record on the verdict (informational; dispatch is
        ``probe_fn``'s responsibility).

    Returns
    -------
    VerdictResult
        The classified verdict.  Never raises (callers use ``probe_many``
        for the never-raises batch contract; this function raises on any
        underlying error so ``probe_many`` can catch and count it).
    """
    raw_result = probe_fn(query, cfg)
    return classify_verdict(
        raw_result,
        target_url=target_url,
        published_article_urls=article_urls,
        brand_aliases=brand_aliases,
        query=query,
        engine=engine,
    )


def probe_many(
    candidates: list[ProbeCandidate],
    *,
    probe_fn: ProbeCallable,
    cfg: GeoProbeConfig,
    store: EventStore,
    article_urls: frozenset[str],
    brand_aliases_map: dict[str, list[str]],
    cost_cap: int,
    wall_clock_budget_s: float,
    engine: str = "perplexity",
    run_id: str | None = None,
) -> tuple[list[dict], ProbeSummary]:
    """Probe a batch of (target, query) candidates.

    Durable append (D5): each pair's ``citation.observed`` event is
    written to ``store`` immediately after a successful probe+classify,
    before moving to the next pair.  A mid-batch abort (cost cap /
    wall-clock) leaves already-written pairs cursored; un-probed pairs
    remain available for the next run.

    Never-raises (D11 batch contract): a per-pair exception is caught,
    logged as ``probe_error``, and the batch continues.

    Parameters
    ----------
    candidates:
        Ordered list from :func:`~backlink_publisher.geo.selection.select_pairs`.
    probe_fn:
        Injectable probe callable — ``(query, cfg) -> ProbeResult``.
    cfg:
        GEO probe config.
    store:
        Event store for durable appends.
    article_urls:
        Published article URL set for article-tier credit.
    brand_aliases_map:
        Per-target brand aliases keyed by target_url.  Missing keys
        yield an empty list (brand_mentioned stays False).
    cost_cap:
        Maximum number of probes this run.  When reached, remaining
        candidates are deferred (summary.cost_cap_hit = True).
    wall_clock_budget_s:
        Maximum wall-clock seconds for the whole batch.  When exceeded
        the current probe finishes and remaining candidates are deferred.
    engine:
        Engine name for ``VerdictResult.engine`` and event payload.
    run_id:
        Optional run-id attached to each appended event for dedup (D11).

    Returns
    -------
    (rows, summary)
        ``rows`` — list of per-probe JSONL dicts (one per probed pair).
        ``summary`` — aggregate ProbeSummary.
    """
    summary = ProbeSummary()
    rows: list[dict] = []
    deadline = time.monotonic() + wall_clock_budget_s

    for index, candidate in enumerate(candidates):
        # -- Cost cap check (before probe, so cap 0 = zero probes) -----------
        if summary.probed >= cost_cap:
            remaining = len(candidates) - index
            summary.deferred += remaining
            summary.cost_cap_hit = True
            _log.warn(
                "probe_many: cost cap reached",
                probed=summary.probed,
                cap=cost_cap,
                deferred=remaining,
            )
            break

        # -- Wall-clock budget ------------------------------------------------
        if time.monotonic() > deadline:
            remaining = len(candidates) - index
            summary.deferred += remaining
            summary.budget_exhausted = True
            _log.warn(
                "probe_many: wall-clock budget exhausted",
                budget_s=wall_clock_budget_s,
                probed=summary.probed,
                deferred=remaining,
            )
            break

        aliases = brand_aliases_map.get(candidate.target_url, [])

        try:
            verdict = probe_one(
                candidate.target_url,
                candidate.query,
                probe_fn=probe_fn,
                cfg=cfg,
                article_urls=article_urls,
                brand_aliases=aliases,
                engine=engine,
            )
        except Exception as exc:  # noqa: BLE001 — never-raises contract
            _log.warn(
                "probe_many: probe error for target",
                target_url=candidate.target_url,
                query=candidate.query,
                error=str(exc),
            )
            summary.probed += 1
            summary.probe_error += 1
            rows.append(
                {
                    "type": "probe",
                    "target_url": candidate.target_url,
                    "query": candidate.query,
                    "verdict": "probe_error",
                    "error": str(exc),
                }
            )
            continue

        # -- Durable append (D5/D8) ------------------------------------------
        payload = carry_verdict(verdict)
        if run_id:
            payload["run_id"] = run_id
        store.append(
            CITATION_OBSERVED,
            payload,
            target_url=candidate.target_url,
            run_id=run_id or "",
        )

        # -- Tally ------------------------------------------------------------
        summary.probed += 1
        tier = verdict.tier
        if tier == "site_cited":
            summary.site_cited += 1
        elif tier == "article_cited":
            summary.article_cited += 1
        elif tier == "absent":
            summary.absent += 1
        elif tier == "refused":
            summary.refused += 1

        row = {
            "type": "probe",
            "target_url": candidate.target_url,
            "query": candidate.query,
            **carry_verdict(verdict),
        }
        rows.append(row)

    return rows, summary
