"""Best-effort backfill of the dedup store from publish history (Unit 6).

Seeds the dedup store with already-live posts so the first *enforced* run does
not treat the back-catalogue as ``NEW`` and re-publish it. May run during the
Phase-A observe soak so the preview manifest is meaningful.

Source: ``events.db`` events of kind ``publish.confirmed`` / ``publish.unverified``,
read via a **snapshot-copy** opened ``mode=ro`` (never ``immutable=1``, never
``EventStore.query()`` — same discipline as ``audit/readers.py``;
``reference_events_db_readonly_wal_snapshot``). ``target_url`` comes from the
event's first-class column; the publishing adapter STRING and ``live_url`` come
from ``payload_json`` (the projector stores ``AdapterResult.adapter`` under the
``"platform"`` key — see ``_project_reducers.py``).

Mapping is an **explicit live adapter-string → platform table** (hand-maintained
literals — NOT a suffix-strip and NOT the registry ``register()`` keys, which are
the bare platforms, not the adapter strings). Only *currently-live* strings are
enumerated; any retired/unknown string falls to **quarantine** (a counted,
reconciliation-blocking outcome that the U7 enforce gate refuses to cross until
the operator acknowledges it) — never a silent drop, never a crash.

Tiering (conservative): ``publish.confirmed`` + cleanly-mapped + has ``live_url``
→ ``done(verify_ok=true)``. ``publish.unverified``, or a missing ``live_url`` →
``uncertain`` (held; surfaced by U4 aged-uncertain / U5 ``--list-uncertain``),
forcing operator review rather than an optimistic skip that would silently drop a
needed backlink.

Decision-preserving / INSERT-only: never overwrites an existing terminal row, and
**skips any key the operator has touched** (consulting the U5 audit log) so
re-running backfill after a ``--forget``/``--adjudicate`` does not resurrect or
flip that key.

Best-effort: ``events.db`` is lossy (PR #222) — completeness is not guaranteed,
and there is no independent oracle for a CLI-only operator.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U6).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..config import _config_dir
from . import audit_log
from .store import DedupKey, DedupStore

_EVENTS_DB_FILENAME = "events.db"
_WAL_SUFFIX = "-wal"

#: Explicit live adapter-string → bare-platform map. A platform's registered
#: fallback chain can emit MORE than one string over its history, so every live
#: string a chain can produce must map. velog/devto register an API adapter as
#: primary (``velog-graphql`` / ``devto``) with the browser dispatcher
#: (``{channel}-browser-attach``) as fallback; mastodon is browser-only. hashnode
#: emits both ``hashnode-gql`` and ``hashnode`` from its one adapter.
#: ``test_every_live_adapter_string_is_mapped`` greps the adapter sources and
#: fails if a new live string appears unmapped. Non-publisher helpers (``llm-*``)
#: and the unregistered ``http-form-post`` are intentionally absent → quarantine.
_ADAPTER_STRING_TO_PLATFORM: dict[str, str] = {
    "blogger-api": "blogger",
    "devto": "devto",
    "ghpages": "ghpages",
    "hashnode": "hashnode",
    "hashnode-gql": "hashnode",
    "linkedin": "linkedin",
    "livejournal-api": "livejournal",
    "medium-api": "medium",
    "medium-brave": "medium",
    "medium-browser": "medium",
    "notion": "notion",
    "rentry": "rentry",
    "substack": "substack",
    "telegraph-api": "telegraph",
    "telegraph-cdp": "telegraph",
    "tumblr": "tumblr",
    "txtfyi-form-post": "txtfyi",
    "velog-graphql": "velog",
    "wordpresscom": "wordpresscom",
    "writeas": "writeas",
    # Browser-dispatcher fallback strings (adapter = f"{channel}-browser-attach").
    "velog-browser-attach": "velog",
    "devto-browser-attach": "devto",
    "mastodon-browser-attach": "mastodon",
}

_BACKFILL_KINDS = ("publish.confirmed", "publish.unverified")


@dataclass
class BackfillResult:
    seeded_done: int = 0
    seeded_uncertain: int = 0
    quarantined: int = 0
    skipped_existing: int = 0
    skipped_operator_touched: int = 0

    @property
    def seeded(self) -> int:
        return self.seeded_done + self.seeded_uncertain


def _events_db_path() -> Path:
    return _config_dir() / _EVENTS_DB_FILENAME


def _read_publish_events(db_path: Path) -> list[tuple[str, str | None, str | None]]:
    """Snapshot-copy ``events.db`` (+ ``-wal``) and read ``(kind, target_url,
    payload_json)`` for the publish-success kinds. Leaves the live store
    byte-identical. Returns ``[]`` if the db is absent OR has no ``events`` table
    (a fresh/foreign db) — a missing table is benign here, not an operator error."""
    if not db_path.exists():
        return []
    wal_path = db_path.with_name(db_path.name + _WAL_SUFFIX)
    tmp_dir = Path(tempfile.mkdtemp(prefix="bp-backfill-"))
    try:
        copy_db = tmp_dir / _EVENTS_DB_FILENAME
        shutil.copy2(db_path, copy_db)
        if wal_path.exists():
            shutil.copy2(wal_path, tmp_dir / (_EVENTS_DB_FILENAME + _WAL_SUFFIX))
        conn = sqlite3.connect(f"file:{copy_db}?mode=ro", uri=True)
        try:
            placeholders = ", ".join("?" for _ in _BACKFILL_KINDS)
            rows = conn.execute(
                f"SELECT kind, target_url, payload_json FROM events "
                f"WHERE kind IN ({placeholders})",
                _BACKFILL_KINDS,
            ).fetchall()
        except sqlite3.OperationalError:
            # No `events` table (fresh/foreign db) → nothing to seed, not a crash.
            return []
        finally:
            conn.close()
        return [(r[0], r[1], r[2]) for r in rows]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def run_backfill() -> BackfillResult:
    """Seed the dedup store from publish-success events. Idempotent and
    decision-preserving; safe to re-run. Returns the per-outcome counts."""
    result = BackfillResult()
    events = _read_publish_events(_events_db_path())
    if not events:
        return result

    store = DedupStore()
    touched = audit_log.touched_keys()

    # Aggregate per key first so the best outcome wins regardless of event order
    # (a key with both a confirmed and an unverified event must seed `done`, not
    # `uncertain` — INSERT-OR-IGNORE alone would let whichever row is processed
    # first win). ``done`` (confirmed + live_url) beats ``uncertain``.
    best: dict[tuple[str, str, str], tuple[DedupKey, str, str | None]] = {}
    for kind, col_target_url, payload_json in events:
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except ValueError:
            payload = {}
        adapter_string = payload.get("platform")  # projector stores adapter here
        target_url = col_target_url or payload.get("target_url")
        live_url = payload.get("live_url")

        platform = _ADAPTER_STRING_TO_PLATFORM.get(adapter_string or "")
        if platform is None or not target_url:
            # Retired/unknown adapter string, or no usable target → quarantine
            # (counted; blocks enforce until acknowledged). Never seed, never crash.
            result.quarantined += 1
            continue

        key = DedupKey(platform=platform, target_url=str(target_url))
        tup = key.as_tuple()
        state = "done" if (kind == "publish.confirmed" and live_url) else "uncertain"
        existing = best.get(tup)
        # Keep the stronger outcome; prefer a non-null live_url when both are done.
        if existing is None or (state == "done" and existing[1] != "done"):
            best[tup] = (key, state, live_url)
        elif state == "done" and existing[2] is None and live_url:
            best[tup] = (key, state, live_url)

    for tup, (key, state, live_url) in best.items():
        if tup in touched:
            # The operator has --forget/--adjudicate-ed this key; never resurrect.
            result.skipped_operator_touched += 1
            continue
        if state == "done":
            inserted = store.seed(key, "done", live_url=live_url, verify_ok=True)
            if inserted:
                result.seeded_done += 1
            else:
                result.skipped_existing += 1
        else:
            inserted = store.seed(key, "uncertain", live_url=live_url)
            if inserted:
                result.seeded_uncertain += 1
            else:
                result.skipped_existing += 1

    return result


def run_backfill_cli() -> None:
    """CLI entry for ``--backfill-dedup``: run the backfill, print a summary on
    stderr, exit 0. The caller raises ``SystemExit(0)``."""
    r = run_backfill()
    print(
        "backfill-dedup: "
        f"seeded done={r.seeded_done}, seeded uncertain={r.seeded_uncertain}, "
        f"quarantined(unmappable)={r.quarantined}, "
        f"skipped existing={r.skipped_existing}, "
        f"skipped operator-touched={r.skipped_operator_touched}.",
        file=sys.stderr,
    )
    if r.quarantined:
        print(
            f"backfill-dedup: {r.quarantined} event(s) had an unmappable/retired "
            "adapter string and were NOT seeded; the enforce gate (U7) will block "
            "until this is acknowledged.",
            file=sys.stderr,
        )
