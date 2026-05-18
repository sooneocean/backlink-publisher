"""/ce:batch + /ce:publish-real — Plan Unit 3."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from datetime import datetime

from flask import Blueprint, request, session

from webui_store import history_store as _history_store

from ..helpers import (
    _REPO_ROOT,
    _parse_publish_results,
    _render,
    _rewrite_cli_cmd,
    get_main_domain,
    run_pipe,
)

bp = Blueprint("batch", __name__)


@bp.route('/ce:batch', methods=['POST'])
def ce_batch():
    """Batch publish: process multiple target URLs through the full pipeline."""
    urls_text = request.form.get('batch_urls', '').strip()
    platform = request.form.get('platform', 'blogger')
    language = request.form.get('language', 'zh-CN')
    url_mode = request.form.get('url_mode', 'A')
    publish_mode = request.form.get('publish_mode', 'draft')

    raw_urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
    if not raw_urls:
        return _render('index.html', error="请输入至少一个网址", batch_tab=True,
                       batch_urls=urls_text, config={})

    urls = []
    for u in raw_urls:
        if not u.startswith(('http://', 'https://')):
            u = 'https://' + u
        urls.append(u)

    if platform == 'blogger':
        try:
            from backlink_publisher.config import load_config as _load_cfg
            from backlink_publisher.config import resolve_blog_id as _resolve
            _cfg = _load_cfg()
            first_domain = get_main_domain(urls[0])
            _resolve(_cfg, first_domain)
        except Exception as _pre_err:
            if 'blog_id' in str(_pre_err).lower() or 'DependencyError' in type(_pre_err).__name__:
                friendly = (
                    f"❌ Blogger Blog ID 未配置。"
                    f"请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                    f"设置 → Blogger Blog ID 映射</a> 添加对应条目。"
                )
                return _render('index.html', error=friendly, batch_tab=True,
                               batch_urls=urls_text, config={})

    seed_jsonl = '\n'.join(
        json.dumps({
            'target_url': u,
            'main_domain': get_main_domain(u),
            'platform': platform,
            'language': language,
            'url_mode': url_mode,
            'publish_mode': publish_mode,
        }, ensure_ascii=False)
        for u in urls
    )

    try:
        plan_res = run_pipe(['plan-backlinks'], seed_jsonl)
    except Exception as e:
        return _render('index.html', error=f"计划阶段失败: {e}", batch_tab=True,
                       batch_urls=urls_text, config={})

    try:
        val_res = run_pipe(['validate-backlinks', '--no-check-urls'], plan_res['stdout'])
    except Exception as e:
        return _render('index.html', error=f"验证阶段失败: {e}", batch_tab=True,
                       batch_urls=urls_text, config={})

    pub_cmd, pub_env = _rewrite_cli_cmd(
        ['publish-backlinks', '--platform', platform, '--mode', publish_mode]
    )
    pub_result = subprocess.run(
        pub_cmd,
        input=val_res['stdout'],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT or os.getcwd(),
        env=pub_env,
    )
    publish_results = _parse_publish_results(pub_result.stdout)

    result_by_url = {r.get('target_url', ''): r for r in publish_results}
    results = []
    for url in urls:
        r = result_by_url.get(url) or result_by_url.get(url.rstrip('/') + '/')
        if r and not r.get('error'):
            article_url = r.get('published_url') or r.get('draft_url', '')
            results.append({
                'url': url, 'status': 'success',
                'article_url': article_url or '',
                'title': r.get('title', ''),
            })
        elif r and r.get('error'):
            results.append({
                'url': url, 'status': 'failed', 'article_url': '',
                'title': r.get('title', ''), 'error': r.get('error', ''),
            })
        else:
            err_hint = pub_result.stderr[:200] if pub_result.stderr else 'no output'
            results.append({
                'url': url, 'status': 'failed', 'article_url': '',
                'title': '', 'error': err_hint,
            })

    success_results = [r for r in results if r['status'] == 'success']
    if success_results:
        article_urls = [r['article_url'] for r in success_results if r['article_url']]
        _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': urls[0] if urls else 'batch',
            'platform': platform,
            'language': language,
            'status': 'drafted' if publish_mode == 'draft' else 'published',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': article_urls,
        }, *hist][:100])

    return _render('index.html', batch_results=results, batch_tab=True,
                   batch_urls=urls_text, config={})


@bp.route('/ce:publish-real', methods=['POST'])
def ce_publish_real():
    """Real publish (mode=publish, not dry-run)."""
    validated = request.form.get('validated', '')
    platform = request.form.get('platform', 'medium')
    config = session.get('config', {})

    if platform == 'blogger':
        try:
            from backlink_publisher.config import load_config as _load_cfg
            from backlink_publisher.config import resolve_blog_id as _resolve
            _cfg = _load_cfg()
            _main_domain = config.get('main_domain', '')
            if _main_domain:
                _resolve(_cfg, _main_domain)
        except Exception as _pre_err:
            if 'blog_id' in str(_pre_err).lower() or 'DependencyError' in type(_pre_err).__name__:
                friendly = (
                    f"❌ Blogger Blog ID 未配置：域名 <code>{config.get('main_domain', '?')}</code> "
                    f"尚未绑定 Blog ID。<br><br>"
                    f"请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                    f"设置 → Blogger Blog ID 映射</a> 添加对应条目。"
                )
                return _render('index.html', error=friendly,
                               config=config, history_active=True)

    try:
        cmd = ['publish-backlinks', '--platform', platform, '--mode', 'publish']
        result = run_pipe(cmd, validated)
        published = result['stdout']

        if not published.strip():
            return _render('index.html',
                error=result['stderr'] or "发布失败",
                config=config, history_active=True)

        publish_results = _parse_publish_results(published)
        article_urls = [r.get('published_url') or r.get('draft_url', '')
                        for r in publish_results if r]
        history = _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'success',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [u for u in article_urls if u],
        }, *hist][:100])

        return _render('index.html', published=published,
            publish_results=publish_results, config=config,
            history=history, history_active=True)

    except Exception as e:
        history = _history_store.update(lambda hist: [{
            'id': str(uuid.uuid4())[:8],
            'target_url': config.get('target_url', 'unknown'),
            'platform': platform,
            'language': config.get('target_language', 'zh-CN'),
            'status': 'failed',
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'article_urls': [],
            'error': str(e),
        }, *hist][:100])

        return _render('index.html', error=f"发布失败: {str(e)}",
            config=config, history=history, history_active=True)
