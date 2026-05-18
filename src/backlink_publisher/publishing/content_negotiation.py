"""Adapter source-format negotiation for ``content_markdown`` / ``content_html``.

Plan 2026-05-18-006 Unit 5. Per the R10 spike (see plan §Adapter Compatibility
Matrix), only the ``blogger`` platform's route accepts pre-rendered HTML in
v1; ``medium`` is classified tier (b) because its dispatcher fallback chain
includes adapters (``medium_brave``, ``medium_browser``) whose WYSIWYG paste
sanitize is lossy. The most-restrictive-tier rule keeps the platform-level
classification conservative.

This module is consulted by:

- :mod:`backlink_publisher.publishing.adapters.*`'s ``publish()`` entry points
  via :func:`extract_publish_html` — replaces the previous
  ``render_to_html(payload.get("content_markdown", ""))`` inline pattern with
  a single helper. Tier (a) routes return ``content_html`` directly when
  present; tier (b)/(c) routes always render markdown (defense in depth — the
  validate-time gate in Unit 6 will already have rejected ``content_html``-only
  rows for non-tier-(a) platforms).
- The validate-time gate in
  :mod:`backlink_publisher.cli.validate_backlinks` via :func:`route_tier_for`
  to decide whether to fail-fast on ``content_html``-only rows whose platform
  cannot accept HTML.

The tier vocabulary is sealed inside this module — adapters call
``extract_publish_html(payload, "blogger")`` and never learn about ``"a"`` vs
``"b"`` vs ``"c"`` (architecture-strategist review).
"""

from __future__ import annotations

from typing import Any

from backlink_publisher._util.markdown import render_to_html
from backlink_publisher.schema import _is_field_present

__all__ = [
    "ROUTE_TIER_MATRIX",
    "route_tier_for",
    "extract_publish_html",
]


#: Per-platform source-format acceptance tier, post-most-restrictive-tier
#: rollup across each platform's adapter dispatch chain. Keys are
#: ``platform`` strings registered in
#: :func:`backlink_publisher.publishing.registry.registered_platforms`.
#:
#: Tier semantics (plan 2026-05-18-006 R10):
#:
#: - ``"a"`` — adapter forwards ``content_html`` verbatim to a platform with
#:   verified server-side sanitize. The forwarder-role contract is locked
#:   by the XSS contract tests (``test_adapter_*_xss_contract.py``).
#: - ``"b"`` — adapter exists but its dispatch chain includes paths whose
#:   sanitize is unknown or lossy (e.g. browser-paste WYSIWYG). ``content_html``
#:   rows are rejected at validate-time; the helper still renders markdown
#:   when called for defense in depth.
#: - ``"c"`` — platform has no integrated adapter on main (e.g. Telegraph
#:   Unit 4 adapter still on a feature branch as of plan write-time).
#:
#: New platforms default to tier ``"c"`` via :func:`route_tier_for` — fail-closed
#: so a forgotten matrix update rejects ``content_html`` rather than silently
#: forwarding to an unverified path.
#:
#: UPDATE this when a new adapter is retrofitted: each tier (a) entry must be
#: paired with an XSS contract test before going live (plan 2026-05-18-006
#: Threat Model Tampering row + pass-2 security P1).
ROUTE_TIER_MATRIX: dict[str, str] = {
    "blogger": "a",  # BloggerAPIAdapter sole; Google API server-side sanitize
    "medium": "b",   # MediumAPI(a) → MediumBrave(b) → MediumBrowser(b)
                     # — most-restrictive rule across fallback chain
}


#: Default tier for platforms not enumerated in :data:`ROUTE_TIER_MATRIX`.
#: Fail-closed: an unknown platform with ``content_html`` is rejected at
#: validate-time (plan 2026-05-18-006 Unit 5 + pass-2 adversarial P2).
_DEFAULT_TIER: str = "c"


def _matrix_targets_registered_platforms() -> list[str]:
    """Test-time drift detector (post-R9e): returns the sorted list of
    :data:`ROUTE_TIER_MATRIX` keys that no longer point to a registered
    adapter — stale config that should be deleted.

    Not run at module import time on purpose: this module is imported by
    ``blogger_api`` during ``adapters/__init__`` registration, so the
    registry is half-populated at our import time. The assertion lives in
    ``tests/test_content_negotiation.py`` instead.

    Runtime safety does not depend on this check: the
    :data:`_DEFAULT_TIER` fail-closed default in :func:`route_tier_for`
    rejects ``content_html``-only rows for unregistered platforms anyway,
    and rows with unknown platforms are rejected earlier by
    ``schema.validate_publish_payload`` before reaching this module.
    """
    from backlink_publisher.publishing.registry import registered_platforms

    return sorted(set(ROUTE_TIER_MATRIX.keys()) - set(registered_platforms()))


def route_tier_for(platform: str) -> str:
    """Return the source-format acceptance tier for ``platform``.

    Normalizes input (``strip().lower()``) before lookup. Unknown platforms
    return :data:`_DEFAULT_TIER` (``"c"``) — fail-closed default per pass-2
    adversarial P2 finding.
    """
    if not isinstance(platform, str):
        return _DEFAULT_TIER
    key = platform.strip().lower()
    return ROUTE_TIER_MATRIX.get(key, _DEFAULT_TIER)


def extract_publish_html(payload: dict[str, Any], platform: str) -> str:
    """Return the HTML that the adapter should publish for ``payload``.

    Tier (a) routes with non-empty ``content_html`` return it verbatim
    (sanitize delegated to the platform — see per-adapter docstring for
    the test fixture locking that contract). All other cases render
    ``content_markdown`` via :func:`render_to_html` — preserves legacy
    behavior bit-exact for tier (b)/(c) routes and for tier (a) rows that
    only supply markdown.

    Tier (b)/(c) ``content_html``-only rows should be rejected at
    validate-time (Unit 6); this helper's tier-(b)/(c) markdown-only return
    is defense in depth in case the validate gate is bypassed (e.g. direct
    adapter invocation from a fixture).

    Plan 2026-05-18-006 Unit 5 R9.
    """
    if (
        _is_field_present(payload.get("content_html"))
        and route_tier_for(platform) == "a"
    ):
        return payload["content_html"]
    return render_to_html(payload.get("content_markdown", ""))
