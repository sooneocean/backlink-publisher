"""Per-site anchor profile state — sliding window of recent link records.

The anchor profile scheduler (zh-CN short-form path) needs a persistent view
of what anchor types and texts a site has recently published, so it can steer
each new article toward the deficit type. That state lives here as one JSON
file per site under ``~/.cache/backlink-publisher/anchor-profile/``.

Sliding window: the most recent ``_MAX_ENTRIES`` link records are kept;
older entries are trimmed on every write. This is "recent" not "all-time" by
design — the scheduler should respond to current drift, not be dragged by
ancient history when proportions or pools change.

Concurrency: ``threading.Lock`` protects the read-modify-write cycle inside
one process. Cross-process safety is NOT provided (single-process operational
convention per plan scope). If multi-process becomes real, layer an
``fcntl.flock`` sidecar lockfile around the same primitives.

Failure posture: profile state is an *advisory* signal, not a system of
record. A corrupt JSON file or version drift returns an empty profile with a
warning rather than raising — the scheduler will treat the site as cold-start
and rebuild state from new writes. The alternative (raising) would block the
entire batch on a recoverable diagnostic-only file.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ANCHOR_TYPES, _cache_dir
from .io_utils import atomic_write_json
from .logger import plan_logger

# Schema version — bump when ProfileEntry shape changes incompatibly.
_PROFILE_SCHEMA_VERSION = 1

# Sliding window size in link records (main + secondary mixed).
_MAX_ENTRIES = 100

# Default size of the anchor-text dedup window passed to ``recent_texts``.
_DEFAULT_TEXT_WINDOW = 20

# Filename sanitization: keep alnum + dot/underscore/hyphen; replace the rest.
# This makes filesystem-safe names from URLs like ``https://example.com/path``.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Lock map keyed by sanitized filename — separate sites can write in parallel
# but two threads against the same site serialize through one lock.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(filename: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(filename)
        if lock is None:
            lock = threading.Lock()
            _locks[filename] = lock
        return lock


@dataclass
class ProfileEntry:
    """A single recorded link from a published article.

    One article produces 2-3 entries (1 main + 1-2 secondary). ``ts`` is the
    moment the article was successfully validated. ``degraded`` is True when
    the entry came from the validator-failure fallback path; the scheduler
    still counts these in its distribution math (they really happened) but
    Unit 9's report surfaces the degradation rate as a quality signal.
    """

    ts: str
    link_role: str  # "main" | "secondary"
    url_category: str  # "home" | "hot" | "animate" | "category" | "topic" | ...
    anchor_type: str  # one of ANCHOR_TYPES
    anchor_text: str
    degraded: bool = False


@dataclass
class ProfileState:
    version: int = _PROFILE_SCHEMA_VERSION
    main_domain: str = ""
    entries: list[ProfileEntry] = field(default_factory=list)


# ── path helpers ────────────────────────────────────────────────────────────


def _sanitize_filename(main_domain: str) -> str:
    """Turn a main_domain URL into a filesystem-safe filename stem."""
    return _FILENAME_UNSAFE.sub("_", main_domain.rstrip("/"))


def _profile_dir() -> Path:
    return _cache_dir() / "anchor-profile"


def _profile_path(main_domain: str) -> Path:
    return _profile_dir() / f"{_sanitize_filename(main_domain)}.json"


# ── load / record ───────────────────────────────────────────────────────────


def load_profile(main_domain: str) -> ProfileState:
    """Read the on-disk profile for ``main_domain``.

    Returns an empty ``ProfileState`` (cold-start) when:
    - the file does not exist
    - the file is unreadable / malformed JSON
    - the schema version differs from ``_PROFILE_SCHEMA_VERSION``
    Each non-happy branch emits a structured warning so anomalies surface in
    logs without blocking the pipeline.
    """
    path = _profile_path(main_domain)
    if not path.exists():
        return ProfileState(main_domain=main_domain)

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        plan_logger.warn(
            "anchor_profile_load_failed",
            main_domain=main_domain,
            path=str(path),
            reason=type(exc).__name__,
            detail=str(exc),
        )
        return ProfileState(main_domain=main_domain)

    version = raw.get("version")
    if version != _PROFILE_SCHEMA_VERSION:
        plan_logger.warn(
            "anchor_profile_version_mismatch",
            main_domain=main_domain,
            expected=_PROFILE_SCHEMA_VERSION,
            got=version,
        )
        return ProfileState(main_domain=main_domain)

    entries_raw = raw.get("entries", [])
    if not isinstance(entries_raw, list):
        plan_logger.warn(
            "anchor_profile_entries_malformed",
            main_domain=main_domain,
            type=type(entries_raw).__name__,
        )
        return ProfileState(main_domain=main_domain)

    entries: list[ProfileEntry] = []
    for item in entries_raw:
        if not isinstance(item, dict):
            continue
        try:
            entries.append(
                ProfileEntry(
                    ts=str(item["ts"]),
                    link_role=str(item["link_role"]),
                    url_category=str(item["url_category"]),
                    anchor_type=str(item["anchor_type"]),
                    anchor_text=str(item["anchor_text"]),
                    degraded=bool(item.get("degraded", False)),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Skip individual malformed entries rather than tossing the whole file.
            continue

    return ProfileState(
        version=version,
        main_domain=str(raw.get("main_domain", main_domain)),
        entries=entries,
    )


def record_article(main_domain: str, new_entries: list[ProfileEntry]) -> None:
    """Atomically append ``new_entries`` and trim to the sliding window.

    Read-modify-write is protected by a per-site lock so two threads recording
    against the same main_domain serialize. Failures to write (e.g. cache_dir
    unwritable) are logged and swallowed — profile state is advisory only and
    must not abort the publishing batch.
    """
    if not new_entries:
        return

    filename = _sanitize_filename(main_domain)
    lock = _lock_for(filename)
    with lock:
        try:
            _profile_dir().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            plan_logger.warn(
                "anchor_profile_dir_create_failed",
                main_domain=main_domain,
                reason=type(exc).__name__,
            )
            return

        existing = load_profile(main_domain)
        merged = existing.entries + list(new_entries)
        # Trim oldest entries to keep the window bounded.
        if len(merged) > _MAX_ENTRIES:
            merged = merged[-_MAX_ENTRIES:]

        payload = {
            "version": _PROFILE_SCHEMA_VERSION,
            "main_domain": main_domain,
            "entries": [asdict(e) for e in merged],
        }
        try:
            atomic_write_json(_profile_path(main_domain), payload)
        except OSError as exc:
            plan_logger.warn(
                "anchor_profile_write_failed",
                main_domain=main_domain,
                reason=type(exc).__name__,
                detail=str(exc),
            )


def now_iso() -> str:
    """Helper to produce ``ts`` values in the canonical form used by ``ProfileEntry``."""
    return datetime.now(timezone.utc).isoformat()


# ── derived views (pure functions over ProfileState) ────────────────────────


def recent_type_counts(profile: ProfileState) -> dict[str, int]:
    """Count anchor types across all entries in the window.

    Returns a dict with every key in ``ANCHOR_TYPES`` so callers can do
    ``counts[type]`` without ``.get`` defaults. Unknown types are ignored
    (defensive — schema validation should have caught them at write time).
    """
    counts: dict[str, int] = {t: 0 for t in ANCHOR_TYPES}
    for entry in profile.entries:
        if entry.anchor_type in counts:
            counts[entry.anchor_type] += 1
    return counts


def recent_url_category_counts(profile: ProfileState) -> dict[str, int]:
    """Count entries per url_category. Categories with zero entries are absent
    from the result — callers should treat missing keys as zero."""
    counts: dict[str, int] = {}
    for entry in profile.entries:
        counts[entry.url_category] = counts.get(entry.url_category, 0) + 1
    return counts


def recent_texts(profile: ProfileState, n: int = _DEFAULT_TEXT_WINDOW) -> list[str]:
    """Return the most recent ``n`` anchor texts, newest first.

    The resolver passes this list to its dedup filter so a recently-used
    anchor isn't re-selected in the next few articles. ``n`` defaults to 20,
    matching the brainstorm-confirmed dedup window.
    """
    return [e.anchor_text for e in reversed(profile.entries[-n:])]


def recent_degradation_rate(profile: ProfileState, n: int = _MAX_ENTRIES) -> float:
    """Fraction of the most recent ``n`` entries marked ``degraded``.

    Returns 0.0 when the profile is empty (cold-start) — there is no rate
    when there is no data. Above 0.1 (10%) is the brainstorm-defined alarm
    threshold; this function is the data source for Unit 9's report.
    """
    sample = profile.entries[-n:]
    if not sample:
        return 0.0
    return sum(1 for e in sample if e.degraded) / len(sample)


def _group_into_articles(entries: list[ProfileEntry]) -> list[list[ProfileEntry]]:
    """Reconstruct article boundaries from a flat link list.

    Each "main" entry starts a new article; subsequent entries belong to that
    article until the next "main" appears. Entries before the first "main"
    are dropped — they came from a sliding-window trim that severed an article
    mid-record, so including them would misreport secondary counts.
    """
    articles: list[list[ProfileEntry]] = []
    current: list[ProfileEntry] = []
    for entry in entries:
        if entry.link_role == "main":
            if current:
                articles.append(current)
            current = [entry]
        elif current:
            current.append(entry)
        # else: secondary before any main → trimmed article remnant, drop.
    if current:
        articles.append(current)
    return articles


def recent_secondary_count_split(
    profile: ProfileState,
    n: int = _DEFAULT_TEXT_WINDOW,
) -> tuple[int, int]:
    """Articles in the most recent ``n`` that had 1 vs 2 secondary links.

    Returned as ``(count_with_1_secondary, count_with_2_secondaries)``. The
    scheduler uses this to keep the (1, 2) split converging on 50/50 — see
    Unit 4 ``pick_secondary_count``.

    Articles with 0 secondaries (a degraded edge case or unusual record) and
    articles with 3+ secondaries (not currently possible by design) are not
    counted in either bucket.
    """
    articles = _group_into_articles(profile.entries)
    sample = articles[-n:]
    count_1 = 0
    count_2 = 0
    for art in sample:
        secondary_count = sum(1 for e in art if e.link_role == "secondary")
        if secondary_count == 1:
            count_1 += 1
        elif secondary_count == 2:
            count_2 += 1
    return count_1, count_2
