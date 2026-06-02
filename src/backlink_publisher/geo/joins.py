"""Article-URL set + brand-alias helpers for GEO verdict classification
(Plan 2026-05-29-006 Unit 5).

Two thin join helpers consumed by :func:`backlink_publisher.geo.verdict.classify_verdict`:

1. :func:`build_published_article_set` — build the canonical article-URL
   frozenset from ``ledger/sources.py`` for article-tier credit matching.
   Prefers ``publish.confirmed`` floor; surfaces the set as a ``frozenset``
   of canonicalized URL strings so the credit gate can do O(1) lookups.

2. :func:`build_brand_aliases` — future hook for per-target brand alias
   resolution.  v1 returns an empty list (alias lists come from operator
   config, wired in U7 dry-run).  Placed here so U5 tests can inject fakes
   without importing U7 config machinery.
"""

from __future__ import annotations

import logging
from typing import Any

from backlink_publisher._util.url import canonicalize_url
from backlink_publisher.ledger.sources import build_target_buckets

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Article-URL set (article_cited join)
# ---------------------------------------------------------------------------


def build_published_article_set(
    *,
    store: Any = None,
    history: list[dict[str, Any]] | None = None,
) -> frozenset[str]:
    """Return the set of canonical published article URLs for article-tier credit.

    Pulls from :func:`~backlink_publisher.ledger.sources.build_target_buckets`
    and collects every ``live_url`` across all buckets.  Both ``store`` and
    ``history`` are injectable for tests; they default to the operator's real
    stores (lazy-imported inside ``build_target_buckets``).

    The returned set contains only URLs whose host+path are meaningful for a
    path-level match — blank or non-http(s) entries are silently dropped.

    ``publish.confirmed`` is the preferred floor because only confirmed
    publications carry a real ``live_url``; intent/failed rows carry ``None``.
    The join is transparent to the caller — ``build_target_buckets`` already
    applies the correct classification internally.
    """
    try:
        buckets = build_target_buckets(store=store, history=history)
    except Exception as exc:  # noqa: BLE001
        # Surface as a warning — a missing events.db is not a fatal error for
        # the verdict classifier; it just means no article URLs are known.
        _log.warning("geo.joins: could not load published article set: %s", exc)
        return frozenset()

    urls: set[str] = set()
    for bucket in buckets.values():
        for live_url in bucket.links:
            if not live_url:
                continue
            # Canonicalize (already done in build_target_buckets but we
            # re-run to ensure idempotency even if the caller supplies a
            # custom store with raw URLs).
            try:
                canon = canonicalize_url(live_url)
            except Exception:
                continue
            if canon:
                urls.add(canon)
    return frozenset(urls)


# ---------------------------------------------------------------------------
# Brand-alias resolver (v1 stub; wired from config in U7)
# ---------------------------------------------------------------------------


def build_brand_aliases(
    target_url: str,
    *,
    config: Any = None,
) -> list[str]:
    """Return the list of brand name aliases for ``target_url``.

    v1 stub: returns ``[]``.  The real implementation (U7) reads
    ``[targets."<domain>"].brand_aliases`` from the operator config and
    warns on missing entries during ``--dry-run``.  A missing alias list
    is intentionally inert (no false positives in ``brand_mentioned``).

    ``config`` is injectable for tests.
    """
    # Placeholder — U7 wires the real config read here.
    return []
