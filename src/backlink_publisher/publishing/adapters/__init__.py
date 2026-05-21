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
from backlink_publisher._util.errors import DependencyError
from ..registry import dispatch, register, registered_platforms
from .._verify import DryRunInterceptError, VerifyResult, dry_run_intercept
from .base import AdapterResult
from .blogger_api import BloggerAPIAdapter
from .ghpages import GitHubPagesAPIAdapter
from .hashnode import HashnodeAPIAdapter
from .instant_web import TelegraphCdpAdapter, WriteAsCdpAdapter
from .medium_api import MediumAPIAdapter
from .medium_brave import MediumBraveAdapter
from .medium_browser import MediumBrowserAdapter
from .telegraph_api import TelegraphAPIAdapter, verify_telegraph_setup
from .velog_graphql import VelogGraphQLAdapter
from .writeas import WriteAsAPIAdapter

# Import the Unit 4a velog browser recipe module so it can populate
# RECIPES["velog"] before the registration line below references it.
# Plan 2026-05-21-001 Unit 4a — registers as auth-missing fallback after
# VelogGraphQLAdapter (DependencyError → fall through; ExternalServiceError
# from API path propagates without fall-through, per registry contract).
from ..browser_publish import BrowserPublishDispatcher
from ..browser_publish.recipes import velog as _velog_recipe  # noqa: F401
from ..browser_publish.recipes import hashnode as _hashnode_recipe  # noqa: F401
from ..browser_publish.recipes import devto as _devto_recipe  # noqa: F401
from ..browser_publish.recipes import mastodon as _mastodon_recipe  # noqa: F401


