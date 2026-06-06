"""DraftAPI — structured CRUD wrapper around ``drafts_store`` + scheduler.

Centralises the draft lifecycle so routes never touch the store or scheduler
directly.  Every mutating method returns a dict with ``ok`` / ``error`` /
``flash_msg`` for the route's redirect response.
"""

from __future__ import annotations

import uuid
import json
from datetime import datetime, timedelta
from typing import Any

from apscheduler.jobstores.base import JobLookupError

from backlink_publisher._util.logger import plan_logger
from webui_store import drafts_store as _drafts_store

from ..helpers.contexts import _calc_next_available
from ..scheduler import _publish_draft_job, _scheduler


# ── helpers ────────────────────────────────────────────────────────────────


def _remove_scheduled_job(job_id: str) -> bool:
    """Remove a scheduler job, distinguishing benign absence from real failure.

    Returns True when removal was clean (job removed, or the job never existed —
    the expected state for a draft that was never scheduled). Returns False on a
    genuine failure (the job may still fire); logs the real cause with the
    exception class. Restores the O1 "removal honesty" (PR #231) that the
    Phase-1 extraction's silent ``_remove_job_silent`` had dropped.
    """
    try:
        _scheduler.remove_job(job_id)
    except JobLookupError:
        return True
    except Exception as exc:
        plan_logger.warn("draft_job_remove_failed", item_id=job_id,
                         reason=type(exc).__name__)
        return False
    return True


def _ai_review_state_from_plans(plans_jsonl: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for line in plans_jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)

    ai_rows = [
        row.get("ai_generation")
        for row in rows
        if isinstance(row.get("ai_generation"), dict)
    ]
    if not ai_rows:
        return {"required": False, "accepted": True, "status": "not_applicable", "issues": []}

    issues = [
        issue
        for ai in ai_rows
        for issue in (ai.get("issues") or [])
        if isinstance(issue, dict)
    ]
    accepted = all(
        ai.get("status") == "reviewable" and ai.get("validation_accepted") is True
        for ai in ai_rows
    )
    status = "reviewable" if accepted else "rejected"
    return {
        "required": True,
        "accepted": accepted,
        "status": status,
        "issues": issues,
    }


def _ai_publish_gate(item: dict[str, Any] | None) -> dict[str, Any] | None:
    ai_review = (item or {}).get("ai_review")
    if not isinstance(ai_review, dict):
        return None
    if ai_review.get("required") and not ai_review.get("accepted"):
        return {
            "ok": False,
            "error_code": "AI_DRAFT_REVIEW_REQUIRED",
            "flash_type": "warning",
            "flash_msg": "AI 草稿尚未通過審核，不能發布。",
        }
    return None


# ── DraftAPI ───────────────────────────────────────────────────────────────


