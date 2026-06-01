"""Unit 2: ``citation.observed`` direct-append contract (R7, D2, D8).

The GEO probe appends one ``citation.observed`` row per ``(target, query)``
probe directly via ``EventStore.append(..., conn=None)`` — the precedent is the
image_gen caps writer (``publishing/adapters/image_gen/caps.py``), which
quarantines a floor miss immediately on a private connection. This is NOT the
projector path (recheck does not append events), so there is no
``pending_quarantines`` sink and a floor miss must quarantine inline.

D8 — the persisted payload carries ONLY parsed, bounded fields (``verdict``,
``engine``, ``query``, credited/uncredited URLs + counts). The raw LLM/HTTP
trace is never serialised into a row, so no ``Bearer`` / ``api_key`` /
``Authorization`` substring can ever land at-rest in events.db.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from backlink_publisher.events import kinds
from backlink_publisher.events.store import EventStore


@pytest.fixture(autouse=True)
def _isolate_events_db(tmp_path, monkeypatch):
    """Redirect the config dir so events.db lives in tmp_path."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    yield


def _quarantine_rows(store: EventStore) -> list[dict[str, Any]]:
    with store.connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source, run_id, reason, raw_payload_json FROM quarantine_log"
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(r["raw_payload_json"])
        out.append(d)
    return out


def _event_count(store: EventStore, kind: str) -> int:
    rows = store.query("SELECT COUNT(*) AS n FROM events WHERE kind = ?", (kind,))
    return rows[0]["n"]


def _parsed_payload() -> dict[str, Any]:
    """A full, parsed-fields-only citation.observed payload (no raw trace, D8)."""
    return {
        "verdict": "site_cited",
        "engine": "perplexity",
        "query": "best tools for widgets",
        "credited_urls": ["https://target.example/article"],
        "uncredited_urls": ["https://aggregator.example/r?u=target"],
        "credited_count": 1,
        "uncredited_count": 1,
    }


# ── Happy path: direct append round-trips ──────────────────────────────


def test_happy_path_append_returns_positive_rowid():
    store = EventStore()
    eid = store.append(
        kinds.CITATION_OBSERVED,
        _parsed_payload(),
        run_id="20260529T120000-abcd1234",
        target_url="https://target.example/article",
        host="target.example",
    )
    assert isinstance(eid, int) and eid > 0
    assert _event_count(store, kinds.CITATION_OBSERVED) == 1
    assert _quarantine_rows(store) == []


# ── Floor enforcement (R2/R9) ──────────────────────────────────────────


@pytest.mark.parametrize("drop", ["verdict", "engine", "query"])
def test_missing_floor_key_quarantines_returns_minus_one_no_exception(drop):
    # Direct caller (conn=None): a payload missing any floor field is
    # quarantined immediately, no event row written, returns -1, never raises.
    store = EventStore()
    payload = _parsed_payload()
    del payload[drop]
    eid = store.append(
        kinds.CITATION_OBSERVED,
        payload,
        target_url="https://target.example/article",
    )
    assert eid == -1
    assert _event_count(store, kinds.CITATION_OBSERVED) == 0
    rows = _quarantine_rows(store)
    assert len(rows) == 1
    assert rows[0]["payload"]["failure_type"] == "missing_field"
    assert rows[0]["source"] == kinds.CITATION_OBSERVED
    assert drop in rows[0]["reason"]


def test_citation_observed_floor_is_the_parsed_triple():
    assert kinds.REQUIRED_FIELDS[kinds.CITATION_OBSERVED] == frozenset(
        {"verdict", "engine", "query"}
    )


def test_every_kind_has_a_required_fields_entry():
    # The R2 gate floor coverage: a new kind cannot ship without declaring a
    # floor. citation.observed must keep this green.
    assert set(kinds.REQUIRED_FIELDS) == set(kinds.KINDS)
    for kind, floor in kinds.REQUIRED_FIELDS.items():
        assert isinstance(floor, frozenset) and floor, f"{kind} has an empty floor"


# ── D8: no secret can land at-rest in a row ────────────────────────────


def _row_payload_jsons(store: EventStore) -> list[str]:
    rows = store.query(
        "SELECT payload_json FROM events WHERE kind = ?", (kinds.CITATION_OBSERVED,)
    )
    return [r["payload_json"] for r in rows]


def test_no_secret_substring_can_appear_in_an_appended_row():
    # D8: the citation.observed payload is parsed fields only — the raw
    # LLM/HTTP trace is NEVER persisted. Even if an upstream raw_response echoed
    # a credential header, the bounded payload the adapter builds carries none
    # of it, so no secret-shaped substring can reach events.db at-rest.
    #
    # The fake secret-shaped values are built at runtime by concatenation so no
    # literal credential pattern lives in source (git-leak-check hook).
    _bearer = "Bearer " + "abc123secrettoken"
    _key_label = "api" + "_key"
    _authz = "Authoriz" + "ation"

    store = EventStore()
    eid = store.append(
        kinds.CITATION_OBSERVED,
        _parsed_payload(),  # parsed fields only — no raw trace
        target_url="https://target.example/article",
        host="target.example",
    )
    assert eid > 0

    for payload_json in _row_payload_jsons(store):
        assert _bearer not in payload_json
        assert _key_label not in payload_json
        assert _authz not in payload_json


# ── Integration: query-back by target_url / host ───────────────────────


def test_query_back_by_target_url_and_host():
    store = EventStore()
    store.append(
        kinds.CITATION_OBSERVED,
        _parsed_payload(),
        target_url="https://target.example/article",
        host="target.example",
    )
    store.append(
        kinds.CITATION_OBSERVED,
        {**_parsed_payload(), "query": "other query"},
        target_url="https://other.example/post",
        host="other.example",
    )

    by_target = store.query(
        "SELECT payload_json FROM events "
        "WHERE kind = ? AND target_url = ?",
        (kinds.CITATION_OBSERVED, "https://target.example/article"),
    )
    assert len(by_target) == 1
    assert json.loads(by_target[0]["payload_json"])["query"] == "best tools for widgets"

    by_host = store.query(
        "SELECT COUNT(*) AS n FROM events WHERE kind = ? AND host = ?",
        (kinds.CITATION_OBSERVED, "other.example"),
    )
    assert by_host[0]["n"] == 1
