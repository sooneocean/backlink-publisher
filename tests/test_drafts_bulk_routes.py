"""Plan 2026-05-19-006 Unit 3 — draft bulk-operation routes."""

from __future__ import annotations

from urllib.parse import unquote

import pytest
from werkzeug.datastructures import MultiDict

from webui_store import drafts_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


@pytest.fixture
def isolated_drafts(tmp_path, monkeypatch):
    monkeypatch.setattr(drafts_store, "_path", tmp_path / "drafts.json")
    return drafts_store


def _seed_drafts(items):
    drafts_store.save(items)


class TestDraftBulkDelete:
    def test_removes_selected(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "pending"},
            {"id": "b", "status": "pending"},
            {"id": "c", "status": "pending"},
        ])
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        # Flask URL-encodes the Chinese flash_msg
        from urllib.parse import unquote
        assert "已删除 2 项" in unquote(resp.location)
        assert [it["id"] for it in isolated_drafts.load()] == ["b"]

    def test_empty_ids_returns_warning(self, client, isolated_drafts):
        _seed_drafts([{"id": "a"}])
        resp = client.post("/ce:draft/bulk-delete", data={})
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.location
        assert len(isolated_drafts.load()) == 1

    def test_unknown_ids_are_silently_ignored(self, client, isolated_drafts):
        _seed_drafts([{"id": "a"}])
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "zzz"), ("ids", "yyy")]),
        )
        assert resp.status_code == 302
        assert len(isolated_drafts.load()) == 1  # 'a' still there

    def test_scheduled_drafts_also_get_job_removed(self, client, isolated_drafts):
        """bulk-delete must call remove_job for each id (catches JobLookupError silently)."""
        _seed_drafts([
            {"id": "a", "status": "scheduled"},
            {"id": "b", "status": "pending"},
        ])
        # No job is actually scheduled in the test scheduler — call should
        # silently succeed because remove_job raises and is caught.
        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "b")]),
        )
        assert resp.status_code == 302
        assert isolated_drafts.load() == []


