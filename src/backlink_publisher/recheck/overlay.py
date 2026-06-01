"""Read-only recheck-verdict deficit overlay (the #310 → #313 bridge).

Sits between ``equity-ledger`` and ``plan-gap`` in the pipe. Reads the *latest*
``link.rechecked`` verdict per link (``article_id``) from events.db — read-only —
and discounts deterministically-dead and ``dofollow_lost`` links from each
target's ``live_dofollow`` count, pruning the dead platform from
``live_dofollow_platforms``. ``plan-gap``'s existing deficit math then re-counts
and proposes replacements that avoid the dead platform — with no change to
``gap/engine.py`` or ``cli/plan_gap.py``.

Pure + read-only: never writes events.db / dedup.db / the ledger / history_store;
no schema change, no projector change, no new event kind. The "proper" fix
(ledger liveness writeback, R6) is deferred; this overlay is throwaway by design
and retires when R6-proper lands.

Discount taxonomy — its OWN set, deliberately not ``verdicts.DETERMINISTIC_DEAD``
alone (``dofollow_lost`` also discounts here, while it is advisory there)::

    host_gone | link_stripped  -> dead:          live_dofollow -= 1; drop platform
    dofollow_lost              -> dofollow_lost:  live_dofollow -= 1; drop platform
    alive                      -> live:           no discount (restores if latest)
    probe_error                -> ignored:        no discount (re-probed later)
    <unrecognized>             -> QUARANTINE:     no discount + loud tally
                                                  (never default-to-alive — see
                                                  projector-silent-drop lesson)

Latest-per-``article_id`` is resolved by ``ts_utc`` (primary) with ``events.id``
as a same-``ts_utc`` tiebreaker, mirroring ``derive_decay_counts`` /
``_recheck_cursors`` so the overlay agrees with the dashboard the operator
already trusts.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from backlink_publisher._util.errors import DependencyError
from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.events.kinds import LINK_RECHECKED
from backlink_publisher.recheck import verdicts
from backlink_publisher.recheck.selection import _parse_ts

if TYPE_CHECKING:
    from backlink_publisher.events.store import EventStore

log = logging.getLogger(__name__)

#: Verdicts that reduce a target's ``live_dofollow`` in the overlay. Defined here
#: (not reused wholesale from ``verdicts``) because ``dofollow_lost`` discounts in
#: this read path while it is explicitly NOT in ``DETERMINISTIC_DEAD``.
_DISCOUNT_VERDICTS = verdicts.DETERMINISTIC_DEAD | {verdicts.DOFOLLOW_LOST}


@dataclass
class TargetDiscount:
    """Per-target discount accumulated from latest verdicts."""

    dead_count: int = 0
    dofollow_lost_count: int = 0
    dead_platforms: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        """Links to subtract from ``live_dofollow`` (dead + dofollow_lost)."""
        return self.dead_count + self.dofollow_lost_count


@dataclass
class DiscountTally:
    """Loud, counted signal for every record the reader saw — no silent drops."""

    discounted: int = 0  # links contributing a discount (dead + dofollow_lost)
    dead_seen: int = 0  # deterministic-dead verdicts (host_gone / link_stripped)
    null_or_blank_target: int = 0  # discount verdict with no usable target_url
    unknown_verdict: int = 0  # quarantined unrecognized verdict strings


@dataclass
class DiscountResult:
    """Per-canonical-target discounts plus the reader tally.

    ``alive_platforms`` records, per canonical target, the platforms whose latest
    verdict is ``alive`` — so the transform never prunes a platform that still
    carries a live dofollow link for that target (multi-placement guard).
    """

    by_target: dict[str, TargetDiscount] = field(default_factory=dict)
    alive_platforms: dict[str, set[str]] = field(default_factory=dict)
    tally: DiscountTally = field(default_factory=DiscountTally)


@dataclass
class TransformTally:
    """Loud signal for the row transform (Unit 2)."""

    targets_reduced: int = 0  # ledger rows whose live_dofollow was reduced
    unmatched_discount: int = 0  # discounts whose target matched no ledger row


def _canon_target(value: object) -> str | None:
    """Canonicalize a target_url for matching; ``None`` for null/blank/unparseable.

    ``link.rechecked.target_url`` is stored raw (not pre-canonicalized), while the
    ledger's ``LedgerRow.target_url`` is canonical — so both sides must pass
    through ``canonicalize_url`` to match. Defensive against a malformed URL whose
    invalid port makes ``urlsplit``/``port`` raise (the url-parse-never-raises
    lesson): such a target is reported as unusable, never a crash.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return canonicalize_url(value)
    except ValueError:
        return None


