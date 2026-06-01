"""Tests for the Copilot advisor route + guarded v3 stub (Plan U3)."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from webui_app.services.copilot_advisor import AggregateResult
from webui_app.services.copilot_models import Finding, ToolResult


@pytest.fixture
def client():
    import webui

    return webui.app.test_client()


def _fake_aggregate(*, findings, tool_results, degraded):
    def _run(*_a, **_k):
        return AggregateResult(
            tool_results=tool_results,
            findings=findings,
            degraded=degraded,
            considered=len(tool_results),
            problem_count=sum(1 for r in tool_results if not r.ok),
        )
    return _run


def test_advice_returns_ranked_json(client, monkeypatch, capsys):
    findings = [
        Finding(type="failed_canary", source_tool="canary",
                source_ref="canary:medium", summary="drift"),
        Finding(type="stale_link", source_tool="equity-ledger",
                source_ref="equity-ledger:t", summary="stale"),
    ]
    tools = [ToolResult(tool="canary", ok=True, outcome="kind", findings=findings[:1]),
             ToolResult(tool="audit-state", ok=False, outcome="quarantine",
                        error_code="audit_unreadable")]
    monkeypatch.setattr("webui_app.routes.copilot.cached_aggregate",
                        _fake_aggregate(findings=findings, tool_results=tools, degraded=True))

    resp = client.get("/copilot/advice?page=equity")
    assert resp.status_code == 200
    body = resp.get_json()
    # critical (failed_canary) ranks above warning (stale_link)
    assert [f["finding_type"] for f in body["findings"]] == ["failed_canary", "stale_link"]
    assert body["findings"][0]["priority"] == 1
    assert body["findings"][0]["source_ref"] == "canary:medium"
    assert body["degraded"] is True
    assert body["page_context"] == "equity"
    # the failed tool is surfaced honestly (no false-green)
    failed = [t for t in body["per_tool_status"] if not t["ok"]]
    assert failed and failed[0]["error_code"] == "audit_unreadable"
    # one non-identifying RECON line was emitted
    recon = [l for l in capsys.readouterr().err.splitlines()
             if l.strip() and json.loads(l).get("level") == "RECON"]
    assert len(recon) == 1
    assert json.loads(recon[0])["kind"] == "advisor"


def test_advice_is_keyless_and_200_even_when_empty(client, monkeypatch):
    monkeypatch.setattr("webui_app.routes.copilot.cached_aggregate",
                        _fake_aggregate(findings=[], tool_results=[], degraded=False))
    resp = client.get("/copilot/advice")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["findings"] == []
    assert body["degraded"] is False


def test_run_live_requires_csrf(client):
    # Global CSRF guard fires before the handler — POST without a token is 403.
    resp = client.post("/copilot/run-live")
    assert resp.status_code == 403


def test_run_live_origin_guard_is_wired(client, disable_csrf):
    # CSRF disabled, but the orthogonal origin guard still rejects a POST with
    # no allowlisted Origin/Referer — proves the stub is not a CSRF-shaped hole.
    resp = client.post("/copilot/run-live")
    assert resp.status_code == 403


def test_run_live_stub_returns_501_when_guards_pass(client, disable_csrf, monkeypatch):
    monkeypatch.setattr("webui_app.routes.copilot._refuse_when_allow_network", lambda: None)
    monkeypatch.setattr("webui_app.routes.copilot._check_bind_origin_or_abort", lambda: None)
    resp = client.post("/copilot/run-live")
    assert resp.status_code == 501
    assert resp.get_json()["error"] == "not_implemented"
