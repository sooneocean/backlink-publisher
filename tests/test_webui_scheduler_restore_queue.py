"""queue-store recovery in ``_restore_scheduled_jobs``.

The recovery lambda (``_restore_processing_tasks``) is a pure function that
resets any ``processing`` tasks back to ``pending``.

Scenarios (S1–S4):

  S1 — No stuck tasks only ``pending``/``success``/``failed`` → no change
  S2 — Single ``processing`` task → reset to ``pending``
  S3 — Multiple stuck + normal tasks → only processing ones reset
  S4 — Empty / corrupt queue → no crash, no-op
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── The recovery lambda under test ──────────────────────────────────────────
# Mirrors the implementation in webui_app/scheduler._restore_processing_tasks.
_PROCESSING_RESET = lambda tasks: [  # noqa: E731
    {**t, 'status': 'pending'} if t.get('status') == 'processing' else t
    for t in tasks
]


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    """Point config dir at a tmp dir so queue_store writes don't leak."""
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir",
        return_value=fake_config_dir,
    ):
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _clear_queue_store():
    """Reset queue_store to empty list before every test."""
    from webui_store import queue_store
    queue_store.save([])


@pytest.fixture(scope="module")
def _scheduler_module():
    """Load ``webui_app.scheduler``, breaking the circular import chain
    by pre-registering ``drafts_api`` in ``sys.modules``.

    ``webui_app.scheduler`` has a module-level circular dependency:
      scheduler → api.pipeline_api → api.__init__ → drafts_api → scheduler

    Pre-registering a placeholder for ``drafts_api`` lets the import of
    ``scheduler`` complete normally without executing ``drafts_api.py``.
    """
    _d = types.ModuleType("webui_app.api.drafts_api")
    _d.__package__ = "webui_app.api"
    _d.DraftAPI = MagicMock()
    _d._publish_draft_job = MagicMock()
    _d._scheduler = MagicMock()
    sys.modules["webui_app.api.drafts_api"] = _d

    import webui_app.scheduler as _mod
    return _mod


# ── Unit tests: pure-function recovery lambda ───────────────────────────────

class TestRecoveryLambda:
    """Unit-test the lambda that ``_restore_processing_tasks`` wraps."""

    def test_no_processing_tasks_noop(self):
        tasks = [
            {"id": "t1", "status": "pending"},
            {"id": "t2", "status": "success"},
            {"id": "t3", "status": "failed", "error": "timeout"},
        ]
        result = _PROCESSING_RESET(tasks)
        assert result == tasks

    def test_single_processing_reset(self):
        tasks = [
            {"id": "t1", "status": "processing", "config": {"platform": "medium"}, "urls": ["https://example.com"]},
        ]
        result = _PROCESSING_RESET(tasks)
        assert len(result) == 1
        assert result[0]["id"] == "t1"
        assert result[0]["status"] == "pending"
        # Other fields preserved
        assert result[0]["config"]["platform"] == "medium"

    def test_multiple_processing_preserving_normal_tasks(self):
        tasks = [
            {"id": "t1", "status": "processing"},
            {"id": "t2", "status": "processing"},
            {"id": "t3", "status": "pending"},
            {"id": "t4", "status": "success"},
        ]
        result = _PROCESSING_RESET(tasks)
        statuses = {t["id"]: t["status"] for t in result}
        assert statuses == {"t1": "pending", "t2": "pending", "t3": "pending", "t4": "success"}

    def test_empty_list_noop(self):
        assert _PROCESSING_RESET([]) == []


# ── Integration test: actual _restore_scheduled_jobs wiring ─────────────────

class TestRestoreScheduledJobsIntegration:
    """Integration-level check: ``_restore_scheduled_jobs()`` calls the
    queue recovery *before* registering APScheduler jobs.

    Uses the module-scoped ``_scheduler_module`` fixture to pre-register
    ``drafts_api`` and break the circular-import chain, then patches
    ``_scheduler`` and ``_drafts_store`` with ``patch.object``.
    """

    def test_processing_tasks_reset_when_restoring_scheduled_jobs(
        self, _scheduler_module,
    ):
        from webui_store import queue_store
        queue_store.save([
            {"id": "t1", "status": "processing"},
        ])

        with patch.object(_scheduler_module, "_scheduler") as fake_sched, \
             patch.object(_scheduler_module._drafts_store, "load", return_value=[]):
            fake_sched.running = True
            _scheduler_module._restore_scheduled_jobs()

        tasks = queue_store.load()
        assert tasks[0]["status"] == "pending"
        # APScheduler interval job was registered (queue processor)
        assert fake_sched.add_job.call_count >= 1

    def test_processing_task_reset_through_restore_processing_tasks(
        self, _scheduler_module,
    ):
        """Direct call to ``_restore_processing_tasks``."""
        from webui_store import queue_store
        queue_store.save([
            {"id": "t1", "status": "processing", "config": {"platform": "blogger"}},
        ])

        _scheduler_module._restore_processing_tasks()

        tasks = queue_store.load()
        assert tasks[0]["status"] == "pending"


