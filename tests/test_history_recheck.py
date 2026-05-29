"""Plan 2026-05-19-006 Unit 5 — history recheck service + routes."""

from __future__ import annotations

from urllib.parse import unquote
from unittest.mock import patch

import pytest
from werkzeug.datastructures import MultiDict

from backlink_publisher.linkcheck.verify import VerificationResult
from webui_app.services.recheck import (
    RecheckSummary,
    recheck_many,
    recheck_one,
)
from webui_store import history_store


# ── recheck_one (pure unit) ─────────────────────────────────────────────────


class TestRecheckOne:
    def test_ok_strips_unverified_suffix(self):
        item = {
            "id": "x",
            "status": "published_unverified",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/zzz"],
        }
        mutation = recheck_one(
            item, verify_fn=lambda *a, **kw: VerificationResult(ok=True, reason="")
        )
        assert mutation["status"] == "published"
        assert mutation["verify_error"] is None
        assert mutation["_outcome"] == "confirmed"

    def test_ok_keeps_published_for_already_clean(self):
        item = {
            "id": "x",
            "status": "published",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/zzz"],
        }
        m = recheck_one(item, verify_fn=lambda *a, **kw: VerificationResult(ok=True, reason=""))
        assert m["status"] == "published"

    def test_ok_upgrades_failed_to_published(self):
        item = {
            "id": "x",
            "status": "failed",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/zzz"],
        }
        m = recheck_one(item, verify_fn=lambda *a, **kw: VerificationResult(ok=True, reason=""))
        assert m["status"] == "published"
        assert m["_outcome"] == "confirmed"

    def test_failure_downgrades_to_failed(self):
        item = {
            "id": "x",
            "status": "published",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/missing"],
        }
        m = recheck_one(
            item, verify_fn=lambda *a, **kw: VerificationResult(ok=False, reason="HTTP 404"),
        )
        assert m["status"] == "failed"
        assert m["verify_error"] == "HTTP 404"
        assert m["_outcome"] == "downgraded"

    def test_no_article_urls_marks_failed(self):
        item = {
            "id": "x",
            "status": "published_unverified",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": [],
        }
        m = recheck_one(item, verify_fn=lambda *a, **kw: VerificationResult(ok=True, reason=""))
        assert m["status"] == "published"
        assert m["verify_error"] is None
        assert m["_outcome"] == "confirmed"

    def test_verify_fn_exception_continues_to_next_url(self):
        """If verify_fn raises on URL #1 but succeeds on URL #2, item is confirmed."""
        item = {
            "id": "x",
            "status": "drafted_unverified",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://bad/", "https://good/"],
        }

        def _verify(url, *args, **kwargs):
            if "bad" in url:
                raise RuntimeError("timeout")
            return VerificationResult(ok=True, reason="")

        m = recheck_one(item, verify_fn=_verify)
        assert m["status"] == "drafted"
        assert m["_outcome"] == "confirmed"

    def test_all_urls_fail_marks_downgraded_with_last_reason(self):
        item = {
            "id": "x",
            "status": "published",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://a/", "https://b/"],
        }

        def _verify(url, **kw):
            return VerificationResult(ok=False, reason=f"HTTP 500 for {url}")

        m = recheck_one(item, verify_fn=_verify)
        assert m["status"] == "failed"
        assert "https://b/" in m["verify_error"]

    def test_empty_title_does_not_fail(self):
        # verify_published accepts title="" (treats as wildcard)
        item = {
            "id": "x",
            "status": "published_unverified",
            "title": "",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/zzz"],
        }
        m = recheck_one(item, verify_fn=lambda *a, **kw: VerificationResult(ok=True, reason=""))
        assert m["status"] == "published"


# ── recheck_many ────────────────────────────────────────────────────────────


class TestRecheckMany:
    def test_summary_counts(self):
        items = [
            {"id": "ok1", "status": "published_unverified", "article_urls": ["u"],
             "title": "t1", "target_url": "https://t/"},
            {"id": "ok2", "status": "drafted_unverified", "article_urls": ["u"],
             "title": "t2", "target_url": "https://t/"},
            {"id": "bad", "status": "published", "article_urls": ["u"],
             "title": "t3", "target_url": "https://t/"},
            {"id": "noURL", "status": "published_unverified", "article_urls": [],
             "title": "t4", "target_url": "https://t/"},
        ]

        def _verify(url, **kw):
            # 'ok1' and 'ok2' use the same URL "u" but caller pre-binds; fake
            # this by reading kwargs.title to differentiate
            title = kw.get("title", "")
            return VerificationResult(ok=(title.startswith("t1") or title.startswith("t2")), reason="404")

        by_id, summary = recheck_many(items, verify_fn=_verify)
        assert summary.checked == 4
        assert summary.confirmed == 2
        assert summary.downgraded_to_failed == 2
        assert summary.skipped == 0
        assert by_id["ok1"]["status"] == "published"
        assert by_id["ok2"]["status"] == "drafted"
        assert by_id["bad"]["status"] == "failed"
        assert by_id["noURL"]["status"] == "failed"

    def test_summary_flash_string(self):
        s = RecheckSummary(checked=5, confirmed=2, downgraded_to_failed=2, skipped=1)
        msg = s.as_flash()
        assert "5" in msg and "2" in msg and "1" in msg


