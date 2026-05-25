"""Tests for mid-run config drift detection (token revocation)."""

from __future__ import annotations

import json
from unittest.mock import patch

from backlink_publisher.cli.publish_backlinks import _run_resume
from backlink_publisher.config.tokens import save_blogger_token


def test_token_drift_aborts_mid_run(tmp_path, monkeypatch):
    """If a token is updated mid-run, the publisher must abort with exit 3.

    Mid-run credential revocation is a dependency/auth condition → exit 3
    (DependencyError family, per the AGENTS.md 0-6 contract), not the
    undocumented 45 this previously emitted.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    
    # Setup initial token with token_rev=1
    save_blogger_token({"client_id": "a", "client_secret": "b"})
    
    # Mock payload
    ckpt = {
        "platform": "blogger",
        "mode": "draft",
        "items": [
            {
                "id": "r0",
                "status": "pending",
                "payload": {"target_url": "https://x.com/a"},
            },
            {
                "id": "r1",
                "status": "pending",
                "payload": {"target_url": "https://x.com/b"},
            },
        ],
    }

    # Mock adapter_publish to increment the token_rev on the first call
    call_count = {"n": 0}

    def fake_publish(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Simulate WebUI saving a new token mid-run
            save_blogger_token({"client_id": "new", "client_secret": "new"})
        from backlink_publisher.publishing.adapters import AdapterResult
        return AdapterResult(status="drafted", adapter="blogger-api", platform="blogger")

    with patch("backlink_publisher.cli._resume.adapter_publish", side_effect=fake_publish):
        with patch("backlink_publisher.cli._resume.verify_adapter_setup"):
            with patch("backlink_publisher.checkpoint.load_checkpoint", return_value=ckpt):
                with patch("backlink_publisher.cli.publish_backlinks._acquire_publish_leases"):
                    with patch("backlink_publisher.checkpoint.update_item"):
                        class DummyArgs:
                            resume = "20260101T000000Z-deadbeef"
                            dry_run = False
                            skip_publish_time_check = True
                            no_verify = True
                        
                        raised = None
                        try:
                            _run_resume(DummyArgs())
                        except SystemExit as exc:
                            raised = exc

    assert raised is not None, "Expected SystemExit on mid-run token drift"
    assert raised.code == 3, "Should exit 3 (DependencyError) due to config drift"
    assert call_count["n"] == 1, "Should have aborted before processing r1"
