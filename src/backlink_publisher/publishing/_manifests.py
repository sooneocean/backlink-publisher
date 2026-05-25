"""Per-channel manifest declarations — Plan 2026-05-25-002 Phase 2.

Extracted from ``adapters/__init__.py`` once the first three migrated
channels (velog pilot + telegraph + blogger) pushed that file past its
580-SLOC ceiling. The dispatcher module stays focused on the
``register(...)`` wiring and adapter imports; the declarative metadata
(UiMeta / BindDescriptor / Policy) lives here.

Usage from ``adapters/__init__.py``:

    from .._manifests import VELOG_MANIFEST, TELEGRAPH_MANIFEST, BLOGGER_MANIFEST

    register("blogger", BloggerAPIAdapter, dofollow=True, **BLOGGER_MANIFEST)

Adding a Phase-2 channel = one new ``<SLUG>_MANIFEST`` dict here + one
``**<SLUG>_MANIFEST`` splat at the existing register() call site. No
new register() shape, no new test scaffold needed (the contract test
parametrizes over ``registered_platforms()`` automatically).
"""

from __future__ import annotations

from typing import Any

from ._manifest_types import BindDescriptor, Policy, UiMeta


# ── velog ──────────────────────────────────────────────────────────────────
#
# First channel to declare a complete manifest (Plan 2026-05-25-002 Unit 3).
# The 5 special velog files (velog_graphql, browser_publish/recipes/velog,
# browser_publish/recipes/_velog_selectors, cli/_bind/recipes/velog,
# cli/velog_login) are NOT relocated — only their paths are declared in
# the bind descriptor's ``extras`` so downstream consumers (Unit 4 WebUI
# wiring) can reverse-lookup them.
#
# Throttle band reflects the hardcoded ``_VELOG_JITTER_MIN/MAX_S`` in
# velog_graphql.py (60-180s). ``env_keys`` is intentionally empty —
# velog does not currently support env overrides; if that changes a
# future PR adds ``VELOG_THROTTLE_MIN/MAX`` and updates this manifest.

VELOG_MANIFEST: dict[str, Any] = dict(
    ui=UiMeta(
        display_name="Velog",
        domain="velog.io",
        category="dev-blog",
        icon="bi-journal-code",
    ),
    bind=[
        BindDescriptor(
            backend="cookie",
            # Default cookies path when config.velog.cookies_path is unset
            # — same default the dispatcher resolves at runtime. Stored
            # as the *shape* (template), not an absolute path — runtime
            # interpolation happens at the bind backend.
            storage_state_path="<config_dir>/velog-cookies.json",
            login_endpoint="/api/velog/login",
            card_template="_settings_channel_velog.html",
            extras={
                "browser_recipe": (
                    "backlink_publisher.publishing.browser_publish."
                    "recipes.velog"
                ),
                "bind_recipe": "backlink_publisher.cli._bind.recipes.velog",
                "login_module": "backlink_publisher.cli.velog_login",
                "selectors_module": (
                    "backlink_publisher.publishing.browser_publish."
                    "recipes._velog_selectors"
                ),
            },
        ),
    ],
    policy=Policy(
        throttle_band=(60, 180),
        env_keys={},
        retry_id="default",
        liveness_probe_sec=900,
        language_whitelist=("ko", "en"),
    ),
    # visibility defaults to "active" — explicit kwarg omitted.
)


# ── telegraph ──────────────────────────────────────────────────────────────
#
# Second migrated channel (first Phase-2 PR). Unlike velog, telegraph
# requires NO user-side binding: the adapter calls ``/createAccount`` on
# first publish and persists the returned token under
# ``<config_dir>/telegraph-token.json`` via the credential-rotation
# pattern (lock + atomic write + orphan archive). That is why
# ``bind=[]`` — there is no settings card, no login endpoint, no
# storage-state path to manage from the UI. The token file IS the
# binding artifact, but its lifecycle is fully automatic. See
# ``telegraph_api._token_path`` / ``_archive_orphan_token``.
#
# ``policy.throttle_band=None`` because telegraph has no documented rate
# limit (the only ``sleep`` in the adapter is a 50-150ms lock-retry
# jitter, which is not a thundering-herd throttle). ``env_keys`` is
# empty for the same reason — there are no ``TELEGRAPH_*`` env knobs.

TELEGRAPH_MANIFEST: dict[str, Any] = dict(
    ui=UiMeta(
        display_name="Telegraph",
        domain="telegra.ph",
        category="instant-publish",
        icon="bi-lightning-charge",
    ),
    bind=[],
    policy=Policy(
        throttle_band=None,
        env_keys={},
        retry_id="default",
        liveness_probe_sec=None,
        language_whitelist=(),
    ),
)


# ── blogger ────────────────────────────────────────────────────────────────
#
# Google OAuth (installed-app flow); token persisted at
# ``<config_dir>/blogger-token.json`` and refreshed under
# ``_refresh_lock()`` (see blogger_api.py). The settings card already
# exists at ``_settings_channel_blogger.html`` and the OAuth client
# credentials live in ``config.blogger_oauth`` (client_id /
# client_secret). ``bind.backend="oauth"`` captures that flow.
#
# No documented Blogger API rate-limit beyond Google's general daily
# quota, which is not a throttle the adapter enforces —
# ``policy.throttle_band=None``. ``language_whitelist`` is empty
# because Blogger accepts any locale.

BLOGGER_MANIFEST: dict[str, Any] = dict(
    ui=UiMeta(
        display_name="Blogger",
        domain="blogger.com",
        category="google-blog",
        icon="bi-google",
    ),
    bind=[
        BindDescriptor(
            backend="oauth",
            storage_state_path="<config_dir>/blogger-token.json",
            card_template="_settings_channel_blogger.html",
            extras={
                "oauth_config_section": "blogger_oauth",
                "token_loader": (
                    "backlink_publisher.config.load_blogger_token"
                ),
            },
        ),
    ],
    policy=Policy(
        throttle_band=None,
        env_keys={},
        retry_id="default",
        liveness_probe_sec=None,
        language_whitelist=(),
    ),
)
