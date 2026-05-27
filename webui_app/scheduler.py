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

def _process_queue_job() -> None:
    """轮询队列中的 pending 任务并执行发布，支持 429 自动退避。"""
    tasks = _queue_store.load()
    now = datetime.now()
    
    # 查找任务：PENDING 且 不在退避时间内
    pending = [t for t in tasks if t.get('status') in ('pending', 'failed') 
               and (not t.get('next_retry_at') or datetime.fromisoformat(t['next_retry_at']) <= now)]
    
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
        else:
            # publish failed — capture the typed error, detect 429 backoff.
            # The string check is kept (not error_class) so backoff fires even
            # when the rate-limit surfaces only in the message text.
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
                    'error': err
                })
    except Exception as exc:
        # Defensive: PipelineAPI returns a PipeResult rather than raising, so
        # this catches only seed-construction / store errors — still mark the
        # task failed rather than leaving it wedged in 'processing'.
        _queue_store.update_task(task_id, {
            'status': 'failed',
            'error': str(exc) or '发布失败'
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

        # Reflect aggregate outcome on the draft row itself. If any row is
        # `*_unverified`, the draft is marked `published_unverified` so the
        # UI badge tells the truth even before recheck runs.
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

        # Plan 2026-05-19-006 Unit 1: per-row truth-propagation. The old
        # implementation hard-wrote `'drafted'` / `'published'` regardless
        # of per-row `status`, hiding `*_unverified` rows as solid ✓.
        _push_history_per_row(
            publish_results,
            target_url_fallback=item.get('target_url', 'unknown'),
            platform_fallback=platform,
            language_fallback=item.get('language', 'zh-CN'),
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


def _restore_scheduled_jobs() -> None:
    """On startup, re-register any 'scheduled' draft items into APScheduler."""
    _scheduler.add_job(
        _process_queue_job,
        trigger='interval',
        minutes=1,
        id='queue_processor',
        replace_existing=True,
    )
    
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
