"""Anchor distribution metrics — pure deterministic computation over ProfileState.

Three formula-bound metrics surface in the report-anchors distribution view:

- **Shannon entropy** of the anchor-text distribution per target_url. Low
  entropy = the operator is repeating the same anchor text at one destination,
  a Penguin-era over-optimization signal.
- **Exact-match ratio** = fraction of entries with ``anchor_type == "exact"``.
  Operates on the anchor_type field directly; immune to normalization.
- **Top-N concentration** over non-branded anchors only, with ``N=3`` default.
  Branded-anchor dominance is healthy and would otherwise alarm every
  well-optimized home page target. When the non-branded sample is too small
  to be meaningful (< 5 entries), top-N returns ``None`` and the caller skips
  the breach check for that target.

All math is deterministic — no LLM, no randomness, no I/O. The ``now``
parameter is injectable so tests can fix the clock without monkeypatching
``datetime``.

Normalization
-------------
Anchor text is normalized via ``casefold()`` + internal-whitespace collapse +
``strip()`` before all distribution math. This matches Google's
case-insensitive anchor evaluation and treats ``"iPhone Repair"`` /
``"iphone  repair"`` / ``"IPHONE REPAIR"`` as the same bucket.

Explicitly NOT done:

- Punctuation stripping (would merge legitimate brand variants like
  ``"Lyft, Inc."`` vs ``"Lyft Inc."``).
- Diacritic folding / NFKC (merges intentional variants like
  ``"café"`` vs ``"cafe"``).
- Stemming (collapses anchor families like ``"repair"`` and ``"repairing"``).

Bias is toward **preserving** distinctness to avoid falsely inflating
exact-ratio or depressing entropy — false positives on a brand-conscious site
would teach the operator to ignore the channel.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable

from .anchor_profile import ProfileEntry, ProfileState

if TYPE_CHECKING:
    from .config import AnchorAlarmConfig

# Default breach thresholds — operator-overridable via [anchor_alarm] in
# config.toml. Same constant-then-overlay pattern as
# anchor_profile._DEGRADATION_ALARM_PCT.
_ENTROPY_FLOOR: float = 1.5
_EXACT_RATIO_CEILING: float = 0.10
_TOP3_CONCENTRATION_CEILING: float = 0.25

# Per-target sample floor for alarm emission. Lower than anchor_profile's
# domain-level _RELIABLE_SAMPLE_MIN=50 because per-target sample counts at
# solo-operator throughput rarely reach 50 — and asymmetric costs argue for
# a permissive floor (false positive = 5-min anchor review; false negative
# = potential Penguin penalty).
_ALARM_SAMPLE_MIN_PER_TARGET: int = 20

# Top-N concentration cutoff: below this many non-branded entries in the
# window, top-3 returns None (degraded signal) and never triggers a breach.
_TOP_N_MIN_NON_BRANDED: int = 5

# Default top-N for concentration metric.
_DEFAULT_TOP_N: int = 3

# Internal-whitespace collapse pattern.
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class WindowMetrics:
    """Distribution metrics for one target_url within one rolling time window."""

    entropy: float
    exact_ratio: float
    top_n_non_branded: float | None  # None when degraded (< _TOP_N_MIN_NON_BRANDED)
    sample_size: int


@dataclass(frozen=True)
class TargetThresholds:
    """Resolved thresholds for one target. Sourced from the config-precedence
    resolver in config.py; tests can construct these directly."""

    entropy_floor: float = _ENTROPY_FLOOR
    exact_ratio_ceiling: float = _EXACT_RATIO_CEILING
    top3_concentration_ceiling: float = _TOP3_CONCENTRATION_CEILING


def normalize(text: str) -> str:
    """Lowercase + collapse internal whitespace + strip. No punctuation strip.

    Public for cross-module reuse (e.g. the alarm renderer surfacing
    "after normalization, X anchors collapse to Y").
    """
    return _WHITESPACE_RE.sub(" ", text.casefold()).strip()


def _round4(x: float) -> float:
    """Float-drift mitigation near thresholds. Round to 4 decimals so
    comparisons against thresholds like 1.5 are stable across IEEE-754 noise.
    Insufficient against sample-discretization flapping (caller's concern)."""
    return round(x, 4)


def parse_ts(ts: str) -> datetime | None:
    """Parse a ProfileEntry.ts value to an aware datetime, or None on failure.

    Failures (malformed ts, naive datetime) return None rather than raising —
    metrics are advisory; one bad row should not crash the report.
    """
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return None
    return dt


def filter_window(
    entries: Iterable[ProfileEntry],
    days: int,
    now: datetime | None = None,
) -> list[ProfileEntry]:
    """Return entries whose ts falls within the last ``days`` days from ``now``.

    ``now`` defaults to ``datetime.now(timezone.utc)``; injectable for tests.
    Entries with unparseable ts are silently excluded.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff_seconds = days * 86400
    out: list[ProfileEntry] = []
    for e in entries:
        ts = parse_ts(e.ts)
        if ts is None:
            continue
        if (now - ts).total_seconds() <= cutoff_seconds:
            out.append(e)
    return out


def group_by_target_url(state: ProfileState) -> dict[str, list[ProfileEntry]]:
    """Partition entries by ``target_url``. Pre-bump entries (target_url == '')
    cluster into a single ``""`` bucket that the report labels "domain-rollup"."""
    buckets: dict[str, list[ProfileEntry]] = {}
    for e in state.entries:
        buckets.setdefault(e.target_url, []).append(e)
    return buckets


def shannon_entropy(entries: list[ProfileEntry]) -> float:
    """Base-2 Shannon entropy over the normalized anchor_text distribution.

    Returns 0.0 for an empty list or a list with all-identical (after
    normalization) anchor texts. Result is rounded to 4 decimals for
    threshold-comparison stability.
    """
    if not entries:
        return 0.0
    counts = Counter(normalize(e.anchor_text) for e in entries)
    total = sum(counts.values())
    entropy = 0.0
    for count in counts.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return _round4(entropy)


def exact_match_ratio(entries: list[ProfileEntry]) -> float:
    """Fraction of entries with anchor_type == 'exact'. 0.0 on empty list.

    Uses the anchor_type field directly — normalization is irrelevant here.
    """
    if not entries:
        return 0.0
    exact = sum(1 for e in entries if e.anchor_type == "exact")
    return _round4(exact / len(entries))


def top_n_concentration(
    entries: list[ProfileEntry],
    n: int = _DEFAULT_TOP_N,
    *,
    exclude_branded: bool = True,
) -> float | None:
    """Sum of the top-N most frequent normalized anchor texts as a fraction
    of the relevant total.

    When ``exclude_branded=True`` (default), branded anchors are filtered out
    before counting — both numerator and denominator operate on non-branded
    entries only. This is the Penguin-relevant view: branded-anchor dominance
    is healthy and should not alarm.

    Returns ``None`` when the non-branded sample is below
    ``_TOP_N_MIN_NON_BRANDED`` — the caller treats None as "degraded signal,
    do not breach on top-N for this target".

    The exact-match-ratio metric is intentionally not subject to a similar
    low-N gate because it keys on the anchor_type field (not text), so it
    remains meaningful at very small samples — a single exact anchor in a
    pool of 4 still indicates 25% exact ratio.
    """
    relevant = (
        [e for e in entries if e.anchor_type != "branded"]
        if exclude_branded
        else list(entries)
    )
    if len(relevant) < _TOP_N_MIN_NON_BRANDED:
        return None
    counts = Counter(normalize(e.anchor_text) for e in relevant)
    top_n_sum = sum(c for _, c in counts.most_common(n))
    return _round4(top_n_sum / len(relevant))


def compute_window_metrics(entries: list[ProfileEntry]) -> WindowMetrics:
    """Bundle the three metrics for one entry slice (already time-windowed)."""
    return WindowMetrics(
        entropy=shannon_entropy(entries),
        exact_ratio=exact_match_ratio(entries),
        top_n_non_branded=top_n_concentration(entries),
        sample_size=len(entries),
    )


def resolve_thresholds(
    alarm_cfg: "AnchorAlarmConfig",
    target_url: str,
    main_domain: str,
) -> TargetThresholds:
    """Apply per-URL > per-domain > global > hardcoded precedence.

    Returns a fully-populated ``TargetThresholds`` instance. Each threshold
    field is filled from the highest-precedence layer that specified a
    non-None value; partial-field overrides fall through layer-by-layer.

    Per-URL overrides match on ``target_url == override.match``; per-domain
    overrides match on ``main_domain == override.match`` (after rstrip("/")).
    The first matching override at each scope wins — operators ordering
    multiple matching rows should put the more specific row first.
    """
    # Start with hardcoded defaults (module-level constants).
    entropy_floor: float = _ENTROPY_FLOOR
    exact_ratio_ceiling: float = _EXACT_RATIO_CEILING
    top3_concentration_ceiling: float = _TOP3_CONCENTRATION_CEILING

    # Layer 1: globals from [anchor_alarm].
    if alarm_cfg.entropy_floor is not None:
        entropy_floor = alarm_cfg.entropy_floor
    if alarm_cfg.exact_ratio_ceiling is not None:
        exact_ratio_ceiling = alarm_cfg.exact_ratio_ceiling
    if alarm_cfg.top3_concentration_ceiling is not None:
        top3_concentration_ceiling = alarm_cfg.top3_concentration_ceiling

    # Layer 2: per-domain overrides (looser). Strip scheme + trailing slash
    # so operators can write `match = "example.com"` regardless of whether the
    # in-memory `main_domain` is bare ("example.com") or full ("https://example.com").
    def _domain_key(s: str) -> str:
        s = s.rstrip("/")
        if s.startswith("https://"):
            s = s[len("https://"):]
        elif s.startswith("http://"):
            s = s[len("http://"):]
        return s

    bare_domain = _domain_key(main_domain)
    for override in alarm_cfg.overrides:
        if override.scope == "domain" and _domain_key(override.match) == bare_domain:
            if override.entropy_floor is not None:
                entropy_floor = override.entropy_floor
            if override.exact_ratio_ceiling is not None:
                exact_ratio_ceiling = override.exact_ratio_ceiling
            if override.top3_concentration_ceiling is not None:
                top3_concentration_ceiling = override.top3_concentration_ceiling
            break

    # Layer 3: per-URL overrides (tightest — wins over domain).
    for override in alarm_cfg.overrides:
        if override.scope == "url" and override.match == target_url:
            if override.entropy_floor is not None:
                entropy_floor = override.entropy_floor
            if override.exact_ratio_ceiling is not None:
                exact_ratio_ceiling = override.exact_ratio_ceiling
            if override.top3_concentration_ceiling is not None:
                top3_concentration_ceiling = override.top3_concentration_ceiling
            break

    return TargetThresholds(
        entropy_floor=entropy_floor,
        exact_ratio_ceiling=exact_ratio_ceiling,
        top3_concentration_ceiling=top3_concentration_ceiling,
    )


def detect_breaches(
    metrics_90d: WindowMetrics,
    thresholds: TargetThresholds,
    *,
    sample_floor: int = _ALARM_SAMPLE_MIN_PER_TARGET,
) -> list[str]:
    """Return the list of breached threshold names for the 90d window.

    Returns an empty list when sample_size < sample_floor (low-N suppression),
    when no threshold is crossed, or when metrics are missing. Breach names
    map 1-to-1 to threshold field names so consumers can pattern-match.

    Breach detection always runs against the 90d window — 30d metrics are
    informational only and never enter this function.
    """
    if metrics_90d.sample_size < sample_floor:
        return []
    breaches: list[str] = []
    if metrics_90d.entropy < thresholds.entropy_floor:
        breaches.append("entropy_floor")
    if metrics_90d.exact_ratio > thresholds.exact_ratio_ceiling:
        breaches.append("exact_ratio_ceiling")
    # Top-3 only breaches when its signal is reliable (non-branded sample
    # cleared the _TOP_N_MIN_NON_BRANDED floor).
    if (
        metrics_90d.top_n_non_branded is not None
        and metrics_90d.top_n_non_branded > thresholds.top3_concentration_ceiling
    ):
        breaches.append("top3_concentration_ceiling")
    return breaches
