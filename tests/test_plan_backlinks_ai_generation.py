from __future__ import annotations

import json

from backlink_publisher.cli.plan_backlinks._engine import plan_rows
from backlink_publisher.cli.plan_backlinks._payload import _build_article_provider
from backlink_publisher.config.types import Config, LLMProviderConfig


def _row() -> dict:
    return {
        "main_domain": "https://example.com/",
        "target_url": "https://example.com/guides/alpha",
        "platform": "telegraph",
        "language": "en",
        "url_mode": "A",
        "topic": "alpha research",
        "publish_mode": "draft",
    }


def _ai_config() -> Config:
    return Config(
        llm_anchor_provider=LLMProviderConfig(
            base_url="https://llm.test/v1",
            api_key="sk-test",
            model="article-model",
            use_article_gen=True,
        )
    )


class _FakeArticleProvider:
    provider_name = "fake-openai-compatible"

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def generate_article_body(self, **kwargs):
        return (
            "A practical alpha research article that includes "
            "[example](https://example.com/guides/alpha) and an additional "
            "[example](https://example.com/guides/alpha) reference for readers."
        )

    def generate_image_prompt(self, title, content):
        return "Clean editorial cover for alpha research."


def test_ai_generation_enabled_uses_content_generation_service(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_generate_draft(request, *, provider, fallback_body=None):
        calls.append(
            {
                "request": request,
                "provider": provider,
                "fallback_body": fallback_body,
            }
        )
        from backlink_publisher.content_generation.types import (
            DraftGenerationResult,
            ValidationResult,
        )

        return DraftGenerationResult(
            status="reviewable",
            provider="fake-openai-compatible",
            body_markdown=provider.generate_article_body(
                domain_label="example",
                main_domain=request.main_domain,
                anchors=list(request.anchors),
                topic=request.topic,
                language=request.language,
            ),
            validation=ValidationResult(accepted=True, issues=[]),
            cover_prompt="Clean editorial cover for alpha research.",
        )

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks._payload.OpenAICompatibleProvider",
        _FakeArticleProvider,
    )
    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks._payload.generate_draft",
        fake_generate_draft,
    )

    outcome = plan_rows([_row()], _ai_config(), fetch_verify_enabled=False)

    assert not outcome.errors
    assert calls, "expected _generate_payload to route through content_generation.generate_draft"
    payload = outcome.outputs[0]
    assert payload["ai_generation"]["status"] == "reviewable"
    assert payload["ai_generation"]["provider"] == "fake-openai-compatible"
    assert payload["cover_prompt"] == "Clean editorial cover for alpha research."
    assert "[example](https://example.com/guides/alpha)" in payload["content_markdown"]


def test_no_key_or_failed_provider_falls_back_to_template(monkeypatch) -> None:
    class FailingProvider(_FakeArticleProvider):
        def generate_article_body(self, **kwargs):
            raise RuntimeError("provider boom sk-secret")

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks._payload.OpenAICompatibleProvider",
        FailingProvider,
    )

    outcome = plan_rows([_row()], _ai_config(), fetch_verify_enabled=False)

    assert not outcome.errors
    payload = outcome.outputs[0]
    assert payload["ai_generation"]["status"] == "fallback_used"
    assert "sk-secret" not in json.dumps(payload["ai_generation"])
    assert payload["content_markdown"].startswith("#")


def test_openai_provider_builds_sdk_article_provider() -> None:
    cfg = Config(
        llm_anchor_provider=LLMProviderConfig(
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-test",
            model="gpt-5.1",
            use_article_gen=True,
        )
    )

    provider = _build_article_provider(cfg)

    assert provider.provider_name == "openai-sdk"
    assert provider.model == "gpt-5.1"
