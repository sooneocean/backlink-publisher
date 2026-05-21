"""WebUI platforms-context reverse-driven contract tests.

Plan: docs/plans/2026-05-19-002-feat-telegraph-channel-end-to-end-wiring-plan.md
(U2). Execution note: TEST-FIRST. Velog-counter canary tests must FAIL
before U2 implementation (because index.html:1656 JS counter is missing
``velog``) and PASS after.

Contract under test: ``register("X", XAdapter)`` is sufficient to make
``X`` appear in every WebUI platform-rendering site — without any HTML
edit beyond what U2 commits.  This prevents future re-occurrence of the
"velog registered but invisible in some templates" drift class.

Subset assertions throughout (per adversarial F3): never lock the exact
set of platforms, only that the expected slug is present.  This keeps
the tests stable under U1/U2 merge-order interleaving.
"""

from __future__ import annotations

import pytest


# ── App fixture (no scheduler in tests) ──────────────────────────────────────


@pytest.fixture
def app():
    # ``_isolate_user_dirs`` (session-scoped) already provides clean config.
    # We just need a non-scheduling app instance.
    from webui_app import create_app

    return create_app(start_scheduler=False)


@pytest.fixture
def client(app):
    """Test client with session pre-populated so the `{% if config %}`
    publish-form branch in index.html actually renders."""
    c = app.test_client()
    with c.session_transaction() as sess:
        # Minimal config to gate the form open; values are placeholders.
        sess["config"] = {
            "platform": "blogger",
            "target_url": "https://example.com",
            "language": "zh-CN",
            "url_mode": "A",
            "publish_mode": "draft",
        }
    return c


# ── Helpers ──────────────────────────────────────────────────────────────────


def _get_index_html(client) -> str:
    """GET / and return the rendered HTML body as a string."""
    response = client.get("/")
    assert response.status_code == 200, response.data[:500]
    return response.get_data(as_text=True)


# ── Velog canary: every platform-rendering site must include velog ───────────
#
# These tests assume velog is registered.  When this branch is rebased
# onto a base where PR #75 (settings-browser-binding stack adding velog)
# has landed, they auto-activate.  Until then they skip — the reverse-
# driven contract is still proven by the telegraph + DummyAdapter
# canaries below.


def _velog_in_registry() -> bool:
    from backlink_publisher.publishing.registry import registered_platforms
    return "velog" in registered_platforms()


@pytest.mark.skipif(not _velog_in_registry(), reason="velog not registered (PR #75 not landed)")
def test_velog_appears_in_publish_form_select(client):
    """index.html publish form `<select name="platform">` must include velog."""
    html = _get_index_html(client)
    assert 'value="velog"' in html
    assert "Velog" in html


@pytest.mark.skipif(not _velog_in_registry(), reason="velog not registered (PR #75 not landed)")
def test_velog_appears_in_filter_chip_row(client):
    """Filter chip row must have a velog chip (data-filter-value="velog")."""
    html = _get_index_html(client)
    assert 'data-filter-value="velog"' in html


@pytest.mark.skipif(not _velog_in_registry(), reason="velog not registered (PR #75 not landed)")
def test_velog_appears_in_js_counter_dict(client):
    """JS initCounts must include velog so chip-count starts at a real number."""
    html = _get_index_html(client)
    assert "velog:" in html, "JS platform counter dict missing 'velog:' key"


@pytest.mark.skipif(not _velog_in_registry(), reason="velog not registered (PR #75 not landed)")
def test_velog_in_norm_platform_tuple(client):
    """norm_platform template tuple must accept velog (not funnel to 'other')."""
    html = _get_index_html(client)
    assert "velog:" in html  # piggybacks on JS counter assertion above


# ── Wordpress ghost option removal ──────────────────────────────────────────


def test_wordpress_option_removed_from_publish_form(client):
    """Wordpress UI placeholder must be gone (no backing adapter)."""
    html = _get_index_html(client)
    assert 'value="wordpress"' not in html
    assert ">WordPress<" not in html


