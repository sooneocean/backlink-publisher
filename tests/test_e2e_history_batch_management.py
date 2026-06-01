"""Plan 2026-05-19-006 Unit 7 — end-to-end stack test of the truth /
batch / recheck / purge flows."""

from __future__ import annotations

import threading
from unittest.mock import patch
from urllib.parse import unquote

import pytest
from werkzeug.datastructures import MultiDict

from webui_store import history_store, drafts_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


class TestFullStackFlow:
    def test_truth_then_purge_then_recheck(self, client):
        """The complete five-stage flow:
        1. Simulate publish output with mixed `published` / `_unverified` / failure (no URL)
        2. Verify history contains per-row entries with real status
        3. Render /ce:history — confirm unverified chip + bulk bar present
        4. Bulk-recheck the unverified rows — mock verify_fn to fail
        5. Purge the resulting failures
        """
        from webui_app.helpers.history import _push_history_per_row

        rows = [
            {"status": "published", "target_url": "https://a/", "platform": "medium",
             "title": "A", "published_url": "https://med/a", "error": None},
            {"status": "published_unverified", "target_url": "https://b/", "platform": "medium",
             "title": "B", "published_url": "https://med/b", "error": None},
            {"status": "drafted_unverified", "target_url": "https://c/", "platform": "medium",
             "title": "C", "draft_url": "https://med/c-draft", "error": None},
            # No URL returned by adapter — coerced to failed
            {"status": "published", "target_url": "https://d/", "platform": "medium",
             "title": "D", "published_url": "", "draft_url": "", "error": None},
        ]
        _push_history_per_row(rows)

        # Stage 2: history shape
        items = {it["target_url"]: it for it in history_store.load()}
        assert items["https://a/"]["status"] == "published"
        assert items["https://b/"]["status"] == "published_unverified"
        assert items["https://c/"]["status"] == "drafted_unverified"
        assert items["https://d/"]["status"] == "failed"
        assert items["https://d/"]["error"] == "no URL returned by adapter"

        # Stage 3: rendered page exposes the unverified items + bulk bar
        resp = client.get("/ce:history")
        body = resp.data.decode("utf-8")
        assert 'data-filter-value="unverified"' in body
        assert 'id="historyBulkForm"' in body
        assert "已发布·未核实" in body
        assert "草稿·未核实" in body

        # Stage 4: bulk-recheck the two unverified rows. The default verify_fn
        # routes through the shared probe_liveness engine (Plan 2026-05-29-004
        # U2), so patch the underlying inspect_target_anchor (host_gone → 404).
        unverified_ids = [it["id"] for it in history_store.load()
                          if it["status"].endswith("_unverified")]
        assert len(unverified_ids) == 2
        with patch(
            "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
            return_value={
                "page_readable": False, "target_anchor_found": False,
                "target_is_nofollow": False, "target_rel": None,
                "target_anchor_text": None, "reason": "http_404", "marker_present": None,
            },
        ):
            resp = client.post(
                "/ce:history/bulk-recheck",
                data=MultiDict([("ids", i) for i in unverified_ids]),
            )
        assert resp.status_code == 302
        msg = unquote(resp.location)
        assert "已核实 2 条" in msg
        # Both unverified now failed
        after = {it["target_url"]: it for it in history_store.load()}
        assert after["https://b/"]["status"] == "failed"
        assert after["https://c/"]["status"] == "failed"
        assert after["https://b/"]["verify_error"] == "http_404"

        # Stage 5: purge-failed wipes them + the original 'd' coerced failure
        resp = client.post("/ce:history/purge-failed")
        assert "已清除 3 条" in unquote(resp.location)
        remaining = history_store.load()
        # Only the originally-clean 'published' row 'a' survives
        assert len(remaining) == 1
        assert remaining[0]["target_url"] == "https://a/"


class TestConcurrencyLockProtection:
    def test_bulk_delete_during_concurrent_update_item(self, tmp_path, monkeypatch):
        """Background scheduler-style write must not race a UI bulk-delete."""
        monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
        history_store.save([
            {"id": f"i{n}", "status": "pending", "target_url": f"https://t/{n}"}
            for n in range(20)
        ])
        barrier = threading.Barrier(2)

        def t_delete():
            barrier.wait()
            history_store.bulk_delete([f"i{n}" for n in range(0, 10)])

        def t_update():
            barrier.wait()
            history_store.update_item("i15", status="failed", verify_error="late")

        threads = [threading.Thread(target=t_delete), threading.Thread(target=t_update)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        remaining = {it["id"]: it for it in history_store.load()}
        # Deleted half
        assert set(remaining) == {f"i{n}" for n in range(10, 20)}
        # The update_item write was preserved through the lock
        assert remaining["i15"]["status"] == "failed"
        assert remaining["i15"]["verify_error"] == "late"
