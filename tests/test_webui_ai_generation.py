from __future__ import annotations

import json

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


def _plans_jsonl(ai_status: str = "rejected") -> str:
    row = {
        "id": "plan-1",
        "platform": "telegraph",
        "target_url": "https://example.com/guides/alpha",
        "content_markdown": "# Alpha\n\nBody",
        "ai_generation": {
            "status": ai_status,
            "validation_accepted": ai_status == "reviewable",
            "provider": "fake",
            "issues": [{"code": "missing_required_anchor"}] if ai_status != "reviewable" else [],
        },
    }
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


def test_settings_renders_ai_service_readiness_without_secret(tmp_path, monkeypatch) -> None:
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
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False

    body = app.test_client().get("/settings").data.decode()

    assert "AI 生成服務狀態" in body
    assert "全文生成已啟用" in body
    assert "https://api.test/v1" in body
    assert "sk-should-not-render" not in body
