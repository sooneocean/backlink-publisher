"""Bind-channel subprocess registry — Plan 2026-05-19-001 Unit 4.

Spawns ``bind-channel --channel <name>`` as a subprocess, streams its
JSONL stdout into an in-memory event list, and exposes a poll API for
the WebUI to render progress + terminal status.

Public API:
  - ``registry`` — singleton ``BindJobRegistry``
  - ``BIND_ERROR_MESSAGES`` — error_code → Chinese operator message
  - ``reap_orphans()`` — startup hook (no-op for v1's in-memory registry)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from backlink_publisher._util.errors import UsageError
from backlink_publisher.cli._bind.channels import CHANNELS


BIND_ERROR_MESSAGES: dict[str, str] = {
    "bound_predicate_timeout": "登录超时，请在 5 分钟内完成浏览器登录后重试",
    "playwright_launch_failed": "无法启动浏览器，请确认已安装 Playwright（运行 `playwright install chromium`）",
    "storage_path_traversal": "凭据路径校验失败（内部错误），请检查 BACKLINK_PUBLISHER_CONFIG_DIR 是否被异常覆盖",
    "persist_io_error": "无法写入凭据文件，请检查磁盘空间和权限",
    "stream_closed_no_terminal_event": "子进程意外退出，请查看 stderr 日志",
    # Plan 2026-05-20-016 Unit 0 Fix 1. The bind recipe is missing
    # cookie_host_filter — chrome backend refuses to persist cookies
    # because the unfiltered cookie jar would include cross-domain
    # entries from the operator's real Chrome profile (security
    # blast-radius). Should never reach operators; if it does, the
    # recipe needs a code fix.
    "recipe_missing_host_filter": "配置错误：channel recipe 缺少 cookie_host_filter（开发者错误）。请联系开发者升级 backlink-publisher 版本。",
    # Plan 2026-05-19-003 Unit 1 + Unit 4. The predicate scraped a
    # @username different from the previously-bound account. The
    # Settings UI renders a confirmation card (keep vs replace) when
    # channel_status_store[<channel>].status == "identity_mismatch".
    "identity_mismatch": "检测到登录账号变更，请在设置页选择保留旧账号或替换为新账号",
}


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_SRC_DIR = os.path.join(_REPO_ROOT, "src")


@dataclass
class BindJob:
    id: str
    channel: str
    status: str  # "running" | "done" | "failed"
    started_at: str
    proc: subprocess.Popen | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    error_code: str | None = None
    reason: str | None = None


class BindJobRegistry:
    """In-memory job registry. v1 has no persistence — ``reap_orphans`` is
    a documented no-op; the call site exists so a future persistent
    registry inherits reaping for free."""

    def __init__(self) -> None:
        self._jobs: dict[str, BindJob] = {}
        self._lock = threading.Lock()
        self._popen = subprocess.Popen  # injectable for tests

    def start(self, channel: str) -> BindJob:
        if channel not in CHANNELS:
            raise UsageError(
                f"bind_job: unknown channel {channel!r} "
                f"(allowed: {sorted(CHANNELS)})"
            )

        with self._lock:
            for job in self._jobs.values():
                if job.channel == channel and job.status == "running":
                    raise UsageError(
                        f"bind_job: channel {channel!r} already has a "
                        f"running bind job ({job.id})"
                    )

            job_id = uuid.uuid4().hex
            env = os.environ.copy()
            env["PYTHONPATH"] = _SRC_DIR + (
                os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
            )
            cmd = [
                sys.executable, "-m", "backlink_publisher.cli.bind_channel",
                "--channel", channel,
            ]
            try:
                proc = self._popen(
                    cmd,
                    cwd=_REPO_ROOT,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise UsageError(
                    f"bind_job: failed to spawn bind-channel: {exc}"
                ) from exc

            job = BindJob(
                id=job_id,
                channel=channel,
                status="running",
                started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                proc=proc,
            )
            self._jobs[job_id] = job

        reader = threading.Thread(
            target=self._drain_stdout, args=(job,), daemon=True,
            name=f"bind-job-{job_id[:8]}",
        )
        reader.start()
        return job

    def _drain_stdout(self, job: BindJob) -> None:
        proc = job.proc
        assert proc is not None and proc.stdout is not None
        terminal_event_seen = False
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                with self._lock:
                    job.events.append(payload)
                event_name = payload.get("event")
                if event_name == "channel.bind.persisted":
                    terminal_event_seen = True
                elif event_name == "channel.bind.failed":
                    terminal_event_seen = True
                    with self._lock:
                        job.error_code = payload.get("error_code")
                        job.reason = payload.get("reason")
                    # Plan 2026-05-19-003 Unit 1 + Unit 4: identity_mismatch
                    # is a special failure that needs the channel_status_store
                    # flipped so the Settings UI renders the keep/replace
                    # confirmation card. Driver's BindResult.extras carried
                    # old_account + new_account through the CLI as JSONL
                    # payload fields; we read them back here.
                    if payload.get("error_code") == "identity_mismatch":
                        old = payload.get("old_account")
                        new = payload.get("new_account")
                        # PR #83 adversarial review: reject same-string and
                        # empty payloads before reaching the store. The
                        # store also guards (mark_identity_mismatch returns
                        # early on these), but skipping the import + lock
                        # acquisition is cheap and makes the intent local.
                        if old and new and old != new:
                            try:
                                from webui_store.channel_status import (
                                    mark_identity_mismatch,
                                )
                                mark_identity_mismatch(
                                    job.channel,
                                    old_account=str(old),
                                    new_account=str(new),
                                )
                            except Exception:  # noqa: BLE001
                                # Store-write failure shouldn't crash the
                                # reader thread; the failed event is already
                                # recorded on the BindJob for the poll API.
                                pass
        finally:
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait()
            with self._lock:
                if not terminal_event_seen:
                    job.status = "failed"
                    job.error_code = job.error_code or "stream_closed_no_terminal_event"
                elif exit_code == 0:
                    job.status = "done"
                else:
                    job.status = "failed"

    def poll(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            error_code = job.error_code
            error_message = None
            if error_code:
                error_message = BIND_ERROR_MESSAGES.get(
                    error_code, f"bind failed: {error_code}"
                )
            return {
                "job_id": job.id,
                "channel": job.channel,
                "status": job.status,
                "started_at": job.started_at,
                "events": list(job.events),
                "error_code": error_code,
                "error_message": error_message,
            }

    def reset_for_tests(self) -> None:
        with self._lock:
            self._jobs.clear()


registry = BindJobRegistry()


def reap_orphans() -> None:
    """Startup hook called by ``create_app`` after blueprint registration.

    v1's registry is in-memory only — there is no persistent bind-job
    state to reap across process restarts. This function is a documented
    no-op so that a future persistent registry inherits reaping for free
    just by adding logic here; callers stay untouched.
    """
    # Intentionally empty for v1 — see docstring.
    return None


__all__ = [
    "BIND_ERROR_MESSAGES",
    "BindJob",
    "BindJobRegistry",
    "registry",
    "reap_orphans",
]
