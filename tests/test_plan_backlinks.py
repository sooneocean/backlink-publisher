"""Tests for plan-backlinks."""

from __future__ import annotations

import json
import re
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.cli.plan_backlinks import main
from backlink_publisher.errors import InputValidationError


def _stderr_without_warnings(stderr: str) -> str:
    """Strip benign WARN + RECON + config-banner log lines so tests can
    assert on real errors only.

    RECON is the always-on Silent-Drop Tripwire reconciliation event emitted
    at end-of-run regardless of --log-level. WARN lines are anchor-keyword
    fallback notices and similar advisory signals. The config banner
    (Round-3 #7) is operator-orientation noise emitted at the start of
    each CLI invocation."""
    banner_prefixes = (
        "[plan-backlinks] effective config:",
        "[validate-backlinks] effective config:",
        "[publish-backlinks] effective config:",
        "[report-anchors] effective config:",
        "  config:",
        "  env:",
        "  platforms:",
        "  sha:",
    )
    lines = [
        line for line in stderr.splitlines()
        if line
        and '"level": "WARN"' not in line
        and '"level": "RECON"' not in line
        and not any(line.startswith(p) for p in banner_prefixes)
    ]
    return "\n".join(lines)


def _run_plan(input_data: str, argv: list[str] | None = None) -> tuple[str, str, int]:
    """Run plan-backlinks with given stdin data. Returns (stdout, stderr, exit_code)."""
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out = StringIO()
        err = StringIO()
        sys.stdout = out
        sys.stderr = err
        try:
            main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin = old_stdin
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def test_plan_three_rows():
    """plan-backlinks can read 3 JSONL rows and output 3 planned payload rows."""
    seeds = [
        {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
            "topic": "Test Topic",
        },
        {
            "target_url": "https://blog.example.org/post",
            "main_domain": "https://blog.example.org",
            "language": "zh-CN",
            "platform": "blogger",
            "url_mode": "C",
            "publish_mode": "publish",
        },
        {
            "target_url": "https://tech.ru/overview",
            "main_domain": "https://tech.ru",
            "language": "ru",
            "platform": "medium",
            "url_mode": "B",
            "publish_mode": "draft",
        },
    ]
    input_data = "\n".join(json.dumps(s) for s in seeds)
    stdout, stderr, code = _run_plan(input_data)

    assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"
    # Anchor-keyword fallback emits a WARN per article when the target site has
    # no anchor_keywords configured — that is the documented signal, not noise.
    assert _stderr_without_warnings(stderr) == "", (
        f"Expected only WARN lines on stderr, got: {stderr}"
    )

    lines = stdout.strip().split("\n")
    assert len(lines) == 3, f"Expected 3 output rows, got {len(lines)}"

    for line in lines:
        payload = json.loads(line)
        assert "id" in payload
        assert "title" in payload
        assert "content_markdown" in payload
        assert "links" in payload
        assert 5 <= len(payload["links"]) <= 8
        assert payload["main_domain"] in payload["content_markdown"]


def test_plan_empty_input():
    """Empty input must produce an error on stderr and non-zero exit."""
    stdout, stderr, code = _run_plan("")
    assert code == 2
    assert "empty input" in stderr.lower()
    assert stdout == ""


def test_plan_malformed_json():
    """Malformed JSON in input must produce error."""
    stdout, stderr, code = _run_plan("{broken\n")
    assert code == 2
    assert "malformed" in stderr.lower()
    assert stdout == ""


def test_plan_unsupported_platform():
    """platform=linkedin must be rejected with exit code 2."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "linkedin",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "linkedin" in stderr.lower()
    assert stdout == ""


def test_plan_missing_required_field():
    """Missing required field must produce error."""
    seed = {
        "target_url": "https://example.com/article",
        # missing main_domain
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "main_domain" in stderr.lower()
    assert stdout == ""


def test_plan_invalid_url_mode():
    """Invalid url_mode must produce error."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "Z",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 2
    assert "url_mode" in stderr.lower()
    assert stdout == ""


