from __future__ import annotations

import json

from flask import render_template

from webui_app.api.drafts_api import DraftAPI
from webui_app import create_app


class _DraftStore:
    def __init__(self):
        self.items: list[dict] = []

    def insert_first(self, item):
        self.items.insert(0, item)

    def get_item(self, item_id):
        return next((item for item in self.items if item["id"] == item_id), None)

    def load(self):
        return list(self.items)

    def update_item(self, item_id, **updates):
        item = self.get_item(item_id)
        if item is None:
            return
        item.update(updates)


class _Scheduler:
    def __init__(self):
        self.jobs: list[str] = []

    def add_job(self, *args, **kwargs):
        self.jobs.append(kwargs["id"])

    def remove_job(self, job_id):
        if job_id in self.jobs:
            self.jobs.remove(job_id)


def _plans_jsonl(
    ai_status: str = "rejected",
    *,
    issue_message: str = "missing required anchor",
    cover_prompt: bool = True,
) -> str:
    row = {
        "id": "plan-1",
        "platform": "telegraph",
        "target_url": "https://example.com/guides/alpha",
        "content_markdown": "# Alpha\n\nBody",
        "ai_generation": {
            "status": ai_status,
            "validation_accepted": ai_status == "reviewable",
            "provider": "fake",
            "issues": [
                {
                    "code": "missing_required_anchor",
                    "severity": "error",
                    "message": issue_message,
                }
            ] if ai_status != "reviewable" else [],
        },
    }
    if cover_prompt:
        row["cover_prompt"] = "clean editorial cover prompt"
    return json.dumps(row)


def test_create_records_ai_review_state(monkeypatch) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)

    result = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")

    assert result["ok"] is True
    item = store.items[0]
    assert item["ai_review"]["required"] is True
    assert item["ai_review"]["accepted"] is False
    assert item["ai_review"]["status"] == "rejected"
    assert item["ai_review"]["provider"] == "fake"
    assert item["ai_review"]["cover_prompt_present"] is True


def test_unaccepted_ai_draft_cannot_publish(monkeypatch) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    created = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")

    result = DraftAPI().publish_now(created["id"])

    assert result["ok"] is False
    assert result["error_code"] == "AI_DRAFT_REVIEW_REQUIRED"
    assert store.items[0]["status"] == "pending"


def test_accepted_ai_draft_can_publish(monkeypatch) -> None:
    store = _DraftStore()
    scheduled: list[str] = []
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    monkeypatch.setattr(
        "webui_app.scheduler._schedule_draft_job",
        lambda item_id, run_date: scheduled.append(item_id),
    )
    created = DraftAPI().create(_plans_jsonl("reviewable"), {}, platform="telegraph")

    result = DraftAPI().publish_now(created["id"])

    assert result["ok"] is True
    assert scheduled == [created["id"]]
    assert store.items[0]["status"] == "scheduled"


def test_accept_ai_draft_allows_publish(monkeypatch) -> None:
    store = _DraftStore()
    scheduled: list[str] = []
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    monkeypatch.setattr(
        "webui_app.scheduler._schedule_draft_job",
        lambda item_id, run_date: scheduled.append(item_id),
    )
    created = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")

    accepted = DraftAPI().accept_ai_review(created["id"])
    result = DraftAPI().publish_now(created["id"])

    row = json.loads(store.items[0]["plans_jsonl"])
    assert accepted["ok"] is True
    assert store.items[0]["ai_review"]["accepted"] is True
    assert store.items[0]["ai_review"]["status"] == "accepted"
    assert row["ai_generation"]["validation_accepted"] is True
    assert row["ai_generation"]["status"] == "accepted"
    assert result["ok"] is True
    assert scheduled == [created["id"]]


