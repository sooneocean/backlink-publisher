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

from typing import Any, Callable, Literal, Optional

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError
from ..registry import _REGISTRY, dispatch, register, registered_platforms
from .._manifests import (
    BLOGGER_MANIFEST,
    DEVTO_MANIFEST,
    GHPAGES_MANIFEST,
    HASHNODE_MANIFEST,
    LINKEDIN_MANIFEST,
    LIVEJOURNAL_MANIFEST,
    MASTODON_MANIFEST,
    MEDIUM_MANIFEST,
    NOTION_MANIFEST,
    RENTRY_MANIFEST,
    SUBSTACK_MANIFEST,
    TELEGRAPH_MANIFEST,
    TUMBLR_MANIFEST,
    TXTFYI_MANIFEST,
    VELOG_MANIFEST,
    WORDPRESSCOM_MANIFEST,
    WRITEAS_MANIFEST,
)
from .._verify import DryRunInterceptError, VerifyResult, dry_run_intercept
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .ghpages import GitHubPagesAPIAdapter
from .devto_api import DevtoAPIAdapter
from .instant_web import TelegraphCdpAdapter  # noqa: F401  kept for test import, not yet wired
from .livejournal_api import LivejournalAPIAdapter
from .txtfyi_api import TxtfyiFormPostAdapter
from .medium_api import MediumAPIAdapter
from .medium_brave import MediumBraveAdapter
from .medium_browser import MediumBrowserAdapter
from .notion_api import NotionAPIAdapter
from .telegraph_api import TelegraphAPIAdapter, verify_telegraph_setup
from .velog_graphql import VelogGraphQLAdapter
from .wordpresscom_api import WordpresscomAPIAdapter
from .hashnode_graphql import HashnodeGraphQLAdapter
from .writeas_api import WriteasAPIAdapter
from .tumblr_api import TumblrAPIAdapter
from .linkedin_api import LinkedInAPIAdapter
from .substack_api import SubstackAPIAdapter
from .rentry_api import RentryAPIAdapter

# Import the Unit 4a velog browser recipe module so it can populate
# RECIPES["velog"] before the registration line below references it.
# Plan 2026-05-21-001 Unit 4a — registers as auth-missing fallback after
# VelogGraphQLAdapter (DependencyError → fall through; ExternalServiceError
# from API path propagates without fall-through, per registry contract).
from ..browser_publish import BrowserPublishDispatcher
from ..browser_publish.recipes import velog as _velog_recipe  # noqa: F401
from ..browser_publish.recipes import devto as _devto_recipe  # noqa: F401
from ..browser_publish.recipes import mastodon as _mastodon_recipe  # noqa: F401
from ._nofollow_rationales import NOFOLLOW_RATIONALES as _R


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
    "mastodon",
    BrowserPublishDispatcher.for_channel("mastodon"),
    dofollow=False,
    rationale=_R["mastodon"],
    **MASTODON_MANIFEST,
    referral_value="high",  # Fediverse referral traffic + topical signal
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

    # mode == "offline" — dispatch table first, then registry-driven fallback
    check = _SETUP_CHECKS.get(platform)
    if check is not None:
        error = check(config)
        if error:
            raise DependencyError(error)
        return None

    # ── Plan 2026-05-26-002 Unit 1: registry-driven fallback ──────────────
    # Platforms not in _SETUP_CHECKS delegate to their adapter chain's
    # ``available(config)`` — EXCEPT two whose ``available()`` does not reflect
    # per-account binding and would false-positive as "bound":
    #   • livejournal — USERPASS adapter inherits base ``available()`` (always
    #     True); probe its stored credential file instead.
    #   • mastodon    — chrome dispatcher gates on environment, not login;
    #     probe its per-channel Chrome profile instead.
    # Delegating ``available()`` is correct for the rest: the credential
    # adapters return False when unconfigured, and the ANON adapters
    # (txtfyi/rentry) return True ("免绑定·就绪"). Replaces the old terminal
    # raise that misreported 20 registered channels as "No adapter configured".
    if platform not in registered_platforms():
        raise DependencyError(f"No adapter configured for platform: {platform}")

    if platform == "livejournal":
        cred = config.config_dir / "livejournal-credentials.json"
        if cred.exists():
            return
        raise DependencyError(
            "LiveJournal not bound: no stored credentials. Save "
            f'{{"username": "...", "hpassword": "..."}} to {cred} '
            "(use a throwaway account — the secret is password-equivalent)."
        )

    if platform == "mastodon":
        profile = config.config_dir / "real-chrome-profile" / "mastodon"
        if profile.exists() and any(profile.iterdir()):
            return
        raise DependencyError(
            f"Mastodon not bound: no Chrome login profile at {profile}. "
            "Bind via browser login (set [mastodon] instance_url first)."
        )

    _entry = _REGISTRY.get(platform)
    chain = _entry.publishers if _entry else []
    for entry in chain:
        publisher_cls = entry if isinstance(entry, type) else type(entry)
        if publisher_cls.available(config):
            return
    raise DependencyError(f"{platform} not bound: credentials not configured.")