# Register the fallback chain per platform. Adding a new platform = one
# more ``register(...)`` call — no dispatcher changes. Each registration
# declares ``dofollow=True|False|"uncertain"`` (R1 / Plan 2026-05-20-009);
# ``False`` and ``"uncertain"`` additionally require ``rationale=`` of
# ≥80 stripped chars (R3, mirrors ``monolith_budget.toml`` discipline).
#
# CDP adapters (``TelegraphCdpAdapter`` / ``WriteAsCdpAdapter``) are
# imported from ``instant_web.py`` so the module is callable from
# regression tests on this branch, but they are NOT added to the
# dispatch chain yet — that wiring ships with Plan 001
# (PR #141 chrome-cdp-multi-channel-publish) which is still open.
register("blogger", BloggerAPIAdapter, dofollow=True)
register(
    "medium",
    MediumAPIAdapter,
    MediumBraveAdapter,
    MediumBrowserAdapter,
    dofollow=True,
)
register("telegraph", TelegraphAPIAdapter, dofollow=True)
register(
    "velog",
    VelogGraphQLAdapter,
    BrowserPublishDispatcher.for_channel("velog"),
    dofollow=True,
)
register("ghpages", GitHubPagesAPIAdapter, dofollow=True)
register(
    "hashnode",
    HashnodeAPIAdapter,
    BrowserPublishDispatcher.for_channel("hashnode"),
    dofollow=False,
    rationale=(
        "Hashnode GraphQL API moved behind a paid subscription on "
        "2026-05-13 — HashnodeAPIAdapter therefore raises DependencyError "
        "for free-tier operators and the chain falls through to "
        "BrowserPublishDispatcher (Plan 2026-05-21-001 Unit 3), which "
        "drives the Web editor at hashnode.com/new and bypasses the "
        "paywall. dofollow stays False pending live link_attr_verifier "
        "measurement — Hashnode injects rel=nofollow on outbound links "
        "for unverified accounts. Pro-account operators retain the API "
        "path without code changes."
    ),
)
register("writeas", WriteAsAPIAdapter, dofollow=True)
register(
    "devto",
    BrowserPublishDispatcher.for_channel("devto"),
    dofollow=False,
    rationale=(
        "Dev.to applies rel=\"nofollow ugc\" to outbound links since "
        "~2022 per platform policy; every external <a> is decorated "
        "server-side regardless of account tier or post format. "
        "Re-registered in Plan 2026-05-21-001 Unit 4b as a chrome "
        "publish channel — backlinks here still drive referral traffic "
        "and topical relevance signals even though they don't transfer "
        "PageRank. Operator dashboard chip surfaces the nofollow "
        "status (Unit 5)."
    ),
)
register(
    "mastodon",
    BrowserPublishDispatcher.for_channel("mastodon"),
    dofollow=False,
    rationale=(
        "Mastodon hardcodes rel=\"nofollow noopener noreferrer\" on "
        "outbound links across all instances — federation-default and "
        "not disableable per-post or per-account. Re-registered in "
        "Plan 2026-05-21-001 Unit 4c as a chrome publish channel — "
        "Fediverse referral traffic + topical signal value despite the "
        "nofollow. Single instance per config.toml [mastodon] "
        "instance_url; security policy: use a throwaway account only, "
        "never a personal Mastodon identity."
    ),
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
    """Verify a platform adapter can do its job. Three modes (Plan 2026-05-19-006 U2):

    - ``mode='offline'`` (default): Backward-compatible. Raises ``DependencyError``
      on failure, returns ``None`` on success. The 14+ pre-Unit-2 call sites
      (``cli/publish_backlinks.py:357``, ``cli/_resume.py:126``, test @patch sites)
      rely on this contract and continue to work unchanged.

    - ``mode='live'``: Calls the platform's lightweight verify endpoint (e.g.
      Telegraph ``getAccountInfo``). Returns ``VerifyResult``; never raises for
      auth failures. Used by ``/api/<channel>/verify`` dashboard endpoint.
      Per-channel live impls land per-adapter — Unit 2 ships stubs returning
      ``last_verify_result='never'`` (for known-unbound) or ``'unverifiable_live'``.

    - ``mode='dry-run'``: Runs the publish path under ``dry_run_intercept()``
      which monkey-patches ``requests.Session.send`` to raise. Returns
      ``VerifyResult``; guarantees zero real HTTP. Defense-in-depth per SEC-5
      review: even an adapter that forgets the flag cannot leak a real publish.
      ``payload`` kwarg supplies the would-be publish content.

    Kept as a module function (not on the ABC) per Plan D8.
    """
    if mode == "live":
        return _verify_live(platform, config)
    if mode == "dry-run":
        return _verify_dry_run(platform, config, payload or {})

    # mode == "offline" — backward-compat path
    if platform == "blogger":
        if not config.blogger_oauth:
            raise DependencyError(
                "Blogger OAuth not configured. "
                "Add [blogger.oauth] to ~/.config/backlink-publisher/config.toml"
            )
        return

    if platform == "medium":
        # verify_adapter_setup is a library-availability check, not an auth
        # check — the four-state badge in /settings is the real auth signal.
        has_token = bool(config.medium_integration_token)
        from backlink_publisher.config import load_medium_token
        has_oauth = bool(load_medium_token())   # existing medium-token.json
        from .medium_browser import sync_playwright as _spw
        has_playwright = _spw is not None
        # has_brave intentionally excluded: MediumBraveAdapter.available()
        # only checks platform.system(), not whether Brave.app is installed.
        # AppleScript failure raises ExternalServiceError (not DependencyError),
        # which does NOT fall through the chain — so counting Brave as ready
        # here would let verify pass but publish crash non-recoverably.

        if not (has_token or has_oauth or has_playwright):
            raise DependencyError(
                "Medium adapter not ready: no integration_token, no OAuth token file, "
                "and Playwright is not installed. "
                "Run 'playwright install chromium' or configure a token in /settings."
            )
        return

    if platform == "telegraph":
        # Telegraph has no required prerequisites: the adapter auto-creates
        # an anonymous account on first publish.  verify_telegraph_setup
        # only raises if the config_dir cannot be created (filesystem-level
        # fault) or an existing token file is malformed / wrong perms.
        verify_telegraph_setup(config)
        return

    if platform == "velog":
        velog_cfg = config.velog
        cookies_path = (
            velog_cfg.cookies_path if velog_cfg else
            config.config_dir / "velog-cookies.json"
        )
        if not cookies_path.exists():
            raise DependencyError(
                f"velog cookies not found: {cookies_path}\n"
                "Run: velog-login"
            )
        return

    if platform == "ghpages":
        if config.ghpages is None or not config.ghpages.repo:
            raise DependencyError(
                "GitHub Pages config missing. Add [ghpages] repo=\"owner/name\" "
                "to ~/.config/backlink-publisher/config.toml"
            )
        if not config.ghpages_token_path.exists():
            raise DependencyError(
                "GitHub Pages PAT not stored. Write "
                f"{{\"token\": \"<pat>\"}} to {config.ghpages_token_path} "
                "(chmod 600). PAT needs Contents:Read+Write on the target repo."
            )
        return

    if platform == "hashnode":
        if config.hashnode is None or not config.hashnode.publication_id:
            raise DependencyError(
                "Hashnode config missing. Add [hashnode] publication_id=\"<id>\" "
                "to ~/.config/backlink-publisher/config.toml"
            )
        if not config.hashnode_token_path.exists():
            raise DependencyError(
                "Hashnode PAT not stored. Write "
                f"{{\"token\": \"<pat>\"}} to {config.hashnode_token_path} "
                "(chmod 600). Generate at hashnode.com/settings/developer."
            )
        return

    if platform == "writeas":
        # Offline verify mirrors the API-adapter contract (config + token);
        # WriteAsCdpAdapter's `available()` only checks for a Chrome binary,
        # so short-circuiting on it would let a machine with Chrome but no
        # writeas-token pass verify and crash at publish-time when the
        # dispatch chain falls through to WriteAsAPIAdapter without a
        # token. The CDP adapter is still registered in the chain and
        # remains tryable by the dispatcher; verify gates on the
        # API-path prerequisites that the chain ultimately depends on.
        if config.writeas is None:
            raise DependencyError(
                "Write.as config missing. Add [writeas] section to "
                "~/.config/backlink-publisher/config.toml"
            )
        if not config.writeas_token_path.exists():
            raise DependencyError(
                "Write.as token not stored. Write "
                f"{{\"token\": \"<access_token>\"}} to {config.writeas_token_path} "
                "(chmod 600). Obtain via POST /api/auth/login or writeas-login CLI."
            )
        return

    raise DependencyError(f"No adapter configured for platform: {platform}")


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

    if platform == "hashnode":
        return _verify_hashnode_live(config)

    if platform == "writeas":
        return _verify_writeas_live(config)

    # Bound but live-verify-endpoint not yet wired. Surface honestly rather
    # than fake-green. Per-adapter live impls (Medium /me) land in follow-up PRs.
    return VerifyResult(
        ok=True,
        last_verify_result="unverifiable_live",
        blockers=["live verify endpoint not yet implemented for this platform"],
    )


def _verify_telegraph_live(config: Config) -> VerifyResult:
    """POST ``/getAccountInfo`` to confirm the stored access_token still works.

    Plan 2026-05-19-006 Unit 6a — replaces the stub for telegraph. Reads the
    token from the existing ``_load_token`` loader in telegraph_api.
    200 + ``ok:true`` → identity = short_name.  Telegraph error markers
    (ACCESS_TOKEN_INVALID / INVALID_ACCESS_TOKEN) → ``token_expired``.
    ``requests.Timeout`` → ``timeout``. Other errors → ``never`` with
    blocker text.

    Read-only by design: NEVER triggers token rotation. Rotation belongs
    to the publish path; live verify must not write token files.
    """
    import requests
    from .telegraph_api import (
        TELEGRAPH_API,
        _HTTP_TIMEOUT_S,
        _INVALID_TOKEN_MARKERS,
        _load_token,
    )

    try:
        token_data = _load_token(config)
    except Exception as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"telegraph token file unreadable: {e}"],
        )

    access_token = token_data.get("access_token") if token_data else None
    if not access_token:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["telegraph token not yet created (publish once to auto-create)"],
        )

    # 5s server-side hard cap per Unit 4 SLA. telegraph_api uses 15s for
    # publish, but live verify is a dashboard-facing snappy call.
    verify_timeout = min(5, _HTTP_TIMEOUT_S)

    try:
        resp = requests.post(
            f"{TELEGRAPH_API}/getAccountInfo",
            data={
                "access_token": access_token,
                "fields": '["short_name","author_name","page_count"]',
            },
            timeout=verify_timeout,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[f"telegraph getAccountInfo timed out after {verify_timeout}s"],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"telegraph network failure: {e}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["telegraph returned non-JSON response"],
        )

    if not body.get("ok"):
        err = str(body.get("error", "unknown"))
        if any(marker in err for marker in _INVALID_TOKEN_MARKERS):
            return VerifyResult(
                ok=False,
                last_verify_result="token_expired",
                blockers=[f"telegraph token rejected: {err}"],
            )
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"telegraph API error: {err}"],
        )

    result_data = body.get("result") or {}
    identity = result_data.get("short_name") or token_data.get("short_name")

    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


