"""Age-based (target, query) pair selection for GEO citation probes
(Plan 2026-05-29-006 Unit 6 / D5).

Selects pairs whose last ``citation.observed`` event is ``> N days`` old,
oldest-first, capped at M pairs per run.

Design notes
------------
- **D5** — cursor derived from the event time-series, NOT a separate state
  file.  The timestamp of the most-recent ``citation.observed`` event for a
  ``(target_url, query)`` pair IS the cursor.  Mid-batch aborts are safe:
  pairs with durable appends advance their cursor; un-probed pairs remain
  oldest-first candidates for the next run.
- **Coverage invariant** — ``M * (N / P) >= C``:
  * M = max pairs per run
  * N = re-probe interval (days)
  * P = runs per day (assumed 1)
  * C = total number of (target, query) pairs in the corpus
  If ``C > M * N`` the corpus CANNOT be fully covered at cadence N: some
  pairs will starve.  :func:`select_pairs` always flags this when it
  occurs; callers can surface the warning to the operator.
- Pairs never probed before are treated as maximally stale (age = +∞),
  represented as ``datetime.min`` for stable sorting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from backlink_publisher.events import EventStore
from backlink_publisher.events.kinds import CITATION_OBSERVED

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (D10)
# ---------------------------------------------------------------------------

#: Default minimum age (days) before a pair is eligible for re-probe.
DEFAULT_STALE_DAYS: int = 7

#: Default maximum pairs selected per run.
DEFAULT_MAX_PAIRS: int = 10

#: Assumed daily run cadence (P) for starvation check.
_RUNS_PER_DAY: int = 1

# Sentinel timestamp for "never probed" — sorts before every real timestamp.
_NEVER_PROBED_TS = datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ProbeCandidate:
    """One ``(target_url, query)`` pair eligible for probing."""

    target_url: str
    query: str
    last_probed_at: str | None  # ISO-8601 UTC string or None if never probed
    staleness_days: float  # days since last probe (inf if never probed)


@dataclass
class SelectionResult:
    """Output of :func:`select_pairs`."""

    candidates: list[ProbeCandidate] = field(default_factory=list)
    #: True when the corpus is too large for full N-day coverage at M/run.
    starvation_risk: bool = False
    #: Total number of distinct (target, query) pairs found in events.db.
    total_pairs: int = 0
    #: Coverage invariant: M * (N/P).  Pairs beyond this count may starve.
    coverage_capacity: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_ts_utc(ts_str: str | None) -> datetime | None:
    """Parse an ISO-8601 UTC string from events.db into an aware datetime.

    Returns ``None`` when the value is blank or unparseable so callers can
    fall back to ``_NEVER_PROBED_TS`` rather than raising.
    """
    if not ts_str:
        return None
    try:
        # events.db stores ISO-8601 with a trailing 'Z' or '+00:00'.
        ts_str = ts_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _build_last_probed_map(
    rows: list,
) -> dict[tuple[str, str], datetime]:
    """Return ``{(target_url, query): last_probe_ts}`` from raw event rows.

    ``rows`` should be the result of a ``citation.observed`` query carrying
    ``target_url``, ``payload_json``, and ``ts_utc`` columns.

    Only rows with a parseable ``ts_utc`` AND a ``query`` key inside the
    JSON payload contribute to the map — malformed rows are skipped with a
    WARNING (never silently dropped without notice).
    """
    last: dict[tuple[str, str], datetime] = {}
    for row in rows:
        target_url = row["target_url"]
        if not target_url:
            continue
        ts = _parse_ts_utc(row["ts_utc"])
        if ts is None:
            _log.warning(
                "geo.selection: unparseable ts_utc %r for target %r; skipping",
                row["ts_utc"],
                target_url,
            )
            continue
        try:
            payload = json.loads(row["payload_json"])
        except (json.JSONDecodeError, TypeError):
            _log.warning(
                "geo.selection: unparseable payload_json for target %r; skipping",
                target_url,
            )
            continue
        query = payload.get("query", "")
        if not isinstance(query, str):
            query = ""
        key = (target_url, query)
        if key not in last or ts > last[key]:
            last[key] = ts
    return last


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_pairs(
    all_pairs: list[tuple[str, str]],
    *,
    store: EventStore | None = None,
    stale_days: int = DEFAULT_STALE_DAYS,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    now: datetime | None = None,
) -> SelectionResult:
    """Select ``(target, query)`` pairs to probe next.

    Parameters
    ----------
    all_pairs:
        The complete operator-defined ``(target_url, query)`` corpus.  Each
        element is a ``(target_url, query)`` 2-tuple.
    store:
        Injectable :class:`~backlink_publisher.events.EventStore` (defaults to
        a fresh instance using the default ``events.db`` path).
    stale_days:
        Pairs whose last probe is older than this many days are eligible.
        Pairs never probed are always eligible (treated as infinitely stale).
    max_pairs:
        Cap on the number of candidates returned.
    now:
        Injectable "current time" for deterministic tests.  Defaults to
        ``datetime.now(timezone.utc)``.

    Returns
    -------
    SelectionResult
        Sorted oldest-first, capped at ``max_pairs``, with a
        ``starvation_risk`` flag when ``len(all_pairs) > max_pairs * stale_days``.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    if store is None:
        store = EventStore()

    # --- Load last-probed timestamps from events.db -------------------------
    rows = store.query(
        "SELECT target_url, payload_json, ts_utc FROM events WHERE kind = ?",
        (CITATION_OBSERVED,),
    )
    last_probed = _build_last_probed_map(rows)

    # --- Score every pair in the corpus -------------------------------------
    threshold = timedelta(days=stale_days)
    eligible: list[tuple[datetime, ProbeCandidate]] = []

    for target_url, query in all_pairs:
        key = (target_url, query)
        last_ts = last_probed.get(key)

        if last_ts is None:
            # Never probed — maximally stale, highest priority.
            age = timedelta.max
            sort_key = _NEVER_PROBED_TS
            last_probed_at = None
            staleness_days = float("inf")
        else:
            age = now - last_ts
            sort_key = last_ts
            last_probed_at = last_ts.isoformat()
            staleness_days = age.total_seconds() / 86400.0

        if last_ts is None or age > threshold:
            eligible.append(
                (
                    sort_key,
                    ProbeCandidate(
                        target_url=target_url,
                        query=query,
                        last_probed_at=last_probed_at,
                        staleness_days=staleness_days,
                    ),
                )
            )

    # Sort oldest-first (never-probed sorts before every real timestamp).
    eligible.sort(key=lambda t: t[0])

    candidates = [cand for _, cand in eligible[:max_pairs]]

    # --- Coverage invariant -------------------------------------------------
    total = len(all_pairs)
    capacity = float(max_pairs) * (stale_days / _RUNS_PER_DAY)
    starvation_risk = total > capacity

    if starvation_risk:
        _log.warning(
            "geo.selection: starvation risk — corpus has %d pairs but capacity "
            "is %.0f (M=%d N=%d P=%d). Some pairs may not be re-probed within %d days.",
            total,
            capacity,
            max_pairs,
            stale_days,
            _RUNS_PER_DAY,
            stale_days,
        )

    return SelectionResult(
        candidates=candidates,
        starvation_risk=starvation_risk,
        total_pairs=total,
        coverage_capacity=capacity,
    )