def _check_medium_setup(config: Config) -> str | None:
    has_token = bool(config.medium_integration_token)
    from backlink_publisher.config import load_medium_token
    has_oauth = bool(load_medium_token())
    from .medium_browser import sync_playwright as _spw
    has_playwright = _spw is not None
    if not (has_token or has_oauth or has_playwright):
        return (
            "Medium adapter not ready: no integration_token, no OAuth token file, "
            "and Playwright is not installed. "
            "Run 'playwright install chromium' or configure a token in /settings."
        )
    return None


def _check_ghpages_setup(config: Config) -> str | None:
    if config.ghpages is None or not config.ghpages.repo:
        return (
            "GitHub Pages config missing. Add [ghpages] repo=\"owner/name\" "
            "to ~/.config/backlink-publisher/config.toml"
        )
    if not config.ghpages_token_path.exists():
        return (
            "GitHub Pages PAT not stored. Write "
            f"{{\"token\": \"<pat>\"}} to {config.ghpages_token_path} "
            "(chmod 600). PAT needs Contents:Read+Write on the target repo."
        )
    return None


def _check_velog_setup(config: Config) -> str | None:
    velog_cfg = config.velog
    cookies_path = (
        velog_cfg.cookies_path if velog_cfg else
        config.config_dir / "velog-cookies.json"
    )
    if not cookies_path.exists():
        return (
            f"velog cookies not found: {cookies_path}\n"
            "Run: velog-login"
        )
    return None


_SETUP_CHECKS: dict[str, Callable[[Config], str | None]] = {
    "blogger": lambda c: (
        None if c.blogger_oauth
        else "Blogger OAuth not configured. "
             "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
    ),
    "medium": _check_medium_setup,
    "telegraph": lambda c: _check_telegraph_setup(c),
    "velog": _check_velog_setup,
    "ghpages": _check_ghpages_setup,
    "notion": lambda c: (
        None if NotionAPIAdapter.available(c)
        else (
            "Notion integration token or database_id not configured. "
            f"Write {{\"integration_token\": \"secret_...\", \"database_id\": \"...\"}} "
            f"to {c.notion_token_path} (chmod 600). "
            "Create an Integration at https://www.notion.so/my-integrations."
        )
    ),
    "devto": lambda c: (
        None if DevtoAPIAdapter.available(c)
        else (
            "Dev.to API key not configured. "
            f"Write {{\"api_key\": \"<key>\"}} to {c.devto_token_path} "
            "(chmod 600). Generate at https://dev.to/settings/extensions."
        )
    ),
}


def _check_telegraph_setup(config: Config) -> str | None:
    try:
        verify_telegraph_setup(config)
        return None
    except DependencyError as e:
        return str(e)


def _verify_live(platform: str, config: Config) -> VerifyResult:
    """Live verify — dispatches to per-platform real-API impls when available,
    falls back to ``unverifiable_live`` for platforms still pending backfill.

    Per-channel real impls land per adapter: Telegraph (Unit 6a) →
    GitHub Pages (Unit 7) → Blogger users.get → Medium /me → Velog currentUser.
    """
    if platform not in registered_platforms():
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"no adapter configured for platform: {platform}"],
        )

    # Probe offline-readiness first — if not even configured, no point pinging API.
    try:
        verify_adapter_setup(platform, config, mode="offline")
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    # Per-platform live verify dispatch.
    if platform == "telegraph":
        return _verify_telegraph_live(config)

    if platform == "ghpages":
        return _verify_ghpages_live(config)

    if platform == "blogger":
        return _verify_blogger_live(config)

    if platform == "velog":
        return _verify_velog_live(config)

    # Bound but live-verify-endpoint not yet wired. Surface honestly rather
    # than fake-green. Per-adapter live impls (Medium /me) land in follow-up PRs.
    return VerifyResult(
        ok=True,
        last_verify_result="unverifiable_live",
        blockers=["live verify endpoint not yet implemented for this platform"],
    )


