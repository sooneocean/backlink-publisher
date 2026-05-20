"""JSON-backed single-process store with atomic writes.

Plan 2026-05-18-001 Unit 8 formalised the store contract as the ``Store``
``typing.Protocol`` below. ``JsonStore`` is the current implementation;
adding a SQLite (or any other) backend is a matter of writing a class
that satisfies the same three-method contract and swapping the
singletons in ``webui_store/__init__.py``. The dispatcher, route layer,
service layer, and tests all depend only on the protocol, so the swap
needs no code changes outside this package.

Adding a new backend (worked example for SQLite)
------------------------------------------------

  # webui_store/sqlite.py
  import sqlite3
  from typing import Any, Callable
  from .base import Store  # for documentation only — Protocol is structural

  class SqliteStore:
      \"\"\"SQLite-backed Store implementation.\"\"\"

      def __init__(self, path, *, default_factory):
          self._conn = sqlite3.connect(path, isolation_level=None)
          self._default = default_factory
          self._conn.execute(
              \"CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB)\"
          )

      def load(self) -> Any: ...
      def save(self, value: Any) -> None: ...
      def update(self, fn: Callable[[Any], Any]) -> Any: ...

  # webui_store/__init__.py
  -from .base import JsonStore
  +from .sqlite import SqliteStore
  -history_store = JsonStore(_CONFIG_DIR / \"publish-history.json\", default_factory=list)
  +history_store = SqliteStore(_CONFIG_DIR / \"publish-history.sqlite\", default_factory=list)

Nothing else in ``webui_app/`` changes. The route layer's
``history_store.update(...)`` calls don't care whether they're writing
JSON or SQL — that's the whole point of the protocol.

Plan Scope Boundaries reminder: the SQLite implementation itself is
explicitly OUT of scope for this plan. We only declare the seam.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class Store(Protocol):
    """Protocol that every backlink-publisher webui state backend satisfies.

    Three methods, all atomic with respect to in-process callers:

    - ``load()``: return the persisted value or ``default_factory()`` if
      the backing storage is missing / corrupt (silent fall-through to
      default — see ``JsonStore.load`` for the historical reasoning).
    - ``save(value)``: replace the persisted value. Must be atomic with
      respect to concurrent readers (i.e. a reader sees either the old
      value or the new value, never a partial write).
    - ``update(fn)``: ``load → fn → save`` under a per-store lock. ``fn``
      receives the current value and returns the new one. The new value
      is also returned to the caller for chaining.

    Structural typing (``@runtime_checkable``): any object exposing these
    three methods with compatible signatures satisfies the protocol —
    explicit inheritance is not required. ``isinstance(x, Store)`` works
    at runtime for diagnostics.

    Cross-process synchronisation is intentionally out of scope; see the
    module docstring for the Plan reference. A future ``SqliteStore``
    could elevate this if multi-process editing becomes a real need.
    """

    def load(self) -> Any: ...

    def save(self, value: Any) -> None: ...

    def update(self, fn: Callable[[Any], Any]) -> Any: ...


class JsonStore:
    """Current implementation of ``Store`` — single-process JSON-backed.

    - ``load()``           — returns parsed JSON, or ``default_factory()``
                             when the file is missing.
    - ``save(value)``      — atomic write via temp-file rename.
    - ``update(fn)``       — atomic load → fn → save under a per-store
                             lock. ``fn`` receives the current value and
                             returns the new value to persist. The new
                             value is also returned to the caller.

    The lock guards in-process concurrency only. Cross-process writers
    are not protected — single-user local-first deployment assumption
    inherited from ``webui.py`` and explicitly out-of-scope for Plan
    2026-05-18-001 (see Scope Boundaries).

    Satisfies ``Store`` structurally — no explicit inheritance needed,
    but ``isinstance(JsonStore(...), Store)`` returns True at runtime.
    """

    __slots__ = ("_path", "_default_factory", "_lock")

    def __init__(self, path: Path, *, default_factory: Callable[[], Any]) -> None:
        self._path = path
        self._default_factory = default_factory
        self._lock = threading.Lock()

    # ── Read-only accessors (test-friendly) ────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    @path.setter
    def path(self, new_path: Path) -> None:
        # Tests may rebind to tmp_path; production code never reassigns.
        self._path = new_path

    # ── Core API (satisfies Store protocol) ────────────────────────────

    def load(self) -> Any:
        if not self._path.exists():
            return self._default_factory()
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Match the legacy behaviour (silent fall-through to default)
            # that the existing webui ``_load_history`` / ``_load_profiles``
            # exhibited. See test_webui_store.py for the lock-in test.
            return self._default_factory()

    def save(self, value: Any) -> None:
        from backlink_publisher.persistence.safe_write import atomic_write
        text = json.dumps(value, ensure_ascii=False, indent=2)
        atomic_write(self._path, text)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        with self._lock:
            current = self.load()
            new_value = fn(current)
            self.save(new_value)
            return new_value
