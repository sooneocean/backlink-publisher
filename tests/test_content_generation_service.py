from backlink_publisher.content_generation.service import generate_draft
from backlink_publisher.content_generation.types import GenerationRequest


def _request() -> GenerationRequest:
    return GenerationRequest(
        target_url="https://example.com/guides/alpha",
        main_domain="https://example.com",
        platform="medium",
        language="en",
        anchors=("Example Alpha", "alpha guide"),
        topic="alpha research",
        title="Alpha Research Guide",
    )


class _Provider:
    provider_name = "stub-provider"

    def generate_article_body(self, **kwargs):
        assert kwargs["main_domain"] == "https://example.com"
        assert kwargs["anchors"] == ["Example Alpha", "alpha guide"]
        return (
            "A useful article about alpha research with a contextual link to "
            "[Example Alpha](https://example.com/guides/alpha) and supporting "
            "[alpha guide](https://example.com/guides/alpha) material for readers."
        )

    def generate_image_prompt(self, title, content):
        assert title == "Alpha Research Guide"
        assert "Example Alpha" in content
        return "Editorial cover image for a practical alpha research guide."


def test_service_returns_reviewable_draft() -> None:
    result = generate_draft(_request(), provider=_Provider())

    assert result.status == "reviewable"
    assert result.provider == "stub-provider"
    assert result.validation.accepted is True
    assert "Example Alpha" in result.body_markdown
    assert result.cover_prompt.startswith("Editorial cover image")


def test_service_fails_soft_without_provider() -> None:
    result = generate_draft(_request(), provider=None, fallback_body="template body")

    assert result.status == "fallback_used"
    assert result.provider == "template"
    assert result.body_markdown == "template body"
    assert result.validation.accepted is False
    assert "provider_unavailable" in result.issue_codes
