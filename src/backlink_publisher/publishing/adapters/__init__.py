"""Adapter dispatcher — table-driven registry (Plan Unit 7).

Replaced the if/elif chain in the previous ``publish()`` with a
single ``dispatch()`` call into ``publishing.registry``. The Medium
fallback chain (MediumAPI → MediumBrave on macOS → MediumBrowser
on Playwright) is now expressed as registration order, and the
macOS gate lives on ``MediumBraveAdapter.available()``.

Behaviour preserved verbatim:

  - Blogger: ``BloggerAPIAdapter`` only.
  - Medium:
      1. ``MediumAPIAdapter`` (Integration Token; deprecated by Medium 2023)
      2. ``MediumBraveAdapter`` (AppleScript + Brave; macOS only;
         ``available()`` short-circuits elsewhere)
      3. ``MediumBrowserAdapter`` (Playwright headed Chrome — terminal)
  - ``DependencyError`` from one adapter → try the next.
  - ``ExternalServiceError`` (401 / 429 / network) → propagate, no fall.
  - ``dry_run=True`` → sentinel ``AdapterResult`` without publishing.
  - Unknown platform → ``ExternalServiceError("unsupported platform: …")``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from backlink_publisher.config import Config

from .._manifests import (
    BLOGGER_MANIFEST,
    DEVTO_MANIFEST,
    GHPAGES_MANIFEST,
    GITLABPAGES_MANIFEST,
    HACKMD_MANIFEST,
    HASHNODE_MANIFEST,
    HATENA_MANIFEST,
    LINKEDIN_MANIFEST,
    LIVEJOURNAL_MANIFEST,
    MASTODON_MANIFEST,
    MATAROA_MANIFEST,
    MEDIUM_MANIFEST,
    NOTION_MANIFEST,
    QIITA_MANIFEST,
    RENTRY_MANIFEST,
    SUBSTACK_MANIFEST,
    TELEGRAPH_MANIFEST,
    TUMBLR_MANIFEST,
    TXTFYI_MANIFEST,
    VELOG_MANIFEST,
    WORDPRESSCOM_MANIFEST,
    WRITEAS_MANIFEST,
    ZENN_MANIFEST,
)
from .._verify import VerifyResult

# Import the Unit 4a velog browser recipe module so it can populate
# RECIPES["velog"] before the registration line below references it.
# Plan 2026-05-21-001 Unit 4a — registers as auth-missing fallback after
# VelogGraphQLAdapter (DependencyError → fall through; ExternalServiceError
# from API path propagates without fall-through, per registry contract).
from ..browser_publish import BrowserPublishDispatcher
from ..browser_publish.recipes import devto as _devto_recipe  # noqa: F401
from ..browser_publish.recipes import mastodon as _mastodon_recipe  # noqa: F401
from ..browser_publish.recipes import velog as _velog_recipe  # noqa: F401
from ..registry import dispatch, register
from ._nofollow_rationales import NOFOLLOW_RATIONALES as _R
from ._setup_checks import _verify_offline_setup
from ._verify_live import _verify_dry_run, _verify_live
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .devto_api import DevtoAPIAdapter
from .ghpages import GitHubPagesAPIAdapter
from .gitlabpages import GitLabPagesAPIAdapter
from .hackmd_api import HackmdAPIAdapter
from .hashnode_graphql import HashnodeGraphQLAdapter
from .hatena_atompub import HatenaAtomPubAdapter
from .instant_web import (
    TelegraphCdpAdapter,  # noqa: F401  kept for test import, not yet wired
)
from .linkedin_api import LinkedInAPIAdapter
from .livejournal_api import LivejournalAPIAdapter
from .mataroa_api import MataroaAPIAdapter
from .medium_api import MediumAPIAdapter
from .medium_brave import MediumBraveAdapter
from .medium_browser import MediumBrowserAdapter
from .notion_api import NotionAPIAdapter
from .qiita_api import QiitaAPIAdapter
from .rentry_api import RentryAPIAdapter
from .zenn_github import ZennGitHubAdapter
from .substack_api import SubstackAPIAdapter
from .telegraph_api import TelegraphAPIAdapter
from .tumblr_api import TumblrAPIAdapter
from .txtfyi_api import TxtfyiFormPostAdapter
from .velog_graphql import VelogGraphQLAdapter
from .wordpresscom_api import WordpresscomAPIAdapter
from .writeas_api import WriteasAPIAdapter

# Register the fallback chain per platform. Adding a new platform = one
# more ``register(...)`` call — no dispatcher changes. Each registration
# declares ``dofollow=True|False|"uncertain"`` (R1 / Plan 2026-05-20-009);
# ``False`` and ``"uncertain"`` additionally require ``rationale=`` of
# ≥80 stripped chars (R3, mirrors ``monolith_budget.toml`` discipline).
#
# ``TelegraphCdpAdapter`` is imported from ``instant_web.py`` so the
# module is callable from regression tests on this branch, but it is
# NOT added to the dispatch chain yet — that wiring ships with Plan 001
# (PR #141 chrome-cdp-multi-channel-publish) which is still open.
# Manifest declarations for migrated channels live in
# ``publishing/_manifests.py`` (Plan 2026-05-25-002 Phase 2). Adding a
# channel = new ``<SLUG>_MANIFEST`` dict in that file + new
# ``**<SLUG>_MANIFEST`` splat here. The dispatcher module stays focused
# on register() wiring and adapter imports.
register("blogger", BloggerAPIAdapter, dofollow=True, **BLOGGER_MANIFEST)
# Phase 1 dofollow truth audit (2026-05-26): every adapter below shipped
# with bare ``dofollow=True`` and no evidence. Hard server-side
# nofollow/redirect-interstitial evidence => dofollow=False; no
# OUR-pipeline canary => dofollow="uncertain". Rationales live in
# ``_nofollow_rationales`` (_R). Operator flips "uncertain" -> True by
# running a fresh canary and reading verify_link_attributes (the
# livejournal/txtfyi workflow).
register(
    "wordpresscom",
    WordpresscomAPIAdapter,
    dofollow="uncertain",  # evidence conflict (#108->#109 vs 2026-05 recheck); canary pending
    rationale=_R["wordpresscom"],
    referral_value="high",
    **WORDPRESSCOM_MANIFEST,
)
register(
    "hashnode",
    HashnodeGraphQLAdapter,
    dofollow="uncertain",  # 3rd-party live check = dofollow; canary pending; retiring (PR #204)
    rationale=_R["hashnode"],
    referral_value="high",
    **HASHNODE_MANIFEST,
)
register(
    "writeas",
    WriteasAPIAdapter,
    dofollow="uncertain",  # 3rd-party live check = dofollow; canary pending; retiring (PR #202)
    rationale=_R["writeas"],
    referral_value="low",
    **WRITEAS_MANIFEST,
)
register(
    "substack",
    SubstackAPIAdapter,
    dofollow="uncertain",  # 3rd-party live check = dofollow; OUR canary pending
    rationale=_R["substack"],
    referral_value="high",
    **SUBSTACK_MANIFEST,
)
register(
    "rentry",
    RentryAPIAdapter,
    dofollow="uncertain",  # 3rd-party live check = dofollow (rel=noreferrer); canary pending
    rationale=_R["rentry"],
    referral_value="low",
    **RENTRY_MANIFEST,
)
register(
    "linkedin",
    LinkedInAPIAdapter,
    dofollow=False,
    rationale=_R["linkedin"],
    referral_value="high",
    **LINKEDIN_MANIFEST,
    visibility="experimental",
)
register(
    "tumblr",
    TumblrAPIAdapter,
    dofollow=False,
    rationale=_R["tumblr"],
    referral_value="high",
    **TUMBLR_MANIFEST,
)
register(
    "medium",
    MediumAPIAdapter,
    MediumBraveAdapter,
    MediumBrowserAdapter,
    dofollow=True,
    **MEDIUM_MANIFEST,
)
register("telegraph", TelegraphAPIAdapter, dofollow=True, **TELEGRAPH_MANIFEST)
register(
    "velog",
    VelogGraphQLAdapter,
    BrowserPublishDispatcher.for_channel("velog"),
    dofollow=True,
    **VELOG_MANIFEST,
)
register(
    "ghpages",
    GitHubPagesAPIAdapter,
    dofollow=True,
    **GHPAGES_MANIFEST,
)
register(
    "livejournal",
    LivejournalAPIAdapter,
    dofollow=False,
    rationale=_R["livejournal"],
    referral_value="high",
    **LIVEJOURNAL_MANIFEST,
)
register(
    "txtfyi",
    TxtfyiFormPostAdapter,
    dofollow="uncertain",  # R4 canary pending; Phase 0 preliminary = dofollow
    rationale=_R["txtfyi"],
    referral_value="low",  # anonymous pastebin; modest DA + R4 pending
    **TXTFYI_MANIFEST,
)
register(
    "devto",
    DevtoAPIAdapter,
    BrowserPublishDispatcher.for_channel("devto"),
    dofollow=False,
    rationale=_R["devto"],
    referral_value="high",  # high DA + referral traffic + topical signal
    **DEVTO_MANIFEST,
)
register(
    "notion",
    NotionAPIAdapter,
    dofollow=False,
    rationale=_R["notion"],
    referral_value="high",  # DA ~75+, entity signal, indexation speed
    **NOTION_MANIFEST,
)
register(
    "hatena",
    HatenaAtomPubAdapter,
    dofollow="uncertain",  # 3rd-party probe = dofollow (11/12); OUR canary pending
    rationale=_R["hatena"],
    referral_value="high",  # JP high-DA + referral + indexation; AtomPub publish API
    **HATENA_MANIFEST,
)
register(
    "mastodon",
    BrowserPublishDispatcher.for_channel("mastodon"),
    dofollow=False,
    rationale=_R["mastodon"],
    **MASTODON_MANIFEST,
    referral_value="high",  # Fediverse referral traffic + topical signal
)
# Plan 2026-06-01-007 Wave 1 — three new channels, all dofollow="uncertain"
# pending an OUR-pipeline canary (the hashnode/substack/hatena discipline; the
# canary-pending tracking artifact + deadline gate live in docs/discovery/).
register(
    "hackmd",
    HackmdAPIAdapter,
    dofollow="uncertain",  # 3rd-party check=dofollow (188/0); OUR canary pending
    rationale=_R["hackmd"],
    referral_value="high",
    **HACKMD_MANIFEST,
)
register(
    "mataroa",
    MataroaAPIAdapter,
    dofollow="uncertain",  # 3rd-party check=dofollow (6/0, site: fresh); OUR canary pending
    rationale=_R["mataroa"],
    referral_value="high",
    **MATAROA_MANIFEST,
)
register(
    "gitlabpages",
    GitLabPagesAPIAdapter,
    dofollow="uncertain",  # rel operator-controlled, but *.gitlab.io index partial + async; OUR canary pending
    rationale=_R["gitlabpages"],
    referral_value="high",
    **GITLABPAGES_MANIFEST,
)
# Wave-2 discovery (2026-06-01) — confirmed nofollow, high JP referral value.
register(
    "qiita",
    QiitaAPIAdapter,
    dofollow=False,  # confirmed rel=nofollow noopener on all outbound links
    rationale=_R["qiita"],
    referral_value="high",  # top JP dev platform, DA ~90+, high referral traffic
    **QIITA_MANIFEST,
)
register(
    "zenn",
    ZennGitHubAdapter,
    dofollow=False,  # confirmed rel=nofollow noopener noreferrer (36/137)
    rationale=_R["zenn"],
    referral_value="high",  # top JP dev platform, DA ~90+, high referral traffic
    **ZENN_MANIFEST,
)


def publish(
    payload: dict[str, Any],
    mode: str,
    config: Config,
    dry_run: bool = False,
    *,
    banner_emit: Any = None,
) -> AdapterResult:
    """Public dispatch entry point — preserved as a function for backward
    compatibility (CLI / tests / WebUI all call ``publish(...)``).

    ``banner_emit`` (Plan 2026-05-20-004 Unit 1): optional
    ``Callable[[str, dict], None]`` event sink for banner embed
    events.  ``None`` (default) suppresses banner work — preserves
    byte-identical behavior for callers that don't configure
    ``[image_gen]``.
    """
    return dispatch(payload, mode, config, dry_run=dry_run, banner_emit=banner_emit)


def verify_adapter_setup(
    platform: str,
    config: Config,
    *,
    mode: Literal["offline", "live", "dry-run"] = "offline",
    payload: Optional[dict[str, Any]] = None,
) -> Optional[VerifyResult]:
    if mode == "live":
        return _verify_live(platform, config)
    if mode == "dry-run":
        return _verify_dry_run(platform, config, payload or {})
    _verify_offline_setup(platform, config)
    return None