_GHPAGES_VERIFY_TIMEOUT_S = 5


def _verify_ghpages_live(config: Config) -> VerifyResult:
    """GET ``api.github.com/user`` to confirm the PAT is still valid.

    Plan 2026-05-19-006 Unit 7 — ships GitHub Pages adapter with live
    verify built in.

    Strict read-only: ``ghpages-token.json`` is never mutated. Verify just
    reads the PAT and pings the user endpoint. Token rotation is the
    operator's job (PAT regeneration in github.com/settings/tokens).

    Status mapping:
      - 200 → ``ok``, identity = ``login``, dofollow=True (Jekyll default)
      - 401 → ``token_expired`` (PAT revoked or scope removed)
      - 403 → ``never`` (rate-limit / scope mismatch — not auth-fixable)
      - ``requests.Timeout`` → ``timeout``
      - other (5xx / connection / parse) → ``never``
    """
    import requests as _r
    from .ghpages import GITHUB_API, _load_token, _required_headers

    try:
        token = _load_token(config)
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    try:
        resp = _r.get(
            f"{GITHUB_API}/user",
            headers=_required_headers(token),
            timeout=_GHPAGES_VERIFY_TIMEOUT_S,
        )
    except _r.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"github.com/user timed out after {_GHPAGES_VERIFY_TIMEOUT_S}s"
            ],
        )
    except _r.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"github network failure: {e}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "GitHub PAT rejected (HTTP 401) — regenerate at "
                "github.com/settings/tokens and re-save to ghpages-token.json"
            ],
        )

    if resp.status_code == 403:
        retry_after = resp.headers.get("retry-after")
        suffix = f" (retry-after={retry_after}s)" if retry_after else ""
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[
                f"GitHub /user forbidden (HTTP 403){suffix} — token missing scope "
                "or hit secondary rate limit"
            ],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"GitHub /user returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["GitHub /user returned non-JSON response"],
        )

    identity = body.get("login") or body.get("name")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


