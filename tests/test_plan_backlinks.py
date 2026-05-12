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
    assert stderr == "", f"Expected empty stderr on success, got: {stderr}"

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
    """On success, stderr must be empty."""
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
    assert stderr == "", f"Expected empty stderr, got: {stderr!r}"


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])