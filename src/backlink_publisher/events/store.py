"""SQLite-backed event store for the read-side projection (events.db).

EventStore is a sibling of JsonStore (not a subclass): JsonStore's
contract is dict/list-shaped JSON files; events.db is schema-bound. The
projector (U4) translates JsonStore writes into EventStore appends.

Connection options (plan §U1 Approach):
- ``journal_mode = WAL`` — concurrent readers + single writer; the file
  layout adds ``events.db-wal`` and ``events.db-shm`` side files.
- ``synchronous = NORMAL`` — durability tradeoff acceptable for a
  rebuildable read-side projection (events.db is recoverable from JSON
  via ``bp-events-rebuild``).
- ``busy_timeout = 5000`` — 5s wait for a write lock before raising; the
  retry layer below adds another bounded backoff on transient errors.
- ``foreign_keys = ON`` — defensive even though v1 has no FK constraints.

File mode 0600, parent dir 0700; macOS Time Machine exclusion via xattr
attempted on first create (failure WARNs, never raises — U10 expands
coverage to ``.db-wal``/``.db-shm``/``persona.salt`` etc).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from . import kinds
from . import schema as _schema
from ._store_sqlite import (
    _BASE_BACKOFF_S,  # noqa: F401 — re-exported for test assertions
    _default_db_path,
    _get_read_connection,  # Stage 2.1: reader connection reuse
    _is_select_statement,
    _now_iso_utc,
    _pid_alive,
    _retry_sqlite,
    _set_backup_exclude_xattr,
    _tighten_wal_sidecars,
)

class EventStore:
    """Append-mostly SQLite store for projected events + articles.

    Construction does not open the file; the first ``connect()`` call
    creates the database (and applies the schema) if absent. Pass
    ``path=`` to override the default location for tests.
    """

    def __init__(
        self,
        *,
        path: Path | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.path: Path = path if path is not None else _default_db_path()
        self._sleep_fn = sleep_fn

    def _connect_raw(self) -> sqlite3.Connection:
        """Open a connection, apply PRAGMAs + schema + file hygiene, return it.

        Shared setup for ``connect`` and ``connect_immediate``. The schema
        upgrade is committed before returning so the caller's own
        transaction starts clean. Caller owns closing the connection.

        File hygiene: parent directory is tightened to 0o700 on first
        create regardless of any pre-existing mode (events.db sits next
        to ``persona.salt`` / ``token/``, and a wider parent leaks
        sensitive sibling filenames). The WAL and SHM side files are
        chmodded to 0o600 after their first appearance — SQLite creates
        them with the process umask (typically 0o644) which would
        otherwise expose uncheckpointed event payloads.
        """
        first_create = not self.path.exists()
        if first_create:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # mkdir(exist_ok=True) does NOT apply mode= to a pre-existing
            # directory. Tighten unconditionally on first create so the
            # parent does not leak siblings via 0o755.
            try:
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass

        conn = sqlite3.connect(str(self.path), timeout=5.0)
        # Apply PRAGMAs before touching tables — WAL mode in particular
        # must be set on a fresh connection.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA foreign_keys = ON")
        _schema.maybe_upgrade_schema(conn)
        conn.commit()

        if first_create:
            # Mode + xattr only on the first creation; subsequent
            # ``connect`` calls do not re-chmod (operator may have
            # widened the mode intentionally).
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            _set_backup_exclude_xattr(self.path)

        # WAL/SHM appear lazily on first write and inherit umask
        # rather than the .db file's mode. Tighten every connect so
        # post-checkpoint recreations stay locked down.
        _tighten_wal_sidecars(self.path)
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, apply PRAGMAs + schema, yield to caller.

        Acts as a transaction boundary: pending work is committed on
        normal exit, rolled back on exception, and the connection is
        always closed. Idempotent: schema upgrade runs every connect,
        but is itself a no-op when the version is current.

        Uses SQLite's default *deferred* isolation — the write lock is
        acquired lazily on the first DML. For a read-modify-write critical
        section that must not interleave with a concurrent writer, use
        ``connect_immediate`` instead.
        """
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
        """Like ``connect`` but opens the transaction with ``BEGIN IMMEDIATE``.

        Acquires SQLite's RESERVED write-lock at transaction *start* rather
        than lazily on first write, so a read-modify-write critical section
        cannot interleave with a concurrent writer's cursor RMW (Plan 006 /
        U1 single-flight). Commits on normal exit, rolls back on exception.

        Caller MUST NOT open a second writing connection in the same thread
        while this context is held — that self-deadlocks on the RESERVED
        lock (busy_timeout → ``OperationalError: database is locked``).
        """
        conn = self._connect_raw()
        # Take manual control of transactions so BEGIN IMMEDIATE is honored
        # (Python's default isolation issues a lazy deferred BEGIN).
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

    def append(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        target_url: str | None = None,
        host: str | None = None,
        article_id: int | None = None,
        ts_raw: str | None = None,
        ts_utc: str | None = None,
        conn: sqlite3.Connection | None = None,
        pending_quarantines: list[dict[str, Any]] | None = None,
    ) -> int:
        """INSERT one row into ``events`` and return its id.

        ``payload`` is JSON-serialised with ``sort_keys=True`` so a given
        logical event always produces the same byte string regardless of
        dict construction order — eases round-trip tests and diff
        tooling.

        ``ts_raw`` defaults to ``ts_utc``; both default to "now" UTC. The
        projector (U4) supplies the source's original timestamp.

        ``conn`` lets the caller share a transaction across multiple
        appends / ``add_article`` calls — useful for projector reducers
        that must atomically emit an event plus its article row. When
        ``conn`` is ``None`` a private connection is opened, used, and
        committed; callers are then *not* protected against partial
        writes across multiple operations.

        R9 required-field floor (see ``kinds.REQUIRED_FIELDS``): a payload
        missing a floor field for its ``kind`` is **quarantined for triage
        instead of written** — a malformed event must not enter events.db as
        if it were truthful. The method then returns ``-1`` (no row inserted).
        Because ``quarantine()`` always opens its own private connection (it
        must never share a reducer ``conn`` — a rollback would discard the
        quarantine row), the miss is handled by caller context:

        * ``pending_quarantines`` provided (projector reducer holding the WAL
          write lock) → the record is appended to that sink and the reducer
          flushes it via ``_write_quarantines`` **after** its transaction
          commits (writing now would deadlock);
        * ``conn`` is ``None`` (direct caller, e.g. image_gen) → quarantined
          immediately on a private connection (safe; no held lock);
        * ``conn`` set but no sink → ``ValueError`` (a misuse guard: we can
          neither quarantine inline nor share the conn safely).
        """
        if ts_utc is None:
            ts_utc = _now_iso_utc()
        if ts_raw is None:
            ts_raw = ts_utc

        missing = kinds.missing_required_fields(kind, payload)
        if missing:
            record = self._missing_field_record(
                kind, payload, missing, run_id=run_id, target_url=target_url
            )
            if pending_quarantines is not None:
                pending_quarantines.append(record)
            elif conn is not None:
                raise ValueError(
                    f"append({kind!r}) on a shared connection is missing "
                    f"required field(s) {sorted(missing)} but no "
                    "pending_quarantines sink was provided to defer the "
                    "quarantine write safely"
                )
            else:
                self.quarantine(**record)
            return -1  # sentinel: payload quarantined, no event row written

        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        params = (
            ts_raw, ts_utc, run_id, kind, target_url, host,
            article_id, payload_json,
        )
        sql = (
            "INSERT INTO events "
            "(ts_raw, ts_utc, run_id, kind, target_url, host, "
            " article_id, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )

        if conn is not None:
            cursor = conn.execute(sql, params)
            row_id = cursor.lastrowid
            assert row_id is not None
            return int(row_id)

        def _op() -> int:
            with self.connect() as own_conn:
                cursor = own_conn.execute(sql, params)
                own_conn.commit()
                row_id = cursor.lastrowid
                assert row_id is not None
                return int(row_id)

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def add_article(
        self,
        article: dict[str, Any],
        *,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        """INSERT one row into ``articles`` and return its article_id.

        Accepted keys: ``body``, ``anchors_json``, ``target_urls_json``,
        ``lang``, ``host``, ``live_url``, ``published_at_raw``,
        ``published_at_utc``, ``run_id``. Unknown keys raise
        ``KeyError`` so a typo doesn't silently drop data — the
        whitelist-vs-shape distinction matters more for the events
        table (U2 enforces it there).

        Raises ``sqlite3.IntegrityError`` if ``live_url`` already exists.
        Callers (projector reducers, U4) catch and route to dedup.

        ``conn`` shares a transaction with the caller (see ``append``).
        """
        allowed = {
            "body", "anchors_json", "target_urls_json", "lang", "host",
            "live_url", "published_at_raw", "published_at_utc", "run_id",
        }
        unknown = set(article) - allowed
        if unknown:
            raise KeyError(f"unknown article columns: {sorted(unknown)}")

        # SQLite default for missing TEXT columns is NULL; the schema
        # provides DEFAULT '[]' for anchors_json / target_urls_json.
        cols = list(article.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        sql = f"INSERT INTO articles ({col_list}) VALUES ({placeholders})"
        params = tuple(article[c] for c in cols)

        if conn is not None:
            cursor = conn.execute(sql, params)
            row_id = cursor.lastrowid
            assert row_id is not None
            return int(row_id)

        def _op() -> int:
            with self.connect() as own_conn:
                cursor = own_conn.execute(sql, params)
                own_conn.commit()
                row_id = cursor.lastrowid
                assert row_id is not None
                return int(row_id)

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def query(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[sqlite3.Row]:
        """Thin SELECT wrapper for CLI consumers. Returns rows as a list.

        Enforces a SELECT-only contract at runtime — refuses any
        statement that doesn't start with ``SELECT`` so a downstream CLI
        (U7/U8) accidentally f-stringing user input into a DML statement
        cannot DROP / ATTACH / INSERT through this entry point. For
        admin-style mutations, use ``connect()`` directly and own the
        risk explicitly.

        Stage 2.1: Uses cached reader connection when BACKLINK_EVENTSTORE_REUSE_CONN=1.
        """
        if not _is_select_statement(sql):
            raise ValueError(
                "EventStore.query() is SELECT-only; use connect() for DML."
            )

        cached_conn = _get_read_connection()  # type: ignore[name-defined]
        if cached_conn is not None:
            try:
                return list(cached_conn.execute(sql, params))
            except Exception:  # noqa: BLE001
                pass  # Fall through to fresh connection on error

        def _op() -> list[sqlite3.Row]:
            with self.connect() as conn:
                conn.row_factory = sqlite3.Row
                return list(conn.execute(sql, params))

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    @staticmethod
    def _missing_field_record(
        kind: str,
        payload: dict[str, Any],
        missing: frozenset[str],
        *,
        run_id: str | None,
        target_url: str | None,
    ) -> dict[str, Any]:
        """Build ``quarantine()`` kwargs for an R9 required-field miss.

        ``record_identity`` falls back ``target_url`` → payload ``draft_id`` →
        ``None``. A null identity is tolerated: ``quarantine()`` folds NULLs
        into the dedup key, so a repeated code-level miss (e.g. an image_gen
        caller with no run id) collapses to one row rather than flooding.
        ``source`` is the kind itself so quarantine_log triage shows which
        writer drifted; ``failure_type="missing_field"`` discriminates these
        from the projector's ``"unmapped_status"`` rows (R6 vs R9).
        """
        identity = target_url or payload.get("draft_id")
        return {
            "reason": f"missing_field: {kind} missing {sorted(missing)}",
            "failure_type": "missing_field",
            "source": kind,
            "run_id": run_id,
            "source_status": None,
            "record_identity": identity if isinstance(identity, str) else None,
            "raw_payload": dict(payload),
        }

    def quarantine(
        self,
        *,
        reason: str,
        failure_type: str,
        source: str | None = None,
        run_id: str | None = None,
        source_status: str | None = None,
        record_identity: str | None = None,
        raw_payload: dict[str, Any] | None = None,
    ) -> bool:
        """Record a record the projector could not classify, idempotently.

        Always opens its own **private, committed connection** — never a
        caller-supplied reducer ``conn`` — so a later reducer rollback cannot
        discard the quarantine row (that would recreate the silent-drop class
        this exists to prevent). An extra quarantine row on a retried record is
        benign (deduped); a lost one is the failure under attack.

        Idempotent via a single non-null ``dedup_key`` hashed over
        ``(run_id, source, source_status, record_identity)`` with NULLs folded
        to a fixed token, plus ``INSERT OR IGNORE``. Re-projecting the same
        record does not duplicate. ``record_identity`` must be per-record
        granular (e.g. checkpoint item_id, history/drafts row_id) so two
        *distinct* unmapped records in one run produce two rows.

        Returns True if a new row was written, False if it was a dedup no-op.
        ``failure_type`` (e.g. ``"unmapped_status"``) is stored inside
        ``raw_payload_json`` so R9 (P2) can widen the set with ``"missing_field"``.
        """
        parts = [run_id or "-", source or "-", source_status or "-", record_identity or "-"]
        dedup_key = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
        payload = {
            "failure_type": failure_type,
            "source_status": source_status,
            "record_identity": record_identity,
            **(raw_payload or {}),
        }
        # ``default=str`` so the quarantine row is ALWAYS recorded: this is the
        # safety net, and it must never itself fail to write because a caller's
        # raw_payload held a non-JSON-serialisable value (Decimal, datetime, …).
        # Such a value degrades to its str() for triage rather than raising —
        # which _write_quarantines would otherwise log-and-skip, silently losing
        # the very signal R6/R9 exist to surface.
        payload_json = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, default=str
        )
        ts_utc = _now_iso_utc()

        def _op() -> bool:
            with self.connect() as conn:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO quarantine_log "
                    "(ts_utc, source, run_id, reason, raw_payload_json, dedup_key) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts_utc, source, run_id, reason, payload_json, dedup_key),
                )
                return cursor.rowcount > 0

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def acquire_lease(self, target_host: str, owner_pid: int, ttl_seconds: int = 3600) -> bool:
        """Atomically acquire a lease on target_host.

        Returns True if acquired, False otherwise. Takeover triggers when
        the lease is expired, owned by the caller, or held by a dead PID
        (crashed publish that bypassed ``atexit`` cleanup — see
        ``cli/_publish_helpers._release_acquired_leases``).
        """
        now = _now_iso_utc()
        from datetime import datetime, timedelta, timezone
        expire = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()

        def _op() -> bool:
            with self.connect() as conn:
                cursor = conn.execute(
                    "SELECT owner_pid, expire_at FROM publish_leases WHERE target_host = ?",
                    (target_host,)
                )
                row = cursor.fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO publish_leases (target_host, owner_pid, started_at, expire_at) VALUES (?, ?, ?, ?)",
                        (target_host, owner_pid, now, expire)
                    )
                    return True

                curr_owner, curr_expire = row
                if (
                    curr_expire < now
                    or curr_owner == owner_pid
                    or not _pid_alive(curr_owner)
                ):
                    conn.execute(
                        "UPDATE publish_leases SET owner_pid = ?, started_at = ?, expire_at = ? WHERE target_host = ?",
                        (owner_pid, now, expire, target_host)
                    )
                    return True
                return False

        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def release_lease(self, target_host: str, owner_pid: int) -> None:
        """Release the lease on target_host if owned by owner_pid."""
        def _op() -> None:
            with self.connect() as conn:
                conn.execute(
                    "DELETE FROM publish_leases WHERE target_host = ? AND owner_pid = ?",
                    (target_host, owner_pid)
                )
        _retry_sqlite(_op, sleep_fn=self._sleep_fn)

    def get_lease(self, target_host: str) -> dict[str, Any] | None:
        """Get lease details for target_host."""
        def _op() -> dict[str, Any] | None:
            with self.connect() as conn:
                cursor = conn.execute(
                    "SELECT target_host, owner_pid, started_at, expire_at FROM publish_leases WHERE target_host = ?",
                    (target_host,)
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                return {
                    "target_host": row[0],
                    "owner_pid": row[1],
                    "started_at": row[2],
                    "expire_at": row[3],
                }
        return _retry_sqlite(_op, sleep_fn=self._sleep_fn)
