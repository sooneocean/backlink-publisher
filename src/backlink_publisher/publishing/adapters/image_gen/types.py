"""Public types for the image-gen adapter — Plan 2026-05-20-001 Unit 2."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BannerArtifact:
    """Result of a successful ``ImageGenAdapter.generate`` call.

    ``data`` is the raw image bytes (after MIME sniffing and size cap
    enforcement).  ``mime`` is one of the recognized image types:
    ``image/png`` / ``image/jpeg`` / ``image/webp``.

    ``source_url`` is the provider-hosted URL when the endpoint
    returned one (``data[].url`` mode), else ``None`` (``data[].b64_json``
    mode).  Carriers without their own media-upload API (e.g.
    ``writeas``) can fall back to embedding ``source_url`` as a hot
    link with the caveat that the upstream CDN's TTL may rot the
    older backlinks.

    ``prompt_sha`` is ``sha256(prompt).hexdigest()[:16]`` and serves
    as the content-addressed key for ``image_gen.storage`` so the
    same prompt never re-generates a banner that already exists on
    disk.
    """

    data: bytes
    mime: str
    source_url: str | None
    prompt_sha: str
