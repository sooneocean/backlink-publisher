"""Chrome backend MUST apply recipe.cookie_host_filter — Plan 2026-05-20-016 Unit 0 Fix 1.

Before this PR, ``RealChromeBrowserRunner._provider()`` persisted
``cdp.all_cookies()`` verbatim, ignoring ``recipe.cookie_host_filter``.
The operator's real-Chrome profile contains cookies for every site they
have ever logged into — banking, email, SSO IdPs, ad networks. All of
those would land in ``<config_dir>/<channel>-storage-state.json``
(mode 0600 but still readable by any process running as the operator).

Spike report (`docs/spike-notes/2026-05-20-hashnode-bind-discovery.md`)
documents capturing 101 cookies during a Hashnode bind attempt, of
which only 9 were on hashnode.com apex — 92 cross-domain trackers
(googleadservices, criteo, doubleclick, youtube, immersivetranslate,
wallethighlighter, stackadapt, etc.) leaked in.

This module's tests exercise:
  - Filter applied correctly (positive case): only host-matching cookies persist.
  - Realistic telegraph regression (50-cookie mock): operator's mixed cookie
    jar reduces to only telegra.ph + telegram.org entries.
  - Filter MISSING → fail-closed (raise ChromeLaunchError, no state file).
  - Malformed cookie dict skipped defensively (matches driver._apply_host_filter).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backlink_publisher.cli._bind.chrome_backend import RealChromeBrowserRunner
from backlink_publisher.cli._bind.driver import ChromeLaunchError


# ─────────── helpers ────────────────────────────────────────────────────────


def _recipe(host_filter):
    """Minimal duck-typed recipe with only the fields _provider touches."""
    r = MagicMock()
    r.login_url = "https://example.com/login"
    r.bound_predicate = lambda page: None
    r.cookie_host_filter = host_filter
    return r


def _stub_cdp_and_runner(cookies):
    """Wire a RealChromeBrowserRunner whose _launch_or_connect short-circuits
    and whose CDP client returns the supplied cookies on all_cookies()."""
    cdp = MagicMock()
    cdp.all_cookies.return_value = cookies

    runner = RealChromeBrowserRunner()
    runner._launch_or_connect = MagicMock(return_value=cdp)
    runner._terminate_proc = MagicMock()
    return runner, cdp


def _drive_provider(runner, recipe, tmp_path):
    """Invoke launch_and_wait + provider to capture the persisted state.

    Returns the parsed JSON dict, or None when provider did not write a file.
    """
    target = tmp_path / "state.json"
    provider = runner.launch_and_wait(
        recipe=recipe,
        on_browser_ready=lambda: None,
        on_login_detected=lambda: None,
    )
    provider(path=str(target))
    if not target.exists():
        return None
    return json.loads(target.read_text())


# ─────────── happy path: filter applied ────────────────────────────────────


def test_host_filter_drops_non_matching_cookies(tmp_path):
    """Recipe accepting only example.com → 3-of-5 cookies persist."""
    cookies = [
        {"name": "session", "domain": "example.com", "value": "abc"},
        {"name": "csrf", "domain": ".example.com", "value": "xyz"},
        {"name": "cf_clearance", "domain": "example.com", "value": "cf"},
        {"name": "_ga", "domain": ".google-analytics.com", "value": "ga"},
        {"name": "DSID", "domain": ".doubleclick.net", "value": "ad"},
    ]
    runner, _ = _stub_cdp_and_runner(cookies)
    recipe = _recipe(host_filter=lambda host: host.lstrip(".") == "example.com")

    state = _drive_provider(runner, recipe, tmp_path)

    assert state is not None
    assert state["origins"] == []
    persisted_names = {c["name"] for c in state["cookies"]}
    assert persisted_names == {"session", "csrf", "cf_clearance"}


# ─────────── telegraph realistic regression ────────────────────────────────


def test_telegraph_filter_strips_50_mixed_cookies_to_2(tmp_path):
    """The spike captured 101 cookies including google/criteo/doubleclick.
    This regression uses a 50-cookie mix (3 telegra.ph apex + 47 cross-domain
    from a real operator's Chrome profile shape) and asserts the persisted
    file contains only the telegra.ph entries."""
    telegraph_cookies = [
        {"name": "stel_sso_session", "domain": "telegra.ph", "value": "real-token"},
        {"name": "stel_token", "domain": ".telegra.ph", "value": "auth"},
        {"name": "stel_dt", "domain": "telegra.ph", "value": "device-token"},
    ]
    cross_domain_cookies = [
        {"name": f"cookie_{i}", "domain": f".{site}", "value": "v"}
        for i, site in enumerate(
            ["google.com", "youtube.com", "doubleclick.net", "criteo.com",
             "stackadapt.com", "immersivetranslate.com", "wallethighlighter.com",
             "googleadservices.com", "github.com", "anthropic.com",
             "openai.com", "stripe.com", "amazon.com", "linkedin.com",
             "twitter.com", "facebook.com", "reddit.com", "discord.com",
             "slack.com", "zoom.us", "notion.so", "atlassian.com",
             "1password.com", "lastpass.com", "fastmail.com", "protonmail.com",
             "icloud.com", "dropbox.com", "google-analytics.com", "mixpanel.com",
             "segment.io", "amplitude.com", "hotjar.com", "intercom.com",
             "zendesk.com", "salesforce.com", "hubspot.com", "shopify.com",
             "paypal.com", "venmo.com", "wise.com", "revolut.com",
             "chase.com", "bankofamerica.com", "wellsfargo.com", "fidelity.com",
             "vanguard.com"]
        )
    ]
    all_cookies = telegraph_cookies + cross_domain_cookies
    assert len(all_cookies) == 50

    runner, _ = _stub_cdp_and_runner(all_cookies)
    recipe = _recipe(
        host_filter=lambda host: host.lstrip(".") == "telegra.ph"
    )

    state = _drive_provider(runner, recipe, tmp_path)

    persisted_names = sorted(c["name"] for c in state["cookies"])
    assert persisted_names == ["stel_dt", "stel_sso_session", "stel_token"]
    # And EVERY cross-domain entry must be gone.
    persisted_domains = {c["domain"].lstrip(".") for c in state["cookies"]}
    assert persisted_domains == {"telegra.ph"}


# ─────────── fail-closed: missing filter ───────────────────────────────────


def test_missing_host_filter_raises_chrome_launch_error(tmp_path):
    """Defensive fallback is fail-CLOSED. A recipe that forgets the field
    MUST NOT silently persist the full cookie jar."""
    cookies = [{"name": "anything", "domain": "anywhere.com", "value": "v"}]
    runner, _ = _stub_cdp_and_runner(cookies)
    recipe = _recipe(host_filter=None)

    with pytest.raises(ChromeLaunchError) as exc_info:
        runner.launch_and_wait(
            recipe=recipe,
            on_browser_ready=lambda: None,
            on_login_detected=lambda: None,
        )
    assert exc_info.value.error_code == "recipe_missing_host_filter"

    # The state file was never created.
    assert not (tmp_path / "state.json").exists()
    # Chrome process was terminated.
    runner._terminate_proc.assert_called_once()


def test_missing_filter_via_attribute_absent_also_fails_closed(tmp_path):
    """Defense against duck-typed recipes that don't even declare the field
    (older recipe versions, partial mocks): getattr(..., None) handles both
    'attribute is None' and 'attribute does not exist'."""

    class BareRecipe:
        login_url = "https://example.com/login"

        @staticmethod
        def bound_predicate(page):
            return None
        # cookie_host_filter intentionally absent

    runner, _ = _stub_cdp_and_runner([])
    with pytest.raises(ChromeLaunchError) as exc_info:
        runner.launch_and_wait(
            recipe=BareRecipe(),
            on_browser_ready=lambda: None,
            on_login_detected=lambda: None,
        )
    assert exc_info.value.error_code == "recipe_missing_host_filter"


# ─────────── defensive: malformed cookies ──────────────────────────────────


def test_malformed_cookie_dicts_are_skipped_not_raised(tmp_path):
    """Mirrors driver._apply_host_filter behavior: non-dict entries are
    silently skipped. The bind must not fail because Chrome returned a
    weird record shape."""
    cookies = [
        {"name": "good", "domain": "example.com", "value": "v"},
        "not-a-dict",
        None,
        {"name": "good2", "domain": "example.com", "value": "w"},
        {"domain": "example.com"},  # no name — still kept (dict + matching domain)
    ]
    runner, _ = _stub_cdp_and_runner(cookies)
    recipe = _recipe(host_filter=lambda host: host.lstrip(".") == "example.com")

    state = _drive_provider(runner, recipe, tmp_path)
    # 3 dicts matched, non-dict entries silently dropped.
    assert len(state["cookies"]) == 3
