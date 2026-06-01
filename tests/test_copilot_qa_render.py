"""Render tests for the Copilot Q&A panel (U6).

Scenarios:
  1. LLM configured → unlocked form rendered (#copilotQaForm present).
  2. LLM not configured → locked state rendered (#copilotQaForm absent).
  3. Unlocked panel has input, send button, and conversation area.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _render_index(tmp_path, monkeypatch, with_llm=True):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    if with_llm:
        settings_path = tmp_path / "llm-settings.json"
        settings_path.write_text(
            json.dumps({
                "endpoint": "https://api.openai.com/v1",
                "api_key": "sk-test",
                "model": "gpt-4",
            }),
            encoding="utf-8",
        )
    from webui_app import create_app
    app = create_app(start_scheduler=False)
    app.config["TESTING"] = True
    app.config["CSRF_ENABLED"] = False
    with app.test_client() as client:
        resp = client.get("/")
        return resp.get_data(as_text=True)


def test_unlocked_panel_renders_when_llm_configured(tmp_path, monkeypatch):
    html = _render_index(tmp_path, monkeypatch, with_llm=True)
    assert 'id="copilotQaForm"' in html
    assert 'id="copilotQaInput"' in html
    assert 'id="copilotQaSend"' in html
    assert 'id="copilotQaConvo"' in html
    assert "copilot-qa--unlocked" in html
    assert "绑定金钥" not in html


def test_locked_panel_renders_when_no_llm(tmp_path, monkeypatch):
    html = _render_index(tmp_path, monkeypatch, with_llm=False)
    assert 'id="copilotQaForm"' not in html
    assert "copilot-qa--locked" in html
    assert "绑定金钥" in html


def test_unlocked_has_input_and_submit(tmp_path, monkeypatch):
    html = _render_index(tmp_path, monkeypatch, with_llm=True)
    assert 'type="text"' in html
    assert 'type="submit"' in html
    assert 'placeholder="输入你的问题' in html
    assert 'maxlength="500"' in html