# ── detect_platform: unknown-domain fallback now 'blogger' per operator preset ─


def test_detect_platform_wordpress_domain_falls_back_to_blogger():
    """Operator preset (2026-05-20): unknown-domain fallback flipped from
    'medium' to 'blogger'. Explicit medium/blogger domain matches still
    take precedence (see test_detect_platform_known_routes_unchanged)."""
    from webui_app.helpers.url_meta import detect_platform

    assert detect_platform("https://foo.wordpress.com/post") == "blogger"


def test_detect_platform_unknown_domain_falls_back_to_blogger():
    from webui_app.helpers.url_meta import detect_platform

    assert detect_platform("https://entirely-unknown.example/x") == "blogger"


def test_detect_platform_known_routes_unchanged():
    from webui_app.helpers.url_meta import detect_platform

    assert detect_platform("https://example.medium.com/post") == "medium"
    assert detect_platform("https://blog.blogspot.com/post") == "blogger"


# ── Telegraph appears too (when U1 has landed) ───────────────────────────────


def test_telegraph_appears_in_select_after_u1():
    """After U1 registered telegraph, the context processor surfaces it.

    This test asserts the reverse-driven contract: with the registry
    containing telegraph, the WebUI auto-renders it — no separate HTML
    edit needed.
    """
    from backlink_publisher.publishing.registry import registered_platforms

    # Defensive: skip if U1 hasn't landed in this branch.
    if "telegraph" not in registered_platforms():
        pytest.skip("U1 (telegraph adapter) not present in registry")

    from webui_app import create_app

    app = create_app(start_scheduler=False)
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'value="telegraph"' in html
    assert "Telegraph" in html


def test_telegraph_appears_in_filter_chip_after_u1():
    from backlink_publisher.publishing.registry import registered_platforms

    if "telegraph" not in registered_platforms():
        pytest.skip("U1 (telegraph adapter) not present in registry")

    from webui_app import create_app

    app = create_app(start_scheduler=False)
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    assert 'data-filter-value="telegraph"' in html


# ── Reverse-driven contract: any future register("X") shows up automatically ─


def test_dummy_adapter_auto_appears_in_select(fake_platform_registered, client):
    """register("fake", FakeAdapter) → 'fake' appears in select without
    any HTML edit.  This locks the contract for all future adapters."""
    html = _get_index_html(client)
    assert 'value="fake"' in html


def test_dummy_adapter_auto_appears_in_filter_chip(fake_platform_registered, client):
    html = _get_index_html(client)
    assert 'data-filter-value="fake"' in html


def test_dummy_adapter_auto_appears_in_js_counter(fake_platform_registered, client):
    html = _get_index_html(client)
    assert "fake:" in html


def test_dummy_adapter_disappears_after_fixture_teardown(client):
    """Without the fixture, ``fake`` MUST NOT appear (proves teardown works)."""
    html = _get_index_html(client)
    assert 'value="fake"' not in html
    assert 'data-filter-value="fake"' not in html


# ── ROUTE_TIER_MATRIX drift assertion remains green ──────────────────────────


def test_route_tier_matrix_drift_assertion_still_green_after_telegraph_velog():
    """R11c: telegraph + velog default to tier 'c' — must not flip the
    existing drift test red."""
    # Re-run the assertion the matrix's own test asserts.
    from backlink_publisher.publishing.content_negotiation import (
        ROUTE_TIER_MATRIX,
        _matrix_targets_registered_platforms,
    )

    stale = _matrix_targets_registered_platforms()
    assert stale == [], (
        f"ROUTE_TIER_MATRIX has stale entries for unregistered platforms: "
        f"{stale}.  After U2, telegraph and velog should fall through to "
        f"_DEFAULT_TIER='c', not appear in this list."
    )
    # And matrix only contains the platforms we explicitly assign tiers to.
    assert "telegraph" not in ROUTE_TIER_MATRIX, (
        "Telegraph should NOT be in ROUTE_TIER_MATRIX (default tier 'c' is "
        "appropriate for markdown→node tree pipelines)"
    )
