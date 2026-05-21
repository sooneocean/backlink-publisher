"""/ce:batch + /ce:publish-real — Plan Unit 3."""

from __future__ import annotations

import json
import os
import subprocess

from flask import Blueprint, request, session

from backlink_publisher.config import load_config as _load_cfg, resolve_blog_id as _resolve

from ..helpers import (
    _REPO_ROOT,
    _parse_publish_results,
    _push_history_per_row,
    _push_history_single_failure,
    _render,
    _rewrite_cli_cmd,
    run_pipe,
)
from ..helpers.url_meta import get_main_domain

bp = Blueprint("batch", __name__)


def _check_blogger_blog_id(domain: str) -> str | None:
    """Return error HTML if blog_id missing, None if OK."""
    try:
        _resolve(_load_cfg(), domain)
    except Exception as exc:
        if 'blog_id' in str(exc).lower() or 'DependencyError' in type(exc).__name__:
            return (
                "❌ Blogger Blog ID 未配置。"
                "请前往 <a href='/settings#blogger-blog-ids' style='color:var(--primary);font-weight:600;'>"
                "设置 → Blogger Blog ID 映射</a> 添加对应条目。"
            )
    return None


@bp.route('/ce:batch', methods=['POST'])
def ce_batch():
    """Batch publish: process multiple target URLs through the full pipeline."""
    urls_text = request.form.get('batch_urls', '').strip()
    platform = request.form.get('platform', 'blogger')
    # Plan 013 U2: converge field name to `target_language`; keep `language` as
    # backwards-compat fallback for any caller still using the old field name.
    language = (
        request.form.get('target_language')
        or request.form.get('language', 'zh-CN')
    )
    url_mode = request.form.get('url_mode', 'C')
    publish_mode = request.form.get('publish_mode', 'publish')

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
        err = _check_blogger_blog_id(get_main_domain(urls[0]))
        if err:
            return _render('index.html', error=err, batch_tab=True,
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

    # Plan 2026-05-19-006 Unit 1: per-row history with real status carried
    # forward (including `*_unverified` suffixes). The CLI stdout already
    # only contains rows whose adapter did not raise — `_unverified` rows
    # are written with error=None, which is precisely the assumption we
    # used to drop. Now we keep them as their real status.
    if publish_results:
        _push_history_per_row(
            publish_results,
            target_url_fallback=urls[0] if urls else 'batch',
            platform_fallback=platform,
            language_fallback=language,
        )

    return _render('index.html', batch_results=results, batch_tab=True,
                   batch_urls=urls_text, config={})


@bp.route('/ce:publish-real', methods=['POST'])
def ce_publish_real():
    """Real publish (mode=publish, not dry-run)."""
    validated = request.form.get('validated', '')
    platform = request.form.get('platform', 'blogger')
    config = session.get('config', {})

    if platform == 'blogger':
        main_domain = config.get('main_domain', '')
        if main_domain:
            err = _check_blogger_blog_id(main_domain)
            if err:
                return _render('index.html', error=err,
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
        # Plan 2026-05-19-006 Unit 1: per-row truth-propagation. Previously
        # one history row hard-coded status='success' regardless of per-row
        # outcome (notably `*_unverified` rows showed as ✓ on UI).
        history = _push_history_per_row(
            publish_results,
            target_url_fallback=config.get('target_url', 'unknown'),
            platform_fallback=platform,
            language_fallback=config.get('target_language', 'zh-CN'),
        )

        return _render('index.html', published=published,
            publish_results=publish_results, config=config,
            history=history, history_active=True)

    except Exception as e:
        history = _push_history_single_failure(
            target_url=config.get('target_url', 'unknown'),
            platform=platform,
            language=config.get('target_language', 'zh-CN'),
            error=str(e),
        )

        return _render('index.html', error=f"发布失败: {str(e)}",
            config=config, history=history, history_active=True)
