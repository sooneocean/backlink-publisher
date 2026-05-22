"""Regression tests: webui_store stores honour BACKLINK_PUBLISHER_CONFIG_DIR.

History: 2026-05-19 PR #87 verification + parallel `bp-cbu5-ui/` pytest run
both wiped the operator's real config files.  Root cause: module-level
singletons captured paths at import time.

Plan 2026-05-22 P7 C1: all module-level stores are now ``_LazyStore`` proxies
that defer path resolution to first access.  ``_refresh_paths()`` is a no-op.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from webui_store.base import JsonStore, _LazyStore
from webui_store import _store_path


def test_singleton_paths_resolve_to_isolated_dir():
    """After ``_refresh_paths()``, every singleton points inside the
    currently-active config dir — not the operator's real ``~/.config/``.

    Calls ``_refresh_paths()`` explicitly to defend against other tests
    that mutate env+singletons via function-scope monkeypatch without
    restoring state (their tmp dirs vanish on teardown, leaving singletons
    pointing at deleted paths). The conftest session fixture sets env at
    session start; this test asserts the contract holds after a fresh
    refresh against current env.
    """
    from webui_store import _refresh_paths
    import webui_store

    _refresh_paths()

    cfg_env = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR", "")
    assert cfg_env, "conftest fixture should have set this env"

    isolated = Path(cfg_env).resolve()
    real_prod = (Path.home() / ".config" / "backlink-publisher").resolve()

    from backlink_publisher.config.loader import _config_dir
    assert str(_config_dir()).startswith(str(isolated)), (
        f"_config_dir() returned {_config_dir()}, expected under {isolated}"
    )

    for store_name in (
        "history_store",
        "drafts_store",
        "profiles_store",
        "schedule_store",
        "queue_store",
        "channel_status_store",
    ):
        store = getattr(webui_store, store_name)
        store_path = Path(store.path).resolve()
        # The store path must be inside the isolated tmp dir
        assert store_path.is_relative_to(isolated), (
            f"{store_name} path {store_path} not inside isolated dir {isolated}"
        )
        # And must NOT be inside the operator's real ~/.config/
        assert not store_path.is_relative_to(real_prod), (
            f"{store_name} path {store_path} leaked into prod {real_prod}"
        )


def test_writes_land_in_isolated_dir_not_prod():
    """Writing through history_store / drafts_store touches the isolated
    tmp file — never the operator's real publish-history.json / draft-queue.json."""
    from webui_store import _refresh_paths, drafts_store, history_store

    _refresh_paths()

    real_history = Path.home() / ".config" / "backlink-publisher" / "publish-history.json"
    real_drafts = Path.home() / ".config" / "backlink-publisher" / "draft-queue.json"

    # Capture pre-write mtimes if files exist (operator may or may not have them)
    pre_history_mtime = real_history.stat().st_mtime if real_history.exists() else None
    pre_drafts_mtime = real_drafts.stat().st_mtime if real_drafts.exists() else None

    history_store.update(lambda lst: lst + [{"id": "iso-test", "status": "published"}])
    drafts_store.update(lambda lst: lst + [{"id": "iso-draft", "status": "drafted"}])

    # Prod files must be untouched (mtime unchanged or still missing)
    post_history_mtime = real_history.stat().st_mtime if real_history.exists() else None
    post_drafts_mtime = real_drafts.stat().st_mtime if real_drafts.exists() else None
    assert pre_history_mtime == post_history_mtime, (
        "history_store write leaked into prod publish-history.json"
    )
    assert pre_drafts_mtime == post_drafts_mtime, (
        "drafts_store write leaked into prod draft-queue.json"
    )

    # The write must be visible via the singleton's own path
    isolated_history = Path(history_store.path)
    assert isolated_history.exists(), "isolated history file was not written"
    assert any(r.get("id") == "iso-test" for r in history_store.load())

    isolated_drafts = Path(drafts_store.path)
    assert isolated_drafts.exists(), "isolated drafts file was not written"
    assert any(r.get("id") == "iso-draft" for r in drafts_store.load())


def test_lazy_store_picks_up_env_on_first_access(tmp_path, monkeypatch):
    """A ``_LazyStore`` resolves its path from the env var at the moment
    of first access.  Mid-session env changes do NOT rebind — the path
    is fixed once the real store is created."""
    fresh_store = _LazyStore(
        lambda: JsonStore(
            _store_path("publish-history.json"), default_factory=list,
        )
    )

    new_dir = tmp_path / "fresh-config"
    new_dir.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(new_dir))

    expected = (new_dir / "publish-history.json").resolve()
    assert Path(fresh_store.path).resolve() == expected

    fresh_store.update(lambda lst: lst + [{"id": "lazy-test"}])
    assert expected.exists()
