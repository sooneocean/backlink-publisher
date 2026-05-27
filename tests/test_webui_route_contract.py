"""Contract smoke tests for all webui.py routes — Plan 2026-05-18-001 Unit 1.

Purpose: establish a route-level regression net BEFORE refactoring webui.py
into webui/{routes,services,store,templates}. Each of the 39 routes must:

  - Respond with a non-5xx status code
  - Return the expected status family (200 OK, 302 redirect, 400/404/422)
  - When redirecting, target a stable URL pattern (e.g. ``/`` or ``/settings``)

This file deliberately does NOT assert HTML content. Body content will change
when inline ``HTML = '''…'''`` blocks move into Jinja2 template files
(Plan Unit 4). Decoupling status from body content is what makes this net
survive the refactor.

Patterns followed:
  - tests/test_webui_three_url.py — Flask app.test_client() + CSRF helper
  - tests/conftest.py — autouse content_fetch / check_url mocks
"""

from __future__ import annotations

import json
import os
import re
import sys
from unittest.mock import patch

import pytest

# Ensure webui module is importable from repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Isolated config dir (mirrors test_webui_three_url.py) ────────────────────


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path):
    """Redirect every config.toml / token / state read+write into tmp_path."""
    fake_config_dir = tmp_path / "config"
    with patch(
        "backlink_publisher.config._config_dir", return_value=fake_config_dir,
    ), patch(
        "backlink_publisher.config._cache_dir", return_value=tmp_path / "cache",
    ):
        yield fake_config_dir


@pytest.fixture(autouse=True)
def _isolated_webui_state(tmp_path, monkeypatch):
    """Redirect the four JsonStore paths to tmp_path.

    Plan 2026-05-18-001 Unit 2 collapsed the original 4 module-level
    file constants into ``webui_store.*_store`` singletons. Patching
    ``.path`` reassigns the underlying ``_path`` via the JsonStore
    property setter — load/save/update reads from the new location
    starting on the next call.
    """
    import webui_store as ws

    state_dir = tmp_path / "webui_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ws.history_store, "path", state_dir / "publish-history.json")
    monkeypatch.setattr(ws.profiles_store, "path", state_dir / "campaign-profiles.json")
    monkeypatch.setattr(ws.drafts_store, "path", state_dir / "draft-queue.json")
    monkeypatch.setattr(ws.schedule_store, "path", state_dir / "schedule-settings.json")


@pytest.fixture(autouse=True)
def _no_real_subprocess():
    """Stub subprocess.run so /ce:batch, /ce:publish-real, /checkpoint/resume
    never shell out to the real CLI binaries."""
    import subprocess as sp_mod

    def _fake_run(cmd, *_args, **_kwargs):
        result = sp_mod.CompletedProcess(args=cmd, returncode=0)
        result.stdout = ""
        result.stderr = ""
        return result

    with patch("subprocess.run", side_effect=_fake_run):
        yield


@pytest.fixture(autouse=True)
def _no_run_pipe():
    """Stub run_pipe in every consumer module so route handlers don't shell out.

    ``run_pipe`` is defined in ``webui_app.helpers.cli_runner`` and imported
    into each module that calls it — so the name binds into that module's
    namespace and must be patched there, not only at the definition site.
    After the route→api extraction the live consumers are ``api.pipeline_api``
    (PipelineAPI holds the binding; sites, scheduler, checkpoint, and seo_viz
    all delegate through it after the Phase 2 Unit 4 funnel). ``routes.sites`` /
    ``scheduler`` no longer import run_pipe directly, so patching them there
    would raise AttributeError at fixture setup. checkpoint resume +
    report_anchors go through the non-raising capture variant.

    The ``_no_real_subprocess`` fixture also stubs ``subprocess.run``
    underneath, so even if a patch is missed the call still won't hit the
    network — but the explicit patches give faster, clearer assertions.
    """
    def _fake(_cmd, _stdin):
        return {"stdout": "", "stderr": ""}

    def _fake_capture(_cmd, _stdin):
        return {"stdout": "", "stderr": "", "returncode": 0}

    targets = [
        ("webui_app.helpers.cli_runner.run_pipe", _fake),
        ("webui_app.api.pipeline_api.run_pipe", _fake),
        ("webui_app.api.pipeline_api.run_pipe_capture", _fake_capture),
    ]
    patches = [patch(t, side_effect=f) for t, f in targets]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in patches:
            p.stop()


@pytest.fixture
def client():
    """Flask test client with TESTING + insecure cookies for session round-trip."""
    import webui

    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


@pytest.fixture
def csrf_client():
    """Like ``client`` but with the global CSRF guard enabled.

    Use for tests that explicitly assert "missing/wrong CSRF returns 403".
    After PR-after-#143 removed inline ``_check_csrf_or_abort`` calls from
    blueprints that had them (now redundant with the global hook), the
    only way to exercise CSRF rejection is to enable the global guard.
    """
    import webui

    webui.app.config["TESTING"] = True
    webui.app.config["SESSION_COOKIE_SECURE"] = False
    webui.app.config["WTF_CSRF_ENABLED"] = True
    webui.app.config["CSRF_ENABLED"] = True
    try:
        yield webui.app.test_client()
    finally:
        webui.app.config["WTF_CSRF_ENABLED"] = False
        webui.app.config["CSRF_ENABLED"] = False


def _fetch_csrf(client) -> str:
    """Grab the hidden csrf_token from GET /sites."""
    resp = client.get("/sites")
    assert resp.status_code == 200, resp.data[:200]
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.data.decode())
    assert match, "csrf_token not found in /sites HTML"
    return match.group(1)


# ═════════════════════════════════════════════════════════════════════════════
# GET routes — read-only
# ═════════════════════════════════════════════════════════════════════════════