class DraftAPI:
    """Encapsulates draft item lifecycle.

    Usage::

        api = DraftAPI()
        result = api.create(plans_jsonl, config, platform="velog")
        # result == {"ok": True, "id": "ab12cd34", "flash_msg": "已加入草稿栏"}
    """

    # ── create ───────────────────────────────────────────────────────────

    def create(
        self,
        plans_jsonl: str,
        config: dict[str, Any],
        *,
        platform: str | None = None,
        publish_mode: str = "publish",
        target_url: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Save validated plans as a pending draft queue item."""
        if not plans_jsonl:
            return {"ok": False, "flash_msg": "没有可保存的内容"}

        platform = platform or config.get("platform", "blogger")
        target_url = target_url or config.get("target_url", "unknown")
        language = language or config.get("target_language", "zh-CN")

        item = {
            "id": str(uuid.uuid4())[:8],
            "target_url": target_url,
            "platform": platform,
            "language": language,
            "publish_mode": publish_mode,
            "plans_jsonl": plans_jsonl,
            "status": "pending",
            "scheduled_at": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [],
            "error": None,
            "ai_review": _ai_review_state_from_plans(plans_jsonl),
        }
        try:
            _drafts_store.insert_first(item)
        except Exception as exc:
            plan_logger.error("draft_create_io_failed", error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"草稿保存失败：本地存储写入失败 ({type(exc).__name__})",
            }

        return {
            "ok": True,
            "id": item["id"],
            "flash_msg": "已加入草稿栏",
        }

    # ── read helpers ──────────────────────────────────────────────────────

    def get(self, item_id: str) -> dict[str, Any] | None:
        """Fetch a single draft item by id."""
        return _drafts_store.get_item(item_id)

    def list_all(self) -> list[dict[str, Any]]:
        """Return all draft items."""
        return _drafts_store.load()

    # ── schedule ──────────────────────────────────────────────────────────

    def schedule(
        self,
        item_id: str,
        scheduled_at_str: str,
    ) -> dict[str, Any]:
        """Schedule a draft for publishing at an ISO-8601 datetime string.

        Returns ``{"ok": True, "flash_msg": ...}`` or error dict.
        """
        if not item_id or not scheduled_at_str:
            return {"ok": False, "flash_msg": "参数缺失"}

        gated = _ai_publish_gate(_drafts_store.get_item(item_id))
        if gated is not None:
            return gated

        try:
            requested_dt = datetime.fromisoformat(scheduled_at_str)
        except ValueError:
            return {"ok": False, "flash_msg": "时间格式错误"}

        final_dt = _calc_next_available(requested_dt)
        try:
            _drafts_store.update_item(
                item_id,
                status="scheduled",
                scheduled_at=final_dt.isoformat(),
            )
        except Exception as exc:
            plan_logger.error("draft_schedule_persistence_failed", item_id=item_id, error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"排程保存失败：本地儲存更新失敗 ({type(exc).__name__})",
            }

        try:
            from ..scheduler import _schedule_draft_job
            _schedule_draft_job(item_id, final_dt)
        except Exception as exc:
            plan_logger.error("draft_schedule_job_failed", item_id=item_id, error=str(exc))
            # Rollback store
            try:
                _drafts_store.update_item(item_id, status="pending", scheduled_at=None)
            except Exception:
                pass
            return {
                "ok": False,
                "error_code": "SCHEDULER_SYNC_FAILED",
                "flash_type": "danger",
                "flash_msg": f"排程註冊失敗：後台調度任務同步失敗 ({type(exc).__name__})",
            }

        adjusted = final_dt != requested_dt
        msg = f'已排程：{final_dt.strftime("%Y-%m-%d %H:%M")}'
        if adjusted:
            msg += "（已依间隔设定自动调整）"
        return {"ok": True, "flash_msg": msg}

    # ── publish-now ───────────────────────────────────────────────────────

    def publish_now(self, item_id: str) -> dict[str, Any]:
        """Immediately schedule a draft to publish in ~5 seconds."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}

        item = _drafts_store.get_item(item_id)
        gated = _ai_publish_gate(item)
        if gated is not None:
            return gated

        run_date = datetime.now() + timedelta(seconds=5)
        try:
            _drafts_store.update_item(
                item_id,
                status="scheduled",
                scheduled_at=run_date.isoformat(),
            )
        except Exception as exc:
            plan_logger.error("draft_publish_now_persistence_failed", item_id=item_id, error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"立即发布失败：本地儲存更新失敗 ({type(exc).__name__})",
            }

        try:
            from ..scheduler import _schedule_draft_job
            _schedule_draft_job(item_id, run_date)
        except Exception as exc:
            plan_logger.error("draft_publish_now_job_failed", item_id=item_id, error=str(exc))
            # Rollback store
            try:
                _drafts_store.update_item(item_id, status="pending", scheduled_at=None)
            except Exception:
                pass
            return {
                "ok": False,
                "error_code": "SCHEDULER_SYNC_FAILED",
                "flash_type": "danger",
                "flash_msg": f"立即发布注册失败：後台調度任務同步失敗 ({type(exc).__name__})",
            }

        return {"ok": True, "flash_msg": "正在发布，请稍候刷新页面"}

    # ── cancel ────────────────────────────────────────────────────────────

    def cancel(self, item_id: str) -> dict[str, Any]:
        """Cancel a scheduled draft."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}

        # Honour operator intent first: mutate store regardless of scheduler
        # removal outcome. If the job can't be removed the operator still sees
        # the draft as cancelled (pending) with a warning that it may still fire.
        try:
            _drafts_store.update_item(item_id, status="pending", scheduled_at=None)
        except Exception as exc:
            plan_logger.error("draft_cancel_persistence_failed", item_id=item_id, error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"取消排程失敗：本地儲存更新失敗 ({type(exc).__name__})"
            }

        if not _remove_scheduled_job(item_id):
            return {
                "ok": False,
                "error_code": "SCHEDULER_SYNC_FAILED",
                "flash_type": "warning",
                "flash_msg": "取消排程失敗：無法同步刪除後台調度任務，該任務可能仍在運行！"
            }

        return {"ok": True, "flash_msg": "已取消排程"}

    # ── delete ────────────────────────────────────────────────────────────

    def delete(self, item_id: str) -> dict[str, Any]:
        """Delete a draft item (cancel job if scheduled)."""
        if not item_id:
            return {"ok": False, "flash_msg": "参数缺失"}

        # Honour operator intent first: delete from store regardless of
        # scheduler removal outcome. See cancel() for rationale.
        try:
            _drafts_store.delete_item(item_id)
        except Exception as exc:
            plan_logger.error("draft_delete_persistence_failed", item_id=item_id, error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"刪除失敗：本地儲存刪除失敗 ({type(exc).__name__})"
            }

        if not _remove_scheduled_job(item_id):
            return {
                "ok": False,
                "error_code": "SCHEDULER_SYNC_FAILED",
                "flash_type": "warning",
                "flash_msg": "刪除失敗：無法同步刪除後台調度任務，該任務可能仍在運行！"
            }

        return {"ok": True, "flash_msg": "已删除"}

    # ── bulk operations ──────────────────────────────────────────────────

    def bulk_delete(self, ids: list[str]) -> dict[str, Any]:
        """Delete multiple drafts by id."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}

        # Collect scheduled IDs before store mutation so we can try job
        # removal afterwards regardless of outcome.
        scheduled_ids = [
            item_id for item_id in ids
            if (item := _drafts_store.get_item(item_id))
            and item.get("status") == "scheduled"
        ]

        try:
            removed = _drafts_store.bulk_delete(ids)
        except Exception as exc:
            plan_logger.error("draft_bulk_delete_persistence_failed", ids=ids, error=str(exc))
            return {
                "ok": False,
                "error_code": "PERSISTENCE_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"批量刪除失敗：本地儲存刪除失敗 ({type(exc).__name__})"
            }

        failed_jobs = []
        for item_id in scheduled_ids:
            if _remove_scheduled_job(item_id) is False:
                failed_jobs.append(item_id)

        if failed_jobs:
            return {
                "ok": False,
                "error_code": "SCHEDULER_SYNC_FAILED",
                "flash_type": "warning",
                "flash_msg": f"批量刪除失敗：無法同步清除後台調度任務，請重試 ({len(failed_jobs)} 項失敗)"
            }

        return {"ok": True, "flash_msg": f"已删除 {removed} 项"}

    def bulk_publish_now(self, ids: list[str]) -> dict[str, Any]:
        """Schedule multiple drafts for near-immediate publish, staggered."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}

        base = datetime.now()
        completed_jobs = []
        store_rollbacks = []

        try:
            for i, item_id in enumerate(ids):
                item = _drafts_store.get_item(item_id)
                if not item:
                    continue
                gated = _ai_publish_gate(item)
                if gated is not None:
                    raise RuntimeError(gated["error_code"])

                run_date = base + timedelta(seconds=5 + i * 5)
                # Track original state for rollback
                original_status = item.get("status", "pending")
                original_scheduled = item.get("scheduled_at")

                _drafts_store.update_item(
                    item_id,
                    status="scheduled",
                    scheduled_at=run_date.isoformat(),
                )
                store_rollbacks.append((item_id, original_status, original_scheduled))

                _scheduler.add_job(
                    _publish_draft_job,
                    trigger="date",
                    run_date=run_date,
                    id=item_id,
                    args=[item_id],
                    replace_existing=True,
                )
                completed_jobs.append(item_id)

        except Exception as exc:
            plan_logger.error("bulk_publish_scheduling_failed", error=str(exc))
            # Rollback successfully scheduled scheduler jobs
            for job_id in completed_jobs:
                try:
                    _scheduler.remove_job(job_id)
                except Exception:
                    pass
            # Rollback store states
            for item_id, status, sched_at in store_rollbacks:
                try:
                    _drafts_store.update_item(item_id, status=status, scheduled_at=sched_at)
                except Exception:
                    pass

            return {
                "ok": False,
                "error_code": "BULK_SCHEDULER_FAILURE",
                "flash_type": "danger",
                "flash_msg": f"批量发布失败：调度器注册异常，已回滚所有变更 ({type(exc).__name__})",
            }

        return {"ok": True, "flash_msg": f"正在批量发布 {len(completed_jobs)} 项，请稍候刷新页面"}

    def bulk_cancel(self, ids: list[str]) -> dict[str, Any]:
        """Cancel scheduling for multiple drafts."""
        if not ids:
            return {"ok": False, "flash_msg": "未选择任何项"}

        staged_items = []
        for item_id in ids:
            item = _drafts_store.get_item(item_id)
            if not item or item.get("status") != "scheduled":
                continue
            staged_items.append(item_id)

        completed_cancels = []
        try:
            for item_id in staged_items:
                if not _remove_scheduled_job(item_id):
                    raise RuntimeError(f"Failed to remove scheduled job for {item_id}")
                completed_cancels.append(item_id)

                _drafts_store.update_item(item_id, status="pending", scheduled_at=None)

        except Exception as exc:
            plan_logger.error("bulk_cancel_failed", error=str(exc))
            return {
                "ok": False,
                "error_code": "BULK_CANCEL_FAILURE",
                "flash_type": "warning",
                "flash_msg": f"批量取消失敗：後台任務同步異常，已中止操作 ({type(exc).__name__})",
            }

        return {"ok": True, "flash_msg": f"已取消 {len(completed_cancels)} 项排程"}