def test_fallback_ai_draft_marks_metadata_without_rewriting_content(monkeypatch) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    created = DraftAPI().create(_plans_jsonl("fallback_used"), {}, platform="telegraph")
    before = json.loads(store.items[0]["plans_jsonl"])["content_markdown"]

    result = DraftAPI().fallback_ai_review(created["id"])

    row = json.loads(store.items[0]["plans_jsonl"])
    assert result["ok"] is True
    assert store.items[0]["ai_review"]["accepted"] is True
    assert store.items[0]["ai_review"]["status"] == "fallback_accepted"
    assert store.items[0]["ai_review"]["accepted_action"] == "fallback_accept"
    assert row["ai_generation"]["validation_accepted"] is True
    assert row["ai_generation"]["status"] == "fallback_accepted"
    assert row["content_markdown"] == before


def test_fallback_ai_draft_requires_fallback_payload(monkeypatch) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    created = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")

    result = DraftAPI().fallback_ai_review(created["id"])

    assert result["ok"] is False
    assert result["error_code"] == "AI_DRAFT_FALLBACK_UNAVAILABLE"
    assert store.items[0]["ai_review"]["accepted"] is False


def test_bulk_publish_reports_ai_review_blocked_without_mutating(monkeypatch) -> None:
    store = _DraftStore()
    scheduler = _Scheduler()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    monkeypatch.setattr("webui_app.api.drafts_api._scheduler", scheduler)
    blocked = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")
    accepted = DraftAPI().create(_plans_jsonl("reviewable"), {}, platform="telegraph")

    result = DraftAPI().bulk_publish_now([blocked["id"], accepted["id"]])

    assert result["ok"] is False
    assert result["error_code"] == "AI_DRAFT_REVIEW_REQUIRED"
    assert result["blocked_count"] == 1
    assert store.get_item(blocked["id"])["status"] == "pending"
    assert store.get_item(accepted["id"])["status"] == "pending"
    assert scheduler.jobs == []


def test_ai_accept_route_redirects_and_updates_state(monkeypatch, disable_csrf) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    created = DraftAPI().create(_plans_jsonl("rejected"), {}, platform="telegraph")
    app = disable_csrf
    app.config["TESTING"] = True

    resp = app.test_client().post("/ce:draft/ai-accept", data={"id": created["id"]})

    assert resp.status_code == 302
    assert "/?tab=draft" in resp.headers["Location"]
    assert store.items[0]["ai_review"]["accepted"] is True


def test_draft_queue_renders_ai_review_controls_without_secret(monkeypatch) -> None:
    store = _DraftStore()
    monkeypatch.setattr("webui_app.api.drafts_api._drafts_store", store)
    DraftAPI().create(
        _plans_jsonl("fallback_used", issue_message="leaked sk-should-not-render"),
        {},
        platform="telegraph",
    )
    app = create_app()
    app.config["TESTING"] = True

    with app.test_request_context("/?tab=draft"):
        body = render_template(
            "_tab_history.html",
            history_active=True,
            draft_queue=store.items,
            history=[],
            grouped_history=[],
            platforms=[],
            csrf_token="test-token",
            now_iso="2026-06-06T12:00",
            suggested_next="2026-06-06T13:00",
        )

    assert "AI 審核" in body
    assert "接受 AI 草稿" in body
    assert "使用 fallback" in body
    assert "fake" in body
    assert "封面 prompt" in body
    assert "sk-should-not-render" not in body


def test_settings_renders_ai_service_readiness_without_secret(
    tmp_path, monkeypatch, disable_csrf
) -> None:
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "llm-settings.json").write_text(
        json.dumps(
            {
                "api_key": "sk-should-not-render",
                "endpoint": "https://api.test/v1",
                "model": "gpt-4o-mini",
                "use_article_gen": True,
                "use_image_gen": False,
            }
        ),
        encoding="utf-8",
    )
    app = disable_csrf
    app.config["TESTING"] = True

    body = app.test_client().get("/settings").data.decode()

    assert "AI 生成服務狀態" in body
    assert "全文生成已啟用" in body
    assert "https://api.test/v1" in body
    assert "sk-should-not-render" not in body