_BLOGGER_USERS_SELF = "https://www.googleapis.com/blogger/v3/users/self"
_BLOGGER_VERIFY_TIMEOUT_S = 5


def _verify_blogger_live(config: Config) -> VerifyResult:
    """GET ``blogger/v3/users/self`` with the stored access_token as Bearer.

    Plan 2026-05-19-006 Unit 6c — replaces the stub for blogger.

    Strict read-only: the stored ``blogger-token.json`` is NEVER mutated.
    OAuth refresh (and the corresponding ``save_blogger_token`` write) is a
    publish-path concern — verify reads whatever access_token is currently
    on disk and reports the outcome. Practical consequence: an operator who
    has not published in over an hour will see ``token_expired`` until they
    re-bind or publish once. The dashboard surfaces a hint to re-bind; this
    is the deliberate trade for the read-only invariant that protects token
    files from being rotated by an observe-only UI action.

    Status mapping:
      - 200 → ``ok`` with identity=displayName, dofollow=True
      - 401 → ``token_expired`` (operator action: re-bind)
      - ``requests.Timeout`` → ``timeout``
      - everything else (403/5xx/connection/parse) → ``never``
    """
    import requests
    from backlink_publisher.config import load_blogger_token

    try:
        token_data = load_blogger_token(config.blogger_token_path)
    except Exception as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"blogger token file unreadable: {e}"],
        )

    access_token = (token_data or {}).get("token")
    if not access_token:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[
                "blogger access token not stored yet (bind via /settings or publish once)"
            ],
        )

    try:
        resp = requests.get(
            _BLOGGER_USERS_SELF,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=_BLOGGER_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"blogger users.self timed out after {_BLOGGER_VERIFY_TIMEOUT_S}s"
            ],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"blogger network failure: {e}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "blogger access token expired or revoked — re-bind from /settings "
                "(access tokens are 1h; refresh happens on publish)"
            ],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"blogger users.self returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["blogger returned non-JSON response"],
        )

    identity = body.get("displayName") or body.get("id")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