class TestGetRoutes:
    def test_root_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_homepage_has_mode_toggle(self, client):
        """Plan 012 Unit 5 — single/batch toggle DOM present on home page."""
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert 'id="modeToggleBar"' in body
        assert 'id="mode-single-btn"' in body
        assert 'id="mode-batch-btn"' in body
        assert 'data-bs-target="#newPanel"' in body
        assert 'data-bs-target="#batchPanel"' in body

    def test_homepage_loads_mode_toggle_script(self, client):
        """Plan 012 Unit 5 — mode_toggle.js is wired up in the template."""
        resp = client.get("/")
        body = resp.data.decode("utf-8", errors="ignore")
        assert "js/mode_toggle.js" in body
        # Server-side hint must be injected so the JS can read batch_tab flag
        # without an extra round-trip.
        assert "window.__batchTabHint" in body

    def test_nav_tabs_reduced_to_two(self, client):
        """Plan 012 Unit 6 — batch-tab nav button removed; nav now has 2 tabs."""
        resp = client.get("/")
        body = resp.data.decode("utf-8", errors="ignore")
        assert 'id="batch-tab"' not in body
        assert 'id="new-tab"' in body
        assert 'id="history-tab"' in body

    def test_batch_panel_still_renders_for_toggle_access(self, client):
        """Plan 012 Unit 6 — #batchPanel tab-pane DOM stays (toggle activates it)."""
        resp = client.get("/")
        body = resp.data.decode("utf-8", errors="ignore")
        assert 'id="batchPanel"' in body
        assert 'action="/ce:batch"' in body

    def test_mode_toggle_js_file_exists(self):
        """Plan 012 Unit 5 — the new static JS asset is present on disk."""
        from pathlib import Path
        js = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "static" / "js" / "mode_toggle.js"
        )
        assert js.exists(), f"mode_toggle.js missing at {js}"
        contents = js.read_text(encoding="utf-8")
        assert "webui_mode_default" in contents
        assert "__batchTabHint" in contents

    def test_mode_toggle_js_u1_behaviors(self):
        """Plan 013 U1 — mode_toggle.js contains all 4 new polish behaviors."""
        from pathlib import Path
        js_path = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "static" / "js" / "mode_toggle.js"
        )
        contents = js_path.read_text(encoding="utf-8")

        # Behavior 1: URL stash key present
        assert "webui_url_stash" in contents, "URL stash key missing"

        # Behavior 2: Mid-pipeline confirm uses _plansData
        assert "_plansData" in contents, "mid-pipeline confirm missing"
        assert "confirm(" in contents, "confirm dialog missing"

        # Behavior 3: ?tab=batch deep-link via URLSearchParams
        assert "URLSearchParams" in contents, "URLSearchParams deep-link missing"
        assert "tab" in contents, "tab param check missing"

        # Behavior 4: body class toggle for CSS scoping
        assert "mode-single" in contents, "mode-single body class missing"
        assert "mode-batch" in contents, "mode-batch body class missing"
        assert "applyBodyModeClass" in contents, "applyBodyModeClass helper missing"

    def test_mode_toggle_tab_deep_link_route_accessible(self, client):
        """Plan 013 U1 — GET /?tab=batch returns 200 (server-side hint injected)."""
        resp = client.get("/?tab=batch")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        # The page still renders; JS handles the deep-link client-side
        assert "batchPanel" in body

    def test_sticky_step_bar_css_scoped_to_single_mode(self, client):
        """Plan 013 U3 — step-bar sticky rule scoped to body.mode-single only."""
        resp = client.get("/")
        body = resp.data.decode("utf-8", errors="ignore")
        # Scoped sticky rules must appear in the inlined CSS
        assert "mode-single" in body, "mode-single CSS scope missing from rendered page"
        assert "step-bar" in body, "step-bar CSS missing from rendered page"
        assert "mode-batch" in body, "mode-batch CSS scope missing"

    def test_sticky_step_bar_css_in_template_source(self):
        """Plan 013 U3 — scoped step-bar rules present in extracted CSS file (Plan B Unit 1)."""
        from pathlib import Path
        # CSS extracted to static file by Plan B Unit 1; check index.css not index.html
        src = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "static" / "css" / "index.css"
        ).read_text(encoding="utf-8")
        # Both mode-scoped rules must be present
        assert "body.mode-single .step-bar" in src, (
            "mode-single step-bar sticky rule missing from index.css"
        )
        assert "body.mode-batch .step-bar" in src, (
            "mode-batch step-bar static rule missing from index.css"
        )
        assert "hide-history-nav" in src, (
            "hide-history-nav CSS rule missing from index.css"
        )

    def test_root_does_not_crash_with_missing_state_files(self, client, tmp_path):
        """Edge case: first-time startup, none of the JSON state files exist
        yet. The autouse fixture points stores at a fresh tmp_path so they're
        guaranteed absent. Index must still render."""
        import webui_store as ws

        assert not ws.history_store.path.exists()
        assert not ws.drafts_store.path.exists()

        resp = client.get("/")
        assert resp.status_code == 200

    def test_settings_returns_200(self, client):
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_settings_with_flash_query_renders(self, client):
        resp = client.get("/settings?flash_type=success&flash_msg=test")
        assert resp.status_code == 200

    def test_settings_html_contract(self, client):
        """Plan 2026-05-18-011 Unit 1 — regression net for the settings
        page channel-collapse refactor.

        Asserts that the ``settings.html`` template source — or any partial
        it includes (post-Unit-4) — still contains the load-bearing form
        action URLs, DOM ids, and inline JS handler call names that
        deep-links, inline JS, and browser users depend on.

        Why template source, not rendered HTML: several URLs live inside
        ``{% if blogger_token %}`` / ``{% if medium_token_set %}`` branches
        that don't render in test conditions (clean tmp_path config). The
        regression risk is "source accidentally drops the URL", not "config
        flips wrong branch" — so source-level grep is the right granularity.
        Survives the refactor: ``webui_app/templates/**/*.html`` includes
        future partials (``_settings_channel_blogger.html`` etc.).

        Also exercises the ``/settings`` GET to confirm rendering still
        succeeds, in case a partial include path is misspelled.

        Structural placement (Blogger form lives inside #channel-blogger,
        not #channel-medium) is covered by the two ``xfail`` BeautifulSoup
        tests below; those flip to green once the partial migration lands.
        """
        from pathlib import Path

        # 1. /settings GET still renders successfully.
        resp = client.get("/settings")
        assert resp.status_code == 200

        # 2. Source-level assertions across all settings templates.
        templates_dir = Path(__file__).parent.parent / "webui_app" / "templates"
        candidates = list(templates_dir.glob("settings*.html")) + list(
            templates_dir.glob("_settings_*.html")
        )
        assert candidates, f"no settings templates under {templates_dir}"
        combined = b"".join(p.read_bytes() for p in candidates)

        # 12 form action URLs (10 channel-related + 2 global).
        # /settings/medium/oauth-start removed: Medium closed new app registration
        # 2023-03-02. Three browser-login routes added in Plan 013 Phase B.
        form_action_urls = [
            b'/settings/blogger/oauth-start',
            b'/settings/save-blogger-oauth',
            b'/settings/revoke-blogger',
            b'/settings/save-blog-ids',
            b'/settings/save-medium-token',
            b'/settings/clear-medium-token',
            b'/settings/clear-medium-oauth',
            b'/settings/medium/launch-browser-login',
            b'/settings/medium/probe-browser-login',
            b'/settings/medium/clear-browser-login',
            b'/settings/save-target-keywords',
            b'/settings/schedule',
        ]
        for url in form_action_urls:
            assert url in combined, f"missing form action URL: {url!r}"

        # 9 DOM ids that inline JS / deep-links / external bookmarks rely on.
        dom_ids = [
            b'id="oauthCredForm"',
            b'id="clientSecretInput"',
            b'id="mediumTokenInput"',
            b'id="blogger-blog-ids"',
            b'id="blogIdRows"',
            b'id="callbackUriDisplay"',
            b'id="copyBtn"',
            b'id="secretEye"',
            b'id="eyeIcon"',
        ]
        for dom_id in dom_ids:
            assert dom_id in combined, f"missing DOM id: {dom_id!r}"

        # 5 inline JS handler call sites.
        js_handlers = [
            b'copyUri(',
            b'toggleSecret(',
            b'toggleToken(',
            b'addRow(',
            b'removeRow(',
        ]
        for handler in js_handlers:
            assert handler in combined, (
                f"missing JS handler call: {handler!r}"
            )

    def test_blogger_forms_scoped_to_channel_panel(self, client):
        """Plan 2026-05-18-011 Unit 1 — structural regression net for the
        Blogger channel partial.

        Asserts every Blogger-related ``<form action>`` / ``<button formaction>``
        lives inside the ``#channel-blogger`` Collapse panel. Catches the
        copy-paste mistake of moving the Blogger form into Medium's partial
        during Unit 3.

        Marked ``xfail`` until Unit 2 lands the Blogger partial + Collapse
        shell. Unit 2 commit must remove this marker.
        """
        from bs4 import BeautifulSoup

        resp = client.get("/settings")
        soup = BeautifulSoup(resp.data, "html.parser")
        panel = soup.find(id="channel-blogger")
        assert panel is not None, "missing #channel-blogger collapse panel"

        blogger_urls = {
            "/settings/blogger/oauth-start",
            "/settings/save-blogger-oauth",
            "/settings/revoke-blogger",
            "/settings/save-blog-ids",
        }
        for url in blogger_urls:
            nodes = soup.select(
                f'form[action="{url}"], button[formaction="{url}"]'
            )
            assert nodes, f"no <form action> or <button formaction> for {url}"
            for node in nodes:
                assert panel in node.parents, (
                    f"{url} is not inside #channel-blogger panel"
                )

    def test_medium_forms_scoped_to_channel_panel(self, client):
        """Plan 2026-05-18-011 Unit 1 — structural regression net for the
        Medium channel partial. See ``test_blogger_forms_scoped_to_channel_panel``
        for design notes. Unit 3 commit must remove this marker.
        """
        from bs4 import BeautifulSoup

        resp = client.get("/settings")
        soup = BeautifulSoup(resp.data, "html.parser")
        panel = soup.find(id="channel-medium")
        assert panel is not None, "missing #channel-medium collapse panel"

        # /settings/medium/oauth-start removed in Plan 013 Phase A.
        # /settings/clear-medium-oauth: conditionally rendered (medium_token_file_exists).
        # /settings/medium/clear-browser-login: conditionally rendered (profile_has_cookies).
        # Both omitted here; test env has neither token file nor cookies.
        # launch + probe are rendered when state != 'not_installed' (Playwright installed).
        medium_urls = {
            "/settings/save-medium-token",
            "/settings/clear-medium-token",
            "/settings/medium/launch-browser-login",
            "/settings/medium/probe-browser-login",
        }
        for url in medium_urls:
            nodes = soup.select(
                f'form[action="{url}"], button[formaction="{url}"]'
            )
            assert nodes, f"no <form action> or <button formaction> for {url}"
            for node in nodes:
                assert panel in node.parents, (
                    f"{url} is not inside #channel-medium panel"
                )

    def test_sites_returns_200(self, client):
        resp = client.get("/sites")
        assert resp.status_code == 200

    def test_sites_with_saved_query_renders(self, client):
        resp = client.get("/sites?saved=https://x.com&autofilled=list_url")
        assert resp.status_code == 200

    def test_ce_history_get_returns_200(self, client):
        resp = client.get("/ce:history")
        assert resp.status_code == 200

    def test_sites_scrape_preview_missing_url_returns_400(self, client):
        resp = client.get("/sites/scrape-preview")
        assert resp.status_code == 400

    def test_sites_scrape_preview_with_url_returns_200_json(
        self, client, monkeypatch,
    ):
        # Avoid real HTTP scraping
        monkeypatch.setattr("webui.fetch_work_metadata", lambda url: None)
        resp = client.get("/sites/scrape-preview?url=https://x.com/work/1")
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/json")

    def test_sites_run_result_unknown_id_returns_404(self, client):
        resp = client.get("/sites/run/00000000T000000-aaaaaaaa/result")
        assert resp.status_code == 404

    def test_sites_run_result_known_id_returns_200(self, client):
        import webui

        run_id = "20260518T000000-deadbeef"
        webui._WORK_THEMED_RUNS[run_id] = {
            "main_url": "https://x.com/",
            "summary": {"total": 0, "generated": 0, "skipped": 0, "fail_empty": True},
            "rows": [],
        }
        try:
            resp = client.get(f"/sites/run/{run_id}/result")
        finally:
            webui._WORK_THEMED_RUNS.pop(run_id, None)
        assert resp.status_code == 200

    def test_blogger_oauth_callback_missing_state_redirects(self, client):
        resp = client.get("/settings/blogger/oauth-callback")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")


