"""Rolling-window citation-share metric with honest states and sample size
(Plan 2026-05-29-006 Unit 6 / D3/D10/D11).

Design notes
------------
- **D3** — Share = ``cited / (cited + absent)``; ``refused`` is excluded from
  the denominator (counting refusals zeros adult targets structurally).
  ``refused`` and ``possibly_cited_unresolved`` are carried as independent
  rates alongside the share.
- **D10** — Share is **always** paired with sample size ``n``.  States below
  the ``min_sample`` floor (``warming_up``) are never reported as 0% — the
  operator sees "insufficient data" instead of a misleading zero.  Only
  ``measured`` (≥ floor) may carry a ``low_confidence`` badge (when
  ``n < LOW_CONFIDENCE_THRESHOLD``).
- **D11** — Read-time dedup by ``(target_url, query, run_id)`` so at-least-once
  delivery does not inflate counts.  ``run_id`` is read from the payload
  ``run_id`` key; rows with a missing ``run_id`` are individually counted (each
  is its own dedup key).
- **D6** — An explicitly excluded target (e.g. high refusal rate) is surfaced
  as ``excluded``, never 0%.

Window convention: the rolling window contains the **W most-recent distinct
probe events** for a target, where "distinct" is after dedup.

Cited tiers that count toward ``cited``:
  ``site_cited`` and ``article_cited``.
Absent tier:
  ``absent``.
Neither side of the denominator:
  ``refused``, any unrecognised verdict value.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (D10 — set at planning time, not deferred)
# ---------------------------------------------------------------------------

#: Rolling window: number of most-recent probes per target considered.
DEFAULT_WINDOW: int = 10

#: Minimum sample size before "measured" state is valid.
#: Below this threshold the state is "warming_up" → never report 0%.
DEFAULT_MIN_SAMPLE: int = 5

#: Above this threshold the state is "measured" with full confidence.
#: Between [min_sample, LOW_CONFIDENCE_THRESHOLD) → "measured" + low_confidence=True.
DEFAULT_LOW_CONFIDENCE_THRESHOLD: int = 10

# Verdict tiers that count as "cited" in the share numerator.
_CITED_TIERS: frozenset[str] = frozenset({"site_cited", "article_cited"})

# Verdict tier that counts as "absent" in the share denominator.
_ABSENT_TIER: str = "absent"

# Verdict tier excluded from denominator (tracked separately as refused_rate).
_REFUSED_TIER: str = "refused"

# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TargetShare:
    """Rolling-window citation share + honest state for one ``target_url``.

    ``state`` is one of:
    * ``"never_probed"``  — zero citation.observed events for this target.
    * ``"warming_up"``    — fewer than ``min_sample`` probes; share is None.
    * ``"measured"``      — at least ``min_sample`` probes; share is a float.
    * ``"excluded"``      — explicitly excluded (e.g. high refusal rate).

    ``share`` is ``None`` for states other than ``"measured"``.  It is
    ``round(share, 6)`` when set.

    ``low_confidence`` is ``True`` when ``state == "measured"`` but ``n``
    is below the full-confidence threshold.
    """

    target_url: str
    state: str  # "never_probed" | "warming_up" | "measured" | "excluded"
    share: float | None  # None unless state == "measured"
    n: int  # sample size used (deduped, window-capped)
    refused_rate: float  # refused / total_events_in_window (after dedup)
    unresolved_rate: float  # possibly_cited_unresolved count / total_events
    low_confidence: bool  # True when state=measured but n < low_conf_threshold
    window_days: int  # W window size used for this computation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_ts_utc(ts_str: str | None) -> datetime | None:
    """Parse events.db ts_utc → aware datetime; None on failure."""
    if not ts_str:
        return None
    try:
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _count_unresolved(payload: dict) -> int:
    """Return 1 if the payload records any possibly_cited_unresolved URLs."""
    v = payload.get("possibly_cited_unresolved")
    if isinstance(v, list) and v:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_share(
    target_url: str,
    *,
    store: EventStore | None = None,
    excluded_targets: frozenset[str] | None = None,
    window: int = DEFAULT_WINDOW,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    low_confidence_threshold: int = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> TargetShare:
    """Compute the rolling-window citation share for ``target_url``.

    Parameters
    ----------
    target_url:
        The target site URL to compute share for.
    store:
        Injectable :class:`~backlink_publisher.events.EventStore`.  Defaults
        to a fresh instance at the default ``events.db`` path.
    excluded_targets:
        Set of target URLs that have been explicitly excluded (e.g. high
        refusal rate per D6).  Returns ``excluded`` state for these.
    window:
        Rolling window size W — number of most-recent deduped probe events
        to consider.
    min_sample:
        Floor below which the state is ``warming_up`` (never 0%, D10).
    low_confidence_threshold:
        ``n`` below this but >= ``min_sample`` → ``low_confidence=True``.

    Returns
    -------
    TargetShare
        The honest share with state, n, and auxiliary rates.
    """
    if store is None:
        store = EventStore()

    excluded_set = excluded_targets or frozenset()

    # --- Excluded check (D6) -----------------------------------------------
    if target_url in excluded_set:
        return TargetShare(
            target_url=target_url,
            state="excluded",
            share=None,
            n=0,
            refused_rate=0.0,
            unresolved_rate=0.0,
            low_confidence=False,
            window_days=window,
        )

    # --- Fetch all citation.observed events for this target -----------------
    rows = store.query(
        "SELECT target_url, payload_json, ts_utc, run_id "
        "FROM events WHERE kind = ? AND target_url = ? "
        "ORDER BY ts_utc DESC",
        (CITATION_OBSERVED, target_url),
    )

    # --- Never probed -------------------------------------------------------
    if not rows:
        return TargetShare(
            target_url=target_url,
            state="never_probed",
            share=None,
            n=0,
            refused_rate=0.0,
            unresolved_rate=0.0,
            low_confidence=False,
            window_days=window,
        )

    # --- Dedup by (target_url, query, run_id) — D11 ------------------------
    # We deduplicate within the window: if the same (query, run_id) appears
    # more than once, only the most-recent row is kept.  Rows with no run_id
    # in the payload fall back to the column-level run_id; if that is also
    # absent, a per-row unique sentinel ensures they are not collapsed.
    seen_dedup_keys: set[str] = set()
    deduped_rows: list[dict] = []

    # Rows are already ordered newest-first; we scan newest-to-oldest so the
    # FIRST occurrence of each dedup key is the canonical one.
    _row_counter = 0
    for row in rows:
        _row_counter += 1
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            _log.warning(
                "geo.share: unparseable payload_json for target %r; skipping",
                target_url,
            )
            continue

        query = payload.get("query") or ""
        # Prefer payload-level run_id; fall back to column-level run_id.
        run_id = payload.get("run_id") or row["run_id"] or f"__row_{_row_counter}__"
        dedup_key = f"{query}\x1f{run_id}"

        if dedup_key in seen_dedup_keys:
            continue
        seen_dedup_keys.add(dedup_key)

        deduped_rows.append({"payload": payload, "ts_utc": row["ts_utc"]})

    # --- Apply rolling window (W most-recent deduped events) ----------------
    windowed = deduped_rows[:window]  # already newest-first; take top-W

    # --- Warming up state ---------------------------------------------------
    n_total = len(windowed)

    if n_total == 0:
        # All rows were filtered (malformed payloads) — treat as never-probed.
        return TargetShare(
            target_url=target_url,
            state="never_probed",
            share=None,
            n=0,
            refused_rate=0.0,
            unresolved_rate=0.0,
            low_confidence=False,
            window_days=window,
        )

    # --- Count tiers (D3) ---------------------------------------------------
    cited = 0
    absent = 0
    refused = 0
    unresolved_count = 0

    for item in windowed:
        payload = item["payload"]
        verdict = payload.get("verdict", "")
        if verdict in _CITED_TIERS:
            cited += 1
        elif verdict == _ABSENT_TIER:
            absent += 1
        elif verdict == _REFUSED_TIER:
            refused += 1
        # unknown verdict → neither side of the denominator (no-op)
        unresolved_count += _count_unresolved(payload)

    denominator = cited + absent  # D3: refused excluded from denominator

    # Auxiliary rates are computed over n_total (all windowed events).
    refused_rate = round(refused / n_total, 6) if n_total > 0 else 0.0
    unresolved_rate = round(unresolved_count / n_total, 6) if n_total > 0 else 0.0

    # --- Warming up (n < floor → never 0%, D10) ----------------------------
    if denominator < min_sample:
        return TargetShare(
            target_url=target_url,
            state="warming_up",
            share=None,
            n=denominator,
            refused_rate=refused_rate,
            unresolved_rate=unresolved_rate,
            low_confidence=False,
            window_days=window,
        )

    # --- Measured -----------------------------------------------------------
    share_value = round(cited / denominator, 6)
    low_conf = denominator < low_confidence_threshold

    return TargetShare(
        target_url=target_url,
        state="measured",
        share=share_value,
        n=denominator,
        refused_rate=refused_rate,
        unresolved_rate=unresolved_rate,
        low_confidence=low_conf,
        window_days=window,
    )


def compute_shares(
    target_urls: list[str],
    *,
    store: EventStore | None = None,
    excluded_targets: frozenset[str] | None = None,
    window: int = DEFAULT_WINDOW,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    low_confidence_threshold: int = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> list[TargetShare]:
    """Batch variant of :func:`compute_share`.

    Returns one :class:`TargetShare` per entry in ``target_urls`` in the same
    order.  A single ``EventStore`` is shared across all targets.
    """
    if store is None:
        store = EventStore()

    return [
        compute_share(
            t,
            store=store,
            excluded_targets=excluded_targets,
            window=window,
            min_sample=min_sample,
            low_confidence_threshold=low_confidence_threshold,
        )
        for t in target_urls
    ]