def _verify_telegraph_live(config: Config) -> VerifyResult:
    import requests
    from backlink_publisher.http import post as http_post
    from .telegraph_api import (
        TELEGRAPH_API,
        _HTTP_TIMEOUT_S,
        _INVALID_TOKEN_MARKERS,
        _load_token,
    )

    try:
        token_data = _load_token(config)
    except Exception as e:
        return _never(f"telegraph token file unreadable: {e}")

    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return _never("telegraph token not yet created (publish once to auto-create)")

    verify_timeout = min(5, _HTTP_TIMEOUT_S)
    try:
        resp = http_post(
            f"{TELEGRAPH_API}/getAccountInfo",
            data={
                "access_token": access_token,
                "fields": '["short_name","author_name","page_count"]',
            },
            timeout=verify_timeout,
        )
    except requests.Timeout:
        return _timeout_result(
            f"telegraph getAccountInfo timed out after {verify_timeout}s"
        )
    except requests.RequestException as e:
        return _network_error("telegraph", e)

    try:
        body = resp.json()
    except Exception:
        return _non_json("telegraph")

    if not body.get("ok"):
        err = str(body.get("error", "unknown"))
        if any(marker in err for marker in _INVALID_TOKEN_MARKERS):
            return _token_expired(f"telegraph token rejected: {err}")
        return _never(f"telegraph API error: {err}")

    result_data = body.get("result") or {}
    identity = result_data.get("short_name") or token_data.get("short_name")
    return _ok_result(identity)


_GHPAGES_VERIFY_TIMEOUT_S = 5