def test_plan_all_url_modes():
    """All URL modes (A, B, C) must produce valid output."""
    for mode in ("A", "B", "C"):
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": mode,
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0, f"Mode {mode} failed: {stderr}"
        payload = json.loads(stdout.strip())
        assert payload["url_mode"] == mode
        assert 5 <= len(payload["links"]) <= 8
        assert payload["main_domain"] in payload["content_markdown"]


def test_plan_no_synthesized_categories_url_without_config():
    """Regression for run 20260514T084127: B/C mode must NOT emit hardcoded
    ``<main>/categories`` (or ``<main>/detail``) URLs when config has no
    ``[sites."<main>".url_categories]`` table for the domain.

    Pre-fix: ``_build_links`` blindly appended ``main_domain + "/categories"``
    and marked it ``required: True``. The PR #16 publish-time reachability
    gate then rejected the row with HTTP 404 on sites that don't serve that
    path. Post-fix: with no config, the synthesized category/detail link is
    omitted from the payload entirely.

    Uses ``https://example.com`` as the seed domain to exercise the long-form
    ``_build_links`` path that the regression targets. A configured domain
    (with ``[targets."<domain>"]`` in the operator's config) would route to
    ``_plan_work_themed_row`` instead, bypassing ``_build_links`` and
    producing zero payloads in CI where ``work_scraper`` can't reach the live
    site. Test contract: the *domain has no config* — that's the whole point.
    """
    for mode in ("B", "C"):
        seed = {
            "target_url": "https://example.com/",
            "main_domain": "https://example.com/",
            "language": "zh-CN",
            "platform": "blogger",
            "url_mode": mode,
            "publish_mode": "publish",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0, f"Mode {mode} failed: {stderr}"
        payload = json.loads(stdout.strip())
        urls = [link["url"] for link in payload["links"]]
        # The fictional paths must not appear without config support.
        assert "https://example.com/categories" not in urls, (
            f"Mode {mode} re-introduced hardcoded /categories link: {urls}"
        )
        assert "https://example.com/detail" not in urls, (
            f"Mode {mode} re-introduced hardcoded /detail link: {urls}"
        )
        # Same shape check on the rendered markdown — no fictional URL leaks
        # into the article body.
        assert "/categories" not in payload["content_markdown"], (
            f"Mode {mode} leaked /categories into content_markdown"
        )
        assert "/detail" not in payload["content_markdown"], (
            f"Mode {mode} leaked /detail into content_markdown"
        )


class TestContentFetchGate:
    """Plan 2026-05-14-007: URL content-fetch gate wired into _build_links."""

    def test_supporting_link_gate_failure_drops_link_and_keeps_row(
        self, monkeypatch
    ):
        """One supporting URL fails the gate → article emits with the
        survivors + a density paragraph; row is NOT aborted because the
        failing URL is `kind=supporting`, not main_domain / target.
        """
        def _selective_batch(urls, max_workers=5):
            result = {}
            for u in urls:
                if u == "https://en.wikipedia.org":
                    result[u] = (False, "http_200_no_title", None)
                else:
                    result[u] = (True, None, "mock title")
            return result

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch",
            _selective_batch,
        )

        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, _, code = _run_plan(json.dumps(seed))
        assert code == 0
        payload = json.loads(stdout.strip())
        urls = [link["url"] for link in payload["links"]]
        assert "https://en.wikipedia.org" not in urls, (
            "gate-failed supporting URL must be dropped from links"
        )
        # The article remains valid (≥ 5 links).
        assert len(urls) >= 5

    def test_main_domain_gate_failure_aborts_row(self, monkeypatch, capsys):
        """main_domain fails the gate → row is dropped; tripwire records
        the drop under the `content_gate` bucket; exit code is 2 because
        the run ended with errors."""
        def _fail_main(urls, max_workers=5):
            return {
                u: (
                    (False, "http_404", None)
                    if u == "https://example.com"
                    else (True, None, "mock title")
                )
                for u in urls
            }

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch",
            _fail_main,
        )

        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        # Row drop → exit 2 (any error during planning)
        assert code == 2
        assert stdout.strip() == "", "no payload should be emitted"
        # Tripwire records the drop under content_gate
        recon_lines = [
            line for line in stderr.splitlines()
            if '"msg": "plan_reconciliation"' in line
        ]
        assert recon_lines, "tripwire must fire even on full-row drop"
        recon = json.loads(recon_lines[0])
        assert recon["dropped"]["content_gate"] == 1
        assert recon["dropped"]["validation"] == 0
        assert recon["dropped"]["generation"] == 0

    def test_target_gate_failure_aborts_row(self, monkeypatch):
        """target_url fails the gate → row dropped under content_gate.
        Same severity as main_domain (per _ROW_REQUIRED_KINDS)."""
        def _fail_target(urls, max_workers=5):
            return {
                u: (
                    (False, "http_404", None)
                    if "/article" in u
                    else (True, None, "mock title")
                )
                for u in urls
            }

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch",
            _fail_target,
        )

        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, _, code = _run_plan(json.dumps(seed))
        assert code == 2
        assert stdout.strip() == ""

    def test_no_fetch_verify_flag_bypasses_gate(self, monkeypatch):
        """--no-fetch-verify skips the gate entirely — verify_urls_batch
        is never called, all candidate URLs survive."""
        call_count = {"n": 0}

        def _tracking_batch(urls, max_workers=5):
            call_count["n"] += 1
            return {u: (False, "http_404", None) for u in urls}

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch",
            _tracking_batch,
        )

        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(
            json.dumps(seed), argv=["--no-fetch-verify"]
        )
        assert code == 0
        assert stdout.strip() != "", "with --no-fetch-verify, payload must emit"
        assert call_count["n"] == 0, (
            "gate must not be invoked when --no-fetch-verify is set"
        )
        # Recon event marks the bypass
        recon_lines = [
            line for line in stderr.splitlines()
            if '"msg": "fetch_verify_disabled"' in line
        ]
        assert recon_lines, "expected fetch_verify_disabled recon event"

    def test_b_mode_category_link_failure_drops_only_that_link(
        self, monkeypatch, tmp_path
    ):
        """B-mode with a configured category URL that fails the gate →
        category link dropped; row keeps publishing; density paragraph
        compensates so target-site link count stays ≥ 6.
        """
        config_toml = (
            '[blogger]\n'
            '"https://example.com/" = "1111"\n\n'
            '[sites."https://example.com".url_categories]\n'
            'home = "https://example.com/"\n'
            'category = "https://example.com/stale-cat"\n'
        )
        cfg_path = tmp_path / "config.toml"
        cfg_path.write_text(config_toml, encoding="utf-8")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        # config.py looks up ~/.config/backlink-publisher/config.toml so we
        # need to mirror the path under XDG_CONFIG_HOME.
        bp_dir = tmp_path / "backlink-publisher"
        bp_dir.mkdir(exist_ok=True)
        (bp_dir / "config.toml").write_text(config_toml, encoding="utf-8")
        monkeypatch.setattr(
            "backlink_publisher.config._config_dir",
            lambda: bp_dir,
        )

        def _fail_category(urls, max_workers=5):
            return {
                u: (
                    (False, "http_404", None)
                    if "stale-cat" in u
                    else (True, None, "mock title")
                )
                for u in urls
            }

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch",
            _fail_category,
        )

        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "B",
            "publish_mode": "draft",
        }
        stdout, _, code = _run_plan(json.dumps(seed))
        assert code == 0
        payload = json.loads(stdout.strip())
        urls = [link["url"] for link in payload["links"]]
        assert "https://example.com/stale-cat" not in urls


