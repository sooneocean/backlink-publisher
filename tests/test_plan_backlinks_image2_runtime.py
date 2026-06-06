from __future__ import annotations

from backlink_publisher.cli.plan_backlinks._banners import (
    _build_image_adapter,
    _resolve_image_gen_key,
)
from backlink_publisher.config.types import Config, ImageGenConfig, LLMProviderConfig
from backlink_publisher.publishing.adapters.image_gen.openai_sdk import (
    OpenAIImageGenAdapter,
)


def _cfg(*, image_key: str | None = None) -> Config:
    return Config(
        llm_anchor_provider=LLMProviderConfig(
            provider="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-llm",
            model="gpt-5.1",
            image_gen_api_key=image_key,
            use_image_gen=True,
        ),
        image_gen=ImageGenConfig(
            provider="image2",
            base_url="https://api.openai.com/v1",
            model="gpt-image-1.5",
            banner_size="1536x1024",
        ),
    )


def test_image2_key_prefers_explicit_image_key():
    assert _resolve_image_gen_key(_cfg(image_key="sk-image")) == "sk-image"


def test_image2_key_falls_back_to_llm_api_key():
    assert _resolve_image_gen_key(_cfg()) == "sk-llm"


def test_image2_builds_openai_sdk_adapter():
    adapter = _build_image_adapter(_cfg(image_key="sk-image"), "sk-image")

    assert isinstance(adapter, OpenAIImageGenAdapter)
    assert adapter.model == "gpt-image-1.5"