class TestDraftBulkCancel:
    def test_only_scheduled_drafts_change_state(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
            {"id": "b", "status": "pending"},
            {"id": "c", "status": "published"},
        ])
        resp = client.post(
            "/ce:draft/bulk-cancel",
            data=MultiDict([("ids", "a"), ("ids", "b"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        items = {it["id"]: it for it in isolated_drafts.load()}
        assert items["a"]["status"] == "pending"
        assert items["a"]["scheduled_at"] is None
        assert items["b"]["status"] == "pending"  # unchanged
        assert items["c"]["status"] == "published"  # unchanged

    def test_empty_ids(self, client, isolated_drafts):
        _seed_drafts([{"id": "a", "status": "scheduled"}])
        resp = client.post("/ce:draft/bulk-cancel", data={})
        assert "flash_type=warning" in resp.location


class TestDraftBulkPublishNow:
    def test_schedules_each_with_5s_stagger(self, client, isolated_drafts):
        _seed_drafts([
            {"id": "a", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
            {"id": "b", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
            {"id": "c", "status": "pending", "plans_jsonl": "{}", "platform": "medium"},
        ])
        resp = client.post(
            "/ce:draft/bulk-publish-now",
            data=MultiDict([("ids", "a"), ("ids", "b"), ("ids", "c")]),
        )
        assert resp.status_code == 302
        items = {it["id"]: it for it in isolated_drafts.load()}
        assert items["a"]["status"] == "scheduled"
        assert items["b"]["status"] == "scheduled"
        assert items["c"]["status"] == "scheduled"
        # The three scheduled_at values must be distinct (stagger applied)
        ts = {items[k]["scheduled_at"] for k in ("a", "b", "c")}
        assert len(ts) == 3

    def test_missing_ids_skipped(self, client, isolated_drafts):
        _seed_drafts([{"id": "a", "status": "pending", "plans_jsonl": "{}", "platform": "medium"}])
        resp = client.post(
            "/ce:draft/bulk-publish-now",
            data=MultiDict([("ids", "a"), ("ids", "zzz")]),
        )
        assert resp.status_code == 302
        assert isolated_drafts.load()[0]["status"] == "scheduled"
        # only 1 was actually scheduled
        assert "1" in resp.location

    def test_empty_ids(self, client, isolated_drafts):
        resp = client.post("/ce:draft/bulk-publish-now", data={})
        assert "flash_type=warning" in resp.location


class TestDraftJobRemovalHonesty:
    """Plan 2026-05-25-009 Unit 2: a genuine scheduler-removal failure must warn
    the operator the job may still fire, while a JobLookupError (draft never
    scheduled) stays a silent success."""

    def test_cancel_benign_joblookup_is_success(self, client, isolated_drafts):
        # Draft is 'scheduled' in the store but no real job exists in the test
        # scheduler → remove_job raises JobLookupError → benign.
        _seed_drafts([{"id": "a", "status": "scheduled",
                       "scheduled_at": "2099-01-01T00:00:00"}])
        resp = client.post("/ce:draft/cancel", data={"id": "a"})
        assert resp.status_code == 302
        assert "flash_type=success" in resp.location
        assert isolated_drafts.load()[0]["status"] == "pending"

    def test_cancel_genuine_failure_warns_and_logs(self, client, isolated_drafts,
                                                   monkeypatch):
        _seed_drafts([{"id": "a", "status": "scheduled",
                       "scheduled_at": "2099-01-01T00:00:00"}])
        import webui_app.routes.drafts as drafts_mod

        def _boom(_job_id):
            raise RuntimeError("jobstore offline")
        monkeypatch.setattr(drafts_mod._scheduler, "remove_job", _boom)
        warned = {}
        monkeypatch.setattr(drafts_mod.plan_logger, "warn",
                            lambda ev, **kw: warned.update(event=ev, **kw))

        resp = client.post("/ce:draft/cancel", data={"id": "a"})
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.location
        assert "排程任务可能仍会触发" in unquote(resp.location)
        # store still mutated (operator intent honored) ...
        assert isolated_drafts.load()[0]["status"] == "pending"
        # ... but the genuine failure was logged with the exception class.
        assert warned["event"] == "draft_job_remove_failed"
        assert warned["reason"] == "RuntimeError"

    def test_delete_genuine_failure_warns(self, client, isolated_drafts,
                                          monkeypatch):
        _seed_drafts([{"id": "a", "status": "scheduled",
                       "scheduled_at": "2099-01-01T00:00:00"}])
        import webui_app.routes.drafts as drafts_mod
        monkeypatch.setattr(drafts_mod._scheduler, "remove_job",
                            lambda _id: (_ for _ in ()).throw(RuntimeError("x")))
        resp = client.post("/ce:draft/delete", data={"id": "a"})
        assert resp.status_code == 302
        assert "flash_type=warning" in resp.location
        assert isolated_drafts.load() == []  # still deleted from store

    def test_bulk_cancel_reports_genuine_failure_count(self, client,
                                                       isolated_drafts, monkeypatch):
        _seed_drafts([
            {"id": "a", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
            {"id": "b", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
        ])
        import webui_app.routes.drafts as drafts_mod
        calls = {"n": 0}

        def _flaky(_job_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("jobstore offline")  # genuine failure
            from apscheduler.jobstores.base import JobLookupError
            raise JobLookupError(_job_id)  # benign — counts clean
        monkeypatch.setattr(drafts_mod._scheduler, "remove_job", _flaky)

        resp = client.post(
            "/ce:draft/bulk-cancel",
            data=MultiDict([("ids", "a"), ("ids", "b")]),
        )
        assert resp.status_code == 302
        loc = unquote(resp.location)
        assert "flash_type=warning" in resp.location
        assert "1 项的排程任务可能仍会触发" in loc

    def test_bulk_delete_reports_genuine_failure_count(self, client,
                                                       isolated_drafts, monkeypatch):
        """ce:review: bulk-delete's job_failures>0 warning branch was untested
        (only bulk-cancel was). A genuine RuntimeError on one removal must
        surface a warning count; benign JobLookupError counts clean; and the
        store deletion still completes (operator intent honored)."""
        _seed_drafts([
            {"id": "a", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
            {"id": "b", "status": "scheduled", "scheduled_at": "2099-01-01T00:00:00"},
        ])
        import webui_app.routes.drafts as drafts_mod
        calls = {"n": 0}

        def _flaky(_job_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("jobstore offline")  # genuine failure
            from apscheduler.jobstores.base import JobLookupError
            raise JobLookupError(_job_id)  # benign — counts clean
        monkeypatch.setattr(drafts_mod._scheduler, "remove_job", _flaky)

        resp = client.post(
            "/ce:draft/bulk-delete",
            data=MultiDict([("ids", "a"), ("ids", "b")]),
        )
        assert resp.status_code == 302
        loc = unquote(resp.location)
        assert "flash_type=warning" in resp.location
        assert "1 项的排程任务可能仍会触发" in loc
        assert isolated_drafts.load() == []  # both still deleted from store
