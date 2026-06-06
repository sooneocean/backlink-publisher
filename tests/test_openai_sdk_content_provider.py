from __future__ import annotations

from types import SimpleNamespace

from backlink_publisher.content_generation.openai_sdk import OpenAISDKArticleProvider


class _Responses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text="Generated markdown body")


class _Client:
    def __init__(self):
        self.responses = _Responses()


def test_openai_sdk_article_provider_uses_responses_api():
    made = []

    def factory(**kwargs):
        made.append(kwargs)
        return _Client()

    provider = OpenAISDKArticleProvider(
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        model="gpt-5.1",
        temperature=0.3,
        client_factory=factory,
    )

    text = provider.generate_article_body(
        domain_label="example",
        main_domain="https://example.com",
        anchors=["example", "example guide"],
        topic="alpha",
        language="en",
    )

    assert text == "Generated markdown body"
    assert made == [
        {
            "api_key": "sk-test",
            "timeout": 30.0,
            "base_url": "https://api.openai.com/v1",
        }
    ]


def test_openai_sdk_image_prompt_returns_text():
    client = _Client()

    def factory(**kwargs):
        return client

    provider = OpenAISDKArticleProvider(
        api_key="sk-test",
        model="gpt-5.1",
        client_factory=factory,
    )

    prompt = provider.generate_image_prompt("Title", "Body")

    assert prompt == "Generated markdown body"
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.1"
    assert call["input"][0]["role"] == "system"
    assert call["input"][1]["role"] == "user"