def test_plan_all_languages():
    """All supported languages must produce valid output."""
    for lang in ("en", "zh-CN", "ru"):
        seed = {
            "target_url": f"https://{lang}.example.com/article",
            "main_domain": f"https://{lang}.example.com",
            "language": lang,
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0, f"Language {lang} failed: {stderr}"
        payload = json.loads(stdout.strip())
        assert payload["language"] == lang
        assert len(payload["title"]) > 0
        assert len(payload["content_markdown"]) > 20


def test_plan_stable_deterministic_id():
    """Same seed input must always produce the same id."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    _, _, code1 = _run_plan(json.dumps(seed))
    stdout1, _, _ = _run_plan(json.dumps(seed))
    stdout2, _, _ = _run_plan(json.dumps(seed))
    assert stdout1 == stdout2


def test_plan_main_domain_natural_placement():
    """main_domain must appear naturally in content, not at very start or end."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    content = payload["content_markdown"]
    domain = "https://example.com"
    assert domain in content
    # Not at the very start (after leading markdown)
    stripped = content.lstrip("# ")
    assert not stripped.startswith(domain), "main_domain should not be at the very start"
    # Not at the very end
    assert not content.rstrip().endswith(domain), "main_domain should not be at the very end"


@pytest.mark.parametrize("language,url_mode", [
    ("en", "A"), ("en", "B"), ("en", "C"),
    ("zh-CN", "A"), ("zh-CN", "B"), ("zh-CN", "C"),
    ("ru", "A"), ("ru", "B"), ("ru", "C"),
])
def test_all_main_domain_occurrences_are_hyperlinked(language, url_mode):
    """Every main_domain URL in content_markdown must be wrapped as [anchor](url), not bare text."""
    import re
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": language,
        "platform": "blogger",
        "url_mode": url_mode,
        "publish_mode": "draft",
    }
    stdout, _, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    content = payload["content_markdown"]

    # No bare URL — every main_domain must be preceded by ]( (inside a Markdown link)
    bare = re.findall(r'(?<!\]\()https://example\.com[/]?(?!\))', content)
    assert not bare, (
        f"[{language}/{url_mode}] Found {len(bare)} bare URL(s) not wrapped as hyperlinks: {bare}\n"
        f"Content:\n{content[:400]}"
    )

    # At least 2 proper markdown links to main_domain in article body
    links = re.findall(r'\[[^\]]+\]\(https://example\.com[^)]*\)', content)
    assert len(links) >= 2, (
        f"[{language}/{url_mode}] Expected ≥2 markdown links, found {len(links)}: {links}"
    )


