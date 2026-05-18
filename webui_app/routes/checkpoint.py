"""/checkpoint/resume + /checkpoint/dismiss — Plan Unit 3."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime

from flask import Blueprint, redirect, request, session

from backlink_publisher import checkpoint as _checkpoint_mod
from webui_store import history_store as _history_store

from ..helpers import (
    _check_localhost,
    _parse_publish_results,
    _render,
    _validate_webui_run_id,
)

bp = Blueprint("checkpoint", __name__)


@bp.route("/checkpoint/resume", methods=["POST"])
def checkpoint_resume():
    _check_localhost()
    run_id = request.form.get("run_id", "")
    _validate_webui_run_id(run_id)

    cmd = ["publish-backlinks", "--resume", run_id]
    result = subprocess.run(
        cmd, input="", capture_output=True, text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))) or os.getcwd(),
    )

    publish_results = _parse_publish_results(result.stdout)
    config = session.get("config", {})
    platform = publish_results[0].get("platform", "unknown") if publish_results else "unknown"

    if result.returncode == 0:
        history = _history_store.update(lambda hist: [{
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "published",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [r.get("published_url") or r.get("draft_url", "")
                             for r in publish_results if r],
        }, *hist][:100])
        return _render('index.html',
            publish_results=publish_results, config=config,
            history=history, history_active=True,
            flash={"type": "success", "msg": f"恢复发布成功，共 {len(publish_results)} 篇"})
    elif result.returncode == 4:
        done = [r for r in publish_results if r.get("error") is None]
        _history_store.update(lambda hist: [{
            "id": str(uuid.uuid4())[:8],
            "target_url": config.get("target_url", "unknown"),
            "platform": platform,
            "language": config.get("target_language", "zh-CN"),
            "status": "failed_partial",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "article_urls": [r.get("published_url") or r.get("draft_url", "")
                             for r in done],
            "stderr_summary": result.stderr[:500] if result.stderr else "",
        }, *hist][:100])
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
    except Exception:
        pass
    return redirect("/")