_VELOG_VERIFY_TIMEOUT_S = 5
_VELOG_CURRENT_USER_QUERY = (
    "query CurrentUser { "
    "auth { id username email is_trusted profile { id thumbnail display_name } } "
    "}"
)


def _verify_velog_live(config: Config) -> VerifyResult:
    """POST Velog v2 GraphQL ``auth`` to confirm the cookie session is live.

    Plan 2026-05-19-006 Unit 6b — replaces the stub for velog.

    Strict read-only: the on-disk ``velog-cookies.json`` is never mutated.
    Velog's implicit-refresh model (server issues a fresh ``access_token``
    via ``Set-Cookie`` on any authenticated request) is captured by
    ``requests.Session`` in-memory only — we do not persist any updated
    cookies back to disk, matching the publish adapter's behaviour.

    Status mapping:
      - 200 + ``data.auth`` non-null → ``ok``, identity=username,
        dofollow=True (velog is confirmed dofollow per Plan R-Phase4 roster)
      - 200 + ``data.auth`` is null → ``token_expired`` (velog's
        silent-drop signal that the session is no longer authenticated)
      - ``requests.Timeout`` → ``timeout``
      - everything else (HTTP non-200 / parse failure / connection error)
        → ``never``
    """
    import requests
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
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    try:
        resp = requests.post(
            _VELOG_GRAPHQL_ENDPOINT,
            json={"query": _VELOG_CURRENT_USER_QUERY},
            cookies=cookies,
            headers=_VELOG_REQUIRED_HEADERS,
            timeout=_VELOG_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"velog auth probe timed out after {_VELOG_VERIFY_TIMEOUT_S}s"
            ],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"velog network failure: {e}"],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"velog GraphQL returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except (ValueError, Exception):
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["velog returned non-JSON response"],
        )

    current_user = ((body or {}).get("data") or {}).get("auth")
    if current_user is None:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "velog cookie session expired or revoked — run velog-login again"
            ],
        )

    identity = current_user.get("username") or current_user.get("display_name")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


_HASHNODE_VERIFY_TIMEOUT_S = 5


