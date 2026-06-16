"""Live + dry-run adapter verification.

Extracted from ``adapters/__init__.py`` so the dispatcher stays focused on
registry wiring. Holds ``_verify_live`` (real-API identity probes for
telegraph / ghpages / blogger / velog), ``_verify_dry_run`` (intercept-based),
and the shared ``VerifyResult`` constructors. HTTP goes through
``backlink_publisher.http`` — the seam the live tests patch — preserved
verbatim across the move.
"""

from __future__ import annotations

from typing import Any

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError
from ..registry import registered_platforms
from .._verify import DryRunInterceptError, VerifyResult, dry_run_intercept
from ._setup_checks import _verify_offline_setup


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
        _verify_offline_setup(platform, config)
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
