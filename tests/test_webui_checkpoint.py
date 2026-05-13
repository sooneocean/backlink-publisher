"""Tests for WebUI checkpoint banner and resume/dismiss routes."""

from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

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
    with patch.object(_webui, "_load_incomplete_run", return_value=_incomplete_run_fixture()):
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode()
        # banner contains the run_id and the dismiss route (unique to banner, not JS)
        assert "20260101T000000-abcdef01" in body
        assert 'action="/checkpoint/dismiss"' in body


def test_banner_absent_when_no_incomplete_run(client):
    with patch.object(_webui, "_load_incomplete_run", return_value=None):
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
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = done_jsonl + "\n"
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result):
        with patch.object(_webui, "_append_history", return_value=[]) as mock_hist:
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            mock_hist.assert_called_once()
            call_arg = mock_hist.call_args[0][0]
            assert call_arg["status"] == "published"


def test_resume_route_exit4_shows_partial(client, tmp_path):
    done_jsonl = json.dumps({
        "id": "r0", "platform": "blogger", "status": "done",
        "title": "T", "draft_url": "", "published_url": "https://x.com",
        "created_at": "t", "adapter": "blogger-api", "error": None,
    })
    mock_result = MagicMock()
    mock_result.returncode = 4
    mock_result.stdout = done_jsonl + "\n"
    mock_result.stderr = "item r1 still failed"

    with patch("subprocess.run", return_value=mock_result):
        with patch.object(_webui, "_append_history", return_value=[]):
            resp = client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            assert resp.status_code == 200
            body = resp.data.decode()
            assert "部分发布失败" in body or "failed" in body.lower()


def test_resume_route_exit2_no_history(client, tmp_path):
    mock_result = MagicMock()
    mock_result.returncode = 2
    mock_result.stdout = ""
    mock_result.stderr = "checkpoint not found"

    with patch("subprocess.run", return_value=mock_result):
        with patch.object(_webui, "_append_history", return_value=[]) as mock_hist:
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


def test_resume_route_uses_subprocess_not_run_pipe(client):
    """Guard: resume must use subprocess.run directly, not run_pipe."""
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""

    with patch("subprocess.run", return_value=mock_result) as mock_sub:
        with patch.object(_webui, "run_pipe") as mock_run_pipe:
            client.post("/checkpoint/resume", data={"run_id": "20260101T000000-abcdef01"})
            mock_run_pipe.assert_not_called()
            mock_sub.assert_called_once()


# ── /checkpoint/dismiss ────────────────────────────────────────────────────────

def test_dismiss_deletes_checkpoint_and_redirects(client, tmp_path):
    with patch("backlink_publisher.checkpoint._cache_dir", return_value=tmp_path / "cache"):
        from backlink_publisher.checkpoint import create_checkpoint
        rows = [{"id": "r0", "title": "T", "platform": "blogger"}]
        run_id, path = create_checkpoint(rows, platform="blogger", mode="draft")

        resp = client.post("/checkpoint/dismiss", data={"run_id": run_id})
        assert resp.status_code in (302, 200)
        assert not path.exists()


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
