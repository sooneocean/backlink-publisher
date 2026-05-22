"""JSON-backed single-process store with atomic writes.

Plan 2026-05-18-001 Unit 8 formalised the store contract as the ``Store``
``typing.Protocol`` below. ``JsonStore`` is the current implementation.

The ``_LazyStore`` proxy (Plan 2026-05-22 P7 C1) wraps a store factory to
defer path resolution to first access. This eliminates the need for
``_refresh_paths()`` — tests set the env var in an autouse fixture and
the real store (with resolved path) is created lazily when first accessed.
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


class _LazyStore:
    """Transparent lazy-loading proxy for a ``Store``.

    Defers the actual store construction (including path resolution)
    from import time to first attribute access.  Every ``Store`` method
    (``load``, ``save``, ``update``) plus ``path`` and any subclass-
    specific method (``get_item``, ``bulk_delete``, …) is forwarded to
    the real instance transparently.

    Usage::

        # Before (eager — path resolved at import time)
        history_store = HistoryStore(_store_path("publish-history.json"))

        # After (lazy — path resolved when first method is called)
        history_store = _LazyStore(
            lambda: HistoryStore(_store_path("publish-history.json"))
        )

    Tests that set ``BACKLINK_PUBLISHER_CONFIG_DIR`` in an autouse fixture
    no longer need ``_refresh_paths()`` — the env var is already set by
    the time any test code accesses the store.
    """

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._instance: Any = None

    def _real(self) -> Any:
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __setattr__(self, name: str, value: Any) -> None:
        if name in ("_factory", "_instance"):
            super().__setattr__(name, value)
        elif name in ("_path",):
            setattr(self._real(), name, value)
        elif name == "path":
            self._real().path = value
        else:
            super().__setattr__(name, value)

    # ── Core Store protocol methods ───────────────────────────────────

    def load(self) -> Any:
        return self._real().load()

    def save(self, value: Any) -> None:
        self._real().save(value)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        return self._real().update(fn)

    # ── Path property (read/write) ────────────────────────────────────

    @property
    def path(self):
        return self._real().path

    @path.setter
    def path(self, value) -> None:
        self._real().path = value

    # ── Fallback for subclass-specific methods (get_item, bulk_delete, …)

    def __getattr__(self, name: str):
        return getattr(self._real(), name)

    def reset(self) -> None:
        """Discard the cached real store so the next access creates a new one."""
        self._instance = None