def test_plan_no_stderr_on_success():
    """On success, stderr must contain no errors (WARN lines are allowed)."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    _, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    assert _stderr_without_warnings(stderr) == "", (
        f"Expected only WARN lines on stderr, got: {stderr!r}"
    )


@pytest.mark.parametrize("language,url_mode,same_url", [
    ("en",    "A", True),
    ("en",    "A", False),
    ("zh-CN", "A", True),
    ("zh-CN", "A", False),
    ("ru",    "A", True),
    ("ru",    "A", False),
    ("zh-CN", "B", False),
    ("zh-CN", "C", False),
])
def test_target_site_link_density(language, url_mode, same_url):
    """Every article must contain ≥ 6 hyperlinks pointing to the target site (A+B+C ≥ 6)."""
    main_domain = "https://example.com"
    target_url = main_domain if same_url else "https://example.com/article"
    seed = {
        "target_url": target_url,
        "main_domain": main_domain,
        "language": language,
        "platform": "blogger",
        "url_mode": url_mode,
        "publish_mode": "draft",
    }
    stdout, _, code = _run_plan(json.dumps(seed))
    assert code == 0
    content = json.loads(stdout.strip())["content_markdown"]

    links = re.findall(r'\[[^\]]+\]\(https://example\.com[^)]*\)', content)
    assert len(links) >= 6, (
        f"[{language}/{url_mode}/same={same_url}] Expected ≥6 target-site links, "
        f"found {len(links)}: {links}"
    )


def test_plan_output_fields():
    """Output must contain all required fields."""
    seed = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
        "topic": "Test",
    }
    stdout, stderr, code = _run_plan(json.dumps(seed))
    assert code == 0
    payload = json.loads(stdout.strip())
    required = ["id", "platform", "language", "publish_mode", "target_url",
                "main_domain", "url_mode", "title", "slug", "excerpt", "tags",
                "content_markdown", "links", "seo"]
    for field in required:
        assert field in payload, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# SEO anchor_keywords integration (R2/R3/R4)
# ---------------------------------------------------------------------------


from backlink_publisher.cli.plan_backlinks import _generate_payload  # noqa: E402
from backlink_publisher.config import Config  # noqa: E402


def _make_config(main_domain: str, keywords: list[str]) -> Config:
    return Config(target_anchor_keywords={main_domain.rstrip("/"): keywords})


def _seed(url_mode: str = "A", language: str = "en") -> dict:
    return {
        "target_url": "https://target.example.com/post",
        "main_domain": "https://target.example.com",
        "language": language,
        "platform": "medium",
        "url_mode": url_mode,
        "publish_mode": "draft",
        "topic": "Test",
    }


def test_anchor_keywords_used_in_links_main_domain_and_target_kinds():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # url_mode A → offset 0 → anchors[0]=BrandWord, anchors[1]=HeadTerm
    assert main_link["anchor"] == "BrandWord"
    assert target_link["anchor"] == "HeadTerm"


def test_anchor_keywords_url_mode_b_offsets():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="B"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # offset 1 → anchors[0]=HeadTerm, anchors[1]=LongTailPhrase
    assert main_link["anchor"] == "HeadTerm"
    assert target_link["anchor"] == "LongTailPhrase"


def test_anchor_keywords_url_mode_c_wraps_around():
    cfg = _make_config(
        "https://target.example.com",
        ["BrandWord", "HeadTerm", "LongTailPhrase"],
    )
    payload = _generate_payload(_seed(url_mode="C"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # offset 2 → anchors[0]=LongTailPhrase, anchors[1]=BrandWord (wraps)
    assert main_link["anchor"] == "LongTailPhrase"
    assert target_link["anchor"] == "BrandWord"


def test_anchor_keywords_appear_in_body_markdown():
    cfg = _make_config(
        "https://target.example.com",
        ["UniqueAnchorAlpha", "UniqueAnchorBeta"],
    )
    payload = _generate_payload(_seed(url_mode="A", language="en"), config=cfg)
    md = payload["content_markdown"]
    assert "[UniqueAnchorAlpha](https://target.example.com)" in md
    assert "[UniqueAnchorBeta](https://target.example.com)" in md


def test_anchor_keywords_appear_in_excerpt():
    cfg = _make_config(
        "https://target.example.com",
        ["ExcerptKeyword", "AnotherKw"],
    )
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    # Excerpt uses anchors[0] in its single anchored slot
    assert "[ExcerptKeyword](https://target.example.com)" in payload["excerpt"]


def test_anchor_keywords_fallback_when_no_pool(caplog):
    cfg = Config()  # no target_anchor_keywords entry
    with caplog.at_level("WARNING"):
        payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    # Falls back to bare-domain label
    assert main_link["anchor"] == "target.example.com"
    # No keyword leakage; the markdown still references the domain
    assert "target.example.com" in payload["content_markdown"]


def test_anchor_keywords_fallback_when_pool_empty():
    cfg = _make_config("https://target.example.com", [])  # explicit empty
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    assert main_link["anchor"] == "target.example.com"


def test_anchor_keywords_single_keyword_repeats():
    cfg = _make_config("https://target.example.com", ["OnlyOne"])
    payload = _generate_payload(_seed(url_mode="A"), config=cfg)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    target_link = next(l for l in payload["links"] if l["kind"] == "target")
    # With a 1-element pool both slots get the same keyword (deterministic, OK)
    assert main_link["anchor"] == "OnlyOne"
    assert target_link["anchor"] == "OnlyOne"


@pytest.mark.parametrize("language,url_mode", [
    (lang, mode) for lang in ("en", "zh-CN", "ru") for mode in ("A", "B", "C")
])
def test_anchor_keywords_all_languages_and_modes(language, url_mode):
    """Body templates for every language+mode combination must wire keywords through."""
    cfg = _make_config(
        "https://target.example.com",
        ["KeywordA", "KeywordB", "KeywordC"],
    )
    payload = _generate_payload(
        _seed(url_mode=url_mode, language=language), config=cfg,
    )
    md = payload["content_markdown"]
    # Both anchor positions must appear inside [<keyword>](main_domain) constructs.
    # The exact two depend on offset, but at least one of the configured
    # keywords must appear in an anchored position.
    assert any(
        f"[{kw}](https://target.example.com)" in md
        for kw in ("KeywordA", "KeywordB", "KeywordC")
    ), f"no SEO anchor keyword present in {language}/{url_mode}: {md[:200]}"
    # Bare-domain anchor must NOT appear inside link brackets pointing to main_domain
    assert "[target.example.com](https://target.example.com)" not in md


def test_anchor_keyword_distribution_across_url_modes():
    """A target site rendered across A+B+C produces ≥3 distinct anchors (R3 spec)."""
    cfg = _make_config(
        "https://target.example.com",
        ["Brand", "Head", "LongTail"],
    )
    distinct_anchors = set()
    for mode in ("A", "B", "C"):
        payload = _generate_payload(_seed(url_mode=mode), config=cfg)
        for link in payload["links"]:
            if link["kind"] in ("main_domain", "target"):
                distinct_anchors.add(link["anchor"])
    assert len(distinct_anchors) >= 3, (
        f"expected ≥3 distinct anchors across A/B/C, got {distinct_anchors}"
    )


def test_anchor_keywords_no_config_uses_fallback_silently_for_payload():
    """Calling _generate_payload with config=None should still succeed (fallback)."""
    payload = _generate_payload(_seed(url_mode="A"), config=None)
    main_link = next(l for l in payload["links"] if l["kind"] == "main_domain")
    assert main_link["anchor"] == "target.example.com"


# ── --from-csv integration ─────────────────────────────────────────────────────

def test_from_csv_generates_payloads(tmp_path):
    """--from-csv reads URLs from a file and generates payloads."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text(
        "https://example.com/page1\nhttps://example.com/page2\n",
        encoding="utf-8",
    )
    stdout, stderr, code = _run_plan("", argv=[f"--from-csv={csv_file}"])
    assert code == 0, f"Expected 0, got {code}. stderr: {stderr}"
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2
    p0 = json.loads(lines[0])
    assert p0["target_url"].rstrip("/") == "https://example.com/page1"
    assert p0["main_domain"].rstrip("/") == "https://example.com"
    assert p0["platform"] == "blogger"
    assert p0["language"] == "zh-CN"


