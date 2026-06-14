from __future__ import annotations

import pytest

from webui_store import QueueStore


def _profile() -> dict:
    return {
        "ok": True,
        "official_url": "https://example.com/",
        "target_url": "https://example.com/",
        "main_url": "https://example.com",
        "category_url": None,
        "work_url": None,
        "main_domain": "https://example.com",
        "language": "zh-CN",
        "title": "Example",
    }


def test_build_target_profile_rejects_invalid_url():
    from webui_app.services.official_url_intake import build_target_profile

    result = build_target_profile("javascript:alert(1)", probe_fn=lambda url: (True, None, "x"))

    assert result["ok"] is False
    assert result["reason"] == "blocked_scheme"


def test_build_target_profile_uses_mocked_content_probe_and_derives_tiers():
    from webui_app.services.official_url_intake import build_target_profile

    seen: dict[str, str] = {}

    def _probe(url: str):
        seen["url"] = url
        return True, None, "Official Site"

    result = build_target_profile("https://user:pass@example.com/blog/post-1?x=1", probe_fn=_probe)

    assert result["ok"] is True
    assert "user" not in seen["url"]
    assert "pass" not in seen["url"]
    assert result["title"] == "Official Site"
    assert result["main_url"] == "https://example.com"
    assert result["category_url"] == "https://example.com/blog"
    assert result["work_url"] == "https://example.com/blog/post-1"
    assert result["target_url"] == "https://example.com/blog/post-1"


def test_resolve_channel_eligibility_imports_adapters_before_registry_read():
    from webui_app.services.official_url_intake import resolve_channel_eligibility

    rows = resolve_channel_eligibility(channel_status={"rentry": {"status": "unbound"}})

    assert rows
    assert any(row["slug"] == "rentry" for row in rows)


def test_resolve_channel_eligibility_marks_bound_dofollow_channel_eligible():
    from webui_app.services.official_url_intake import resolve_channel_eligibility

    rows = resolve_channel_eligibility(channel_status={"blogger": {"status": "bound"}})
    blogger = next(row for row in rows if row["slug"] == "blogger")

    assert blogger["eligible"] is True
    assert blogger["reason"] == "eligible"


def test_resolve_channel_eligibility_uses_offline_binding_status_by_default(monkeypatch):
    from webui_app.services.official_url_intake import resolve_channel_eligibility

    monkeypatch.setattr("backlink_publisher.config.load_config", lambda: object())

    def _fake_status(name, config):
        return {"bound": name == "blogger", "blockers": []}

    monkeypatch.setattr("webui_app.binding_status.get_channel_status", _fake_status)

    rows = resolve_channel_eligibility()
    blogger = next(row for row in rows if row["slug"] == "blogger")

    assert blogger["eligible"] is True
    assert blogger["reason"] == "eligible"


def test_resolve_channel_eligibility_blocks_nofollow_and_expired_channels():
    from webui_app.services.official_url_intake import resolve_channel_eligibility

    rows = resolve_channel_eligibility(
        channel_status={
            "blogger": {"status": "expired"},
            "devto": {"status": "bound"},
        }
    )
    by_slug = {row["slug"]: row for row in rows}

    assert by_slug["blogger"]["eligible"] is False
    assert by_slug["blogger"]["reason"] == "auth_expired"
    assert by_slug["devto"]["eligible"] is False
    assert by_slug["devto"]["reason"] == "nofollow_only"


def test_enqueue_official_url_tasks_writes_draft_first_queue_items(tmp_path):
    from webui_app.services.official_url_intake import enqueue_official_url_tasks

    queue = QueueStore(tmp_path / "queue.json", default_factory=list)
    task_ids = enqueue_official_url_tasks(
        _profile(),
        selected_channels=["blogger"],
        eligibility=[{"slug": "blogger", "eligible": True}],
        queue_store=queue,
    )

    tasks = queue.load()
    assert len(task_ids) == 1
    assert len(tasks) == 1
    assert tasks[0]["status"] == "pending"
    assert tasks[0]["urls"] == ["https://example.com/"]
    assert tasks[0]["config"]["platform"] == "blogger"
    assert tasks[0]["config"]["publish_mode"] == "draft"
    assert tasks[0]["config"]["source"] == "official_url_intake"


def test_enqueue_official_url_tasks_rejects_ineligible_channel(tmp_path):
    from webui_app.services.official_url_intake import (
        OfficialUrlIntakeError,
        enqueue_official_url_tasks,
    )

    queue = QueueStore(tmp_path / "queue.json", default_factory=list)

    with pytest.raises(OfficialUrlIntakeError) as exc:
        enqueue_official_url_tasks(
            _profile(),
            selected_channels=["devto"],
            eligibility=[{"slug": "devto", "eligible": False, "reason": "nofollow_only"}],
            queue_store=queue,
        )

    assert exc.value.reason == "ineligible_channel"
    assert queue.load() == []
