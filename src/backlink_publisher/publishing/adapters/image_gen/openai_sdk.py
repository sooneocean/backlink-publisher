"""First-party OpenAI SDK image adapter."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from typing import Any

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.llm.client import _redact_for_log

from .adapter import _MAX_RESPONSE_BYTES, _sniff_mime
from .types import BannerArtifact


class OpenAIImageGenAdapter:
    """Generate a banner through the OpenAI Images API using the SDK."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        banner_size: str,
        base_url: str | None = None,
        timeout_s: float = 30.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.banner_size = banner_size
        self.base_url = base_url.rstrip("/") if isinstance(base_url, str) else None
        self.timeout_s = timeout_s
        self._client_factory = client_factory

    def generate(self, prompt: str) -> BannerArtifact:
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        client = self._client()
        try:
            response = client.images.generate(
                model=self.model,
                prompt=prompt,
                size=self.banner_size,
                n=1,
            )
        except Exception as exc:  # pragma: no cover - SDK exception matrix drifts.
            raise ExternalServiceError(
                f"OpenAI image generation failed: {_redact_for_log(str(exc))}"
            ) from exc

        item = _first_image_item(response)
        b64 = getattr(item, "b64_json", None)
        if not isinstance(b64, str) or not b64:
            raise RuntimeError("OpenAI image response missing data[0].b64_json")

        raw = base64.b64decode(b64)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ExternalServiceError(
                f"image-gen banner exceeds 5MB cap "
                f"({len(raw)} > {_MAX_RESPONSE_BYTES} bytes); refusing to persist."
            )

        return BannerArtifact(
            data=raw,
            mime=_sniff_mime(raw),
            source_url=None,
            prompt_sha=prompt_sha,
        )

    def _client(self) -> Any:
        factory = self._client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover - dependency is optional at import.
                raise ExternalServiceError(
                    "OpenAI SDK is not installed; install package dependency `openai`."
                ) from exc
            factory = OpenAI

        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "timeout": self.timeout_s,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return factory(**kwargs)


def _first_image_item(response: Any) -> Any:
    data = getattr(response, "data", None)
    if isinstance(data, list) and data:
        return data[0]
    raise RuntimeError("OpenAI image response missing data")
