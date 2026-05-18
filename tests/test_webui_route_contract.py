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

    After Plan Unit 3 split, ``run_pipe`` lives in ``webui_app.helpers`` and
    each route blueprint does ``from ..helpers import run_pipe`` — that binds
    the name into the blueprint module's namespace. Patching only
    ``webui_app.helpers.run_pipe`` would miss the blueprint-local references.
    Patch each consumer explicitly.

    The ``_no_real_subprocess`` fixture also stubs ``subprocess.run``
    underneath, so even if a patch is missed the call still won't hit the
    network — but the explicit patches give faster, clearer assertions.
    """
    def _fake(_cmd, _stdin):
        return {"stdout": "", "stderr": ""}

    targets = [
        "webui_app.helpers.run_pipe",
        "webui_app.routes.pipeline.run_pipe",
        "webui_app.routes.batch.run_pipe",
        "webui_app.routes.sites.run_pipe",
        "webui_app.scheduler.run_pipe",
    ]
    patches = [patch(t, side_effect=_fake) for t in targets]
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

    def test_medium_oauth_callback_missing_state_redirects(self, client):
        """No session state → redirect with warning flash."""
        resp = client.get("/settings/medium/oauth-callback")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_medium_oauth_callback_with_error_param_redirects(self, client):
        resp = client.get("/settings/medium/oauth-callback?error=access_denied")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

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

    def test_ce_generate_with_empty_session_returns_200(self, client):
        """No urls in session/form → error re-render, still 200."""
        resp = client.post("/ce:generate", data={})
        assert resp.status_code == 200

    def test_ce_generate_with_urls_returns_200(self, client):
        urls_json = json.dumps(["https://example.com/"])
        resp = client.post("/ce:generate", data={"urls_json": urls_json})
        assert resp.status_code == 200

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

    def test_medium_oauth_start_missing_creds_redirects(self, client):
        resp = client.post(
            "/settings/medium/oauth-start", data={"client_id": "", "client_secret": ""},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

    def test_medium_oauth_start_with_creds_redirects(self, client):
        resp = client.post(
            "/settings/medium/oauth-start",
            data={"client_id": "fake-id", "client_secret": "fake-secret"},
        )
        # Either redirects to Medium OAuth URL or back to /settings with flash —
        # both are non-5xx; we just assert no server error.
        assert resp.status_code == 302

    def test_clear_medium_oauth_redirects(self, client):
        resp = client.post("/settings/clear-medium-oauth")
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/settings?")

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
    def test_save_three_url_missing_csrf_returns_403(self, client):
        resp = client.post(
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
            "backlink_publisher.work_scraper.fetch_work_urls_from_list",
            lambda *a, **k: [],
        )
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/save-three-url",
            data={"csrf_token": token, "main_url": "https://x.com/"},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("/sites?")

    def test_sites_run_missing_csrf_returns_403(self, client):
        resp = client.post("/sites/run", data={"main_url": "https://x.com/"})
        assert resp.status_code == 403

    def test_sites_run_unknown_domain_returns_400(self, client):
        token = _fetch_csrf(client)
        resp = client.post(
            "/sites/run",
            data={"csrf_token": token, "main_url": "https://never-saved.example/"},
        )
        assert resp.status_code == 400


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
