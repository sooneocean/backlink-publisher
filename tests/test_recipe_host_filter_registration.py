"""Every registered recipe MUST declare cookie_host_filter — Plan 2026-05-20-016 Unit 0 Fix 1.

Pairs with ``test_chrome_backend_host_filter.py``. That module verifies
``RealChromeBrowserRunner._provider()`` fails closed when a recipe lacks
``cookie_host_filter``. This module is the registration-time gate: it
walks every entry in ``RECIPES`` and asserts the field is non-None and
callable. A future recipe that forgets the field will fail at
test-collection rather than corrupting the operator's bind credentials.

This is a defense-in-depth pair with the runtime fail-closed check:
runtime catches the misconfigured recipe AT bind time; this catches it
AT test time, ideally during PR review before the recipe ever ships.
"""

from __future__ import annotations

import pytest

from backlink_publisher.cli._bind.recipes import RECIPES


@pytest.mark.parametrize("channel", sorted(RECIPES.keys()))
def test_every_recipe_declares_cookie_host_filter(channel):
    """Each channel's recipe must define a non-None, callable
    cookie_host_filter. Per-channel parametrization gives a clear
    failure name if the regression hits one specific recipe."""
    recipe = RECIPES[channel]
    assert recipe.cookie_host_filter is not None, (
        f"Recipe for {channel!r} is missing cookie_host_filter — chrome "
        f"backend will refuse to persist cookies (security fail-closed). "
        f"Add a host predicate matching this channel's apex + subdomains."
    )
    assert callable(recipe.cookie_host_filter), (
        f"Recipe for {channel!r}: cookie_host_filter must be callable "
        f"(got {type(recipe.cookie_host_filter).__name__})."
    )


def test_recipes_dict_is_non_empty():
    """Sanity: parametrized test above would silently pass on an empty
    RECIPES dict. Explicit non-emptiness assertion keeps the
    registration gate honest."""
    assert len(RECIPES) >= 3, (
        f"Expected at least 3 registered recipes (velog/medium/blogger); "
        f"got {len(RECIPES)}. If recipes were removed intentionally, "
        f"update this assertion."
    )


def test_every_host_filter_rejects_empty_string():
    """Defense against accidental ``lambda h: True`` or similar
    wildcard filters that would re-introduce the bug. Empty-host input
    is a common edge case (cookies sometimes have ``domain=""``) and
    a sane filter rejects it."""
    for channel, recipe in sorted(RECIPES.items()):
        assert recipe.cookie_host_filter("") is False, (
            f"Recipe for {channel!r}: cookie_host_filter('') returned "
            f"True. Empty host should never match — this filter is too "
            f"permissive."
        )
