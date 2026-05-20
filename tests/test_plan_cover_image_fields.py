"""Unit 1 of Plan 2026-05-19-007: plan dict surfaces ``cover_image_url`` and
``cover_image_warning`` so webui can render cover + failure badge.

Tests directly call ``_generate_payload`` with stub config and monkeypatched
image-gen entrypoints to avoid real LLM/network round-trips.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli.plan_backlinks.core import _generate_payload
from backlink_publisher.config.types import Config, LLMProviderConfig


def _row() -> dict:
    return {
        "main_domain": "https://example.com",
        "target_url": "https://example.com/page",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Cover Image Test",
    }


def _llm_provider(*, use_image_gen: bool, image_key: str | None) -> LLMProviderConfig:
    return LLMProviderConfig(
        base_url="https://api.example.com",
        api_key="llm-key",
        model="gpt-test",
        temperature=0.7,
        system_prompt=None,
        use_image_gen=use_image_gen,
        image_gen_api_key=image_key,
    )


def test_image_gen_off_emits_none_fields() -> None:
    """When ``use_image_gen`` is False, both new fields are None and content
    has no cover image markdown prepended."""
    cfg = Config(llm_anchor_provider=_llm_provider(use_image_gen=False, image_key=None))
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is None
    assert "![" not in payload["content_markdown"].splitlines()[0]


def test_image_gen_unbound_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``use_image_gen`` is True but ``image_gen_api_key`` is empty, the
    try block is never entered. New fields stay None, no surprise calls."""

    def _explode(*_args, **_kwargs):  # pragma: no cover - should not run
        raise AssertionError("generate_cover_image must not be invoked when key is unset")

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.generate_cover_image",
        _explode,
    )
    cfg = Config(llm_anchor_provider=_llm_provider(use_image_gen=True, image_key=None))
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is None


def test_image_gen_success_carries_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: ``generate_cover_image`` returns a URL → plan dict carries
    it and body gets ``![title](url)\\n\\n`` prepend."""

    class _FakeProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate_image_prompt(self, *_args, **_kwargs) -> str:
            return "stub image prompt"

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.OpenAICompatibleProvider",
        _FakeProvider,
    )
    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.generate_cover_image",
        lambda _key, _prompt: "https://cdn.example.com/cover.png",
    )

    cfg = Config(llm_anchor_provider=_llm_provider(use_image_gen=True, image_key="img-key"))
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert payload["cover_image_url"] == "https://cdn.example.com/cover.png"
    assert payload["cover_image_warning"] is None
    # body got prepended with the cover markdown
    assert "![" in payload["content_markdown"]
    assert "https://cdn.example.com/cover.png" in payload["content_markdown"]


def test_image_gen_failure_records_warning_and_keeps_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """R8 + R9 + R10: when image gen raises, plan dict still produces, body
    has no ``![]()`` prepend, cover_image_warning carries truncated repr."""

    class _FakeProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate_image_prompt(self, *_args, **_kwargs) -> str:
            return "stub prompt"

    def _raise(*_args, **_kwargs):
        raise TimeoutError("upstream network unreachable after 30s")

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.OpenAICompatibleProvider",
        _FakeProvider,
    )
    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.generate_cover_image",
        _raise,
    )

    cfg = Config(llm_anchor_provider=_llm_provider(use_image_gen=True, image_key="img-key"))
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is not None
    assert "TimeoutError" in payload["cover_image_warning"]
    assert "network unreachable" in payload["cover_image_warning"]
    assert len(payload["cover_image_warning"]) <= 120
    # body must NOT contain the markdown image syntax — failure path skips prepend
    first_line = payload["content_markdown"].splitlines()[0]
    assert not first_line.startswith("![")


def test_image_gen_failure_truncates_long_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Warning is capped at 120 chars so noisy upstream errors don't bloat
    plan-dict size."""

    class _FakeProvider:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def generate_image_prompt(self, *_args, **_kwargs) -> str:
            return "stub"

    long_msg = "x" * 500

    def _raise(*_args, **_kwargs):
        raise RuntimeError(long_msg)

    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.OpenAICompatibleProvider",
        _FakeProvider,
    )
    monkeypatch.setattr(
        "backlink_publisher.cli.plan_backlinks.core.generate_cover_image",
        _raise,
    )

    cfg = Config(llm_anchor_provider=_llm_provider(use_image_gen=True, image_key="img-key"))
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert payload["cover_image_warning"] is not None
    assert len(payload["cover_image_warning"]) <= 120


def test_no_llm_provider_emits_none_fields() -> None:
    """When ``config.llm_anchor_provider`` is None, fields are still emitted
    as None (consistent plan-dict schema)."""
    cfg = Config(llm_anchor_provider=None)
    payload = _generate_payload(_row(), cfg, fetch_verify_enabled=False)

    assert "cover_image_url" in payload
    assert "cover_image_warning" in payload
    assert payload["cover_image_url"] is None
    assert payload["cover_image_warning"] is None