def _verify_ghpages_live(config: Config) -> VerifyResult:
    import requests as _r
    from backlink_publisher.http import get as http_get
    from .ghpages import GITHUB_API, _load_token, _required_headers

    try:
        token = _load_token(config)
    except DependencyError as e:
        return _never(str(e))

    try:
        resp = http_get(
            f"{GITHUB_API}/user",
            headers=_required_headers(token),
            timeout=_GHPAGES_VERIFY_TIMEOUT_S,
        )
    except _r.Timeout:
        return _timeout_result(
            f"github.com/user timed out after {_GHPAGES_VERIFY_TIMEOUT_S}s"
        )
    except _r.RequestException as e:
        return _network_error("github", e)

    if resp.status_code == 401:
        return _token_expired(
            "GitHub PAT rejected (HTTP 401) — regenerate at "
            "github.com/settings/tokens and re-save to ghpages-token.json"
        )

    if resp.status_code == 403:
        retry_after = resp.headers.get("retry-after")
        suffix = f" (retry-after={retry_after}s)" if retry_after else ""
        return _never(
            f"GitHub /user forbidden (HTTP 403){suffix} — token missing scope "
            "or hit secondary rate limit"
        )

    if resp.status_code != 200:
        return _never(f"GitHub /user returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except Exception:
        return _non_json("GitHub /user")

    identity = body.get("login") or body.get("name")
    return _ok_result(identity)


_BLOGGER_USERS_SELF = "https://www.googleapis.com/blogger/v3/users/self"
_BLOGGER_VERIFY_TIMEOUT_S = 5


def _verify_blogger_live(config: Config) -> VerifyResult:
    import requests
    from backlink_publisher.http import get as http_get
    from backlink_publisher.config import load_blogger_token

    try:
        token_data = load_blogger_token(config.blogger_token_path)
    except Exception as e:
        return _never(f"blogger token file unreadable: {e}")

    access_token = (token_data or {}).get("token")
    if not access_token:
        return _never(
            "blogger access token not stored yet (bind via /settings or publish once)"
        )

    try:
        resp = http_get(
            _BLOGGER_USERS_SELF,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_BLOGGER_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return _timeout_result(
            f"blogger users.self timed out after {_BLOGGER_VERIFY_TIMEOUT_S}s"
        )
    except requests.RequestException as e:
        return _network_error("blogger", e)

    if resp.status_code == 401:
        return _token_expired(
            "blogger access token expired or revoked — re-bind from /settings "
            "(access tokens are 1h; refresh happens on publish)"
        )

    if resp.status_code != 200:
        return _never(f"blogger users.self returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except Exception:
        return _non_json("blogger")

    identity = body.get("displayName") or body.get("id")
    return _ok_result(identity)


_VELOG_VERIFY_TIMEOUT_S = 5
_VELOG_CURRENT_USER_QUERY = (
    "query CurrentUser { "
    "auth { id username email is_trusted profile { id thumbnail display_name } } "
    "}"
)


def _verify_velog_live(config: Config) -> VerifyResult:
    import requests
    from backlink_publisher.http import post as http_post
    from .velog_graphql import (
        _VELOG_GRAPHQL_ENDPOINT,
        _VELOG_REQUIRED_HEADERS,
        _load_cookies,
    )

    velog_cfg = config.velog
    cookies_path = (
        velog_cfg.cookies_path if velog_cfg else
        config.config_dir / "velog-cookies.json"
    )

    try:
        cookies = _load_cookies(cookies_path)
    except DependencyError as e:
        return _never(str(e))

    try:
        resp = http_post(
            _VELOG_GRAPHQL_ENDPOINT,
            json={"query": _VELOG_CURRENT_USER_QUERY},
            cookies=cookies,
            headers=_VELOG_REQUIRED_HEADERS,
            timeout=_VELOG_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return _timeout_result(
            f"velog auth probe timed out after {_VELOG_VERIFY_TIMEOUT_S}s"
        )
    except requests.RequestException as e:
        return _network_error("velog", e)

    if resp.status_code != 200:
        return _never(f"velog GraphQL returned HTTP {resp.status_code}")

    try:
        body = resp.json()
    except (ValueError, Exception):
        return _non_json("velog")

    current_user = ((body or {}).get("data") or {}).get("auth")
    if current_user is None:
        return _token_expired(
            "velog cookie session expired or revoked — run velog-login again"
        )

    identity = current_user.get("username") or current_user.get("display_name")
    return _ok_result(identity)


def _utc_now_iso() -> str:
    """UTC iso8601 timestamp for last_verified_at.

    Always UTC — never local time (per project_velog_adapter_pr75 lesson:
    TZ regressions bit daily-cap; same trap applies to verify timestamps
    crossing midnight boundaries).
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Shared verify result helpers ─────────────────────────────────────
# Each eliminates the 6-line VerifyResult(...) construction at its call
# site (4× each for timeout/network/non-json/ok, 3-4× for expired/never).


def _ok_result(identity: str, *, dofollow: bool = True) -> VerifyResult:
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=dofollow,
    )


def _timeout_result(message: str) -> VerifyResult:
    return VerifyResult(
        ok=False,
        last_verify_result="timeout",
        blockers=[message],
    )


def _network_error(platform: str, error: Exception) -> VerifyResult:
    return VerifyResult(
        ok=False,
        last_verify_result="never",
        blockers=[f"{platform} network failure: {error}"],
    )


def _non_json(platform: str) -> VerifyResult:
    return VerifyResult(
        ok=False,
        last_verify_result="never",
        blockers=[f"{platform} returned non-JSON response"],
    )


def _token_expired(message: str) -> VerifyResult:
    return VerifyResult(
        ok=False,
        last_verify_result="token_expired",
        blockers=[message],
    )


def _never(message: str) -> VerifyResult:
    return VerifyResult(
        ok=False,
        last_verify_result="never",
        blockers=[message],
    )


def _verify_dry_run(
    platform: str, config: Config, payload: dict[str, Any]
) -> VerifyResult:
    """Dry-run mode: build payload via adapter.publish() under intercept.

    The intercept (``dry_run_intercept()``) monkey-patches ``Session.send`` to
    raise ``DryRunInterceptError``, so even if the adapter forgets to honor
    any dry-run flag, the HTTP send is blocked. Adapters using non-``requests``
    HTTP libs (e.g. SDKs / urllib3 direct) are NOT caught — those fall through
    to ``last_verify_result='unverifiable_live'``.

    Unit 2 scope: ship the contract + intercept. Full per-adapter dry-run
    fidelity (anchor validation, content sanity, image rejection preview)
    lands in Unit 6 backfill.
    """
    if platform not in registered_platforms():
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"no adapter configured for platform: {platform}"],
        )

    try:
        with dry_run_intercept():
            # Today: just validate the platform routes via the existing
            # dispatch. Real adapter.publish() invocation under intercept is
            # the Unit 6 deliverable (needs payload-shape validation per
            # adapter). Surface as 'unverifiable_live' to signal "intercept
            # works but per-adapter dry-run not yet wired".
            pass
    except DryRunInterceptError as e:
        # Should never reach here for the no-op body above; future per-adapter
        # logic may.
        return VerifyResult(
            ok=False,
            last_verify_result="payload_invalid",
            blockers=[f"dry-run intercept fired: {e}"],
        )

    return VerifyResult(
        ok=True,
        last_verify_result="unverifiable_live",
        blockers=["per-adapter dry-run not yet implemented (Unit 6 deliverable)"],
    )