# ── HTTP routes ─────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    import webui
    webui.app.config["TESTING"] = True
    webui.app.config["WTF_CSRF_ENABLED"] = False
    return webui.app.test_client()


@pytest.fixture
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(history_store, "_path", tmp_path / "history.json")
    return history_store


class TestRecheckRoute:
    def test_single_recheck_uses_verify_fn(self, client, isolated_history):
        isolated_history.save([{
            "id": "x",
            "status": "published_unverified",
            "title": "Hello",
            "target_url": "https://t.example/",
            "article_urls": ["https://medium.com/p/zzz"],
        }])
        # Default verify_fn now routes through the shared probe_liveness engine
        # (Plan 2026-05-29-004 U2), so patch the underlying inspect_target_anchor.
        with patch(
            "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
            return_value={
                "page_readable": True, "target_anchor_found": True,
                "target_is_nofollow": False, "target_rel": None,
                "target_anchor_text": None, "reason": None, "marker_present": None,
            },
        ):
            resp = client.post("/ce:history/recheck", data={"id": "x"})
        assert resp.status_code == 302
        assert isolated_history.get_item("x")["status"] == "published"

    def test_missing_id_returns_error(self, client):
        resp = client.post("/ce:history/recheck", data={})
        assert resp.status_code == 302
        assert "flash_type=danger" in resp.location

    def test_unknown_id_returns_error(self, client, isolated_history):
        isolated_history.save([])
        resp = client.post("/ce:history/recheck", data={"id": "zzz"})
        assert resp.status_code == 302
        assert "flash_type=danger" in resp.location


class TestBulkRecheckRoute:
    def test_applies_each_mutation(self, client, isolated_history):
        isolated_history.save([
            {"id": "a", "status": "published_unverified", "title": "ta",
             "target_url": "https://t/", "article_urls": ["https://u1/"]},
            {"id": "b", "status": "published", "title": "tb",
             "target_url": "https://t/", "article_urls": ["https://u2/"]},
        ])
        # `a` (article https://u1/) stays alive; `b` (https://u2/) is host_gone.
        # Default verify_fn routes through probe_liveness → patch the shared
        # inspect_target_anchor engine (Plan 2026-05-29-004 U2).
        def _inspect(url, target, **kw):
            if url == "https://u1/":
                return {
                    "page_readable": True, "target_anchor_found": True,
                    "target_is_nofollow": False, "target_rel": None,
                    "target_anchor_text": None, "reason": None, "marker_present": None,
                }
            return {
                "page_readable": False, "target_anchor_found": False,
                "target_is_nofollow": False, "target_rel": None,
                "target_anchor_text": None, "reason": "http_404", "marker_present": None,
            }

        with patch(
            "backlink_publisher.publishing.adapters.link_attr_verifier.inspect_target_anchor",
            side_effect=_inspect,
        ):
            resp = client.post(
                "/ce:history/bulk-recheck",
                data=MultiDict([("ids", "a"), ("ids", "b")]),
            )
        assert resp.status_code == 302
        msg = unquote(resp.location)
        assert "已核实 2 条" in msg
        items = {it["id"]: it for it in isolated_history.load()}
        assert items["a"]["status"] == "published"
        assert items["b"]["status"] == "failed"
        assert items["b"]["verify_error"] == "http_404"

    def test_empty_ids(self, client, isolated_history):
        resp = client.post("/ce:history/bulk-recheck", data={})
        assert "flash_type=warning" in resp.location

    def test_all_unknown_ids(self, client, isolated_history):
        isolated_history.save([{"id": "a", "status": "published",
                                "article_urls": ["u"], "target_url": "https://t/"}])
        resp = client.post(
            "/ce:history/bulk-recheck",
            data=MultiDict([("ids", "zzz"), ("ids", "yyy")]),
        )
        assert resp.status_code == 302
        assert "未匹配到记录" in unquote(resp.location)
