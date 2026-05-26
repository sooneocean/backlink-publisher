"""/checkpoint/resume + /checkpoint/dismiss — Plan Unit 3."""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime

from flask import Blueprint, redirect, request, session

from backlink_publisher import checkpoint as _checkpoint_mod
from backlink_publisher._util.logger import plan_logger
from ..helpers.contexts import _render
from ..helpers.cli_runner import _REPO_ROOT, _rewrite_cli_cmd
from ..helpers.security import _check_localhost, _validate_webui_run_id
from ..helpers.history import _parse_publish_results, _push_history_aggregate

bp = Blueprint("checkpoint", __name__)


@bp.route("/checkpoint/resume", methods=["POST"])
def checkpoint_resume():
    _check_localhost()
    run_id = request.form.get("run_id", "")
    _validate_webui_run_id(run_id)

    cmd, env = _rewrite_cli_cmd(["publish-backlinks", "--resume", run_id])
    result = subprocess.run(
        cmd, input="", capture_output=True, text=True,
        cwd=_REPO_ROOT, env=env,
    )

    publish_results = _parse_publish_results(result.stdout)
    config = session.get("config", {})
    platform = publish_results[0].get("platform", "unknown") if publish_results else "unknown"

    if result.returncode == 0:
        article_urls = [u for u in (
            (r.get("published_url") or r.get("draft_url", ""))
            for r in publish_results if r
        ) if u]
        # exit 0 + 无可解析结果 / 无 URL = stale checkpoint or silent no-op.
        # Do NOT persist a fake "published" row — mirror the invariant that
        # _push_history_per_row enforces for the other publish routes
        # (Plan 2026-05-19-006 Unit 1).
        if not publish_results or not article_urls:
            return _render('index.html', config=config, history_active=True,
                flash={"type": "warning",
                       "msg": "没有可恢复的发布任务（checkpoint 已无待处理项），未写入历史记录"})
        history = _push_history_aggregate({
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "published",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": article_urls,
        })
        return _render('index.html',
            publish_results=publish_results, config=config,
            history=history, history_active=True,
            flash={"type": "success", "msg": f"恢复发布成功，共 {len(publish_results)} 篇"})
    elif result.returncode == 4:
        done = [r for r in publish_results if r.get("error") is None]
        _push_history_aggregate({
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "failed_partial",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [r.get("published_url") or r.get("draft_url", "")
                             for r in done],
            "stderr_summary": result.stderr[:500] if result.stderr else "",
        })
        return _render('index.html',
            publish_results=publish_results, config=config,
            history_active=True,
            error=f"部分发布失败。{result.stderr[:200] if result.stderr else ''}")
    else:
        return _render('index.html', config=config,
            error=f"恢复发布失败 (exit {result.returncode}): {result.stderr[:300] if result.stderr else ''}")


@bp.route("/checkpoint/dismiss", methods=["POST"])
def checkpoint_dismiss():
    _check_localhost()
    run_id = request.form.get("run_id", "")
    _validate_webui_run_id(run_id)
    try:
        _checkpoint_mod.delete(run_id)
    except FileNotFoundError:
        # Idempotent dismiss: the checkpoint is already gone, which is exactly
        # the operator's intent. Benign — keep the success redirect.
        pass
    except Exception as exc:
        # Genuine delete failure (e.g. permission/OS error): the checkpoint is
        # still present. Do NOT pretend it was dismissed — surface it.
        plan_logger.warn("checkpoint_dismiss_failed", run_id=run_id,
                         reason=type(exc).__name__)
        return redirect("/?flash_type=danger&flash_msg=删除检查点失败，该检查点仍然存在")
    return redirect("/")