def _verify_hashnode_live(config: Config) -> VerifyResult:
    """POST ``query { me { ... } }`` to confirm the PAT is still valid.

    Plan 2026-05-19-006 Unit 8 — first-class live verify built in with
    the adapter (same model as ghpages Unit 7).

    Strict read-only: ``hashnode-token.json`` is never mutated. The PAT
    is bearer-equivalent (server-side rotation only) so the adapter has
    nothing to write back. Token rotation is the operator's job
    (regenerate at hashnode.com/settings/developer).

    Status mapping:
      - 200 + ``data.me`` non-null → ``ok``, identity = username, dofollow=True
        (Hashnode is confirmed dofollow on canonical post URLs)
      - 200 + ``errors`` only       → ``token_expired`` when the message
        mentions auth/unauthorized; otherwise ``never``
      - 401                          → ``token_expired``
      - ``requests.Timeout``         → ``timeout``
      - other (4xx/5xx/connection/parse) → ``never`` with blocker text
    """
    import requests
    from .hashnode import HASHNODE_API, ME_QUERY, _required_headers, _load_token

    try:
        token = _load_token(config)
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    try:
        resp = requests.post(
            HASHNODE_API,
            headers=_required_headers(token),
            json={"query": ME_QUERY},
            timeout=_HASHNODE_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"hashnode /me timed out after {_HASHNODE_VERIFY_TIMEOUT_S}s"
            ],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"hashnode network failure: {e}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "Hashnode PAT rejected (HTTP 401) — regenerate at "
                "hashnode.com/settings/developer and re-save to hashnode-token.json"
            ],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"Hashnode /me returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["Hashnode returned non-JSON response"],
        )

    me = ((body or {}).get("data") or {}).get("me")
    if me is None:
        # GraphQL errors-only response. Classify by message content.
        errors = body.get("errors") or []
        msg = (errors[0].get("message") if errors else "") or "unknown"
        lowered = msg.lower()
        if any(k in lowered for k in ("unauthorized", "auth", "invalid token")):
            return VerifyResult(
                ok=False,
                last_verify_result="token_expired",
                blockers=[f"Hashnode auth error: {msg}"],
            )
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"Hashnode GraphQL error: {msg}"],
        )

    identity = me.get("username") or me.get("name")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


_WRITEAS_VERIFY_TIMEOUT_S = 5


def _verify_writeas_live(config: Config) -> VerifyResult:
    """GET ``/api/me`` to confirm the stored token still works.

    Plan 2026-05-19-006 Unit 9 — replaces the stub for writeas. Reads
    the token via the adapter's standard ``_load_token`` helper.

    Strict read-only: ``writeas-token.json`` is never mutated. Write.as
    tokens are revoked on operator logout (or on server policy) — verify
    never rotates the token, that's a re-login concern.

    Status mapping:
      - 200 + ``data`` non-null → ``ok``, identity = username, dofollow=True
        (Write.as collection-bound posts confirmed dofollow per Phase 3 roster)
      - 401 → ``token_expired`` (signals operator must re-login)
      - ``requests.Timeout`` → ``timeout``
      - other (4xx/5xx/connection/parse) → ``never``
    """
    import requests
    from .writeas import _load_token, _required_headers, DEFAULT_API_BASE

    try:
        token = _load_token(config)
    except DependencyError as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[str(e)],
        )

    wa_cfg = config.writeas
    api_base = (wa_cfg.api_base if wa_cfg else DEFAULT_API_BASE) or DEFAULT_API_BASE

    try:
        resp = requests.get(
            f"{api_base.rstrip('/')}/me",
            headers=_required_headers(token),
            timeout=_WRITEAS_VERIFY_TIMEOUT_S,
        )
    except requests.Timeout:
        return VerifyResult(
            ok=False,
            last_verify_result="timeout",
            blockers=[
                f"write.as /me timed out after {_WRITEAS_VERIFY_TIMEOUT_S}s"
            ],
        )
    except requests.RequestException as e:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"write.as network failure: {e}"],
        )

    if resp.status_code == 401:
        return VerifyResult(
            ok=False,
            last_verify_result="token_expired",
            blockers=[
                "Write.as token rejected (HTTP 401) — re-login at write.as "
                "and re-save to writeas-token.json"
            ],
        )

    if resp.status_code != 200:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=[f"Write.as /me returned HTTP {resp.status_code}"],
        )

    try:
        body = resp.json()
    except Exception:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["Write.as returned non-JSON response"],
        )

    data = (body or {}).get("data")
    if not data:
        return VerifyResult(
            ok=False,
            last_verify_result="never",
            blockers=["Write.as /me returned empty data"],
        )

    identity = data.get("username") or data.get("email")
    return VerifyResult(
        ok=True,
        identity=identity,
        last_verified_at=_utc_now_iso(),
        last_verify_result="ok",
        dofollow=True,
    )


def _utc_now_iso() -> str:
    """UTC iso8601 timestamp for last_verified_at.

    Always UTC — never local time (per project_velog_adapter_pr75 lesson:
    TZ regressions bit daily-cap; same trap applies to verify timestamps
    crossing midnight boundaries).
    """
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