def _is_newer(
    ts: datetime | None,
    rid: int,
    prev_ts: datetime | None,
    prev_rid: int,
) -> bool:
    """True if ``(ts, rid)`` is a later verdict than ``(prev_ts, prev_rid)``.

    ``ts_utc`` is primary (a real timestamp beats ``None``); ``events.id`` breaks a
    same-``ts_utc`` tie. Deterministic given a fixed event set (R8).
    """
    if ts is None and prev_ts is None:
        return rid > prev_rid
    if prev_ts is None:
        return True
    if ts is None:
        return False
    if ts != prev_ts:
        return ts > prev_ts
    return rid > prev_rid


def build_discount_map(store: "EventStore") -> DiscountResult:
    """Read the latest ``link.rechecked`` verdict per ``article_id`` and build a
    per-canonical-target discount map. Read-only; never creates events.db.

    Raises ``DependencyError`` (exit 3) when an existing events.db is unreadable.
    """
    result = DiscountResult()
    # Absent events.db → nothing to discount. Check before any connect() so the
    # read-only verb never materializes an empty database as a side effect.
    if not store.path.exists():
        return result

    try:
        rows = store.query(
            "SELECT article_id, target_url, payload_json, ts_utc, id "
            "FROM events WHERE kind = ? AND article_id IS NOT NULL",
            (LINK_RECHECKED,),
        )
    except sqlite3.Error as exc:
        raise DependencyError(
            f"recheck-overlay: events.db unreadable: {exc}"
        ) from exc

    # Latest verdict per article_id (ts_utc primary, id tiebreak).
    latest: dict[int, tuple[datetime | None, int, dict, object]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        ts = _parse_ts(row["ts_utc"])
        rid = row["id"]
        aid = row["article_id"]
        prev = latest.get(aid)
        if prev is None or _is_newer(ts, rid, prev[0], prev[1]):
            latest[aid] = (ts, rid, payload, row["target_url"])

    for _ts, _rid, payload, target_col in latest.values():
        verdict = payload.get("verdict")
        if verdict == verdicts.PROBE_ERROR:
            continue  # indeterminate — re-probed later, no discount
        canon = _canon_target(target_col)
        platform = payload.get("platform")
        if verdict == verdicts.ALIVE:
            # Record the live platform so a dead link elsewhere on the same target
            # cannot prune a platform that still carries a live dofollow link.
            if canon is not None and isinstance(platform, str) and platform:
                result.alive_platforms.setdefault(canon, set()).add(platform)
            continue
        if verdict not in _DISCOUNT_VERDICTS:
            # Unknown / future verdict: quarantine loudly, never treat as alive.
            result.tally.unknown_verdict += 1
            continue
        if canon is None:
            result.tally.null_or_blank_target += 1
            continue
        td = result.by_target.setdefault(canon, TargetDiscount())
        if verdict in verdicts.DETERMINISTIC_DEAD:
            td.dead_count += 1
            result.tally.dead_seen += 1
        else:  # dofollow_lost
            td.dofollow_lost_count += 1
        if isinstance(platform, str) and platform:
            td.dead_platforms.add(platform)
        result.tally.discounted += 1

    return result


def apply_discounts(
    ledger_rows: list[dict], discounts: DiscountResult
) -> tuple[list[dict], TransformTally]:
    """Apply the discount map to ledger JSONL rows.

    Decrement ``live_dofollow`` (floored at 0), prune ``dead_platforms`` from
    ``live_dofollow_platforms``, and pass every other key — and every unaffected
    row — through verbatim so ``plan-gap`` sees an unchanged contract. A discount
    whose canonical target matches no ledger row is surfaced (``unmatched_discount``),
    never silently dropped.
    """
    tally = TransformTally()
    out: list[dict] = []
    matched: set[str] = set()

    for row in ledger_rows:
        canon = _canon_target(row.get("target_url"))
        td = discounts.by_target.get(canon) if canon is not None else None
        if td is None or td.total == 0:
            out.append(row)  # passthrough, byte-for-byte identical
            continue
        matched.add(canon)
        new_row = dict(row)  # preserve all keys; mutate only the two live fields
        live = new_row.get("live_dofollow")
        live = live if isinstance(live, int) and not isinstance(live, bool) else 0
        new_row["live_dofollow"] = max(0, live - td.total)
        # Prune only platforms with NO surviving alive link on this target — a dead
        # link must not evict a platform another live dofollow link still occupies.
        prune = td.dead_platforms - discounts.alive_platforms.get(canon, set())
        platforms = new_row.get("live_dofollow_platforms")
        if isinstance(platforms, list) and prune:
            new_row["live_dofollow_platforms"] = [
                p for p in platforms if p not in prune
            ]
        out.append(new_row)
        tally.targets_reduced += 1

    tally.unmatched_discount = sum(
        1 for canon in discounts.by_target if canon not in matched
    )
    return out, tally
