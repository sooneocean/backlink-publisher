"""Tests for webui_store — Plan 2026-05-18-001 Unit 2.

Locks in the contract behind the JsonStore abstraction: atomic save,
default-on-missing-file, idempotent update under concurrent writers,
and DraftsStore item-level helpers.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from webui_store import DraftsStore, JsonStore


# ── JsonStore basic API ──────────────────────────────────────────────────────


class TestJsonStoreLoad:
    def test_returns_default_when_file_missing(self, tmp_path):
        store = JsonStore(tmp_path / "missing.json", default_factory=list)
        assert store.load() == []

    def test_default_factory_called_each_time_on_missing(self, tmp_path):
        """Defensive: factory must be called fresh so callers can mutate."""
        store = JsonStore(tmp_path / "missing.json", default_factory=dict)
        a = store.load()
        b = store.load()
        assert a == b == {}
        assert a is not b  # distinct instances

    def test_returns_parsed_json_when_present(self, tmp_path):
        path = tmp_path / "x.json"
        path.write_text(json.dumps([{"id": "x"}]), encoding="utf-8")
        store = JsonStore(path, default_factory=list)
        assert store.load() == [{"id": "x"}]

    def test_returns_default_when_file_is_corrupted(self, tmp_path):
        """Matches legacy ``_load_history`` silent-fall-through behaviour."""
        path = tmp_path / "broken.json"
        path.write_text("not json {", encoding="utf-8")
        store = JsonStore(path, default_factory=list)
        assert store.load() == []

    def test_returns_default_when_file_is_empty(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        store = JsonStore(path, default_factory=list)
        assert store.load() == []


class TestJsonStoreSave:
    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "file.json"
        store = JsonStore(path, default_factory=list)
        store.save([1, 2, 3])
        assert path.read_text(encoding="utf-8")
        assert path.parent.exists()

    def test_uses_atomic_rename(self, tmp_path):
        """tmp suffix must not remain after save."""
        path = tmp_path / "x.json"
        store = JsonStore(path, default_factory=list)
        store.save([{"id": "y"}])
        # No stray .tmp left behind
        stray = list(tmp_path.glob("*.tmp"))
        assert stray == []
        # Content readable
        assert json.loads(path.read_text(encoding="utf-8")) == [{"id": "y"}]

    def test_save_round_trip(self, tmp_path):
        path = tmp_path / "x.json"
        store = JsonStore(path, default_factory=dict)
        store.save({"a": 1, "b": [2, 3]})
        assert store.load() == {"a": 1, "b": [2, 3]}

    def test_unicode_round_trip(self, tmp_path):
        path = tmp_path / "x.json"
        store = JsonStore(path, default_factory=list)
        store.save([{"title": "中文测试 🚀"}])
        assert store.load() == [{"title": "中文测试 🚀"}]


class TestJsonStoreUpdate:
    def test_update_persists_returned_value(self, tmp_path):
        store = JsonStore(tmp_path / "x.json", default_factory=list)
        result = store.update(lambda xs: xs + ["new"])
        assert result == ["new"]
        assert store.load() == ["new"]

    def test_update_returns_new_value(self, tmp_path):
        store = JsonStore(tmp_path / "x.json", default_factory=list)
        store.save([1, 2])
        result = store.update(lambda xs: xs + [3])
        assert result == [1, 2, 3]

    def test_update_serializes_concurrent_writers(self, tmp_path):
        """Two threads racing must produce a final state reflecting both
        writes, not either-or. Locks-in the per-store mutex."""
        store = JsonStore(tmp_path / "x.json", default_factory=list)
        store.save([])

        n = 20

        def worker(tag: str):
            for _ in range(n):
                store.update(lambda xs: xs + [tag])

        t1 = threading.Thread(target=worker, args=("a",))
        t2 = threading.Thread(target=worker, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        final = store.load()
        assert len(final) == 2 * n
        assert final.count("a") == n
        assert final.count("b") == n


# ── Path mutability for tests ────────────────────────────────────────────────


class TestPathSetter:
    def test_path_setter_redirects_subsequent_io(self, tmp_path):
        original = tmp_path / "a.json"
        redirected = tmp_path / "b.json"
        store = JsonStore(original, default_factory=list)
        store.save([1])

        store.path = redirected
        assert store.load() == []  # b.json absent → default
        store.save([2])
        assert json.loads(redirected.read_text(encoding="utf-8")) == [2]
        # Original untouched
        assert json.loads(original.read_text(encoding="utf-8")) == [1]


# ── DraftsStore item helpers ─────────────────────────────────────────────────


class TestDraftsStore:
    @pytest.fixture
    def store(self, tmp_path):
        return DraftsStore(tmp_path / "drafts.json")

    def test_get_item_missing_returns_none(self, store):
        assert store.get_item("missing") is None

    def test_get_item_present_returns_dict(self, store):
        store.save([{"id": "a", "x": 1}, {"id": "b", "x": 2}])
        assert store.get_item("b") == {"id": "b", "x": 2}

    def test_update_item_merges_fields(self, store):
        store.save([{"id": "a", "status": "pending"}])
        ok = store.update_item("a", status="scheduled", extra="value")
        assert ok is True
        items = store.load()
        assert items == [{"id": "a", "status": "scheduled", "extra": "value"}]

    def test_update_item_missing_id_returns_false(self, store):
        store.save([{"id": "a"}])
        ok = store.update_item("nonexistent", status="x")
        assert ok is False
        # File untouched
        assert store.load() == [{"id": "a"}]

    def test_delete_item_present_returns_true(self, store):
        store.save([{"id": "a"}, {"id": "b"}])
        ok = store.delete_item("a")
        assert ok is True
        assert store.load() == [{"id": "b"}]

    def test_delete_item_missing_returns_false(self, store):
        store.save([{"id": "a"}])
        ok = store.delete_item("nonexistent")
        assert ok is False
        assert store.load() == [{"id": "a"}]

    def test_insert_first_prepends(self, store):
        store.save([{"id": "a"}])
        result = store.insert_first({"id": "b"})
        assert result == [{"id": "b"}, {"id": "a"}]
        assert store.load() == [{"id": "b"}, {"id": "a"}]


# ── Module-level singletons ──────────────────────────────────────────────────


class TestSingletons:
    def test_default_paths_under_config_dir(self):
        from webui_store import (
            drafts_store, history_store, profiles_store, schedule_store,
        )

        assert "publish-history.json" in str(history_store.path)
        assert "campaign-profiles.json" in str(profiles_store.path)
        assert "draft-queue.json" in str(drafts_store.path)
        assert "schedule-settings.json" in str(schedule_store.path)

    def test_default_factories_match_legacy_types(self):
        from webui_store import (
            drafts_store, history_store, profiles_store, schedule_store,
        )

        # Quick smoke: load() on a non-existent path returns the right
        # container type. Uses path override so we don't touch real config.
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            for store, expected in (
                (history_store, list),
                (profiles_store, list),
                (drafts_store, list),
                (schedule_store, dict),
            ):
                orig = store.path
                store.path = Path(td) / "x.json"
                try:
                    loaded = store.load()
                    assert isinstance(loaded, expected), (
                        f"{store!r} default-factory should return {expected.__name__}"
                    )
                finally:
                    store.path = orig