# ── Regression: _process_queue_job delegates to QueueStore.get_runnable ──────

class TestProcessQueueJobUsesGetRunnable:
    """Guards the dedup: ``_process_queue_job`` must honor the status +
    retry-due gate via the shared ``QueueStore.get_runnable()`` helper, not a
    divergent inline copy. Two paths: a not-yet-due failed task is skipped (no
    publish attempted, status untouched); a due task is picked and published.
    """

    def test_future_retry_task_is_skipped_no_publish(self, _scheduler_module):
        from datetime import datetime, timedelta
        from webui_store import queue_store

        future = (datetime.now() + timedelta(hours=1)).isoformat()
        queue_store.save([
            {"id": "future-failed", "status": "failed",
             "config": {"platform": "medium"}, "urls": ["https://x.example/p"],
             "next_retry_at": future},
        ])

        with patch.object(_scheduler_module, "PipelineAPI") as fake_api:
            _scheduler_module._process_queue_job()

        # get_runnable() filtered it out → early return, no publish attempt …
        fake_api.assert_not_called()
        # … and the task was never flipped to 'processing'.
        assert queue_store.load()[0]["status"] == "failed"

    def test_due_task_is_picked_and_published(self, _scheduler_module):
        from datetime import datetime, timedelta
        from webui_store import queue_store

        past = (datetime.now() - timedelta(minutes=1)).isoformat()
        queue_store.save([
            {"id": "due-failed", "status": "failed",
             "config": {"platform": "medium"}, "urls": ["https://x.example/p"],
             "next_retry_at": past},
        ])

        fake_result = MagicMock()
        fake_result.success = True
        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_score_after_publish"):
            fake_api.return_value.publish_seed.return_value = fake_result
            _scheduler_module._process_queue_job()

        fake_api.return_value.publish_seed.assert_called_once()
        assert queue_store.load()[0]["status"] == "success"


# ── Regression: non-429 failure must clear stale next_retry_at ───────────────

class TestProcessQueueJobClearsStaleRetryTime:
    """Guards: when a task fails with a non-429 error (or raises), the
    scheduler must clear any stale next_retry_at left from a prior 429
    failure. Without the fix, get_runnable() would keep filtering the
    task out until the old retry window expired, even though the new
    failure has nothing to do with rate-limiting.
    """

    def test_non_429_failure_clears_stale_next_retry_at(self, _scheduler_module):
        from datetime import datetime, timedelta
        from webui_store import queue_store

        past = (datetime.now() - timedelta(seconds=1)).isoformat()
        queue_store.save([{
            "id": "t-stale", "status": "failed",
            "error": "频率限制 (429)", "next_retry_at": past,
            "config": {"platform": "medium"}, "urls": ["https://example.com"],
        }])

        fake_result = MagicMock()
        fake_result.success = False
        fake_result.error = "Connection timeout"
        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_score_after_publish"):
            fake_api.return_value.publish_seed.return_value = fake_result
            _scheduler_module._process_queue_job()

        task = queue_store.load()[0]
        assert task["status"] == "failed"
        assert task["error"] == "Connection timeout"
        assert task.get("next_retry_at") is None, (
            f"stale next_retry_at={task.get('next_retry_at')!r} must be "
            f"cleared after non-429 error"
        )

    def test_exception_path_clears_stale_next_retry_at(self, _scheduler_module):
        from datetime import datetime, timedelta
        from webui_store import queue_store

        past = (datetime.now() - timedelta(seconds=1)).isoformat()
        queue_store.save([{
            "id": "t-exc", "status": "failed",
            "error": "频率限制 (429)", "next_retry_at": past,
            "config": {"platform": "medium"}, "urls": ["https://example.com"],
        }])

        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_score_after_publish"):
            fake_api.return_value.publish_seed.side_effect = RuntimeError("boom")
            _scheduler_module._process_queue_job()

        task = queue_store.load()[0]
        assert task["status"] == "failed"
        assert task.get("next_retry_at") is None, (
            "stale next_retry_at must be cleared after exception-path failure"
        )


