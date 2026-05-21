"""AuthExpiredError must propagate, NOT fallthrough — Plan 2026-05-20-016 Unit 0b.

Before this PR, ``registry.dispatch()`` had:

    try:
        return adapter.publish(...)
    except DependencyError as e:
        last_dep_error = e
        continue   # ← would silently catch AuthExpiredError too

Because ``AuthExpiredError`` is a subclass of ``DependencyError``
(_util/errors.py), expired-credentials raised from one adapter would
be swallowed by the dispatcher and the chain would try the next
adapter — burying the "user must re-bind" signal and producing
confusing downstream errors (or, worse, silent success on a wrong
adapter).

This module's tests exercise every bind channel currently in
``cli._bind.channels.CHANNELS`` (medium, velog, blogger) plus the
compose-correctness scenario where a plain ``DependencyError`` falls
through to a second adapter that raises ``AuthExpiredError``. The fix
is a 4-line ``except AuthExpiredError: raise`` clause inserted
*before* the DependencyError catch in ``registry.py``.

Channels NOT covered here:
  - telegraph: no bind flow (token rotates via createAccount API),
    not in CHANNELS, so ``AuthExpiredError(channel='telegraph')``
    would itself raise UsageError. The fix still benefits any future
    re-introduction.
  - hashnode: browser adapter ships in plan-016 Unit 3, not yet
    in the chain at the time of this PR.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher._util.errors import (
    AuthExpiredError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.config import Config, BloggerOAuthConfig
from backlink_publisher.publishing.adapters import publish
from backlink_publisher.publishing.adapters.base import AdapterResult


# ─────────── shared fixtures ────────────────────────────────────────────────


def _payload(platform: str) -> dict:
    return {
        "id": f"id-{platform}",
        "platform": platform,
        "title": "Test",
        "content_markdown": "Hello.",
        "tags": [],
        "main_domain": "https://x.example.com/",
        "seo": {"canonical_url": ""},
    }


CONFIG_BLOGGER = Config(
    blogger_blog_ids={"https://x.example.com": "999"},
    blogger_oauth=BloggerOAuthConfig("cid", "csecret"),
)
CONFIG_MEDIUM = Config(medium_integration_token="tok123")
CONFIG_VELOG = Config()


# ─────────── core invariant: AuthExpired propagates per channel ────────────


def test_blogger_authexpired_propagates_no_fallthrough():
    """Plan 016 Unit 0b R3 — blogger chain has one adapter; AuthExpired
    must propagate, not be reabsorbed as a plain DependencyError."""
    with patch(
        "backlink_publisher.publishing.adapters.BloggerAPIAdapter.publish",
        side_effect=AuthExpiredError(channel="blogger", reason="cookies expired"),
    ) as mock_pub:
        with pytest.raises(AuthExpiredError) as exc_info:
            publish(_payload("blogger"), mode="draft", config=CONFIG_BLOGGER)
        assert exc_info.value.channel == "blogger"
        mock_pub.assert_called_once()


def test_velog_authexpired_propagates_no_fallthrough():
    """Plan 016 Unit 0b R3 — velog has a single GraphQL adapter; expired
    cookies must surface, not be reclassified as missing-dependency."""
    with patch(
        "backlink_publisher.publishing.adapters.VelogGraphQLAdapter.publish",
        side_effect=AuthExpiredError(channel="velog", reason="cookies expired"),
    ) as mock_pub:
        with pytest.raises(AuthExpiredError) as exc_info:
            publish(_payload("velog"), mode="draft", config=CONFIG_VELOG)
        assert exc_info.value.channel == "velog"
        mock_pub.assert_called_once()


@patch("backlink_publisher.publishing.adapters.MediumBrowserAdapter.publish")
@patch("backlink_publisher.publishing.adapters.MediumBraveAdapter.publish")
@patch(
    "backlink_publisher.publishing.adapters.MediumAPIAdapter.publish",
    side_effect=AuthExpiredError(channel="medium", reason="integration token revoked"),
)
def test_medium_authexpired_from_api_does_not_fallthrough_to_brave_or_browser(
    mock_api, mock_brave, mock_browser
):
    """Plan 016 Unit 0b R3 — medium's 3-adapter chain (API → Brave →
    Browser) is the strongest fallthrough surface in the repo. When the
    API adapter raises AuthExpiredError, neither Brave nor Browser may
    be invoked — operator must see the re-bind prompt directly."""
    with pytest.raises(AuthExpiredError) as exc_info:
        publish(_payload("medium"), mode="draft", config=CONFIG_MEDIUM)
    assert exc_info.value.channel == "medium"
    mock_api.assert_called_once()
    mock_brave.assert_not_called()
    mock_browser.assert_not_called()


# ─────────── regression guards: non-Auth semantics preserved ───────────────


# Patch available() on Brave + Browser so dispatch reaches their .publish
# regardless of host environment. Brave's natural available() returns False
# on machines without the Brave binary (CI hosts don't have it), which
# would cause dispatch to skip the adapter and these tests to fail with
# spurious "publish was not called" assertions.

@patch("backlink_publisher.publishing.adapters.MediumBrowserAdapter.available", return_value=True)
@patch(
    "backlink_publisher.publishing.adapters.MediumBrowserAdapter.publish",
    return_value=AdapterResult(
        status="drafted",
        adapter="medium-browser",
        platform="medium",
        draft_url="https://medium.com/new-story?id=abc",
    ),
)
@patch("backlink_publisher.publishing.adapters.MediumBraveAdapter.available", return_value=True)
@patch(
    "backlink_publisher.publishing.adapters.MediumBraveAdapter.publish",
    side_effect=DependencyError("brave not running"),
)
@patch(
    "backlink_publisher.publishing.adapters.MediumAPIAdapter.publish",
    side_effect=DependencyError("no token"),
)
def test_plain_dependency_error_still_fallthroughs(
    mock_api, mock_brave_pub, mock_brave_avail, mock_browser_pub, mock_browser_avail
):
    """Regression guard — adding the AuthExpired short-circuit must NOT
    have broken the original DependencyError fallthrough semantics.
    With API + Brave both raising plain DependencyError, dispatch must
    reach the Browser adapter and return its result."""
    result = publish(_payload("medium"), mode="draft", config=CONFIG_MEDIUM)
    assert result.adapter == "medium-browser"
    mock_api.assert_called_once()
    mock_brave_pub.assert_called_once()
    mock_browser_pub.assert_called_once()


@patch("backlink_publisher.publishing.adapters.MediumBrowserAdapter.available", return_value=True)
@patch("backlink_publisher.publishing.adapters.MediumBrowserAdapter.publish")
@patch("backlink_publisher.publishing.adapters.MediumBraveAdapter.available", return_value=True)
@patch(
    "backlink_publisher.publishing.adapters.MediumBraveAdapter.publish",
    side_effect=AuthExpiredError(channel="medium", reason="cookies expired"),
)
@patch(
    "backlink_publisher.publishing.adapters.MediumAPIAdapter.publish",
    side_effect=DependencyError("no token"),
)
def test_dependency_then_authexpired_composes_correctly(
    mock_api, mock_brave_pub, mock_brave_avail, mock_browser_pub, mock_browser_avail
):
    """Compose-correctness — first adapter raises plain DependencyError
    (legal fallthrough), second adapter then raises AuthExpiredError.
    AuthExpired from the second adapter must propagate; Browser adapter
    must never be called. Confirms the new ``except AuthExpiredError:
    raise`` clause is checked on every iteration of the chain loop,
    not just the first."""
    with pytest.raises(AuthExpiredError) as exc_info:
        publish(_payload("medium"), mode="draft", config=CONFIG_MEDIUM)
    assert exc_info.value.channel == "medium"
    mock_api.assert_called_once()
    mock_brave_pub.assert_called_once()
    mock_browser_pub.assert_not_called()


# ─────────── independence: ExternalServiceError still propagates ───────────


@patch("backlink_publisher.publishing.adapters.MediumBrowserAdapter.publish")
@patch("backlink_publisher.publishing.adapters.MediumBraveAdapter.publish")
@patch(
    "backlink_publisher.publishing.adapters.MediumAPIAdapter.publish",
    side_effect=ExternalServiceError("401 from medium"),
)
def test_external_service_error_unchanged_by_authexpired_fix(
    mock_api, mock_brave, mock_browser
):
    """Independence guard — ExternalServiceError is a sibling of
    DependencyError, not a subclass; it has always propagated
    immediately. The AuthExpired fix must not have accidentally
    altered that behavior."""
    with pytest.raises(ExternalServiceError):
        publish(_payload("medium"), mode="draft", config=CONFIG_MEDIUM)
    mock_api.assert_called_once()
    mock_brave.assert_not_called()
    mock_browser.assert_not_called()
