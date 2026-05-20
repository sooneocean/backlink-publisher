"""Unit 1 of Plan 2026-05-19-007: plan dict surfaces ``cover_image_url`` and
``cover_image_warning`` fields.

The old generate_cover_image code path was removed during the banner-image-gen
PR (#110) merge — banner artifacts are now handled by ``image_gen`` adapter +
``Config.image_gen``. The fields remain in the schema as ``None`` for pipeline
compatibility.
"""

from __future__ import annotations

from backlink_publisher.cli.plan_backlinks.core import _generate_payload
from backlink_publisher.config.types import Config


def _row() -> dict:
    return {
        "main_domain": "https://example.com",
        "target_url": "https://example.com/page",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Cover Image Field Test",
    }


def test_cover_image_fields_are_always_none() -> None:
    """Post-banner-image-gen-PR: both fields are always None via the retained
    schema field — actual banner artifact is emitted as a separate JSONL field
    by the image_gen adapter."""
    payload = _generate_payload(_row(), Config(), fetch_verify_enabled=False)

    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is None


def test_no_llm_provider_emits_none_fields() -> None:
    """When ``config.llm_anchor_provider`` is None, fields are still emitted
    as None (consistent plan-dict schema)."""
    cfg = Config(llm_anchor_provider=None)
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert "cover_image_url" in payload
    assert "cover_image_warning" in payload
    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is None
