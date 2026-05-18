"""WebUI state persistence — Plan 2026-05-18-001 Unit 2.

Four module-level singletons replace the legacy ``_load_*`` / ``_save_*``
helpers that were inlined in ``webui.py``:

  - ``history_store``       — publish history list
  - ``profiles_store``      — campaign profile list
  - ``drafts_store``        — draft queue list (specialized with
                              ``get_item`` / ``update_item`` / ``delete_item``)
  - ``schedule_store``      — schedule settings dict

Each store has identical load/save semantics:
  - File missing → returns ``default_factory()``
  - File present + valid JSON → returns parsed value
  - ``save(value)`` atomically writes via temp-file rename
  - ``update(fn)`` runs ``load → fn → save`` under a per-store lock

This package will move to ``webui/store/`` in Unit 3 when ``webui.py`` is
split into a ``webui/`` package. Path-only rename; no API change planned.
"""

from __future__ import annotations

from pathlib import Path

from .base import JsonStore
from .drafts import DraftsStore

_CONFIG_DIR = Path.home() / ".config" / "backlink-publisher"

history_store: JsonStore = JsonStore(
    _CONFIG_DIR / "publish-history.json", default_factory=list,
)
profiles_store: JsonStore = JsonStore(
    _CONFIG_DIR / "campaign-profiles.json", default_factory=list,
)
drafts_store: DraftsStore = DraftsStore(
    _CONFIG_DIR / "draft-queue.json",
)
schedule_store: JsonStore = JsonStore(
    _CONFIG_DIR / "schedule-settings.json", default_factory=dict,
)


__all__ = [
    "JsonStore",
    "DraftsStore",
    "history_store",
    "profiles_store",
    "drafts_store",
    "schedule_store",
]
