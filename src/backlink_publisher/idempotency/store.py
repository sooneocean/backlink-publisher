"""Authoritative, durable, ACID dedup store for cross-run publish idempotency.

The unit of idempotency is a record keyed on ``(platform, account,
canonicalized target_url)``. This is a **new authoritative** SQLite sidecar
store (``dedup.db``), deliberately separate from ``events.db``:

* ``events.db`` is a *rebuildable read-side projection* (a rebuild wipes
  non-projected tables) and is lossy, so it cannot be the write-gating
  authority.
* The dedup record is load-bearing for correctness, so it owns its durable
  store with atomic, single-flight state transitions.

State model (``absent`` = no row)::

    absent --intent_write--> attempting --+--> done (carries verify_ok)
                                          +--> failed     (re-publishable)
                                          +--> uncertain  (held; adjudicate)

    done / failed         : terminal w.r.t. the gate
    uncertain             : held; --adjudicate-uncertain resolves to done/failed
    --forget              : any state -> absent (row deleted)

The ``absent -> attempting`` transition is **single-flight**: it runs inside a
``BEGIN IMMEDIATE`` transaction (RESERVED write lock at transaction start) so two
concurrent publish runs cannot both observe ``absent`` and both dispatch
(TOCTOU). The loser of the race observes the existing row and *holds* rather than
re-posting.

Plan: ``docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md``
(Unit 1).
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

from .._util.url import canonicalize_url
from ..config import _config_dir
from ..events._store_sqlite import (
    _pid_alive,
    _retry_sqlite,
    _set_backup_exclude_xattr,
    _tighten_wal_sidecars,
)

#: Filename of the dedup store inside ``_config_dir()``. Separate from
#: ``events.db`` on purpose (see module docstring).
_DB_FILENAME: str = "dedup.db"

State = Literal["attempting", "done", "failed", "uncertain"]

#: Terminal states the gate treats as "already settled — skip" (``done``) or
#: "confirmed not landed — re-publishable" (``failed``).
_TERMINAL: frozenset[str] = frozenset({"done", "failed"})

#: Absolute age (seconds) beyond which an ``attempting`` row is considered
#: crashed regardless of PID liveness. Backstops PID reuse on long-lived cron
#: hosts: a stale ``attempting`` left by a dead run whose PID was recycled to an
#: unrelated live process ages out here even though ``_pid_alive`` reports True.
#: Chosen >= the publish lease TTL (3600s) — no single dispatch runs that long.
_STALE_TTL_S: int = 3600


def _default_dedup_db_path() -> Path:
    return _config_dir() / _DB_FILENAME


def _now() -> float:
    return time.time()


@dataclass(frozen=True)
class DedupKey:
    """Identity of a logical backlink: a post on ``platform`` (published by
    ``account``) that links to ``target_url``.

    ``target_url`` is canonicalized on construction (``canonicalize_url``) so
    scheme/host-case/trailing-slash/utm differences collapse to one key. ``account``
    defaults to a stable marker today (one account per channel); it is part of the
    key so a future second account on the same platform is a *distinct* key and is
    not false-skipped.
    """

    platform: str
    target_url: str
    account: str = "default"

    def __post_init__(self) -> None:
        object.__setattr__(self, "target_url", canonicalize_url(self.target_url))

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.platform, self.account, self.target_url)


@dataclass(frozen=True)
class DedupRecord:
    """A persisted dedup row."""

    platform: str
    account: str
    target_url: str
    state: State
    verify_ok: bool | None
    live_url: str | None
    run_id: str | None
    owner_pid: int | None
    owner_run_id: str | None
    owner_started_at: float | None
    updated_at: float

    @property
    def key(self) -> DedupKey:
        # target_url is already canonical in the row; reconstruct without
        # re-canonicalizing (idempotent anyway).
        return DedupKey(
            platform=self.platform, target_url=self.target_url, account=self.account
        )


@dataclass(frozen=True)
class IntentOutcome:
    """Result of :meth:`DedupStore.intent_write`.

    ``won`` True  -> this caller inserted ``attempting`` and owns the dispatch.
    ``won`` False -> a row already existed (``existing_state`` set); the caller
                     must NOT dispatch — it holds (or skips, per the gate).
    """

    won: bool
    existing_state: State | None = None


class DedupStore:
    """SQLite-backed dedup record store.

    Mirrors the ``events/store.py`` connection discipline (WAL,
    ``synchronous=NORMAL``, ``busy_timeout``, 0o600 file / 0o700 dir, tightened
    WAL/SHM sidecars) and reuses its shared plumbing helpers. Writes that must be
    single-flight use ``BEGIN IMMEDIATE`` via :meth:`connect_immediate`.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path = path if path is not None else _default_dedup_db_path()

    # ------------------------------------------------------------------ #
    # Connection plumbing (mirrors EventStore; dedup schema instead of events)
    # ------------------------------------------------------------------ #
    def _connect_raw(self) -> sqlite3.Connection:
        first_create = not self.path.exists()
        if first_create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass

        conn = sqlite3.connect(str(self.path), timeout=5.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute(_SCHEMA_DDL)
        conn.commit()

        if first_create:
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            _set_backup_exclude_xattr(self.path)

        # WAL/SHM appear lazily and inherit umask (often 0o644); campaign URLs
        # live at rest in the WAL, so tighten every connect.
        _tighten_wal_sidecars(self.path)
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect_raw()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def connect_immediate(self) -> Iterator[sqlite3.Connection]:
        """Open the transaction with ``BEGIN IMMEDIATE`` (RESERVED write lock at
        start) so a read-modify-write critical section cannot interleave with a
        concurrent writer. Caller must not open a second writing connection in the
        same thread while held (self-deadlock)."""
        conn = self._connect_raw()
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def get(self, key: DedupKey) -> DedupRecord | None:
        def _op() -> DedupRecord | None:
            with self.connect() as conn:
                row = conn.execute(
                    f"SELECT {_COLS} FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                ).fetchone()
            return _row_to_record(row) if row is not None else None

        return _retry_sqlite(_op)

    def is_stale_attempting(
        self, record: DedupRecord, *, now: float | None = None, ttl_s: int = _STALE_TTL_S
    ) -> bool:
        """An ``attempting`` row is stale (its owning run died mid-dispatch) when
        the owner PID is gone, OR the row has aged past ``ttl_s`` (the absolute
        backstop that also defeats PID reuse). A non-``attempting`` row is never
        stale."""
        if record.state != "attempting":
            return False
        now = _now() if now is None else now
        if record.owner_pid is not None and not _pid_alive(record.owner_pid):
            return True
        if (now - record.updated_at) > ttl_s:
            return True
        return False

    # ------------------------------------------------------------------ #
    # Writes
    # ------------------------------------------------------------------ #
    def intent_write(
        self,
        key: DedupKey,
        *,
        run_id: str | None = None,
        owner_pid: int | None = None,
        owner_run_id: str | None = None,
        owner_started_at: float | None = None,
        account_binding_id: str | None = None,
    ) -> IntentOutcome:
        """Single-flight ``absent -> attempting`` transition.

        Runs inside ``BEGIN IMMEDIATE`` so concurrent callers serialize: the first
        inserts ``attempting`` and wins; a later caller observes the existing row
        and loses (``IntentOutcome.won is False`` with ``existing_state``). A
        ``--forget``-cleared key (absent) re-inserts cleanly. ``sqlite3.IntegrityError``
        from the UNIQUE constraint is treated as a normal lost-race (held), never an
        error; only ``database is locked`` is retried (via ``_retry_sqlite``)."""
        owner_pid = os.getpid() if owner_pid is None else owner_pid

        def _op() -> IntentOutcome:
            with self.connect_immediate() as conn:
                existing = conn.execute(
                    "SELECT state FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                ).fetchone()
                if existing is not None:
                    return IntentOutcome(won=False, existing_state=existing[0])
                try:
                    conn.execute(
                        "INSERT INTO dedup_keys "
                        "(platform, account, account_binding_id, target_url, state, "
                        " verify_ok, live_url, run_id, owner_pid, owner_run_id, "
                        " owner_started_at, updated_at) "
                        "VALUES (?, ?, ?, ?, 'attempting', NULL, NULL, ?, ?, ?, ?, ?)",
                        (
                            key.platform,
                            key.account,
                            account_binding_id,
                            key.target_url,
                            run_id,
                            owner_pid,
                            owner_run_id,
                            owner_started_at,
                            _now(),
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Belt-and-suspenders: another writer inserted between our
                    # SELECT and INSERT despite the RESERVED lock (should not
                    # happen, but a UNIQUE conflict is a lost race, not an error).
                    row = conn.execute(
                        "SELECT state FROM dedup_keys "
                        "WHERE platform = ? AND account = ? AND target_url = ?",
                        key.as_tuple(),
                    ).fetchone()
                    return IntentOutcome(
                        won=False, existing_state=row[0] if row else None
                    )
            return IntentOutcome(won=True)

        return _retry_sqlite(_op)

    def transition(
        self,
        key: DedupKey,
        to_state: State,
        *,
        live_url: str | None = None,
        verify_ok: bool | None = None,
        run_id: str | None = None,
        allow_from_terminal: bool = False,
    ) -> None:
        """Move an existing key to a terminal/held state.

        Legal: ``attempting -> done|failed|uncertain`` and (adjudication)
        ``uncertain -> done|failed``. ``done``/``failed`` are terminal and not
        re-transitioned unless ``allow_from_terminal`` (used by adjudication's
        explicit override). Raises ``ValueError`` for an illegal transition or a
        missing row (call :meth:`intent_write` first)."""
        if to_state not in ("done", "failed", "uncertain"):
            raise ValueError(f"illegal target state {to_state!r}")

        def _op() -> None:
            with self.connect_immediate() as conn:
                row = conn.execute(
                    "SELECT state FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                ).fetchone()
                if row is None:
                    raise ValueError(f"transition on absent key {key.as_tuple()!r}")
                current = row[0]
                if current in _TERMINAL and not allow_from_terminal:
                    raise ValueError(
                        f"key {key.as_tuple()!r} is terminal ({current}); "
                        "cannot transition without allow_from_terminal"
                    )
                conn.execute(
                    "UPDATE dedup_keys SET state = ?, live_url = COALESCE(?, live_url), "
                    "verify_ok = COALESCE(?, verify_ok), "
                    "run_id = COALESCE(?, run_id), updated_at = ? "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    (
                        to_state,
                        live_url,
                        verify_ok,
                        run_id,
                        _now(),
                        *key.as_tuple(),
                    ),
                )

        _retry_sqlite(_op)

    def set_verify_ok(self, key: DedupKey, ok: bool) -> None:
        """Record post-flight verification outcome on a ``done`` row WITHOUT
        changing its state — verify is orthogonal to dedup identity (a verify
        failure leaves the key ``done`` so a flake cannot trigger a re-post)."""

        def _op() -> None:
            with self.connect_immediate() as conn:
                conn.execute(
                    "UPDATE dedup_keys SET verify_ok = ?, updated_at = ? "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    (ok, _now(), *key.as_tuple()),
                )

        _retry_sqlite(_op)

    def forget(self, key: DedupKey) -> str | None:
        """Delete the row for ``key`` (any state -> absent). Returns the prior
        state, or ``None`` if there was no row. Audited by the caller (Unit 5a)."""

        def _op() -> str | None:
            with self.connect_immediate() as conn:
                row = conn.execute(
                    "SELECT state FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                ).fetchone()
                if row is None:
                    return None
                conn.execute(
                    "DELETE FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                )
                return row[0]

        return _retry_sqlite(_op)


# Column list shared by SELECT helpers, in DedupRecord field order.
_COLS = (
    "platform, account, target_url, state, verify_ok, live_url, run_id, "
    "owner_pid, owner_run_id, owner_started_at, updated_at"
)

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS dedup_keys (
    platform           TEXT NOT NULL,
    account            TEXT NOT NULL,
    account_binding_id TEXT,
    target_url         TEXT NOT NULL,
    state              TEXT NOT NULL,
    verify_ok          INTEGER,
    live_url           TEXT,
    run_id             TEXT,
    owner_pid          INTEGER,
    owner_run_id       TEXT,
    owner_started_at   REAL,
    updated_at         REAL NOT NULL,
    PRIMARY KEY (platform, account, target_url)
)
"""


def _row_to_record(row: tuple) -> DedupRecord:
    (
        platform,
        account,
        target_url,
        state,
        verify_ok,
        live_url,
        run_id,
        owner_pid,
        owner_run_id,
        owner_started_at,
        updated_at,
    ) = row
    return DedupRecord(
        platform=platform,
        account=account,
        target_url=target_url,
        state=state,
        verify_ok=None if verify_ok is None else bool(verify_ok),
        live_url=live_url,
        run_id=run_id,
        owner_pid=owner_pid,
        owner_run_id=owner_run_id,
        owner_started_at=owner_started_at,
        updated_at=updated_at,
    )
