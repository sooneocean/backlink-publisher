"""Tests for the work-themed dispatcher branch in plan_backlinks — Plan 2026-05-13-004 Unit 5a.

Covers:
- ``_plan_work_themed_row`` per-row iterator: happy path, count truncation,
  sitemap fallback chain, fail-empty (returns []), fail-abort (emit_error
  exit 4), fail-continue (per-URL metadata skip + warn).
- Determinism: identical row + clean profile → identical output.
- Three-path dispatcher routing: target_three_url-only / sites-only /
  both / neither → work-themed / zh-short / work-themed (priority) / long-form.
- ``anchor_profile.record_article`` is invoked with the right ProfileEntry
  shape (anchor_type='work', url_category='work_themed').
- Autouse fixtures isolate from real network/time and from the user's
  profile cache.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backlink_publisher.anchor.profile import ProfileEntry, ProfileState
from backlink_publisher.cli import plan_backlinks
from backlink_publisher.cli.plan_backlinks import _plan_work_themed_row
from backlink_publisher.config import Config, ThreeUrlConfig
from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.content.scraper import WorkMetadata


# ── autouse isolation fixtures ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_profile(tmp_path):
    """Redirect anchor_profile cache writes into tmp."""
    fake = tmp_path / "cache"
    with patch("backlink_publisher.anchor.profile._cache_dir", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """Mock retry sleep on the off-chance a path engages the retry helper."""
    with patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
        yield


# ── helpers ──────────────────────────────────────────────────────────────────


def _row(*, main_domain="https://site.com/", target_url=None, language="zh-CN"):
    return {
        "target_url": target_url or main_domain.rstrip("/"),
        "main_domain": main_domain,
        "language": language,
        "platform": "blogger",
        "url_mode": "A",
        "publish_mode": "draft",
    }


def _three_url_cfg(
    *,
    work_urls: list[str] | None = None,
    insecure_tls: bool = False,
) -> ThreeUrlConfig:
    return ThreeUrlConfig(
        main_url="https://site.com/",
        list_url="https://site.com/list",
        branded_pool=["品牌站", "品牌首页"],
        partial_pool=["品牌部分关键词"],
        exact_pool=["关键词"],
        work_urls=work_urls if work_urls is not None else [],
        insecure_tls=insecure_tls,
    )


def _meta(title: str = "夜空中最亮的星") -> WorkMetadata:
    return WorkMetadata(title=title, description=None, h1=None)


# ═════════════════════════════════════════════════════════════════════════════
# _plan_work_themed_row — happy path + count truncation
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkThemedRowHappy:
    def test_yields_one_payload_per_work_url_padded_to_seven_links(self):
        # Per plan 2026-05-15-003 Unit 3: work-themed payloads are padded
        # from the generator's bare 3 links to _TARGET_WORK_THEMED_LINK_COUNT
        # (= 7) with supporting URLs, so schema.py:143's 6-8 gate accepts
        # them. Kind taxonomy is also normalized (Unit 2): list → category,
        # work → target.
        cfg = _three_url_cfg(
            work_urls=[
                "https://site.com/work/1",
                "https://site.com/work/2",
            ]
        )
        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            side_effect=lambda url, **_kw: _meta(title=f"作品-{url[-1]}"),
        ):
            payloads = list(_plan_work_themed_row(_row(), cfg, count=10))

        assert len(payloads) == 2
        for p in payloads:
            assert len(p["links"]) == 7
            kinds = [link["kind"] for link in p["links"]]
            # First three: path-specific (main_domain / category / target),
            # remap order matches work_themed_generator output order.
            assert kinds[:3] == ["main_domain", "category", "target"]
            # Remaining four: supporting padding from _SUPPORTING_POOL.
            assert kinds[3:] == ["supporting"] * 4
            # Schema invariant: every emitted kind is in LINK_KINDS.
            from backlink_publisher.schema import LINK_KINDS, validate_output_payload
            assert all(k in LINK_KINDS for k in kinds)
            # Schema invariant: every link MUST carry `required` (validator
            # demands it). main_domain + target are row-required; category
            # + supporting are not.
            for link in p["links"]:
                assert "required" in link, f"link missing 'required': {link}"
            assert [link["required"] for link in p["links"]] == [
                True,   # main_domain
                False,  # category (from list_url — auxiliary)
                True,   # target (from work_url)
                False,  # supporting × 4
                False,
                False,
                False,
            ]
            # The 3 work-themed anchors still render in the body via
            # work_themed_generator's HTML <a> tags.
            assert p["content_markdown"].count("<a ") == 3
            assert 'rel="noopener"' in p["content_markdown"]
            assert "nofollow" not in p["content_markdown"]
            # The 4 supporting URLs appear as markdown anchors in the
            # appended "延伸阅读" / "Further reading" paragraph (R3 — body
            # must contain every URL so verify_publish's link-presence
            # check passes downstream).
            for sup_link in p["links"][3:]:
                assert f"({sup_link['url']})" in p["content_markdown"]
            # End-to-end schema gate: the whole payload now passes
            # `validate_output_payload` — the exact validator that emits
            # the "row 1: links[0]: missing field 'required'" errors at
            # publish-backlinks time.
            errors = validate_output_payload(p)
            assert errors == [], f"validator rejected payload: {errors}"

    def test_count_truncates_provided_work_urls(self):
        cfg = _three_url_cfg(
            work_urls=[f"https://site.com/work/{i}" for i in range(10)],
        )
        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            return_value=_meta(),
        ):
            payloads = list(_plan_work_themed_row(_row(), cfg, count=3))
        assert len(payloads) == 3

    def test_payload_shape_matches_short_form_contract(self):
        cfg = _three_url_cfg(work_urls=["https://site.com/work/1"])
        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            return_value=_meta(),
        ):
            payload = list(_plan_work_themed_row(_row(), cfg, count=1))[0]
        # Must satisfy the OUTPUT_REQUIRED_FIELDS contract used by validate-backlinks
        for field in (
            "id", "platform", "language", "publish_mode", "target_url",
            "main_domain", "url_mode", "title", "slug", "excerpt", "tags",
            "content_markdown", "links", "seo",
        ):
            assert field in payload, f"missing {field}"
        assert payload["main_domain"] == cfg.main_url
        assert payload["seo"]["canonical_url"] == "https://site.com/work/1"

    def test_deterministic_for_same_row_and_clean_profile(self):
        cfg = _three_url_cfg(work_urls=["https://site.com/work/1"])

        def _gen():
            with patch.object(
                plan_backlinks.work_scraper, "fetch_work_metadata",
                return_value=_meta(),
            ):
                return list(_plan_work_themed_row(_row(), cfg, count=1))

        a = _gen()
        # Reset profile cache (tmp_path autouse → fresh per test, so we patch
        # load_profile to always return empty for this idempotency check).
        with patch(
            "backlink_publisher.anchor.profile.load_profile",
            return_value=ProfileState(main_domain="https://site.com"),
        ):
            with patch.object(
                plan_backlinks.work_scraper, "fetch_work_metadata",
                return_value=_meta(),
            ):
                b = list(_plan_work_themed_row(_row(), cfg, count=1))

        assert a[0]["content_markdown"] == b[0]["content_markdown"]


# ═════════════════════════════════════════════════════════════════════════════
# _plan_work_themed_row — sitemap / HTML fallback discovery
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkThemedRowDiscovery:
    def test_empty_work_urls_invokes_scraper_and_truncates(self):
        cfg = _three_url_cfg(work_urls=[])  # forces discovery
        with patch.object(
            plan_backlinks.work_scraper,
            "fetch_work_urls_from_list",
            return_value=[
                "https://site.com/work/a",
                "https://site.com/work/b",
                "https://site.com/work/c",
                "https://site.com/work/d",
                "https://site.com/work/e",
            ],
        ) as mock_discover, patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            return_value=_meta(),
        ):
            payloads = list(_plan_work_themed_row(_row(), cfg, count=3))

        assert len(payloads) == 3
        mock_discover.assert_called_once()
        # Discovery received list_url + main_url for filtering
        kwargs = mock_discover.call_args.kwargs
        assert kwargs.get("main_url") == cfg.main_url


class TestWorkThemedRowFailureSemantics:
    def test_fail_empty_returns_empty_iterator(self, caplog):
        cfg = _three_url_cfg(work_urls=[])
        with patch.object(
            plan_backlinks.work_scraper,
            "fetch_work_urls_from_list",
            return_value=[],  # fail-empty
        ):
            with caplog.at_level("WARNING"):
                payloads = list(_plan_work_themed_row(_row(), cfg, count=3))
        assert payloads == []  # no raise; just empty

    def test_fail_abort_external_service_error_raises_systemexit_4(self):
        cfg = _three_url_cfg(work_urls=[])
        with patch.object(
            plan_backlinks.work_scraper,
            "fetch_work_urls_from_list",
            side_effect=ExternalServiceError("network down"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                list(_plan_work_themed_row(_row(), cfg, count=3))
        assert exc_info.value.code == 4

    def test_fail_continue_skips_failed_metadata_fetches(self):
        cfg = _three_url_cfg(work_urls=[
            "https://site.com/work/1",
            "https://site.com/work/2",
            "https://site.com/work/3",
            "https://site.com/work/4",
            "https://site.com/work/5",
        ])
        # 2nd and 4th fail (return None). Other 3 succeed.
        side = [
            _meta(title="作品一"),
            None,
            _meta(title="作品三"),
            None,
            _meta(title="作品五"),
        ]
        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            side_effect=side,
        ):
            payloads = list(_plan_work_themed_row(_row(), cfg, count=10))
        assert len(payloads) == 3


# ═════════════════════════════════════════════════════════════════════════════
# anchor_profile.record_article shape (real ProfileEntry signature)
# ═════════════════════════════════════════════════════════════════════════════


class TestProfileRecording:
    def test_records_one_entry_per_payload_with_correct_fields(self):
        cfg = _three_url_cfg(work_urls=["https://site.com/work/1"])
        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            return_value=_meta(title="标题甲"),
        ), patch.object(
            plan_backlinks.anchor_profile, "record_article",
        ) as mock_record:
            list(_plan_work_themed_row(_row(), cfg, count=1))

        assert mock_record.call_count == 1
        call_args = mock_record.call_args
        main_domain_arg = call_args.args[0]
        entries = call_args.args[1]
        assert main_domain_arg == "https://site.com"
        assert isinstance(entries, list) and len(entries) == 1
        e = entries[0]
        assert isinstance(e, ProfileEntry)
        assert e.anchor_type == "work"
        assert e.url_category == "work_themed"
        # anchor_text matches the work_anchor selected by the generator
        assert isinstance(e.anchor_text, str) and e.anchor_text


# ═════════════════════════════════════════════════════════════════════════════
# Three-path dispatcher routing
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatcherRouting:
    def _make_cfg(
        self, *, with_three_url: bool, with_zh_scheduler: bool
    ) -> Config:
        cfg = Config()
        if with_three_url:
            cfg.target_three_url["https://site.com"] = ThreeUrlConfig(
                main_url="https://site.com/",
                list_url="https://site.com/list",
                branded_pool=["B"],
                partial_pool=["p"],
                exact_pool=["e"],
                work_urls=["https://site.com/work/1"],
            )
        if with_zh_scheduler:
            cfg.site_url_categories["https://site.com"] = {
                "home": "https://site.com/",
                "hot": "https://site.com/hot",
            }
            cfg.target_anchor_pools_v2["https://site.com"] = {
                "home": {"branded": ["品牌"]},
                "hot": {"branded": ["热门"]},
            }
        return cfg

    def test_three_url_only_routes_to_work_themed(self):
        cfg = self._make_cfg(with_three_url=True, with_zh_scheduler=False)
        with patch.object(
            plan_backlinks, "_plan_work_themed_row",
            return_value=iter([{"id": "wt"}]),
        ) as wt, patch.object(
            plan_backlinks, "_plan_zh_short_row", return_value={"id": "zh"},
        ) as zh, patch.object(
            plan_backlinks, "_generate_payload", return_value={"id": "long"},
        ) as lf:
            outputs = _drive_dispatcher([_row()], cfg)
        assert wt.called
        assert not zh.called
        assert not lf.called
        assert outputs[0]["id"] == "wt"

    def test_zh_scheduler_only_routes_to_zh_short(self):
        cfg = self._make_cfg(with_three_url=False, with_zh_scheduler=True)
        with patch.object(
            plan_backlinks, "_plan_work_themed_row",
            return_value=iter([{"id": "wt"}]),
        ) as wt, patch.object(
            plan_backlinks, "_plan_zh_short_row", return_value={"id": "zh"},
        ) as zh, patch.object(
            plan_backlinks, "_generate_payload", return_value={"id": "long"},
        ) as lf:
            outputs = _drive_dispatcher([_row()], cfg)
        assert not wt.called
        assert zh.called
        assert not lf.called
        assert outputs[0]["id"] == "zh"

    def test_neither_routes_to_long_form(self):
        cfg = self._make_cfg(with_three_url=False, with_zh_scheduler=False)
        # Use language=en to force long-form path immediately
        row = _row(language="en")
        with patch.object(
            plan_backlinks, "_plan_work_themed_row",
            return_value=iter([{"id": "wt"}]),
        ) as wt, patch.object(
            plan_backlinks, "_plan_zh_short_row", return_value={"id": "zh"},
        ) as zh, patch.object(
            # U7: _dispatch_row was extracted to _engine.py, which imports
            # _generate_payload directly from ._payload — patch _engine, not core.
            plan_backlinks._engine, "_generate_payload", return_value={"id": "long"},
        ) as lf:
            outputs = _drive_dispatcher([row], cfg)
        assert not wt.called
        assert not zh.called
        assert lf.called
        assert outputs[0]["id"] == "long"

    def test_both_present_prefers_work_themed(self):
        cfg = self._make_cfg(with_three_url=True, with_zh_scheduler=True)
        with patch.object(
            plan_backlinks, "_plan_work_themed_row",
            return_value=iter([{"id": "wt"}]),
        ) as wt, patch.object(
            plan_backlinks, "_plan_zh_short_row", return_value={"id": "zh"},
        ) as zh:
            outputs = _drive_dispatcher([_row()], cfg)
        assert wt.called
        assert not zh.called
        assert outputs[0]["id"] == "wt"


def _drive_dispatcher(rows: list[dict], cfg: Config) -> list[dict]:
    """Drive plan_backlinks._dispatch_row over a list of seed rows.

    Wraps the dispatcher so tests can assert which path each row took without
    having to spin up the whole CLI argparse / IO surface.
    """
    out: list[dict] = []
    for row in rows:
        for payload in plan_backlinks._dispatch_row(
            row, cfg, llm_provider=None, rng=None, work_count=10,
        ):
            out.append(payload)
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Plan 2026-05-15-003 Unit 4 — RECON instrumentation at _dispatch_row
# ═════════════════════════════════════════════════════════════════════════════


class TestDispatchReconInstrumentation:
    """``_dispatch_row`` emits one ``link_count_at_plan`` RECON event per
    yielded payload, tagging the dispatch branch + the link count + the
    sorted kinds. Bypasses ``--log-level`` so cron operators see it.
    """

    def test_work_themed_branch_emits_one_recon_per_yielded_payload(self):
        cfg = _three_url_cfg(
            work_urls=[
                "https://site.com/work/1",
                "https://site.com/work/2",
            ]
        )
        # Minimal config wrapper for the dispatcher (no llm, no scheduler).
        # Key is the un-trailing-slash form per existing helper precedent.
        config = Config()
        config.target_three_url["https://site.com"] = cfg

        captured: list[dict] = []
        original_recon = plan_backlinks.plan_logger.recon

        def _capture(msg, **fields):
            if msg == "link_count_at_plan":
                captured.append({"msg": msg, **fields})
            return original_recon(msg, **fields)

        with patch.object(
            plan_backlinks.work_scraper, "fetch_work_metadata",
            side_effect=lambda url, **_kw: _meta(title=f"作品-{url[-1]}"),
        ):
            with patch.object(
                plan_backlinks.plan_logger, "recon", side_effect=_capture,
            ):
                payloads = _drive_dispatcher([_row()], config)

        assert len(payloads) == 2
        # One recon per yielded payload, all branch=work_themed
        assert len(captured) == 2
        for event, payload in zip(captured, payloads):
            assert event["branch"] == "work_themed"
            assert event["count"] == len(payload["links"]) == 7
            assert event["main_domain"] == payload["main_domain"]
            assert event["article_id"] == payload["id"]
            # kinds is the sorted unique set
            assert event["kinds"] == sorted(
                {lk["kind"] for lk in payload["links"]}
            )

    def test_long_form_branch_emits_one_recon(self):
        # A row with NO three-URL config and NOT zh-CN-scheduler-enabled
        # → falls through to long-form (_generate_payload).
        config = Config()  # bare config — no target_three_urls, no scheduler
        captured: list[dict] = []
        original_recon = plan_backlinks.plan_logger.recon

        def _capture(msg, **fields):
            if msg == "link_count_at_plan":
                captured.append({"msg": msg, **fields})
            return original_recon(msg, **fields)

        en_row = {
            "target_url": "https://example.com/post",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "blogger",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        with patch.object(
            plan_backlinks.plan_logger, "recon", side_effect=_capture,
        ):
            payloads = _drive_dispatcher([en_row], config)

        assert len(payloads) == 1
        assert len(captured) == 1
        assert captured[0]["branch"] == "long_form"
        assert captured[0]["count"] == len(payloads[0]["links"])
        assert 6 <= captured[0]["count"] <= 8  # schema gate


# ---------------------------------------------------------------------------
# Korean language prose branches
# ---------------------------------------------------------------------------


from backlink_publisher.cli.plan_backlinks._work_themed import _further_reading_paragraph  # noqa: E402
from backlink_publisher.cli.plan_backlinks._links import _build_link_density_paragraph  # noqa: E402


class TestKoreanProseBranches:
    """Korean (ko) branches in _further_reading_paragraph and _build_link_density_paragraph."""

    def test_further_reading_ko_contains_hangul(self):
        supporting = [
            {"anchor": "관련 링크", "url": "https://example.com/rel"},
        ]
        result = _further_reading_paragraph(supporting, language="ko")
        assert result
        assert any("가" <= c <= "힣" for c in result), "Expected Hangul in ko further reading"

    def test_further_reading_ko_contains_anchor(self):
        supporting = [{"anchor": "추가자료", "url": "https://example.com/more"}]
        result = _further_reading_paragraph(supporting, language="ko")
        assert "추가자료" in result
        assert "https://example.com/more" in result

    def test_further_reading_en_unchanged(self):
        supporting = [{"anchor": "Related", "url": "https://example.com/rel"}]
        assert _further_reading_paragraph(supporting, language="en").startswith("\n\nFurther reading")

    def test_further_reading_ru_unchanged(self):
        supporting = [{"anchor": "Ресурс", "url": "https://example.com/ru"}]
        assert _further_reading_paragraph(supporting, language="ru").startswith("\n\nДополнительные")

    def test_link_density_ko_same_url_contains_hangul(self):
        result = _build_link_density_paragraph(
            domain="example.com",
            main_domain="https://example.com",
            target_url="https://example.com",
            language="ko",
            url_mode="A",
            extra_url_count=0,
            anchors=["앵커A", "앵커B"],
        )
        assert any("가" <= c <= "힣" for c in result)
        assert "https://example.com" in result

    def test_link_density_ko_different_url_contains_main_domain(self):
        result = _build_link_density_paragraph(
            domain="example.com",
            main_domain="https://example.com",
            target_url="https://example.com/article",
            language="ko",
            url_mode="A",
            extra_url_count=0,
            anchors=["앵커A", "앵커B"],
        )
        assert "https://example.com" in result
        assert any("가" <= c <= "힣" for c in result)

    def test_link_density_en_unchanged(self):
        result = _build_link_density_paragraph(
            domain="example.com",
            main_domain="https://example.com",
            target_url="https://example.com",
            language="en",
            url_mode="A",
            extra_url_count=0,
            anchors=["AnchorA", "AnchorB"],
        )
        assert "For more resources" in result

    def test_link_density_ru_unchanged(self):
        result = _build_link_density_paragraph(
            domain="example.com",
            main_domain="https://example.com",
            target_url="https://example.com",
            language="ru",
            url_mode="A",
            extra_url_count=0,
            anchors=["АнкорА", "АнкорБ"],
        )
        assert "Больше материалов" in result
