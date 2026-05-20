"""AI banner image-gen adapter package — Plan 2026-05-20-001.

Replaces the deprecated ``frw_image_gen.py`` stub. Public API:

  * ``ImageGenAdapter`` — generates a banner from a text prompt
    against any OpenAI-compatible ``/images/generations`` endpoint.
  * ``BannerArtifact`` — the dataclass returned by ``generate()``:
    raw bytes + MIME + sha + optional source_url.

The adapter does NOT decide where to persist the banner; storage
lives in ``image_gen.storage`` (Unit 3) and per-platform CDN upload
lives in ``image_gen.banner_embed`` (Unit 5).
"""

from .adapter import ImageGenAdapter
from .types import BannerArtifact

__all__ = ["ImageGenAdapter", "BannerArtifact"]
