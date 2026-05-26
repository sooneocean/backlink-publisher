"""Unit 2 — three-store read + per-target join.

Collapses three physically separate stores into per-target buckets keyed by
``canonicalize_url(target_url)``:

- **events.db** ``articles`` — one row per ``live_url`` (the projector explodes
  each history row's ``article_urls`` into one article row, all sharing the
  single ``target_url``; ``target_urls_json`` is therefore single-element).
  We GROUP these by canonical target — we do **not** explode ``target_urls_json``.
  The R1a "all attempted targets" universe also reads ``events`` for the
  ``publish.intent/confirmed/failed`` kinds so failed/never-published targets
  surface as ``0/0``.
- **history_store** — platform + ``verified_at``/``verify_error`` liveness +
  the item ``id`` (needed by the WebUI recheck, U6), matched to article links
  by canonical ``live_url`` ↔ canonical ``article_urls``.
- **anchor-profile store** — per-entry ``target_url`` (re-keyed through
  ``canonicalize_url``) supplies the anchor data for exact-match% (U3).

Pure read-side: no writes, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.anchor import profile as anchor_profile
from backlink_publisher.anchor.metrics import group_by_target_url
from backlink_publisher.anchor.profile import ProfileEntry
from backlink_publisher.events import EventStore, kinds

# Event kinds that mark a target as "attempted" (R1a row universe), sourced from
# the registry (no duplicated literals). publish.unverified is intentionally
# omitted (a link whose liveness was never confirmed shouldn't count as
# attempted-confirmed); that omission is allowlisted in the R8b reader gate.
ATTEMPTED_KINDS: tuple[str, ...] = (
    kinds.PUBLISH_INTENT,
    kinds.PUBLISH_CONFIRMED,
    kinds.PUBLISH_FAILED,
)


@dataclass
class LinkRecord:
    """One published backlink (a single ``live_url``) pointing at a target.

    Platform + liveness come from the matching history row; an article with no
    history match leaves them ``None`` (orphan article → unverified/no-platform).
    """

    live_url: str  # canonical
    platform: str | None = None
    history_item_id: str | None = None
    verified_at: str | None = None
    verify_error: str | None = None


@dataclass
class TargetBucket:
    """Raw joined materials for one target page; dimensions computed in U3."""

    target_url: str  # canonical dedup key
    links: dict[str, LinkRecord] = field(default_factory=dict)  # by canonical live_url
    profile_entries: list[ProfileEntry] = field(default_factory=list)
    has_anchor_data: bool = False


def _canon(url: str | None) -> str:
    """Canonicalize, tolerating ``None``/blank (returns "")."""
    if not url:
        return ""
    return canonicalize_url(url)


def _load_history(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if history is not None:
        return history
    # Lazy import so a pure-events.db consumer never imports the WebUI store.
    from webui_store import history_store

    return history_store.load()


def build_target_buckets(
    *,
    store: EventStore | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, TargetBucket]:
    """Join the three stores into ``{canonical_target_url: TargetBucket}``.

    ``store`` / ``history`` are injectable for tests; both default to the
    operator's real stores resolved via the config/cache dirs.
    """
    store = store or EventStore()
    buckets: dict[str, TargetBucket] = {}

    def bucket_for(target: str) -> TargetBucket:
        return buckets.setdefault(target, TargetBucket(target_url=target))

    # 1. Articles → one LinkRecord per canonical live_url, grouped by canonical target.
    live_index: dict[str, tuple[str, LinkRecord]] = {}  # canonical live_url -> (target, link)
    for row in store.query(
        "SELECT target_urls_json, live_url FROM articles"
    ):
        try:
            targets = json.loads(row["target_urls_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            targets = []
        target = _canon(targets[0]) if targets else ""
        live = _canon(row["live_url"])
        if not target or not live:
            continue
        b = bucket_for(target)
        link = b.links.setdefault(live, LinkRecord(live_url=live))
        live_index[live] = (target, link)

    # 2. R1a universe: every attempted target appears, even with zero links.
    placeholders = ",".join("?" for _ in ATTEMPTED_KINDS)
    for row in store.query(
        f"SELECT DISTINCT target_url FROM events WHERE kind IN ({placeholders})",
        ATTEMPTED_KINDS,
    ):
        target = _canon(row["target_url"])
        if target:
            bucket_for(target)

    # 3. History → attach platform + liveness to links (matched by canonical live_url).
    for item in _load_history(history):
        platform = item.get("platform")
        item_id = item.get("id")
        verified_at = item.get("verified_at")
        verify_error = item.get("verify_error")
        item_target = _canon(item.get("target_url"))
        for raw_url in item.get("article_urls") or []:
            live = _canon(raw_url)
            if not live:
                continue
            found = live_index.get(live)
            if found is not None:
                _, link = found
            else:
                # Orphan history link (no article row) — attach under the
                # item's own target so the published link still counts.
                target = item_target or live
                b = bucket_for(target)
                link = b.links.setdefault(live, LinkRecord(live_url=live))
                live_index[live] = (target, link)
            link.platform = platform
            link.history_item_id = item_id
            link.verified_at = verified_at
            link.verify_error = verify_error

    # 4. Anchor profiles → per-target ProfileEntry lists (re-keyed canonical).
    for state in anchor_profile.iter_profiles():
        for raw_target, entries in group_by_target_url(state).items():
            if not raw_target:
                # Pre-bump "" domain-rollup bucket — not attributable per-target.
                continue
            target = _canon(raw_target)
            b = buckets.get(target)
            if b is None:
                continue
            b.profile_entries.extend(entries)
            b.has_anchor_data = True

    return buckets
