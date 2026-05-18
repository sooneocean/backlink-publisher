"""APScheduler for draft-queue background publishing — Plan Unit 3.

Single-worker scheduler (max_workers=1) so two drafts never publish
simultaneously. _publish_draft_job is registered per-item at schedule
time and at startup via _restore_scheduled_jobs.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from apscheduler.executors.pool import ThreadPoolExecutor as APSThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from webui_store import drafts_store as _drafts_store
from webui_store import history_store as _history_store

from .helpers import _parse_publish_results, run_pipe


_scheduler = BackgroundScheduler(
    executors={'default': APSThreadPoolExecutor(max_workers=1)},
    job_defaults={'misfire_grace_time': 3600},
)


def _publish_draft_job(item_id: str) -> None:
    """APScheduler job: publish a draft item and update history."""
    item = _drafts_store.get_item(item_id)
    if not item or item.get('status') != 'scheduled':
        return

    platform = item.get('platform', 'medium')
    publish_mode = item.get('publish_mode', 'draft')
    plans_jsonl = item.get('plans_jsonl', '')

    try:
        cmd = ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
        result = run_pipe(cmd, plans_jsonl)
        published = result['stdout']

        if not published.strip():
            raise RuntimeError(result.get('stderr') or '发布失败，无输出')

        publish_results = _parse_publish_results(published)
        article_urls = [r.get('published_url') or r.get('draft_url', '')
                        for r in publish_results if r]
        article_urls = [u for u in article_urls if u]

        _drafts_store.update_item(
            item_id, status='published',
            article_urls=article_urls,
            published_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        )
        _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': item.get('target_url', 'unknown'),
            'platform': platform,
            'language': item.get('language', 'zh-CN'),
            'status': 'drafted' if publish_mode == 'draft' else 'published',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': article_urls,
        }, *hist][:100])
    except Exception as exc:
        _drafts_store.update_item(item_id, status='failed', error=str(exc))
        _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': item.get('target_url', 'unknown'),
            'platform': platform,
            'language': item.get('language', 'zh-CN'),
            'status': 'failed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [],
            'error': str(exc),
        }, *hist][:100])


def _restore_scheduled_jobs() -> None:
    """On startup, re-register any 'scheduled' draft items into APScheduler."""
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
            _scheduler.add_job(
                _publish_draft_job,
                trigger='date',
                run_date=run_date,
                id=item_id,
                args=[item_id],
                replace_existing=True,
            )
        except Exception:
            pass
