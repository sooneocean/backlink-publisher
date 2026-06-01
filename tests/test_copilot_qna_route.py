"""Tests for the Copilot Q&A route (U5: POST /copilot/ask).

Scenarios:
  1. Happy path — LLM configured, question answered.
  2. LLM not configured (no endpoint / no api_key) → 400.
  3. Empty / non-JSON body → 400.
  4. LLM guard violation (redirect) → 502.
  5. LLM returns non-200 → 502.
  6. LLM response missing content → 502.
  7. CSRF guard fires with no token → 403.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from webui_app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def csrf_client(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = True
    return app.test_client()


def _write_llm_settings(tmp_path, **overrides):
    settings = {
        "endpoint": "https://api.openai.com/v1",
        "api_key": "sk-test123",
        "model": "gpt-4",
        "temperature": 0.7,
    }
    settings.update(overrides)
    path = tmp_path / "llm-settings.json"
    path.write_text(json.dumps(settings), encoding="utf-8")
    return path


def _patch_safe_post_json(monkeypatch, status=200, body=None):
    if body is None:
        body = {"choices": [{"message": {"content": "这是一个测试回答。"}}]}
    monkeypatch.setattr(
        "webui_app.routes.copilot.safe_post_json",
        lambda _url, _headers, _payload, **_kw: (status, body),
    )


# ── Happy path ───────────────────────────────────────────────────────────────


def test_ask_returns_answer(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch)
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "问题?"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["answer"] == "这是一个测试回答。"
    assert "error" not in body


def test_ask_question_is_sanitized(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    sent = []

    def _capture(url, headers, payload, **kw):
        sent.append(payload["messages"][1]["content"])
        return 200, {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr("webui_app.routes.copilot.safe_post_json", _capture)
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "hello\u0000world"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert "\u0000" not in sent[0]


# ── Missing / bad configuration ──────────────────────────────────────────────


def test_ask_no_endpoint_returns_400(client, tmp_path):
    _write_llm_settings(tmp_path, endpoint="")
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "llm_not_configured"


def test_ask_no_api_key_returns_400(client, tmp_path):
    _write_llm_settings(tmp_path, api_key="")
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "llm_not_configured"


def test_ask_no_settings_file_returns_400(client, tmp_path):
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "llm_not_configured"


# ── Bad request body ─────────────────────────────────────────────────────────


def test_ask_empty_body_returns_400(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch)
    resp = client.post(
        "/copilot/ask", data="{}", content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


def test_ask_missing_question_returns_400(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch)
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"not_question": "hi"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


def test_ask_non_json_body_returns_400(client, tmp_path):
    _write_llm_settings(tmp_path)
    resp = client.post(
        "/copilot/ask", data="not json", content_type="text/plain",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


# ── LLM error scenarios ──────────────────────────────────────────────────────


def test_ask_llm_guard_violation_returns_502(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    monkeypatch.setattr(
        "webui_app.routes.copilot.safe_post_json",
        lambda _u, _h, _p, **_kw: (_ for _ in ()).throw(ValueError("redirect")),
    )
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "llm_call_failed"


def test_ask_llm_http_error_returns_502(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch, status=500, body={"error": "x"})
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "llm_call_failed"


def test_ask_llm_response_missing_content_returns_502(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch, body={"choices": [{"message": {}}]})
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "llm_response_invalid"


def test_ask_llm_empty_answer_returns_502(client, tmp_path, monkeypatch):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch, body={
        "choices": [{"message": {"content": ""}}]
    })
    resp = client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 502
    assert resp.get_json()["error"] == "llm_response_empty"


# ── CSRF ─────────────────────────────────────────────────────────────────────


def test_ask_requires_csrf(csrf_client, tmp_path):
    _write_llm_settings(tmp_path)
    resp = csrf_client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    assert resp.status_code == 403


# ── RECON logging ────────────────────────────────────────────────────────────


def test_ask_emits_recon_log(client, tmp_path, monkeypatch, capsys):
    _write_llm_settings(tmp_path)
    _patch_safe_post_json(monkeypatch)
    client.post(
        "/copilot/ask",
        data=json.dumps({"question": "test"}),
        content_type="application/json",
    )
    recon = [l for l in capsys.readouterr().err.splitlines()
             if l.strip() and json.loads(l).get("level") == "RECON"]
    assert len(recon) == 1
    r = json.loads(recon[0])
    assert r["kind"] == "qa"
    assert r["tool_or_route"] == "/copilot/ask"
