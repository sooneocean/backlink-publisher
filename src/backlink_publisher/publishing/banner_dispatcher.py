"""Publish-time banner embed dispatcher.

Plan 2026-05-20-004 Unit 1.  Reads the per-row ``banner`` JSONL
field (emitted by ``plan-backlinks`` per Unit 4 + R12 source_url
amendment), invokes ``adapter.embed_banner`` when the adapter
opts in via duck-typed ``hasattr`` check (per AGENTS.md "Adding
banner embedding to an adapter"), and prepends the resulting
URL — or ``BannerArtifact.source_url`` fallback — to the body
before ``adapter.publish()`` is called.

Pure function: no I/O, no ``EventStore``, no ``Config``.  Caller
supplies an ``emit`` callback whose signature mirrors
``EventStore.append(kind, payload)``.  This lets unit tests
exercise the 10 branches (5 happy + 3 error + 2 no-op) without
spinning up the publish pipeline.

5 happy + 3 error + 2 no-op branches:

  * adapter has ``embed_banner`` AND returns a URL
      → prepend ``![alt](url)\\n\\n`` to body
      → emit ``banner.embedded``
  * adapter has ``embed_banner`` AND returns ``None``
      AND banner has ``source_url`` (truthy)
      → prepend ``![alt](source_url)\\n\\n``
      → emit ``banner.source_url_fallback`` (``reason=adapter_returned_none``)
  * adapter has ``embed_banner`` AND returns ``None``
      AND no usable ``source_url``
      → emit ``banner.skipped_no_artifact`` (body unchanged)
  * adapter has ``embed_banner`` AND raises ``BannerUploadError``
      AND ``strict=False`` → emit ``banner.failed`` (body unchanged)
  * adapter has ``embed_banner`` AND raises ``BannerUploadError``
      AND ``strict=True`` → propagate (no event — caller writes
      the checkpoint row)
  * adapter has ``embed_banner`` AND raises non-``BannerUploadError``
      → propagate unconditionally (strict gate does NOT govern
      adapter bugs)
  * adapter does NOT have ``embed_banner`` (Medium-style)
      AND banner has ``source_url`` → fallback prepend
      → emit ``banner.source_url_fallback`` (``reason=adapter_no_method``)
  * adapter does NOT have ``embed_banner`` AND no ``source_url``
      → emit ``banner.skipped_no_method`` (body unchanged)
  * ``banner`` is ``None`` → silent (body unchanged, no events)
  * ``banner["path"]`` is ``None`` (plan-time degraded path) → silent
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from backlink_publisher._util.errors import BannerUploadError
from backlink_publisher.events import kinds  # dependency-free; preserves no-I/O purity


EmitFn = Callable[[str, dict[str, Any]], None]


def _markdown_image(alt: str, url: str) -> str:
    return f"![{alt}]({url})"


def apply(
    adapter: object,
    *,
    banner: dict[str, Any] | None,
    body: str,
    platform: str,
    strict: bool,
    emit: EmitFn,
) -> str:
    """Return ``body`` with an optional ``![alt](url)`` prepended.

    See module docstring for the 10-branch contract.  Caller is
    responsible for passing ``banner`` exactly as it appears on the
    row (``row.get("banner")``); the dispatcher handles the ``None``
    and ``{"path": None, ...}`` degraded shapes silently.
    """
    if banner is None or banner.get("path") is None:
        return body

    alt = banner.get("alt", "")
    source_url = banner.get("source_url")

    embed_banner = getattr(adapter, "embed_banner", None)

    if embed_banner is None:
        # Medium-style: not opting in.
        if source_url:
            emit(
                kinds.BANNER_SOURCE_URL_FALLBACK,
                {"platform": platform, "reason": "adapter_no_method"},
            )
            return f"{_markdown_image(alt, source_url)}\n\n{body}"
        emit(kinds.BANNER_SKIPPED_NO_METHOD, {"platform": platform})
        return body

    artifact_path = Path(banner["path"])

    try:
        uploaded_url = embed_banner(artifact_path, alt)
    except BannerUploadError as exc:
        if strict:
            raise
        emit(
            kinds.BANNER_FAILED,
            {"platform": platform, "reason": str(exc)},
        )
        return body
    # Non-BannerUploadError exceptions propagate unconditionally.
    # Strict gating governs only banner-specific failures; adapter
    # bugs (KeyError / TypeError / etc.) should fail loud.

    if uploaded_url is not None:
        emit(kinds.BANNER_EMBEDDED, {"platform": platform})
        return f"{_markdown_image(alt, uploaded_url)}\n\n{body}"

    # embed_banner returned None — explicit "can't upload" signal.
    if source_url:
        emit(
            kinds.BANNER_SOURCE_URL_FALLBACK,
            {"platform": platform, "reason": "adapter_returned_none"},
        )
        return f"{_markdown_image(alt, source_url)}\n\n{body}"

    emit(kinds.BANNER_SKIPPED_NO_ARTIFACT, {"platform": platform})
    return body
