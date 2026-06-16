from __future__ import annotations

import base64
from types import SimpleNamespace

from backlink_publisher.publishing.adapters.image_gen.openai_sdk import (
    OpenAIImageGenAdapter,
)


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Images:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(b64_json=base64.b64encode(_PNG_MAGIC).decode())]
        )


class _Client:
    def __init__(self):
        self.images = _Images()


def test_openai_sdk_image_adapter_generates_banner_artifact():
    client = _Client()
    made = []

    def factory(**kwargs):
        made.append(kwargs)
        return client

    adapter = OpenAIImageGenAdapter(
        api_key="sk-image",
        base_url="https://api.openai.com/v1",
        model="gpt-image-1.5",
        banner_size="1536x1024",
        client_factory=factory,
    )

    artifact = adapter.generate("editorial cover")

    assert artifact.data == _PNG_MAGIC
    assert artifact.mime == "image/png"
    assert artifact.source_url is None
    assert made == [
        {
            "api_key": "sk-image",
            "timeout": 30.0,
            "base_url": "https://api.openai.com/v1",
        }
    ]
    assert client.images.calls == [
        {
            "model": "gpt-image-1.5",
            "prompt": "editorial cover",
            "size": "1536x1024",
            "n": 1,
        }
    ]
