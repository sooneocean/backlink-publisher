"""Tests for WebUI checkpoint banner and resume/dismiss routes."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Skip entire module if Flask is not installed (CI without webui deps)
pytest.importorskip("flask")

# We need to import webui without running the Flask dev server startup.
# webui.py uses sys.path.insert at module level, so we add the src dir here too.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import webui as _webui


@pytest.fixture()
def client(tmp_path):
    _webui.app.config["TESTING"] = True
    _webui.app.config["WTF_CSRF_ENABLED"] = False
    _webui.app.secret_key = "test-secret"
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        with _webui.app.test_client() as c:
            yield c


def _incomplete_run_fixture():
    return {
        "run_id": "20260101T000000-abcdef01",
        "started_at": "2026-01-01T00:00:00+00:00",
        "platform": "blogger",
        "mode": "draft",
        "status": None,
        "items": [
            {"id": "r0", "status": "pending", "title": "T", "platform": "blogger",
             "adapter": None, "published_url": None, "error": None, "error_class": None,
             "completed_at": None, "payload": {}},
        ],
        "pending_count": 1,
    }


# ── _load_incomplete_run ───────────────────────────────────────────────────────

def test_load_incomplete_run_returns_most_recent(tmp_path):
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        from backlink_publisher.checkpoint import create_checkpoint
        rows = [{"id": "r0", "title": "T", "platform": "blogger"}]
        create_checkpoint(rows, platform="blogger", mode="draft")

        result = _webui._load_incomplete_run()
        assert result is not None
        assert "run_id" in result
        assert "pending_count" in result
        assert result["pending_count"] == 1


def test_load_incomplete_run_returns_none_when_empty(tmp_path):
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        result = _webui._load_incomplete_run()
        assert result is None


def test_load_incomplete_run_returns_none_on_exception():
    with patch.object(_webui._checkpoint_mod, "list_incomplete", side_effect=Exception("disk error")):
        result = _webui._load_incomplete_run()
        assert result is None


# ── banner rendering ───────────────────────────────────────────────────────────

def test_banner_shown_when_incomplete_run(client):
    with patch("webui_app.helpers.contexts._load_incomplete_run", return_value=_incomplete_run_fixture()):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        # banner contains the run_id and the dismiss route (unique to banner, not JS)
        assert "20260101T000000-abcdef01" in body
        assert 'action="/checkpoint/dismiss"' in body


def test_banner_absent_when_no_incomplete_run(client):
    with patch("webui_app.helpers.contexts._load_incomplete_run", return_value=None):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        # dismiss form only renders when banner is shown
        assert 'action="/checkpoint/dismiss"' not in body


# ── /checkpoint/resume ─────────────────────────────────────────────────────────

def test_resume_route_exit0_appends_history(client, tmp_path):
    done_jsonl = json.dumps({
        "id": "r0", "platform": "blogger", "status": "done",
        "title": "T", "draft_url": "", "published_url": "https://x.com",
        "created_at": "2026-01-01T00:00:00+00:00", "adapter": "blogger-api", "error": None,
    })
    capture = {"stdout": done_jsonl + "\n", "stderr": "", "returncode": 0}

    with patch("webui_app.api.pipeline_api.run_pipe_capture", return_value=capture):
        with patch.object(__import__("webui_store").base.JsonStore, "update", return_value=[]) as mock_hist:
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            mock_hist.assert_called_once()
            # After Plan Unit 3, the route calls history_store.update(lambda hist: [...]).
            # Patching JsonStore.update replaces the descriptor — the instance no
            # longer binds, so call_args[0][0] is the updater lambda directly.
            # Apply it to recover the dict that would have been prepended.
            updater = mock_hist.call_args[0][0]
            result = updater([])
            assert result[0]["status"] == "published"


def test_resume_route_exit0_empty_stdout_does_not_persist_fake_published(client):
    """Regression: exit 0 + empty stdout (stale checkpoint with no work left)
    must NOT write a status='published' row with article_urls=[].

    Before this guard, _checkpoint_path → ``publish-backlinks --resume`` could
    return 0 with no output (nothing pending), and the route still appended a
    {status:'published', platform:'unknown', article_urls:[]} entry, giving
    operators a green check for a publish that never happened."""
    capture = {"stdout": "", "stderr": "", "returncode": 0}

    with patch("webui_app.api.pipeline_api.run_pipe_capture", return_value=capture):
        with patch.object(__import__("webui_store").base.JsonStore, "update", return_value=[]) as mock_hist:
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            # The regression signal is no history write — proves the route
            # no longer persists a fake "published" entry.
            mock_hist.assert_not_called()


def test_resume_route_exit0_results_without_urls_does_not_persist_fake_published(client):
    """Regression: exit 0 + parsed rows but all URLs blank must NOT write a
    status='published' row. Adapter returning success with empty URL is a
    silent no-op, not a real publish."""
    no_url_jsonl = json.dumps({
        "id": "r0", "platform": "blogger", "status": "done",
        "title": "T", "draft_url": "", "published_url": "",
        "created_at": "2026-01-01T00:00:00+00:00", "adapter": "blogger-api", "error": None,
    })
    capture = {"stdout": no_url_jsonl + "\n", "stderr": "", "returncode": 0}

    with patch("webui_app.api.pipeline_api.run_pipe_capture", return_value=capture):
        with patch.object(__import__("webui_store").base.JsonStore, "update", return_value=[]) as mock_hist:
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            mock_hist.assert_not_called()


def test_resume_route_exit4_shows_partial(client, tmp_path):
    done_jsonl = json.dumps({
        "id": "r0", "platform": "blogger", "status": "done",
        "title": "T", "draft_url": "", "published_url": "https://x.com",
        "created_at": "t", "adapter": "blogger-api", "error": None,
    })
    # Long stderr (> the old 200-char cap) with the config_echo banner prepended,
    # to prove the truncation fix: the operator sees the full banner-stripped
    # error, not a 200-char slice and not the banner.
    banner = (
        "[publish-backlinks] effective config:\n"
        "  config:    /tmp/x.toml\n"
        "  env:       (none)\n"
        "  platforms: blogger\n"
        "  sha:       0123456789abcdef\n"
    )
    real_error = "item r1 still failed: " + ("X" * 400) + " END_OF_ERROR"
    mock_result = {"stdout": done_jsonl + "\n", "stderr": banner + real_error,
                   "returncode": 4}

    with patch("webui_app.api.pipeline_api.run_pipe_capture", return_value=mock_result):
        with patch.object(__import__("webui_store").base.JsonStore, "update", return_value=[]):
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "部分发布失败" in body or "failed" in body.lower()
            # Full error surfaced (banner stripped, not truncated at 200 chars).
            assert "END_OF_ERROR" in body
            assert "effective config:" not in body


def test_resume_route_exit2_no_history(client, tmp_path):
    capture = {"stdout": "", "stderr": "checkpoint not found", "returncode": 2}

    with patch("webui_app.api.pipeline_api.run_pipe_capture", return_value=capture):
        with patch.object(__import__("webui_store").base.JsonStore, "update", return_value=[]) as mock_hist:
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            mock_hist.assert_not_called()


def test_resume_route_rejects_non_localhost(client):
    resp = client.post(
        "/checkpoint/resume",
        data={"run_id": "20260101T000000-abcdef01"},
        environ_base={"REMOTE_ADDR": "1.2.3.4"},
    )
    assert resp.status_code == 403


def test_resume_route_rejects_invalid_run_id(client):
    resp = client.post("/checkpoint/resume", data={"run_id": "../etc/passwd"})
    assert resp.status_code == 400


def test_resume_route_uses_run_pipe_capture_not_raising_run_pipe(client):
    """Guard: resume must use the NON-raising ``run_pipe_capture`` (preserves
    stdout + returncode so exit-4 partial-publish rows survive), never the
    raising ``run_pipe`` (which discards stdout on any non-zero exit — the
    original reason this route hand-rolled ``subprocess.run``)."""
    capture = {"stdout": "", "stderr": "", "returncode": 0}

    with patch("webui_app.api.pipeline_api.run_pipe_capture",
               return_value=capture) as mock_capture:
        with patch("webui_app.helpers.cli_runner.run_pipe") as mock_run_pipe:
            client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            mock_run_pipe.assert_not_called()
            mock_capture.assert_called_once()


# ── /checkpoint/dismiss ────────────────────────────────────────────────────────

def test_dismiss_deletes_checkpoint_and_redirects(client, tmp_path):
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        from backlink_publisher.checkpoint import create_checkpoint
        rows = [{"id": "r0", "title": "T", "platform": "blogger"}]
        run_id, path = create_checkpoint(rows, platform="blogger", mode="draft")

        resp = client.post("/checkpoint/dismiss", data={"run_id": run_id})
        assert resp.status_code in (302, 200)
        assert not path.exists()


def test_dismiss_genuine_delete_failure_surfaces_danger_flash(client):
    """Plan 009 Unit 1: a real delete failure (not FileNotFoundError) must NOT
    redirect to a clean '/' as if dismissed — it surfaces a danger flash and
    logs, because the checkpoint is still present."""
    with patch.object(_webui._checkpoint_mod, "delete",
                       side_effect=PermissionError("locked")):
        with patch("webui_app.routes.checkpoint.plan_logger.warn") as mock_warn:
            resp = client.post("/checkpoint/dismiss",
                               data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 302
            assert "flash_type=danger" in resp.headers["Location"]
            mock_warn.assert_called_once()
            assert mock_warn.call_args[0][0] == "checkpoint_dismiss_failed"
            assert mock_warn.call_args[1]["reason"] == "PermissionError"


def test_dismiss_missing_checkpoint_is_benign_success(client):
    """Plan 009 Unit 1: dismissing an already-gone checkpoint (FileNotFoundError)
    is idempotent — keep the plain success redirect, no danger flash, no log."""
    with patch.object(_webui._checkpoint_mod, "delete",
                       side_effect=FileNotFoundError("checkpoint not found")):
        with patch("webui_app.routes.checkpoint.plan_logger.warn") as mock_warn:
            resp = client.post("/checkpoint/dismiss",
                               data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 302
            assert "flash_type=danger" not in resp.headers["Location"]
            mock_warn.assert_not_called()


def test_dismiss_rejects_non_localhost(client):
    resp = client.post(
        "/checkpoint/dismiss",
        data={"run_id": "20260101T000000-abcdef01"},
        environ_base={"REMOTE_ADDR": "1.2.3.4"},
    )
    assert resp.status_code == 403


def test_dismiss_rejects_invalid_run_id(client):
    resp = client.post("/checkpoint/dismiss", data={"run_id": "../../evil"})
    assert resp.status_code == 400


# ── integration: page load reflects banner state ───────────────────────────────

def test_banner_absent_after_dismiss(client, tmp_path):
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        from backlink_publisher.checkpoint import create_checkpoint
        rows = [{"id": "r0", "title": "T", "platform": "blogger"}]
        run_id, _ = create_checkpoint(rows, platform="blogger", mode="draft")

        # before dismiss: banner has the run_id
        resp = client.get("/")
        assert run_id in resp.data.decode()

        # dismiss
        client.post("/checkpoint/dismiss", data={"run_id": run_id})

        # after dismiss: run_id no longer in page (banner absent)
        resp = client.get("/")
        assert run_id not in resp.data.decode()
