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

import time
from pathlib import Path

from backlink_publisher.config.loader import _resolve_config_dir

from .base import JsonStore, Store, _LazyStore
from .channel_status import channel_status_store
from .drafts import DraftsStore
from .history import HistoryStore
from .queue_store import QueueStore
from .score_store import ScoreStore
from .seen_urls_store import SeenUrlsStore
from .wizard_config_store import WizardConfigStore


def _store_path(filename: str) -> Path:
    """Resolve a store file path under the current config dir."""
    return _resolve_config_dir() / filename


# ── Cleanup hook ────────────────────────────────────────────────────────

#: Default TTL in seconds after which an idle store is considered cold.
_DEFAULT_STORE_TTL_S: float = 1800.0  # 30 minutes

#: Registry of all lazy stores for cleanup and test introspection.
_ALL_LAZY_STORES: dict[str, _LazyStore] = {}


def _register(name: str, store: _LazyStore) -> _LazyStore:
    _ALL_LAZY_STORES[name] = store
    return store


def cleanup_expired_stores(ttl_s: float = _DEFAULT_STORE_TTL_S) -> list[str]:
    """Reset lazy stores whose underlying real store has been idle past ``ttl_s``.

    Returns names of reset stores (empty list = nothing to clean). Designed
    to be called from an APScheduler job in ``webui_app.scheduler``.
    """
    now = time.time()
    cleaned: list[str] = []
    for name, store in _ALL_LAZY_STORES.items():
        try:
            instance = store._instance
            if instance is not None and (now - instance.last_accessed_at) > ttl_s:
                store.reset()
                cleaned.append(name)
        except Exception:
            pass  # Best-effort cleanup
    return cleaned


# ── Singleton bindings ──────────────────────────────────────────────────

history_store = _register(
    "history",
    _LazyStore(lambda: HistoryStore(_store_path("publish-history.json"))),
)
profiles_store = _register(
    "profiles",
    _LazyStore(lambda: JsonStore(
        _store_path("campaign-profiles.json"), default_factory=list,
    )),
)
drafts_store = _register(
    "drafts",
    _LazyStore(lambda: DraftsStore(_store_path("draft-queue.json"))),
)
schedule_store = _register(
    "schedule",
    _LazyStore(lambda: JsonStore(
        _store_path("schedule-settings.json"), default_factory=dict,
    )),
)
queue_store = _register(
    "queue",
    _LazyStore(lambda: QueueStore(
        _store_path("publish-queue.json"), default_factory=list,
    )),
)
score_store = _register(
    "score",
    _LazyStore(lambda: ScoreStore(_store_path("score-store.json"))),
)
seen_urls_store = _register(
    "seen_urls",
    _LazyStore(lambda: SeenUrlsStore(_store_path("seen-urls.json"))),
)
wizard_config_store = _register(
    "wizard_config",
    _LazyStore(lambda: WizardConfigStore(_store_path("wizard-config.json"))),
)

# channel_status_store is a module-level singleton from its own submodule;
# it doesn't go through _LazyStore so we register it manually after creation.
try:
    _register("channel_status", channel_status_store)  # type: ignore[arg-type]
except Exception:
    pass  # channel_status_store might not be _LazyStore


def _refresh_paths() -> None:
    """Rebind every lazy store so the next access resolves a fresh path.

    Test fixtures that mutate ``BACKLINK_PUBLISHER_CONFIG_DIR``
    mid-session (e.g. ``test_config_dir_falls_back_when_env_var_unset``)
    must call this to discard previously-cached store instances and
    have them re-resolve from the updated env var.
    """
    for store in (history_store, profiles_store, drafts_store,
                  schedule_store, queue_store, channel_status_store,
                  score_store, seen_urls_store, wizard_config_store):
        store.reset()


__all__ = [
    "Store",
    "JsonStore",
    "_LazyStore",
    "_store_path",
    "DraftsStore",
    "HistoryStore",
    "QueueStore",
    "ScoreStore",
    "SeenUrlsStore",
    "WizardConfigStore",
    "history_store",
    "profiles_store",
    "drafts_store",
    "schedule_store",
    "queue_store",
    "channel_status_store",
    "score_store",
    "seen_urls_store",
    "wizard_config_store",
    "_refresh_paths",
    "cleanup_expired_stores",
]