# ═════════════════════════════════════════════════════════════════════════════
# Pipeline POST routes — /ce:*
# ═════════════════════════════════════════════════════════════════════════════


class TestPipelineRoutes:
    def test_ce_clear_returns_200(self, client):
        resp = client.post("/ce:clear")
        assert resp.status_code == 200

    def test_ce_plan_with_main_url_returns_200(self, client):
        resp = client.post("/ce:plan", data={"main_url": "https://example.com/"})
        assert resp.status_code == 200

    def test_ce_plan_missing_main_url_returns_200_with_error(self, client):
        """Empty submit re-renders index with error (not 400/422)."""
        resp = client.post("/ce:plan", data={})
        assert resp.status_code == 200

    def test_ce_plan_non_https_main_url_returns_200_with_error(self, client):
        """http:// triggers field error; renders index, not 4xx."""
        resp = client.post("/ce:plan", data={"main_url": "http://insecure.example/"})
        assert resp.status_code == 200

    def test_ce_plan_ignores_derive_source_extra_field(self, client):
        """Plan 2026-05-20-002 R7 invariant: the nameless ``derive_source``
        input is the new paste-to-derive entry on the homepage; browsers
        never submit nameless inputs. If a malicious client smuggles it
        anyway, the extras-loop (``key.startswith('url_')``) must NOT
        capture it (``derive_source`` doesn't match ``url_*``). The
        backend treats it as an unknown form key and ignores it; the
        ``main_url`` happy path is unaffected.
        """
        resp = client.post(
            "/ce:plan",
            data={
                "main_url": "https://example.com/",
                "derive_source": "http://attacker.example/internal",
            },
        )
        assert resp.status_code == 200
        # The attacker URL must not appear in the rendered page (no leak
        # into derived form state / config preview / extras list).
        body = resp.get_data(as_text=True)
        assert "attacker.example" not in body

    def test_ce_generate_with_empty_session_returns_200(self, client):
        """No urls in session/form → error re-render, still 200."""
        resp = client.post("/ce:generate", data={})
        assert resp.status_code == 200

    def test_ce_generate_with_urls_returns_200(self, client):
        urls_json = json.dumps(["https://example.com/"])
        resp = client.post("/ce:generate", data={"urls_json": urls_json})
        assert resp.status_code == 200

    def test_ce_generate_corrupt_urls_json_surfaces_error(self, client):
        """Plan 009 Unit 4: non-empty malformed urls_json must surface an error
        and NOT silently generate against stale stored urls."""
        with client.session_transaction() as sess:
            sess["config"] = {"urls": ["https://stale-last-session.example/"]}
        with patch("webui_app.routes.pipeline.plan_logger.warn") as mock_warn:
            resp = client.post("/ce:generate",
                               data={"urls_json": "[not valid json"})
        assert resp.status_code == 200
        body = resp.data.decode()
        assert "连结格式无效" in body
        assert "stale-last-session" not in body  # did not use stale urls
        mock_warn.assert_called_once()
        assert mock_warn.call_args[0][0] == "urls_json_parse_error"

    def test_ce_generate_default_urls_json_does_not_error(self, client):
        """Default '[]' is not 'corrupt' — must not trigger the parse error."""
        resp = client.post("/ce:generate", data={"urls_json": "[]"})
        assert resp.status_code == 200
        assert "连结格式无效" not in resp.data.decode()

    def test_ce_validate_with_no_plans_returns_200(self, client):
        resp = client.post("/ce:validate", data={})
        assert resp.status_code == 200

    def test_ce_validate_with_plans_returns_200(self, client):
        resp = client.post("/ce:validate", data={"plans": '{"id": "x"}'})
        assert resp.status_code == 200

    def test_ce_publish_with_no_data_returns_200(self, client):
        resp = client.post("/ce:publish", data={})
        assert resp.status_code == 200

    def test_ce_batch_with_no_urls_returns_200_with_error(self, client):
        resp = client.post("/ce:batch", data={"batch_urls": ""})
        assert resp.status_code == 200

    def test_ce_batch_with_urls_returns_200(self, client):
        resp = client.post(
            "/ce:batch",
            data={
                "batch_urls": "https://example.com/",
                "platform": "medium",
                "language": "zh-CN",
                "publish_mode": "draft",
            },
        )
        assert resp.status_code == 200

    def test_ce_batch_accepts_target_language(self, client):
        """Plan 013 U2 — batch route must accept `target_language` field."""
        resp = client.post(
            "/ce:batch",
            data={
                "batch_urls": "https://example.com/",
                "platform": "medium",
                "target_language": "zh-CN",
                "publish_mode": "draft",
            },
        )
        assert resp.status_code == 200

    def test_ce_batch_language_fallback_still_works(self, client):
        """Plan 013 U2 — legacy `language` field still accepted (backwards compat)."""
        resp = client.post(
            "/ce:batch",
            data={
                "batch_urls": "https://example.com/",
                "platform": "medium",
                "language": "en",
                "publish_mode": "draft",
            },
        )
        assert resp.status_code == 200

    def test_shared_config_selects_included_in_both_forms(self, client):
        """Plan 013 U2 — shared select partial used in both configForm and batchForm.

        The configForm is guarded by {% if config %} so it only renders when
        pipeline state is present.  We verify:
        - The batch form always renders target_language (always visible on GET /).
        - The index.html template source contains the include in both locations.
        - _shared_config_selects.html is present on disk.
        """
        from pathlib import Path

        # 1. Batch form always renders target_language
        resp = client.get("/")
        body = resp.data.decode("utf-8", errors="ignore")
        assert 'name="target_language"' in body, "batch form missing target_language"

        # 2. Template source uses the shared include in both form contexts.
        # Plan B Unit 2 moved the tab panes to _tab_*.html partials, so
        # _shared_config_selects.html now appears in _tab_new.html and
        # _tab_batch.html rather than index.html directly.
        templates_dir = Path(__file__).resolve().parents[1] / "webui_app" / "templates"
        all_template_src = "".join(
            p.read_text(encoding="utf-8")
            for p in templates_dir.glob("*.html")
        )
        count = all_template_src.count("_shared_config_selects.html")
        assert count == 2, (
            "expected 2 includes of _shared_config_selects.html across templates, got "
            + str(count)
        )

        # 3. The partial itself is on disk
        partial_path = (
            Path(__file__).resolve().parents[1]
            / "webui_app" / "templates" / "_shared_config_selects.html"
        )
        assert partial_path.exists(), "_shared_config_selects.html missing"
        partial_src = partial_path.read_text(encoding="utf-8")
        assert 'name="target_language"' in partial_src
        assert 'name="publish_mode"' in partial_src

    def test_ce_publish_real_with_no_data_returns_200(self, client):
        resp = client.post(
            "/ce:publish-real",
            data={"validated": "", "platform": "medium"},
        )
        assert resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# History POST routes — /ce:history*
