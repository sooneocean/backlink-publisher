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

import hashlib
import hmac
import os
import secrets as _secrets
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

#: Per-store HMAC secret (suffix appended to the db path). Created 0o600 on first
#: use. Keyed HMAC — not a bare hash — is required for the manifest key digest:
#: the operator's URL space is small and enumerable, so an unsalted hash is
#: trivially reversible and would defeat the manifest's stderr leak boundary.
_SECRET_SUFFIX: str = ".hmac-secret"

#: Hex length of the key digest surfaced in the manifest stderr summary.
_DIGEST_LEN: int = 16

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


#: Enforce-gate verdict (R4): dispatch (claimed), skip (already done), hold
#: (uncertain / live-attempting — surface, do not publish), conflict (a manifest
#: force-flag targeting a ``done`` key — R11; rejected, never republished).
GateVerdict = Literal["dispatch", "skip", "hold", "conflict"]


@dataclass(frozen=True)
class GateDecision:
    """Result of :meth:`DedupStore.gate_and_claim`. ``record`` is the pre-claim
    row (``None`` for an absent key) — carried so the caller can emit the recorded
    ``live_url`` on a SKIP and surface the held state on a HOLD."""

    verdict: GateVerdict
    record: DedupRecord | None = None


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
    # Key digest (keyed HMAC — manifest leak boundary)
    # ------------------------------------------------------------------ #
    def _secret_path(self) -> Path:
        return self.path.with_name(self.path.name + _SECRET_SUFFIX)

    def _load_or_create_secret(self) -> bytes:
        """Per-store HMAC secret. Created once (``O_CREAT|O_EXCL``, fsync'd, 0o600).
        A concurrent loser of the create race re-reads the winner's bytes, briefly
        retrying if it observes the file before the winner's write lands — so two
        processes always converge on the same secret (never an ephemeral one that
        would silently invalidate manifests)."""
        sp = self._secret_path()
        existing = self._read_secret(sp)
        if existing:
            return existing
        sp.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        token = _secrets.token_bytes(32)
        try:
            fd = os.open(str(sp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            # Lost the create race: the winner is mid-write. Re-read with a short
            # backoff until its bytes are visible rather than returning `token`
            # (which would differ from the persisted secret).
            for _ in range(50):
                won = self._read_secret(sp)
                if won:
                    return won
                time.sleep(0.01)
            return self._read_secret(sp) or token
        try:
            os.write(fd, token)
            os.fsync(fd)
        finally:
            os.close(fd)
        return token

    @staticmethod
    def _read_secret(sp: Path) -> bytes:
        try:
            return sp.read_bytes()
        except FileNotFoundError:
            return b""

    def key_digest(self, key: DedupKey) -> str:
        """Keyed HMAC over the key tuple, truncated to :data:`_DIGEST_LEN` hex
        chars. Two stores (distinct secrets) digest the same key differently, so
        the manifest's stderr digest cannot be reversed back to a campaign URL."""
        secret = self._load_or_create_secret()
        msg = "\x1f".join(key.as_tuple()).encode("utf-8")
        return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:_DIGEST_LEN]

    def store_token(self) -> str:
        """Per-store identity token embedded in the preview manifest and rechecked
        when force-flags are consumed (U7c). Derived from the per-store HMAC
        secret, so a manifest generated against a *different* store (campaign /
        config dir) is rejected — within-store staleness is caught separately by
        the gate's live-state recheck (a key that advanced to ``done`` conflicts)."""
        secret = self._load_or_create_secret()
        return hmac.new(
            secret, b"dedup-store-generation-v1", hashlib.sha256
        ).hexdigest()[:_DIGEST_LEN]

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

    def list_by_state(
        self, state: State, *, platform: str | None = None
    ) -> list[DedupRecord]:
        """All rows in ``state`` (optionally one ``platform``), newest-first.
        Used by ``--list-uncertain`` and the bulk ``--adjudicate`` selectors."""

        def _op() -> list[DedupRecord]:
            sql = f"SELECT {_COLS} FROM dedup_keys WHERE state = ?"
            params: list[object] = [state]
            if platform:
                sql += " AND platform = ?"
                params.append(platform)
            sql += " ORDER BY updated_at DESC"
            with self.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [_row_to_record(r) for r in rows]

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
    def gate_and_claim(
        self,
        key: DedupKey,
        *,
        run_id: str | None = None,
        owner_pid: int | None = None,
        owner_run_id: str | None = None,
        owner_started_at: float | None = None,
        now: float | None = None,
        ttl_s: int = _STALE_TTL_S,
        force: bool = False,
    ) -> GateDecision:
        """Atomic **enforce-mode** gate (R4): read the current state, decide, and
        CLAIM in one ``BEGIN IMMEDIATE`` transaction so the read-decide-write is
        TOCTOU-safe against a concurrent run.

        * ``done``                  -> ``skip``   (already published).
        * ``uncertain``             -> ``hold``   (held; surface, do not publish).
        * live ``attempting``       -> ``hold``   (another run owns the dispatch).
        * stale ``attempting``      -> reclaim to ``attempting`` (new owner) -> ``dispatch``.
        * ``failed`` (re-publishable)-> reclaim to ``attempting`` -> ``dispatch``.
        * absent                    -> INSERT ``attempting`` -> ``dispatch``.

        On ``dispatch`` the row is left ``attempting`` owned by this run, so the
        terminal write (``record_done``/``record_failure``) settles it exactly as
        on the fresh-intent path."""
        owner_pid = os.getpid() if owner_pid is None else owner_pid

        def _claim(conn: sqlite3.Connection, exists: bool) -> None:
            if exists:
                conn.execute(
                    "UPDATE dedup_keys SET state = 'attempting', verify_ok = NULL, "
                    "live_url = NULL, run_id = ?, owner_pid = ?, owner_run_id = ?, "
                    "owner_started_at = ?, updated_at = ? "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    (run_id, owner_pid, owner_run_id, owner_started_at, _now(),
                     *key.as_tuple()),
                )
            else:
                conn.execute(
                    "INSERT INTO dedup_keys "
                    "(platform, account, target_url, state, verify_ok, live_url, "
                    " run_id, owner_pid, owner_run_id, owner_started_at, updated_at) "
                    "VALUES (?, ?, ?, 'attempting', NULL, NULL, ?, ?, ?, ?, ?)",
                    (key.platform, key.account, key.target_url, run_id, owner_pid,
                     owner_run_id, owner_started_at, _now()),
                )

        def _op() -> GateDecision:
            with self.connect_immediate() as conn:
                row = conn.execute(
                    f"SELECT {_COLS} FROM dedup_keys "
                    "WHERE platform = ? AND account = ? AND target_url = ?",
                    key.as_tuple(),
                ).fetchone()
                if row is None:
                    _claim(conn, exists=False)
                    return GateDecision("dispatch", None)
                rec = _row_to_record(row)
                if rec.state == "done":
                    # R11: a manifest force-flag on an already-live key is a
                    # conflict (forcing would double-post) — reject, never claim.
                    return GateDecision("conflict" if force else "skip", rec)
                if rec.state == "uncertain":
                    # Force overrides the hold: reclaim the held key and dispatch.
                    if force:
                        _claim(conn, exists=True)
                        return GateDecision("dispatch", rec)
                    return GateDecision("hold", rec)
                if rec.state == "failed":
                    _claim(conn, exists=True)
                    return GateDecision("dispatch", rec)
                # attempting: reclaim only if the owning run is gone (R3 crash /
                # lease-takeover topology); a live owner holds.
                if self.is_stale_attempting(rec, now=now, ttl_s=ttl_s):
                    _claim(conn, exists=True)
                    return GateDecision("dispatch", rec)
                return GateDecision("hold", rec)

        return _retry_sqlite(_op)

    def intent_write(
        self,
        key: DedupKey,
        *,
        run_id: str | None = None,
        owner_pid: int | None = None,
        owner_run_id: str | None = None,
        owner_started_at: float | None = None,
        # Reserved for multi-account support (see DedupKey.account): persisted to
        # the dedup_keys column but deliberately NOT in _COLS/DedupRecord yet — no
        # reader until a second account per platform exists. All callers pass None.
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
        expect_from: tuple[str, ...] | None = None,
    ) -> None:
        """Move an existing key to a terminal/held state.

        Legal: ``attempting -> done|failed|uncertain`` and (adjudication)
        ``uncertain -> done|failed``. ``done``/``failed`` are terminal and not
        re-transitioned unless ``allow_from_terminal`` (used by adjudication's
        explicit override). Raises ``ValueError`` for an illegal transition or a
        missing row (call :meth:`intent_write` first).

        ``expect_from`` asserts the current state is one of the given states
        **inside the same transaction** as the write — used by adjudication to
        avoid a TOCTOU where a concurrent enforce run reclaims an ``uncertain``
        row to ``attempting`` between the caller's read and this write. A mismatch
        raises ``ValueError`` and the row is left untouched."""
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
                if expect_from is not None and current not in expect_from:
                    raise ValueError(
                        f"key {key.as_tuple()!r} is {current!r}, expected one of "
                        f"{expect_from!r} (concurrent change?)"
                    )
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

    def seed(
        self,
        key: DedupKey,
        state: State,
        *,
        live_url: str | None = None,
        verify_ok: bool | None = None,
    ) -> bool:
        """INSERT a backfill row in ``state``; **no-op if the key already exists**
        (``INSERT OR IGNORE`` — decision-preserving / INSERT-only). Returns True
        iff a row was inserted. Used by U6 backfill to seed already-live posts
        without ever overwriting a live run's record or an operator decision."""

        def _op() -> bool:
            with self.connect_immediate() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO dedup_keys "
                    "(platform, account, target_url, state, verify_ok, live_url, "
                    " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        key.platform,
                        key.account,
                        key.target_url,
                        state,
                        None if verify_ok is None else int(verify_ok),
                        live_url,
                        _now(),
                    ),
                )
                return cur.rowcount > 0

        return _retry_sqlite(_op)

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
