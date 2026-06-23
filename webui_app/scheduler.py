import json
import uuid
from datetime import datetime, timedelta

from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from backlink_publisher._util.logger import plan_logger

from webui_store import drafts_store as _drafts_store
from webui_store import history_store as _history_store
from webui_store import queue_store as _queue_store

from .api.pipeline_api import PipelineAPI
from .helpers.cli_runner import strip_cli_diagnostic_banner
from .helpers.history import (
    _parse_publish_results,
    _push_history_per_row,
    _push_history_single_failure,
)


_scheduler = BackgroundScheduler(
    executors={'default': APSThreadPoolExecutor(max_workers=1)},
    job_defaults={'misfire_grace_time': 3600},
)


# ── Scorig hook ──────────────────────────────────────────────────────────


def _score_after_publish(target_url: str, channel: str) -> str | None:
    try:
        from webui_store.score_store import compute_score, platform_weight_from_dofollow
        from webui_store import score_store as _score_store
        from backlink_publisher.publishing.registry import dofollow_status

        ds = dofollow_status(channel)
        if ds is None:
            plan_logger.warn("score_unknown_dofollow_status", channel=channel)
            return None
        weight = platform_weight_from_dofollow(ds)
        return _score_store.record_publish(
            target_url=target_url,
            channel=channel,
            platform_weight=weight,
        )
    except Exception as exc:
        plan_logger.warn("score_after_publish_failed", channel=channel, error=str(exc))
        return None


# ── Watch service job ────────────────────────────────────────────────────


def _run_watch_cycle() -> None:
    """Execute one watch-service cycle. Called by APScheduler."""
    try:
        from webui_store import seen_urls_store as _seen_urls_store
        from webui_store import history_store as _history_store_watch
        from webui_store import queue_store as _queue_store_watch
        from webui_store import wizard_config_store as _wizard_config_store
        from webui_store import channel_status_store as _channel_status_store
        from .services.watch_service import WatchService

        cfg = _wizard_config_store._get()
        if not cfg.get("completed"):
            return

        service = WatchService(
            seen_urls_store=_seen_urls_store,
            history_store=_history_store_watch,
            queue_store=_queue_store_watch,
            channel_status_store=_channel_status_store,
        )
        report = service.run_once(cfg)
        plan_logger.recon("watch_cycle_complete", report=dict(report))
    except Exception as exc:
        plan_logger.error("watch_cycle_failed", error=str(exc))


def _trigger_watch_cycle() -> None:
    """Trigger an immediate watch cycle (called on wizard completion)."""
    from datetime import timezone as tz

    now = datetime.now(tz.utc)
    if _scheduler.get_job("watch_service"):
        _scheduler.reschedule_job("watch_service", trigger="date", run_date=now)
        plan_logger.recon("watch_cycle_triggered_immediate")
    else:
        plan_logger.warn("watch_job_not_found_for_trigger")


def _register_watch_job() -> None:
    """Register the watch-service polling job (does not start until wizard completes)."""
    _scheduler.add_job(
        _run_watch_cycle,
        trigger="interval",
        seconds=21600,
        id="watch_service",
        name="Seed source polling (watch service)",
        misfire_grace_time=3600,
        replace_existing=True,
        next_run_time=None,
    )


# ── Queue processor ──────────────────────────────────────────────────────


def _process_queue_job() -> None:
    """轮询队列中的 pending 任务并执行发布，支持 429 自动退避。"""
    now = datetime.now()
    # Delegate the status + retry-due filter to the shared, unit-tested
    # QueueStore.get_runnable() helper instead of a divergent inline copy.
    # The two were byte-identical, but the inline copy was the untested one and
    # could silently drift from the helper's semantics.
    pending = _queue_store.get_runnable()

    if not pending:
        return

    task = pending[0]
    task_id = task['id']
    _queue_store.update_task(task_id, {'status': 'processing'})

    try:
        config = task['config']
        urls = task['urls']
        target_url = urls[0] if urls else ''
        
        seed = {
            'target_url': target_url,
            'platform': config.get('platform', 'medium'),
            'language': config.get('target_language', 'zh-CN'),
            'url_mode': config.get('url_mode', 'A'),
            'publish_mode': 'draft',
            'custom_title': config.get('custom_title', ''),
            'custom_tags': config.get('custom_tags', ''),
            'extra_urls': urls[1:] if urls else [],
        }
        
        result = PipelineAPI().publish_seed(json.dumps([seed]))
        if result.success:
            _queue_store.update_task(task_id, {
                'status': 'success',
                'completed_at': now.isoformat()
            })
            # Record score for successful publish
            _score_after_publish(
                target_url=target_url,
                channel=config.get('platform', 'medium'),
            )
        else:
            err = result.error or '发布失败'
            if "429" in err or "Too Many Requests" in err:
                retry_delay = 300
                next_retry = now + timedelta(seconds=retry_delay)
                _queue_store.update_task(task_id, {
                    'status': 'failed',
                    'error': f'频率限制 (429)，将在 {next_retry.strftime("%H:%M")} 重试',
                    'next_retry_at': next_retry.isoformat()
                })
            else:
                _queue_store.update_task(task_id, {
                    'status': 'failed',
                    'error': err,
                    'next_retry_at': None,
                })
    except Exception as exc:
        _queue_store.update_task(task_id, {
            'status': 'failed',
            'error': str(exc) or '发布失败',
            'next_retry_at': None,
        })


