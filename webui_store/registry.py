"""WebUIStores — Flask app-context store registry (Plan 2026-05-22 P7 C1).

Replaces ad-hoc module-level singleton imports in WebUI code with a
proper Flask extension pattern::

    from flask import current_app

    stores = current_app.extensions['webui_stores']
    history = stores.history.load()

Module-level singletons in ``webui_store/__init__.py`` are kept for
CLI code (``publish_backlinks``, ``bind-channel``, …) that runs outside
Flask context.  They share the same lazy-initialisation pattern.
"""

from __future__ import annotations

from flask import Flask

from backlink_publisher.config.loader import _config_dir

from .base import JsonStore
from .drafts import DraftsStore
from .history import HistoryStore
from .queue_store import QueueStore
from .score_store import ScoreStore
from .seen_urls_store import SeenUrlsStore
from .wizard_config_store import WizardConfigStore


class WebUIStores:
    """Lazy-initialised container for all WebUI state stores.

    Each store is created on first property access so the config dir
    (resolved from ``BACKLINK_PUBLISHER_CONFIG_DIR``) is read no earlier
    than ``init_app()`` time.
    """

    def __init__(self) -> None:
        self._app: Flask | None = None
        self._history: HistoryStore | None = None
        self._profiles: JsonStore | None = None
        self._drafts: DraftsStore | None = None
        self._schedule: JsonStore | None = None
        self._queue: QueueStore | None = None
        self._score: ScoreStore | None = None
        self._seen_urls: SeenUrlsStore | None = None
        self._wizard_config: WizardConfigStore | None = None

    def init_app(self, app: Flask) -> None:
        self._app = app
        app.extensions['webui_stores'] = self

    # ── Store properties (lazily initialised) ─────────────────────────

    @property
    def history(self) -> HistoryStore:
        if self._history is None:
            self._history = HistoryStore(_config_dir() / "publish-history.json")
        return self._history

    @property
    def profiles(self) -> JsonStore:
        if self._profiles is None:
            self._profiles = JsonStore(
                _config_dir() / "campaign-profiles.json",
                default_factory=list,
            )
        return self._profiles

    @property
    def drafts(self) -> DraftsStore:
        if self._drafts is None:
            self._drafts = DraftsStore(_config_dir() / "draft-queue.json")
        return self._drafts

    @property
    def schedule(self) -> JsonStore:
        if self._schedule is None:
            self._schedule = JsonStore(
                _config_dir() / "schedule-settings.json",
                default_factory=dict,
            )
        return self._schedule

    @property
    def queue(self) -> QueueStore:
        if self._queue is None:
            self._queue = QueueStore(
                _config_dir() / "publish-queue.json",
                default_factory=list,
            )
        return self._queue

    @property
    def score(self) -> ScoreStore:
        if self._score is None:
            self._score = ScoreStore(_config_dir() / "score-store.json")
        return self._score

    @property
    def seen_urls(self) -> SeenUrlsStore:
        if self._seen_urls is None:
            self._seen_urls = SeenUrlsStore(_config_dir() / "seen-urls.json")
        return self._seen_urls

    @property
    def wizard_config(self) -> WizardConfigStore:
        if self._wizard_config is None:
            self._wizard_config = WizardConfigStore(_config_dir() / "wizard-config.json")
        return self._wizard_config