# ═════════════════════════════════════════════════════════════════════════════


class TestHistoryRoutes:
    def test_ce_history_post_returns_200(self, client):
        resp = client.post("/ce:history")
        assert resp.status_code == 200

    def test_ce_history_delete_with_unknown_id_returns_200(self, client):
        resp = client.post("/ce:history/delete", data={"id": "nonexistent"})
        assert resp.status_code == 200

    def test_ce_history_update_status_with_unknown_id_returns_200(self, client):
        resp = client.post(
            "/ce:history/update-status",
            data={"id": "nonexistent", "status": "published"},
        )
        assert resp.status_code == 200

    def test_ce_history_reuse_returns_200(self, client):
        resp = client.post(
            "/ce:history/reuse", data={"target_url": "https://x.com/"},
        )
        assert resp.status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# Draft queue POST routes — /ce:draft/*
# ═════════════════════════════════════════════════════════════════════════════


class TestDraftRoutes:
    def test_draft_save_with_empty_plans_redirects(self, client):
        resp = client.post("/ce:draft/save", data={"plans": ""})
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/?tab=draft")

    def test_draft_save_with_plans_redirects(self, client):
        resp = client.post(
            "/ce:draft/save",
            data={
                "plans": '{"id": "x"}',
                "platform": "medium",
                "publish_mode": "draft",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/?tab=draft")

    def test_draft_schedule_missing_params_redirects(self, client):
        resp = client.post("/ce:draft/schedule", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_schedule_invalid_datetime_redirects(self, client):
        resp = client.post(
            "/ce:draft/schedule",
            data={"id": "abc", "scheduled_at": "not-a-datetime"},
        )
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_publish_now_missing_id_redirects(self, client):
        resp = client.post("/ce:draft/publish-now", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_publish_now_with_id_redirects(self, client):
        resp = client.post("/ce:draft/publish-now", data={"id": "anything"})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_cancel_missing_id_redirects(self, client):
        resp = client.post("/ce:draft/cancel", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_cancel_with_id_redirects(self, client):
        resp = client.post("/ce:draft/cancel", data={"id": "nonexistent"})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_delete_missing_id_redirects(self, client):
        resp = client.post("/ce:draft/delete", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_delete_with_id_redirects(self, client):
        resp = client.post("/ce:draft/delete", data={"id": "nonexistent"})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    # Plan 2026-05-19-006 Unit 3 — bulk operations
    def test_draft_bulk_delete_empty_redirects(self, client):
        resp = client.post("/ce:draft/bulk-delete", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_bulk_publish_now_empty_redirects(self, client):
        resp = client.post("/ce:draft/bulk-publish-now", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]

    def test_draft_bulk_cancel_empty_redirects(self, client):
        resp = client.post("/ce:draft/bulk-cancel", data={})
        assert resp.status_code == 302
        assert "/?tab=draft" in resp.headers["Location"]


class TestHistoryBulkRoutes:
    """Plan 2026-05-19-006 Unit 4+5 — bulk + recheck history routes."""

    def test_history_bulk_delete_empty_redirects(self, client):
        resp = client.post("/ce:history/bulk-delete", data={})
        assert resp.status_code == 302

    def test_history_purge_failed_redirects(self, client):
        resp = client.post("/ce:history/purge-failed", data={})
        assert resp.status_code == 302

    def test_history_recheck_missing_id_redirects(self, client):
        resp = client.post("/ce:history/recheck", data={})
        assert resp.status_code == 302

    def test_history_bulk_recheck_empty_redirects(self, client):
        resp = client.post("/ce:history/bulk-recheck", data={})
        assert resp.status_code == 302


# ═════════════════════════════════════════════════════════════════════════════
# Settings POST routes — /settings/* and /profiles/*
# ═════════════════════════════════════════════════════════════════════════════


class TestSettingsRoutes:
    def test_save_target_keywords_empty_redirects(self, client):
        resp = client.post(
            "/settings/save-target-keywords", data={"domain_count": "0"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_target_keywords_with_oversize_keyword_redirects(self, client):
        """Keyword >60 chars → redirect with danger flash, not 422."""
        resp = client.post(
            "/settings/save-target-keywords",
            data={
                "domain_count": "1",
                "domain_1": "https://x.com/",
                "keywords_1": "X" * 100,
            },
        )
        assert resp.status_code == 302
        assert "/settings?" in resp.headers["Location"]

    def test_settings_schedule_save_valid_redirects(self, client):
        resp = client.post(
            "/settings/schedule",
            data={"min_interval_hours": "4", "jitter_minutes": "30"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_settings_schedule_save_invalid_redirects(self, client):
        resp = client.post(
            "/settings/schedule",
            data={"min_interval_hours": "not-a-number"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_blog_ids_redirects(self, client):
        resp = client.post(
            "/settings/save-blog-ids",
            data={"domain[]": "https://x.com/", "blog_id[]": "12345"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_blog_ids_empty_redirects(self, client):
        resp = client.post("/settings/save-blog-ids", data={})
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_medium_token_with_value_redirects(self, client):
        resp = client.post(
            "/settings/save-medium-token",
            data={"medium_token": "Bearer test-token-1234"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_medium_token_empty_redirects(self, client):
        resp = client.post(
            "/settings/save-medium-token", data={"medium_token": ""},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_clear_medium_token_redirects(self, client):
        resp = client.post("/settings/clear-medium-token")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_clear_medium_oauth_redirects(self, client):
        resp = client.post("/settings/clear-medium-oauth")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    # ── Plan 013 Phase B: browser-login routes ────────────────────────────────
    # CSRF: the bespoke before_request 302-danger layer was retired (it was dead
    # code behind the app-level _global_csrf_guard). A POST without a valid
    # canonical csrf_token is now rejected with 403 by the global guard.

    def test_medium_launch_browser_login_no_csrf_forbidden(self, client):
        resp = client.post("/settings/medium/launch-browser-login", data={})
        assert resp.status_code == 403

    def test_medium_probe_browser_login_no_csrf_forbidden(self, client):
        resp = client.post("/settings/medium/probe-browser-login", data={})
        assert resp.status_code == 403

    def test_medium_clear_browser_login_no_csrf_forbidden(self, client):
        resp = client.post("/settings/medium/clear-browser-login", data={})
        assert resp.status_code == 403

    def test_revoke_blogger_redirects(self, client):
        resp = client.post("/settings/revoke-blogger")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_blogger_oauth_missing_creds_redirects(self, client):
        resp = client.post(
            "/settings/save-blogger-oauth",
            data={"client_id": "", "client_secret": ""},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_blogger_oauth_with_creds_redirects(self, client):
        resp = client.post(
            "/settings/save-blogger-oauth",
            data={"client_id": "fake-id", "client_secret": "fake-secret"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_blogger_oauth_start_missing_creds_redirects(self, client):
        resp = client.post(
            "/settings/blogger/oauth-start",
            data={"client_id": "", "client_secret": ""},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_profiles_save_empty_name_returns_json_error(self, client):
        resp = client.post("/profiles/save", data={"profile_name": ""})
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["ok"] is False

    def test_profiles_save_with_name_returns_json_ok(self, client):
        resp = client.post(
            "/profiles/save",
            data={
                "profile_name": "test",
                "platform": "blogger",
                "language": "zh-CN",
            },
        )
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["ok"] is True

    def test_profiles_delete_redirects(self, client):
        resp = client.post(
            "/profiles/delete", data={"profile_name": "nonexistent"},
        )
        # No referer → redirect to /
        assert resp.status_code == 302


# ═════════════════════════════════════════════════════════════════════════════
# Checkpoint POST routes — /checkpoint/*
# ═════════════════════════════════════════════════════════════════════════════


class TestCheckpointRoutes:
    def test_resume_invalid_run_id_returns_400(self, client):
        resp = client.post("/checkpoint/resume", data={"run_id": "not-a-run-id"})
        assert resp.status_code == 400

    def test_resume_missing_run_id_returns_400(self, client):
        resp = client.post("/checkpoint/resume", data={})
        assert resp.status_code == 400

    def test_resume_valid_run_id_returns_200(self, client):
        # subprocess.run autouse-mocked → returns empty stdout success
        run_id = "20260518T000000-deadbeef"
        resp = client.post("/checkpoint/resume", data={"run_id": run_id})
        assert resp.status_code == 200

    def test_dismiss_invalid_run_id_returns_400(self, client):
        resp = client.post("/checkpoint/dismiss", data={"run_id": "bogus"})
        assert resp.status_code == 400

    def test_dismiss_valid_run_id_redirects_to_root(self, client):
        run_id = "20260518T000000-deadbeef"
        resp = client.post("/checkpoint/dismiss", data={"run_id": run_id})
        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"


# ═════════════════════════════════════════════════════════════════════════════
# Sites POST routes — /sites/save-three-url, /sites/run
# (GET /sites and GET /sites/scrape-preview / run/<id>/result covered above.)
# ═════════════════════════════════════════════════════════════════════════════


class TestSitesPostRoutes:
    def test_save_three_url_missing_csrf_returns_403(self, csrf_client):
        resp = csrf_client.post(
            "/sites/save-three-url",
            data={"main_url": "https://x.com/"},
        )
        assert resp.status_code == 403

    def test_save_three_url_invalid_main_url_returns_422(self, client):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={"csrf_token": token, "main_url": "http://insecure.com/"},
        )
        assert resp.status_code == 422

    def test_save_three_url_valid_redirects(self, client, monkeypatch):
        # Avoid TDK fetch + work_scraper hitting the network
        monkeypatch.setattr(
            "webui.fetch_full_tdk",
            lambda url: {"title": "T", "description": "D"},
        )
        monkeypatch.setattr(
            "backlink_publisher.content.scraper.fetch_work_urls_from_list",
            lambda *a, **k: [],
        )
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={"csrf_token": token, "main_url": "https://x.com/"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/sites?")

    def test_sites_run_missing_csrf_returns_403(self, csrf_client):
        resp = csrf_client.post("/sites/run", data={"main_url": "https://x.com/"})
        assert resp.status_code == 403

    def test_sites_run_unknown_domain_returns_400(self, client):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/run",
            data={"csrf_token": token, "main_url": "https://never-saved.example/"},
        )
        assert resp.status_code == 400


# ═════════════════════════════════════════════════════════════════════════════
# Queue + Dashboard routes — /ce:queue-task, /ce:dashboard, /ce:retry-task
# ═════════════════════════════════════════════════════════════════════════════


class TestQueueDashboardRoutes:
    def test_ce_dashboard_redirects_to_health(self, client):
        """Plan 2026-05-25-006 U3 — /ce:dashboard 302 → /ce:health.

        Repurposed from the Plan 012 target (/ce:history?section=in-progress):
        "dashboard" now means the publishing health dashboard. The in-progress
        task list is still reachable directly at /ce:history?section=in-progress.
        """
        resp = client.get("/ce:dashboard", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/ce:health")

    def test_ce_health_renders_read_only_dashboard(self, client):
        """Plan 2026-05-25-006 U3 — /ce:health GET renders the health dashboard."""
        resp = client.get("/ce:health")
        assert resp.status_code == 200
        assert "Publishing Health" in resp.get_data(as_text=True)

    def test_dashboard_html_template_removed(self):
        """Plan 012 Unit 2 — dashboard.html template file deleted."""
        from pathlib import Path
        tpl = Path(__file__).resolve().parents[1] / "webui_app" / "templates" / "dashboard.html"
        assert not tpl.exists(), f"dashboard.html should be deleted but exists at {tpl}"

    def test_publish_panel_dom_removed(self, client):
        """Plan 012 Unit 1 — #publishPanel tab + pane removed from index.html."""
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8", errors="ignore")
        assert 'id="publishPanel"' not in body
        assert 'id="publish-tab"' not in body
        assert "ready_to_publish" not in body

    def test_ce_queue_task_returns_json(self, client):
        resp = client.post(
            "/ce:queue-task",
            data={"platform": "medium", "urls_json": '["https://example.com/"]'},
        )
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["status"] == "queued"
        assert "task_id" in data

    def test_ce_retry_task_missing_id_returns_error(self, client):
        resp = client.post("/ce:retry-task", data={})
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["status"] == "error"

    def test_ce_retry_task_unknown_id_returns_success(self, client):
        resp = client.post("/ce:retry-task", data={"task_id": "nonexistent-id"})
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert data["status"] == "success"


class TestBindRoutes:
    """Plan 2026-05-19-001 Unit 4 + Plan 003 Unit 4 — POST + GET smoke for
    the bind blueprint and identity-mismatch resolution routes.

    Deeper lifecycle assertions live in test_webui_bind_routes.py. The
    smoke tests here exist to satisfy the route-coverage gate below.
    """

    def test_post_bind_missing_csrf_returns_403(self, client):
        resp = client.post("/settings/channels/medium/bind", data={})
        assert resp.status_code == 403

    def test_poll_bind_unknown_job_returns_404(self, client):
        resp = client.get("/settings/channels/medium/bind/deadbeef")
        assert resp.status_code == 404

    def test_post_identity_mismatch_keep_missing_csrf_returns_403(self, client):
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/keep", data={}
        )
        assert resp.status_code == 403

    def test_post_identity_mismatch_replace_missing_csrf_returns_403(self, client):
        resp = client.post(
            "/settings/channels/medium/identity-mismatch/replace", data={}
        )
        assert resp.status_code == 403


class TestChannelBindingAPIRoutes:
    """Plan 2026-05-19-006 Unit 4 — generic /api/<channel>/* dashboard endpoints.

    Full behavior tests live in tests/test_generic_channel_api.py. These
    smoke tests satisfy the route-coverage gate below.
    """

    def test_get_channel_status_returns_200(self, client):
        resp = client.get("/api/blogger/status")
        assert resp.status_code == 200

    def test_post_channel_verify_missing_csrf_returns_403(self, csrf_client):
        resp = csrf_client.post("/api/blogger/verify")
        assert resp.status_code == 403


class TestTokenPasteRoutes:
    """Plan 006 follow-up (2026-05-20) — token-paste binding for ghpages.
    (Legacy retired channel.) Full lifecycle in
    tests/test_webui_token_paste.py; this smoke test satisfies the
    route-coverage gate below."""

    def test_post_save_channel_token_missing_csrf_returns_403(self, csrf_client):
        resp = csrf_client.post("/settings/save-channel-token")
        assert resp.status_code == 403


class TestUrlVerifyRoutes:
    """Plan v1.0 Unit 3 — /url-verify route smoke. Full lifecycle in
    tests/test_webui_url_verify_routes.py; this satisfies the route-coverage
    gate below."""

    def test_post_url_verify_missing_csrf_returns_403(self, client):
        resp = client.post("/url-verify")
        assert resp.status_code == 403


class TestEquityLedgerRoutes:
    """Plan 2026-05-25-004 — equity-ledger route smoke. Full lifecycle in
    tests/test_webui_equity_ledger_route.py + _recheck.py; this satisfies the
    route-coverage gate below."""

    def test_get_equity_ledger(self, client):
        resp = client.get("/ce:equity-ledger")
        assert resp.status_code == 200

    def test_post_equity_ledger_recheck_missing_csrf_or_body(self, client):
        resp = client.post("/ce:equity-ledger/recheck")
        assert resp.status_code in (400, 403, 404, 415)


class TestChannelBindSaveRoutes:
    """Plan 2026-05-26-002 Unit 4 — generic credential save route smoke.
    Full lifecycle in tests/test_channel_bind_save.py; this satisfies the
    route-coverage gate below."""

    def test_post_save_channel_credential_missing_csrf_returns_403(self, csrf_client):
        resp = csrf_client.post("/settings/save-channel-credential")
        assert resp.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# Coverage assertion — make sure we exercised every @app.route declared.
# This is the file's primary regression net for "did anyone add a route?".
# ═════════════════════════════════════════════════════════════════════════════


def test_every_route_has_at_least_one_contract_test():
    """Enumerate routes in webui.app + assert each is exercised by a real
    client.get / client.post call in this module.

    Two intentional design choices:

    1. Parametrized rules like '/sites/run/<run_id>/result' are translated to
       a regex where each '<param>' becomes a non-slash / non-quote run. This
       matches both literal forms (``client.get("/sites/run/abc/result")``)
       and f-string forms (``client.get(f"/sites/run/{run_id}/result")``).
    2. Coverage is matched against actual ``client.get(...)`` /
       ``client.post(...)`` calls — not raw string presence. A route that
       appears only in a docstring, comment, or assertion message MUST NOT
       count as covered, otherwise the gate gives a false sense of safety.
    """
    import webui

    rules = {r.rule for r in webui.app.url_map.iter_rules() if r.endpoint != "static"}

    this_file = open(__file__, encoding="utf-8").read()

    uncovered = []
    for rule in sorted(rules):
        # Translate Flask path params to a permissive segment regex; escape
        # the literal segments so special chars (e.g. ':' in '/ce:plan')
        # cannot become regex metacharacters.
        parts = re.split(r"(<[^>]+>)", rule)
        rule_pattern = "".join(
            r"[^/\"']+" if p.startswith("<") else re.escape(p)
            for p in parts
        )
        # Require a real client.{get,post}(...) invocation. The closing char
        # is a quote (rule ends there) or '?' (query string immediately
        # follows the path, e.g. '?error=access_denied').
        call_re = re.compile(
            rf"client\.(?:get|post)\(\s*f?[\"']{rule_pattern}[\"'?]"
        )
        if not call_re.search(this_file):
            uncovered.append(rule)

    assert not uncovered, (
        f"Routes without contract test coverage: {uncovered}. "
        f"Plan 2026-05-18-001 Unit 1 requires every route to have ≥1 test "
        f"that invokes client.get/post on the route."
    )


class TestLlmRoutes:
    def test_llm_logs_route_is_removed(self, client):
        # /settings/llm-logs (llm_diag blueprint) was a placeholder returning
        # mocked metrics; removed in webui-006 Quick-Wins along with its
        # settings.html consumer block. A 404 here is the contract.
        resp = client.get("/settings/llm-logs")
        assert resp.status_code == 404

    def test_test_llm_connection_returns_json(self, client):
        resp = client.post("/settings/test-llm-connection", data={})
        assert resp.status_code == 200
        assert resp.is_json

    def test_save_llm_config_redirects(self, client):
        resp = client.post(
            "/settings/save-llm-config",
            data={"endpoint": "https://api.example.com/v1", "api_key": "sk-test",
                  "model": "gpt-4o", "temperature": "0.7"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_llm_config_clear_action_redirects(self, client):
        resp = client.post("/settings/save-llm-config", data={"action": "clear"})
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")


class TestNotionTokenRoutes:
    """Contract tests for Plan 003 Phase 2 Notion token routes."""

    def test_save_notion_token_redirects_on_success(self, client):
        resp = client.post(
            "/settings/save-notion-token",
            data={
                "integration_token": "secret_test123",
                "database_id": "db_abc456",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_save_notion_token_rejects_empty_token(self, client):
        resp = client.post(
            "/settings/save-notion-token",
            data={"integration_token": "", "database_id": "db_abc456"},
        )
        assert resp.status_code == 302
        assert b"flash_type=danger" in resp.data or b"flash_type=info" in resp.data


class TestCsrfGuard:
    """Lock in that ``_global_csrf_guard`` rejects state-mutating verbs
    without a valid token. The class above's ``client`` fixture disables
    CSRF via TESTING/WTF_CSRF_ENABLED; this test builds its own app so
    the production hook path is exercised."""

    def test_post_without_csrf_token_returns_403(self):
        from webui_app import create_app
        a = create_app(start_scheduler=False)
        with a.test_client() as c:
            resp = c.post('/settings/save-llm-config', data={'endpoint': 'x'})
            assert resp.status_code == 403, (
                f"Expected 403 from global CSRF guard, got {resp.status_code}. "
                "Regression of _global_csrf_guard in webui_app/__init__.py."
            )


class TestSecretLeakRegression:
    """Guard against the P3 pattern reappearing — long-term credentials must
    never be re-rendered into HTML where DevTools can read them."""

    def test_llm_settings_file_is_0o600(self, client):
        """llm-settings.json holds the LLM api_key — must not be world-readable.

        PR #139 hand-rolled the write path and shipped without chmod, leaving
        the file 0644. The fix routes through ``atomic_write`` (chmods 0o600
        on the tmp file before rename).
        """
        import stat as _stat
        from webui_app.helpers.contexts import _llm_settings_file

        resp = client.post("/settings/save-llm-config", data={
            "endpoint": "https://api.example.com/v1",
            "api_key": "sk-perms-canary",
            "model": "gpt-4o",
            "temperature": "0.7",
        })
        assert resp.status_code == 302
        path = _llm_settings_file()
        assert path.exists(), "settings file not created by save handler"
        mode = _stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, (
            f"llm-settings.json mode is {oct(mode)} — must be 0o600 (api_key "
            "is a long-term secret; PR #139 originally shipped 0644)."
        )

    def test_llm_settings_loose_perms_fixed_on_load(self, client):
        """O8: pre-#140 llm-settings.json files written at 0o644 must be
        auto-tightened to 0o600 when the read path loads them.

        The write path routes through ``atomic_write`` (0o600), but a file
        created by pre-#140 code stays world-readable until re-saved. The
        loader mirrors ``_util/secrets.py``'s frw-token reader: warn + chmod.
        """
        import json as _json
        import os as _os
        import stat as _stat
        from webui_app.helpers.contexts import (
            _llm_settings_file,
            _load_llm_settings,
        )

        path = _llm_settings_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Simulate a pre-#140 hand-rolled write: real file, loose 0o644 perms.
        path.write_text(_json.dumps({"api_key": "sk-legacy-0644"}),
                        encoding="utf-8")
        _os.chmod(path, 0o644)
        assert _stat.S_IMODE(path.stat().st_mode) == 0o644

        settings = _load_llm_settings()
        # Behaviour otherwise identical: the api_key still loads.
        assert settings["api_key"] == "sk-legacy-0644"
        # ...but the file is now 0o600.
        mode = _stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, (
            f"llm-settings.json mode is {oct(mode)} — loader must auto-chmod "
            "a pre-existing 0o644 file to 0o600 (O8)."
        )

    def test_blogger_client_secret_not_rendered(self, client):
        from backlink_publisher.config import load_config, save_config
        canary = "GOCSPX-LEAK-CANARY-do-not-render"
        save_config(load_config(),
                    blogger_client_id="canary.apps.googleusercontent.com",
                    blogger_client_secret=canary,
                    target_three_url=None)
        resp = client.get("/settings")
        assert resp.status_code == 200
        assert canary.encode() not in resp.data, (
            "client_secret leaked into rendered HTML — check helpers.py "
            "_settings_context and _settings_channel_blogger.html for raw "
            "secret backfill (regression of PR #139 P3 fix)."
        )

    def test_test_llm_generation_returns_json(self, client):
        resp = client.post("/settings/test-llm-generation", data={})
        assert resp.status_code == 200
        assert resp.is_json

    def test_test_image_gen_returns_json(self, client):
        # No [image_gen] section in this isolated fixture → expect
        # ok=False but JSON shape (no 500). Full coverage in
        # tests/test_webui_image_gen.py.
        resp = client.post("/settings/test-image-gen")
        assert resp.status_code == 200
        assert resp.is_json
        body = resp.get_json()
        assert "ok" in body


class TestPreviewRoutes:
    def test_ce_preview_returns_200(self, client):
        resp = client.post("/ce:preview", data={"urls_json": '["https://example.com"]'})
        assert resp.status_code == 200


class TestVelogApiRoutes:
    def test_velog_login_spawns_ok(self, client, monkeypatch, tmp_path):
        """POST /api/velog/login returns 200 + ok:true when helper reports survival."""

        from webui_app.services import browser_login as bl

        log_path = tmp_path / "velog_login.log"
        log_path.write_bytes(b"")
        monkeypatch.setattr(
            bl,
            "spawn_browser_login",
            lambda module, **kw: bl.SpawnResult(ok=True, error=None, log_path=log_path),
        )
        resp = client.post("/api/velog/login")
        assert resp.status_code == 200
        assert resp.is_json
        body = resp.get_json()
        assert body["ok"] is True
        assert body["log_path"] == str(log_path)

    def test_velog_login_surfaces_subprocess_error(self, client, monkeypatch, tmp_path):
        """POST /api/velog/login returns 500 + tail when helper reports early death."""
        from webui_app.services import browser_login as bl

        log_path = tmp_path / "velog_login.log"
        log_path.write_bytes(b"")
        monkeypatch.setattr(
            bl,
            "spawn_browser_login",
            lambda module, **kw: bl.SpawnResult(
                ok=False,
                error="TypeError: PipelineLogger.info() takes 2 positional arguments",
                log_path=log_path,
            ),
        )
        resp = client.post("/api/velog/login")
        assert resp.status_code == 500
        body = resp.get_json()
        assert body["ok"] is False
        assert "PipelineLogger" in body["error"]
        assert body["log_path"] == str(log_path)

    def test_velog_status_returns_json(self, client):
        """GET /api/velog/status returns JSON with a 'state' key."""
        resp = client.get("/api/velog/status")
        assert resp.status_code == 200
        assert resp.is_json
        data = resp.get_json()
        assert "state" in data
        assert data["state"] in ("err", "ok", "fresh", "warn", "cap_reached", "permission_denied")


class TestSeoVizRoutes:
    """Contract test for /api/seo/anchors — exposes report-anchors data
    to the dashboard charting layer."""

    def test_anchors_missing_domain_returns_400(self, client):
        """GET /api/seo/anchors without ?domain= rejects with 400 + json."""
        resp = client.get("/api/seo/anchors")
        assert resp.status_code == 400
        assert resp.is_json
        body = resp.get_json()
        assert "error" in body

    def test_anchors_with_domain_invokes_report_anchors(self, client, monkeypatch):
        """GET /api/seo/anchors?domain=<d> calls AnchorData.from_report
        and returns the chart-data shape on success."""
        from webui_app.services import seo_viz as svc

        fake = svc.AnchorData(
            main_domain="https://example.com",
            total_entries=3,
            type_stats={"brand": {"count": 1}, "natural": {"count": 2}},
            alarm={},
        )
        monkeypatch.setattr(svc.AnchorData, "from_report", classmethod(lambda cls, d: fake))
        resp = client.get("/api/seo/anchors?domain=https://example.com")
        assert resp.status_code == 200
        assert resp.is_json
        body = resp.get_json()
        assert "labels" in body
        assert "datasets" in body
