"""Pure deficit-driven re-plan engine (``plan-gap``).

Transforms ``equity-ledger`` rows into ``plan-backlinks`` seed rows: for each
target it computes the live-dofollow deficit (``D - live_dofollow``) and fans it
out across the distinct active dofollow platforms the target does NOT already
hold a live-dofollow link on.

Pure engine (mirrors the contract in ``validate/engine.py`` and
``ledger/aggregate.py``): this module MUST NOT touch ``sys.stdout`` /
``sys.stderr``, call ``set_log_level``, raise ``SystemExit``, read stdin, write
stdout, emit the ``config_echo`` banner, or do network I/O. Registry lookups
(``active_platforms`` / ``dofollow_status``) are in-memory pure reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backlink_publisher.bulk_input import derive_main_domain
from backlink_publisher.publishing._registry_manifest import active_platforms
from backlink_publisher.publishing.registry import dofollow_status

#: Liveness values the engine recognizes (mirror ``ledger.model.LivenessStatus``).
#: Anything else is the fail-safe ``unknown_liveness`` outcome (R9) — never a raise.
_KNOWN_LIVENESS = frozenset({"live", "stale", "failed", "unverified"})


@dataclass
class GapOptions:
    """Operator-supplied knobs for one ``plan-gap`` run."""

    desired: int
    language: str
    url_mode: str = "A"
    publish_mode: str = "draft"
    desired_map: dict[str, int] = field(default_factory=dict)
    emit_stale: bool = False
    include_failed: bool = False
    #: Freshness floor in days; ``None`` disables it. A target whose
    #: ``liveness_verified_at`` is older than this (or absent) is suppressed.
    stale_after_days: int | None = None


@dataclass
class SuppressionCounts:
    """Per-reason tally so every dropped target is a loud, counted signal."""

    satisfied: int = 0
    suppressed_stale: int = 0
    suppressed_unverified: int = 0
    suppressed_stale_floor: int = 0
    failed: int = 0
    unknown_liveness: int = 0
    #: Rows that are valid JSON objects but lack a usable ``target_url`` — the
    #: engine skips them fail-safe (never raises) rather than crashing the pipe.
    malformed: int = 0
    channel_exhausted: int = 0
    #: Named targets that have a real deficit but no remaining candidate platform.
    channel_exhausted_targets: list[str] = field(default_factory=list)


def active_dofollow_platforms() -> list[str]:
    """Active platforms whose registry dofollow verdict is exactly ``True``.

    ``"uncertain"`` / ``None`` / ``False`` are excluded. Order follows
    ``active_platforms()`` (sorted) for deterministic fan-out.
    """
    return [p for p in active_platforms() if dofollow_status(p) is True]


def _coerce_live_dofollow(value: object) -> int:
    """Treat a missing/``None``/non-int ``live_dofollow`` as 0 (full deficit)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _verified_older_than(verified_at: object, days: int, now: datetime) -> bool:
    """True when ``verified_at`` is absent, unparseable, or older than ``days``."""
    if not isinstance(verified_at, str) or not verified_at:
        return True
    try:
        dt = datetime.fromisoformat(verified_at)
    except ValueError:
        return True
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return (now - dt).days > days


def plan_gap(
    rows,
    opts: GapOptions,
    *,
    active_dofollow: list[str] | None = None,
    now: datetime | None = None,
):
    """Transform ledger rows → (seed_rows, suppression_counts, liveness_meta).

    ``rows`` is an iterable of equity-ledger dicts (already weakest-first).
    ``active_dofollow`` is injectable for tests; defaults to the live registry.
    ``now`` is injectable for the freshness floor; defaults to ``datetime.now()``.
    """
    candidates_universe = (
        list(active_dofollow) if active_dofollow is not None else active_dofollow_platforms()
    )
    now = now or datetime.now()
    counts = SuppressionCounts()
    seeds: list[dict] = []
    as_of: str | None = None

    for row in rows:
        # Fail-safe: a valid-JSON row missing target_url is skipped + counted,
        # never a KeyError that would crash the pipe (R9 spirit / pure-engine).
        target = row.get("target_url")
        if not isinstance(target, str) or not target:
            counts.malformed += 1
            continue

        verified_at = row.get("liveness_verified_at")
        # as_of: latest verification stamp seen (advisory). Relies on ISO-8601
        # lexical order == chronological order (the build_ledger contract).
        if isinstance(verified_at, str) and (as_of is None or verified_at > as_of):
            as_of = verified_at

        liveness = row.get("liveness", "unverified")
        live_dofollow = _coerce_live_dofollow(row.get("live_dofollow"))

        # --- Classify (R6/R9): EMIT / SUPPRESSED-with-reason / unknown_liveness.
        if liveness not in _KNOWN_LIVENESS:
            counts.unknown_liveness += 1
            continue
        if liveness == "failed":
            if not opts.include_failed:
                counts.failed += 1
                continue
        elif liveness in ("stale", "unverified") and live_dofollow == 0:
            # Deficit unverifiable: no live-dofollow evidence to trust.
            if not opts.emit_stale:
                if liveness == "stale":
                    counts.suppressed_stale += 1
                else:
                    counts.suppressed_unverified += 1
                continue
        # Freshness floor: even an otherwise-eligible target is held if its
        # liveness evidence is older than the operator's threshold.
        if (
            opts.stale_after_days is not None
            and not opts.emit_stale
            and _verified_older_than(verified_at, opts.stale_after_days, now)
        ):
            counts.suppressed_stale_floor += 1
            continue

        # --- Deficit (R3).
        desired = opts.desired_map.get(target, opts.desired)
        deficit = max(0, desired - live_dofollow)
        if deficit == 0:
            counts.satisfied += 1
            continue

        # --- Channel-aware fan-out (R4): subtract the LIVE-DOFOLLOW platform set.
        already_live_df = set(row.get("live_dofollow_platforms") or [])
        candidates = [p for p in candidates_universe if p not in already_live_df]
        emitted = candidates[:deficit]  # one seed per distinct candidate, capped
        if emitted:
            main_domain = derive_main_domain(target)
            for platform in emitted:
                seeds.append({
                    "target_url": target,
                    "platform": platform,
                    "main_domain": main_domain,
                    "language": opts.language,
                    "url_mode": opts.url_mode,
                    "publish_mode": opts.publish_mode,
                })
        # Couldn't fully close the deficit under the current roster — name it so
        # the operator knows the target maxes out below D (incl. the 0-candidate
        # case). Distinct from a silent partial.
        if deficit > len(candidates):
            counts.channel_exhausted += 1
            counts.channel_exhausted_targets.append(target)

    return seeds, counts, {"as_of": as_of}
