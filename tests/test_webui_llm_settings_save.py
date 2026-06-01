"""WebUI LLM-settings save guard: reject non-https endpoints (Unit 3).

A non-https endpoint saved here would be silently ignored by the pipeline
bridge (``_llm_provider_from_sidecar`` requires https), leaving "I enabled Pro
Mode but nothing happens". The save route rejects a non-empty non-https endpoint
up front so the on-disk file is always bridge-usable.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

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


def test_save_image_gen_pro_mode_writes_pipeline_config_and_token(
    client, tmp_path, monkeypatch
):
    """The Pro Mode image switch must drive the real banner pipeline.

    The pipeline reads ``Config.image_gen`` and ``frw-token.json``; it does not
    read ``llm-settings.json`` for banner credentials.  Saving Settings must
    bridge all three surfaces.
    """
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "https://api.test/v1",
            "api_key": "sk-chat",
            "model": "gpt-4o-mini",
            "use_article_gen": "on",
            "image_gen_api_key": "sk-frw",
            "image_gen_endpoint": "https://image.test/v1",
            "image_gen_model": "banner-m",
            "image_gen_banner_size": "1200x630",
            "use_image_gen": "on",
        },
    )

    assert resp.status_code == 302
    assert "flash_type=success" in resp.location

    from backlink_publisher._util.secrets import load_frw_token
    from backlink_publisher.config import load_config

    assert load_frw_token() == "sk-frw"
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.image_gen is not None
    assert cfg.image_gen.base_url == "https://image.test/v1"
    assert cfg.image_gen.model == "banner-m"
    assert cfg.image_gen.banner_size == "1200x630"
    assert cfg.image_gen.use_image_gen is True

    stored = _read(tmp_path)
    assert stored["use_image_gen"] is True
    assert stored["image_gen_endpoint"] == "https://image.test/v1"
    assert stored["image_gen_model"] == "banner-m"


def test_save_image_gen_pro_mode_rejects_non_https_image_endpoint(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    _seed(tmp_path)

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "https://api.test/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
            "image_gen_endpoint": "http://image.test/v1",
            "image_gen_model": "banner-m",
            "use_image_gen": "on",
        },
    )

    assert resp.status_code == 302
    assert "flash_type=danger" in resp.location
    assert not (tmp_path / "frw-token.json").exists()


def test_save_unchecked_image_gen_disables_existing_pipeline_config(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        '[image_gen]\n'
        'base_url = "https://image.test/v1"\n'
        'model = "banner-m"\n'
        'use_image_gen = true\n',
        encoding="utf-8",
    )
    _seed(
        tmp_path,
        image_gen_endpoint="https://image.test/v1",
        image_gen_model="banner-m",
        use_image_gen=True,
    )

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "https://api.test/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
    )

    assert resp.status_code == 302
    assert "flash_type=success" in resp.location
    from backlink_publisher.config import load_config

    cfg = load_config(tmp_path / "config.toml")
    assert cfg.image_gen is not None
    assert cfg.image_gen.use_image_gen is False


def test_settings_image_gen_save_feeds_plan_banner_generation(
    client, tmp_path, monkeypatch
):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(tmp_path / "cache"))

    resp = client.post(
        "/settings/save-llm-config",
        data={
            "endpoint": "",
            "api_key": "",
            "model": "",
            "image_gen_api_key": "sk-frw",
            "image_gen_endpoint": "https://image.test/v1",
            "image_gen_model": "banner-m",
            "image_gen_banner_size": "1200x630",
            "use_image_gen": "on",
        },
    )
    assert resp.status_code == 302

    from backlink_publisher.cli.plan_backlinks._engine import plan_rows
    from backlink_publisher.config import load_config

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    b64 = base64.b64encode(png).decode()
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"data": [{"b64_json": b64}]}
    fake_resp.text = "ok"
    fake_resp.raise_for_status = MagicMock()

    seed = {
        "main_domain": "https://example.com/",
        "target_url": "https://example.com/post-1",
        "platform": "telegraph",
        "language": "en",
        "url_mode": "A",
        "publish_mode": "draft",
    }

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
        return_value=fake_resp,
    ):
        outcome = plan_rows([seed], load_config(), fetch_verify_enabled=False)

    assert not outcome.errors
    assert outcome.outputs
    banner = outcome.outputs[0].get("banner")
    assert isinstance(banner, dict)
    assert banner["path"]
    assert banner["mime"] == "image/png"


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