# ── Regression: history write failure must not corrupt draft status ───────────

class TestPublishDraftJobHistoryFailure:
    """Guards: an I/O error in _push_history_per_row after a successful
    publish must not overwrite the draft status from 'published' to 'failed'.

    Root cause: _push_history_per_row() was inside the outer try/except that
    catches publish errors. A history write failure (e.g. disk full) propagated
    to the except handler which called _drafts_store.update_item(status='failed'),
    corrupting the 'published' status already written on success.
    """

    def test_history_write_failure_does_not_corrupt_published_status(
        self, _scheduler_module
    ):
        from webui_store import drafts_store as _ws_drafts_store

        _ws_drafts_store.save([])
        _ws_drafts_store.insert_first({
            'id': 'draft-pub',
            'status': 'scheduled',
            'target_url': 'https://example.com',
            'platform': 'medium',
            'publish_mode': 'draft',
            'language': 'zh-CN',
            'plans_jsonl': '{"target_url": "https://example.com"}',
        })

        fake_result = MagicMock()
        fake_result.success = True
        fake_result.stdout = (
            '{"published_url": "https://medium.com/p/test", "status": "drafted"}'
        )
        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_push_history_per_row",
                          side_effect=OSError("disk full")):
            fake_api.return_value.publish.return_value = fake_result
            _scheduler_module._publish_draft_job('draft-pub')

        item = _ws_drafts_store.get_item('draft-pub')
        assert item is not None
        assert item['status'] in ('published', 'published_unverified'), (
            f"Draft status corrupted to {item['status']!r} by history write failure"
        )


# ── Regression: _publish_draft_job must score on successful publish ───────────

class TestPublishDraftJobScoreAfterPublish:
    """Guards: _publish_draft_job must call _score_after_publish after a
    successful scheduled-draft publish, mirroring _process_queue_job.

    Root cause: _score_after_publish was only called from _process_queue_job
    (queue-mode publishes) and never from _publish_draft_job (scheduled-draft
    publishes). Every scheduled-draft publish was silently omitted from the
    score store, even though score_store.backfill_from_history shows the store
    is designed to capture all publishes.
    """

    def test_score_recorded_on_successful_draft_publish(self, _scheduler_module):
        from webui_store import drafts_store as _ws_drafts_store

        _ws_drafts_store.save([])
        _ws_drafts_store.insert_first({
            'id': 'draft-score',
            'status': 'scheduled',
            'target_url': 'https://example.com/page',
            'platform': 'medium',
            'publish_mode': 'draft',
            'language': 'zh-CN',
            'plans_jsonl': '{"target_url": "https://example.com/page"}',
        })

        fake_result = MagicMock()
        fake_result.success = True
        fake_result.stdout = (
            '{"published_url": "https://medium.com/p/abc", "status": "drafted"}'
        )
        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_score_after_publish") as mock_score:
            fake_api.return_value.publish.return_value = fake_result
            _scheduler_module._publish_draft_job('draft-score')

        mock_score.assert_called_once_with(
            target_url='https://example.com/page',
            channel='medium',
        )

    def test_score_not_called_on_failed_draft_publish(self, _scheduler_module):
        from webui_store import drafts_store as _ws_drafts_store

        _ws_drafts_store.save([])
        _ws_drafts_store.insert_first({
            'id': 'draft-fail',
            'status': 'scheduled',
            'target_url': 'https://example.com/fail',
            'platform': 'medium',
            'publish_mode': 'draft',
            'language': 'zh-CN',
            'plans_jsonl': '{"target_url": "https://example.com/fail"}',
        })

        fake_result = MagicMock()
        fake_result.success = False
        fake_result.error = 'API error'
        with patch.object(_scheduler_module, "PipelineAPI") as fake_api, \
             patch.object(_scheduler_module, "_score_after_publish") as mock_score:
            fake_api.return_value.publish.return_value = fake_result
            _scheduler_module._publish_draft_job('draft-fail')

        mock_score.assert_not_called()
