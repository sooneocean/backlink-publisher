"""WebUI LLM-settings save guard: reject non-https endpoints (Unit 3).

A non-https endpoint saved here would be silently ignored by the pipeline
bridge (``_llm_provider_from_sidecar`` requires https), leaving "I enabled Pro
Mode but nothing happens". The save route rejects a non-empty non-https endpoint
up front so the on-disk file is always bridge-usable.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def client(disable_csrf):
    """``disable_csrf`` (conftest) is the sanctioned, self-restoring way to turn
    the global CSRF guard off — the AST gate in
    ``test_security_toggle_mutation_gate.py`` bans raw ``config['CSRF_ENABLED']``
    mutation in new test files. It yields the webui app singleton."""
    return disable_csrf.test_client()


def _settings_path(config_dir):
    return config_dir / "llm-settings.json"


def _seed(config_dir, **overrides):
    payload = {
        "api_key": "sk-existing",
        "endpoint": "https://good.test/v1",
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "system_prompt": "",
        "use_article_gen": False,
        "article_system_prompt": "",
        "image_gen_api_key": "",
        "use_image_gen": False,
    }
    payload.update(overrides)
    _settings_path(config_dir).write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _read(config_dir):
    return json.loads(_settings_path(config_dir).read_text(encoding="utf-8"))


def test_save_rejects_non_https_endpoint_and_does_not_persist(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed(tmp_path)  # stored endpoint is https://good.test/v1

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "http://evil.test/v1",
            "api_key": "",  # blank → preserve existing
            "model": "gpt-4o-mini",
        },
    )

    assert resp.status_code == 302
    assert "flash_type=danger" in resp.location
    # The write must be aborted — the stored https endpoint is untouched.
    assert _read(tmp_path)["endpoint"] == "https://good.test/v1"


def test_save_accepts_https_endpoint(client, tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "https://api.test/v1",
            "api_key": "sk-new",
            "model": "gpt-4o-mini",
            "use_article_gen": "on",
        },
    )

    assert resp.status_code == 302
    assert "flash_type=success" in resp.location
    stored = _read(tmp_path)
    assert stored["endpoint"] == "https://api.test/v1"
    assert stored["use_article_gen"] is True


def test_save_blank_endpoint_not_rejected(client, tmp_path, monkeypatch):
    # A blank endpoint is a partial edit, not a non-https violation — it must
    # not trip the guard.
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed(tmp_path)

    resp = client.post(
        "/settings/save-llm-config",
        data={"endpoint": "", "api_key": "", "model": "gpt-4o-mini"},
    )

    assert resp.status_code == 302
    assert "flash_type=danger" not in resp.location
