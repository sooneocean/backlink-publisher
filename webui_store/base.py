"""JSON-backed single-process store with atomic writes."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable


class JsonStore:
    """Single-process JSON-backed store.

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

    # ── Core API ───────────────────────────────────────────────────────

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)

    def update(self, fn: Callable[[Any], Any]) -> Any:
        with self._lock:
            current = self.load()
            new_value = fn(current)
            self.save(new_value)
            return new_value