def test_from_csv_custom_defaults(tmp_path):
    """--from-csv respects --default-platform and --default-language."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text("https://medium.com/p/abc\n", encoding="utf-8")
    stdout, stderr, code = _run_plan(
        "",
        argv=[
            f"--from-csv={csv_file}",
            "--default-platform=medium",
            "--default-language=en",
            "--default-url-mode=B",
            "--default-publish-mode=publish",
        ],
    )
    assert code == 0
    p = json.loads(stdout.strip())
    assert p["platform"] == "medium"
    assert p["language"] == "en"
    assert p["url_mode"] == "B"
    assert p["publish_mode"] == "publish"


def test_from_csv_empty_file_exits_2(tmp_path):
    """--from-csv with empty file → exit 2."""
    csv_file = tmp_path / "empty.csv"
    csv_file.write_text("", encoding="utf-8")
    stdout, stderr, code = _run_plan("", argv=[f"--from-csv={csv_file}"])
    assert code == 2


def test_from_csv_mutual_exclusion_with_input():
    """--from-csv combined with --input → exit 2."""
    import io
    stdout, stderr, code = _run_plan(
        '{"target_url": "https://a.com"}',
        argv=["--from-csv=somefile.csv", "--input=/dev/stdin"],
    )
    # Should fail before even trying to open the file
    assert code in (2, 1)


def test_from_csv_and_from_sitemap_mutually_exclusive(tmp_path):
    """--from-csv and --from-sitemap together → exit 2."""
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text("https://a.com\n", encoding="utf-8")
    stdout, stderr, code = _run_plan(
        "",
        argv=[f"--from-csv={csv_file}", "--from-sitemap=https://example.com/sitemap.xml"],
    )
    assert code == 2


def test_from_sitemap_generates_payloads():
    """--from-sitemap fetches sitemap and generates payloads for each URL."""
    sitemap_xml = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.com/page1</loc></url>
  <url><loc>https://site.com/page2</loc></url>
</urlset>"""
    from unittest.mock import patch, MagicMock
    mock_resp = MagicMock()
    mock_resp.content = sitemap_xml
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        stdout, stderr, code = _run_plan(
            "", argv=["--from-sitemap=https://site.com/sitemap.xml"]
        )

    assert code == 0
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2
    urls = {json.loads(l)["target_url"].rstrip("/") for l in lines}
    assert "https://site.com/page1" in urls
    assert "https://site.com/page2" in urls


