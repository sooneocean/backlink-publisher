"""Tests for Wave 1 Setup Wizard — WizardConfigStore + wizard routes."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from webui_store.wizard_config_store import WizardConfigStore


# ── WizardConfigStore ─────────────────────────────────────────────────────


class TestWizardConfigStore:
    def test_default_state_is_not_completed(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        assert not store.is_completed()
        assert not store.is_skipped()

    def test_mark_completed(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.mark_completed(
            seed_sources=[{"type": "sitemap", "value": "https://example.com/sitemap.xml"}],
            channels=[{"channel": "medium", "bound": True}],
            automation_rules={"polling_interval_seconds": 21600},
        )
        assert store.is_completed()
        assert not store.is_skipped()

    def test_mark_skipped(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.mark_skipped()
        assert not store.is_completed()
        assert store.is_skipped()

    def test_is_completed_checks_completed_flag(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.save({"wizard_config": {"completed": True}})
        assert store.is_completed()

    def test_is_skipped_checks_skipped_flag(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.save({"wizard_config": {"skipped": True}})
        assert store.is_skipped()

    def test_mark_completed_is_idempotent(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.mark_completed()
        store.mark_completed()
        # Does not raise

    def test_add_seed_source(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.add_seed_source(
            source_type="sitemap",
            value="https://example.com/sitemap.xml",
        )
        sources = store.get_seed_sources()
        assert len(sources) == 1
        assert sources[0]["type"] == "sitemap"

    def test_get_seed_sources_empty_by_default(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        assert store.get_seed_sources() == []

    def test_get_automation_rules_with_defaults(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        rules = store.get_automation_rules()
        assert rules["polling_interval_seconds"] == 21600
        assert rules["default_daily_cap"] == 10

    def test_get_automation_rules_after_mark_completed(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.mark_completed(
            automation_rules={"polling_interval_seconds": 3600, "max_daily_publish": 25}
        )
        rules = store.get_automation_rules()
        assert rules["polling_interval_seconds"] == 3600
        assert rules["max_daily_publish"] == 25

    def test_seed_sources_persist_across_loads(self, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard.json")
        store.add_seed_source("manual", "https://target-1.com")
        store2 = WizardConfigStore(tmp_path / "wizard.json")
        assert len(store2.get_seed_sources()) == 1


# ── Wizard routes (smoke tests with patched config) ───────────────────────


@pytest.fixture
def _wizard_client(tmp_path):
    """Create a Flask test client with wizard blueprint and stores."""
    import os
    old_env = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
    os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
    try:
        from webui_app import create_app
        app = create_app(start_scheduler=False)
        app.config["TESTING"] = True
        app.config["CSRF_ENABLED"] = False
        app.config["WTF_CSRF_ENABLED"] = False
        with app.test_client() as client:
            yield client
    finally:
        if old_env is None:
            os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
        else:
            os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old_env


class TestWizardRoutes:
    def test_get_wizard_returns_200(self, _wizard_client):
        resp = _wizard_client.get("/wizard")
        assert resp.status_code == 200

    def test_get_wizard_redirects_when_completed(self, _wizard_client, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard-config.json")
        store.mark_completed()
        resp = _wizard_client.get("/wizard")
        assert resp.status_code == 302

    def test_get_api_wizard_status_not_completed(self, _wizard_client):
        resp = _wizard_client.get("/api/wizard/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["completed"] is False
        assert data["skipped"] is False

    def test_get_api_wizard_status_completed(self, _wizard_client, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard-config.json")
        store.mark_completed()
        resp = _wizard_client.get("/api/wizard/status")
        data = resp.get_json()
        assert data["completed"] is True

    def test_post_seed_sources_sitemap(self, _wizard_client):
        resp = _wizard_client.post(
            "/wizard/step/seed-sources",
            json={"sitemap_urls": ["https://example.com/sitemap.xml"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] >= 1

    def test_post_seed_sources_invalid_url(self, _wizard_client):
        resp = _wizard_client.post(
            "/wizard/step/seed-sources",
            json={"sitemap_urls": ["not-a-url"]},
        )
        assert resp.status_code == 400

    def test_post_channels(self, _wizard_client):
        resp = _wizard_client.post(
            "/wizard/step/channels",
            json={
                "channels": [
                    {
                        "channel": "medium",
                        "daily_cap": 5,
                        "dofollow_preference": True,
                        "language_whitelist": ["en"],
                    }
                ]
            },
        )
        assert resp.status_code == 200

    def test_post_rules(self, _wizard_client):
        resp = _wizard_client.post(
            "/wizard/step/rules",
            json={
                "polling_interval_seconds": 21600,
                "default_daily_cap": 10,
                "language_filter": ["en"],
            },
        )
        assert resp.status_code == 200

    def test_post_launch_returns_active(self, _wizard_client, tmp_path):
        store = WizardConfigStore(tmp_path / "wizard-config.json")
        store.add_seed_source("manual", "https://target-1.com")
        store.mark_completed()
        resp = _wizard_client.post("/wizard/step/launch")
        assert resp.status_code == 200

    def test_wizard_with_csrf_returns_403(self, tmp_path):
        """Without CSRF_ENABLED=False, POST returns 403."""
        import os
        old = os.environ.get("BACKLINK_PUBLISHER_CONFIG_DIR")
        os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = str(tmp_path)
        try:
            from webui_app import create_app
            app = create_app(start_scheduler=False)
            app.config["TESTING"] = True
            with app.test_client() as client:
                resp = client.post("/wizard/step/seed-sources", json={})
                assert resp.status_code == 403
        finally:
            if old is None:
                os.environ.pop("BACKLINK_PUBLISHER_CONFIG_DIR", None)
            else:
                os.environ["BACKLINK_PUBLISHER_CONFIG_DIR"] = old