def _publish_draft_job(item_id: str) -> None:
    """APScheduler job: publish a draft item and update history."""
    item = _drafts_store.get_item(item_id)
    if not item or item.get('status') != 'scheduled':
        return

    platform = item.get('platform', 'medium')
    publish_mode = item.get('publish_mode', 'draft')
    plans_jsonl = item.get('plans_jsonl', '')

    def _push_history(status, article_urls=None, error=None):
        _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': item.get('target_url', 'unknown'),
            'platform': platform,
            'language': item.get('language', 'zh-CN'),
            'status': status,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': article_urls or [],
            **({'error': error} if error else {}),
        }, *hist][:100])

    try:
        result = PipelineAPI().publish(plans_jsonl, platform, publish_mode)
        if not result.success:
            raise RuntimeError(result.error or '发布失败')
        published = result.stdout

        if not published.strip():
            raise RuntimeError(result.error or '发布失败，无输出')

        publish_results = _parse_publish_results(published)
        article_urls = [
            u for r in publish_results
            for u in ((r.get('published_url'), r.get('draft_url')))
            if u
        ]

        draft_status = 'published'
        any_unverified = any(
            (r.get('status') or '').endswith('_unverified') for r in publish_results
        )
        if any_unverified:
            draft_status = 'published_unverified'
        _drafts_store.update_item(
            item_id, status=draft_status,
            article_urls=article_urls,
            published_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        )
        _score_after_publish(
            target_url=item.get('target_url', 'unknown'),
            channel=platform,
        )

        try:
            _push_history_per_row(
                publish_results,
                target_url_fallback=item.get('target_url', 'unknown'),
                platform_fallback=platform,
                language_fallback=item.get('language', 'zh-CN'),
            )
        except Exception as exc:
            # History write failure must not corrupt draft status — the publish
            # already succeeded and the draft is marked published above.
            plan_logger.warn(
                "draft_history_write_failed", item_id=item_id, error=str(exc)
            )
    except Exception as exc:
        msg = strip_cli_diagnostic_banner(str(exc)) or str(exc)
        _drafts_store.update_item(item_id, status='failed', error=msg)
        _push_history_single_failure(
            target_url=item.get('target_url', 'unknown'),
            platform=platform,
            language=item.get('language', 'zh-CN'),
            error=msg,
        )


def _schedule_draft_job(item_id: str, run_date: datetime) -> None:
    _scheduler.add_job(
        _publish_draft_job, trigger='date', run_date=run_date,
        id=item_id, args=[item_id], replace_existing=True,
    )


def _restore_processing_tasks() -> None:
    """Reset queue tasks left in 'processing' back to 'pending'."""
    _queue_store.update(lambda tasks: [
        {**t, 'status': 'pending'} if t.get('status') == 'processing' else t
        for t in tasks
    ])


def _restore_scheduled_jobs() -> None:
    """On startup, re-register scheduled jobs and register watch service."""
    _restore_processing_tasks()

    _scheduler.add_job(
        _process_queue_job,
        trigger='interval',
        minutes=1,
        id='queue_processor',
        replace_existing=True,
    )

    # Register watch service job (does not run until wizard completes)
    _register_watch_job()
    
    now = datetime.now()
    for item in _drafts_store.load():
        if item.get('status') != 'scheduled':
            continue
        item_id = item.get('id')
        ts = item.get('scheduled_at')
        if not item_id or not ts:
            continue
        try:
            run_date = datetime.fromisoformat(ts)
            if run_date < now:
                run_date = now + timedelta(seconds=5)
            _schedule_draft_job(item_id, run_date)
        except Exception:
            plan_logger.warn("restore_scheduled_job_failed", item_id=item_id, ts=ts)