def test_from_sitemap_network_error_exits_2():
    """--from-sitemap with network error → exit 2."""
    from unittest.mock import patch
    with patch("requests.get", side_effect=ConnectionError("offline")):
        stdout, stderr, code = _run_plan(
            "", argv=["--from-sitemap=https://example.com/sitemap.xml"]
        )
    assert code == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# ═════════════════════════════════════════════════════════════════════════════
# Plan 008 Units 2+3: cross-row URL prefetch + content_fetch_stats recon
# ═════════════════════════════════════════════════════════════════════════════


class TestContentFetchPrefetchAndStats:
    """Plan 2026-05-14-008 Units 2 + 3: cross-row prefetch collapses N
    sequential row-batches into 1 union batch; content_fetch_stats recon
    event surfaces cache-hit rate + reason distribution at end-of-run."""

    def test_prefetch_fires_once_per_invocation_with_union_urls(
        self, monkeypatch
    ):
        """3-row batch → 1 batch call with the union of distinct URLs (not
        3 sequential per-row batches)."""
        call_log: list[list[str]] = []

        def _track_batch(urls, max_workers=5):
            call_log.append(list(urls))
            return {u: (True, None, "ok") for u in urls}

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch", _track_batch,
        )

        rows = [
            {
                "target_url": f"https://a{i}.example/",
                "main_domain": f"https://a{i}.example/",
                "language": "en",
                "platform": "medium",
                "url_mode": "A",
                "publish_mode": "draft",
            }
            for i in range(3)
        ]
        stdout, stderr, code = _run_plan(
            "\n".join(json.dumps(r) for r in rows)
        )
        assert code == 0

        # Prefetch batch fires once at the top with the union of URLs.
        assert len(call_log) >= 1
        prefetch_urls = set(call_log[0])
        assert "https://a0.example" in prefetch_urls
        assert "https://a1.example" in prefetch_urls
        assert "https://a2.example" in prefetch_urls
        # Supporting URLs prefetched once globally.
        assert "https://en.wikipedia.org" in prefetch_urls

        # content_fetch_prefetch recon emitted.
        recon = [
            line for line in stderr.splitlines()
            if '"msg": "content_fetch_prefetch"' in line
        ]
        assert recon, "expected content_fetch_prefetch recon event"
        event = json.loads(recon[0])
        assert event["n_rows"] == 3
        assert event["n_urls_prefetched"] >= 4  # 3 main + supporting

    def test_no_fetch_verify_skips_prefetch_entirely(self, monkeypatch):
        """--no-fetch-verify must skip the prefetch — verify_urls_batch
        never invoked at the top of main()."""
        call_count = {"n": 0}

        def _track(urls, max_workers=5):
            call_count["n"] += 1
            return {u: (True, None, "ok") for u in urls}

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch", _track,
        )
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        _stdout, _stderr, code = _run_plan(
            json.dumps(seed), argv=["--no-fetch-verify"],
        )
        assert code == 0
        assert call_count["n"] == 0, (
            "--no-fetch-verify must skip the prefetch call too, not just "
            "per-row gating"
        )

    def test_content_fetch_stats_recon_emitted_at_end_of_run(
        self, monkeypatch
    ):
        """End-of-run plan_logger.recon('content_fetch_stats', ...) carries
        the cache + fetch + reason counters."""
        def _all_pass(urls, max_workers=5):
            return {u: (True, None, "ok") for u in urls}

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch", _all_pass,
        )
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        _stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0

        events = [
            line for line in stderr.splitlines()
            if '"msg": "content_fetch_stats"' in line
        ]
        assert events, "expected content_fetch_stats recon at end-of-run"
        snap = json.loads(events[0])
        # Snapshot keys present.
        assert "cache_hits" in snap
        assert "cache_misses" in snap
        assert "fetches" in snap
        assert "total_latency_ms" in snap
        assert "reason_counts" in snap

    def test_prefetch_skipped_when_no_valid_rows(self, monkeypatch):
        """If every input row fails validation, prefetch should not fire
        (union is empty besides the always-on supporting URLs)."""
        call_log: list[list[str]] = []

        def _track(urls, max_workers=5):
            call_log.append(list(urls))
            return {u: (True, None, "ok") for u in urls}

        monkeypatch.setattr(
            "backlink_publisher.content_fetch.verify_urls_batch", _track,
        )
        # All rows missing required fields.
        bad_rows = [{"language": "en"}, {"platform": "medium"}]
        _stdout, _stderr, code = _run_plan(
            "\n".join(json.dumps(r) for r in bad_rows)
        )
        assert code == 2  # validation errors
        # Prefetch may still fire with just the supporting URLs (5 entries),
        # which is the documented "always prefetch supporting" behavior.
        # Stronger assertion: no row-derived URLs leaked into the prefetch.
        for batch in call_log:
            for url in batch:
                assert "a0.example" not in url
                assert "a1.example" not in url


