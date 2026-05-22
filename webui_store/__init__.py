"""WebUI state persistence — Plan 2026-05-18-001 Unit 2.

Six ``_LazyStore`` wrappers replace the eager module-level singletons.
Each store resolves its backing-file path from ``_config_dir()`` on
first access rather than at import time.

Plan 2026-05-22 P7 C1: ``_refresh_paths()`` is now a no-op (stores are
lazy) and is retained only for backward compatibility. New code should
access stores through ``current_app.extensions['webui_stores']`` (see
``registry.py``) or just import the module-level names below — they
work identically.
"""

from __future__ import annotations

from pathlib import Path

from backlink_publisher.config.loader import _resolve_config_dir

from .base import JsonStore, Store, _LazyStore
from .channel_status import channel_status_store
from .drafts import DraftsStore
from .history import HistoryStore
from .queue_store import QueueStore


def _store_path(filename: str) -> Path:
    """Resolve a store file path under the current config dir."""
    return _resolve_config_dir() / filename


# Singleton bindings — lazily resolved on first access so test fixtures
# that set BACKLINK_PUBLISHER_CONFIG_DIR before accessing these don't
# need _refresh_paths().
history_store = _LazyStore(
    lambda: HistoryStore(_store_path("publish-history.json"))
)
profiles_store = _LazyStore(
    lambda: JsonStore(
        _store_path("campaign-profiles.json"), default_factory=list,
    )
)
drafts_store = _LazyStore(
    lambda: DraftsStore(_store_path("draft-queue.json"))
)
schedule_store = _LazyStore(
    lambda: JsonStore(
        _store_path("schedule-settings.json"), default_factory=dict,
    )
)
queue_store = _LazyStore(
    lambda: QueueStore(_store_path("publish-queue.json"), default_factory=list)
)


def _refresh_paths() -> None:
    """Rebind every lazy store so the next access resolves a fresh path.

    Test fixtures that mutate ``BACKLINK_PUBLISHER_CONFIG_DIR``
    mid-session (e.g. ``test_config_dir_falls_back_when_env_var_unset``)
    must call this to discard previously-cached store instances and
    have them re-resolve from the updated env var.
    """
    for store in (history_store, profiles_store, drafts_store,
                  schedule_store, queue_store, channel_status_store):
        store.reset()


__all__ = [
    "Store",
    "JsonStore",
    "_LazyStore",
    "_store_path",
    "DraftsStore",
    "HistoryStore",
    "QueueStore",
    "history_store",
    "profiles_store",
    "drafts_store",
    "schedule_store",
    "queue_store",
    "channel_status_store",
    "_refresh_paths",
]