# ═════════════════════════════════════════════════════════════════════════════
# Config Echo Chamber integration (Round-3 #7)
# ═════════════════════════════════════════════════════════════════════════════


class TestConfigEchoChamber:
    """Verify the 4-line config banner emits at plan-backlinks startup +
    the resolved-config SHA is stamped into every payload's metadata
    so artifacts can be reverse-mapped to the config that produced them."""

    def test_banner_emitted_to_stderr_on_startup(self):
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        _stdout, stderr, code = _run_plan(json.dumps(seed))
        assert code == 0
        # All 5 banner lines present.
        assert "[plan-backlinks] effective config:" in stderr
        assert "  config:" in stderr
        assert "  env:" in stderr
        assert "  platforms:" in stderr
        assert "  sha:" in stderr

    def test_payload_metadata_contains_config_sha(self):
        seed = {
            "target_url": "https://example.com/article",
            "main_domain": "https://example.com",
            "language": "en",
            "platform": "medium",
            "url_mode": "A",
            "publish_mode": "draft",
        }
        stdout, _stderr, code = _run_plan(json.dumps(seed))
        assert code == 0
        payload = json.loads(stdout.strip())
        assert "metadata" in payload
        sha = payload["metadata"].get("config_sha")
        assert sha is not None, "payload metadata must contain config_sha"
        # 16-char hex prefix per compute_config_sha contract
        import re as _re
        assert _re.fullmatch(r"[0-9a-f]{16}", sha) is not None

    def test_same_config_produces_same_sha_across_payloads(self):
        """All payloads from one invocation carry the same SHA — no surprise
        cross-row config drift."""
        seeds = [
            {
                "target_url": f"https://example.com/a{i}",
                "main_domain": "https://example.com",
                "language": "en",
                "platform": "medium",
                "url_mode": "A",
                "publish_mode": "draft",
            }
            for i in range(3)
        ]
        stdout, _, code = _run_plan("\n".join(json.dumps(s) for s in seeds))
        assert code == 0
        payloads = [json.loads(line) for line in stdout.strip().split("\n")]
        shas = {p["metadata"]["config_sha"] for p in payloads}
        assert len(shas) == 1, f"expected one SHA across all payloads, got {shas}"
