"""Additional edge case tests for the backlink pipeline."""

from __future__ import annotations

import json
import os
import sys
import copy
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from backlink_publisher.cli.plan_backlinks import main as plan_main
from backlink_publisher.cli.validate_backlinks import main as validate_main
from backlink_publisher.errors import DependencyError, ExternalServiceError


# ──────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────

def _run(input_data: str, cli_main, argv: list[str] | None = None) -> tuple[str, str, int]:
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    try:
        sys.stdin = StringIO(input_data)
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            cli_main(argv or [])
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr


def _valid_seed(overrides: dict | None = None) -> dict:
    base = {
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "language": "en",
        "platform": "medium",
        "url_mode": "A",
        "publish_mode": "draft",
    }
    if overrides:
        base.update(overrides)
    return base


VALID_OUTPUT = {
    "id": "abc123",
    "platform": "medium",
    "language": "en",
    "publish_mode": "draft",
    "target_url": "https://example.com/article",
    "main_domain": "https://example.com",
    "url_mode": "A",
    "title": "Test Article",
    "slug": "test-article",
    "excerpt": "A test excerpt.",
    "tags": ["tag1"],
    "content_markdown": "Content about https://example.com is here.",
    "links": [
        {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
        {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
        {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
        {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
        {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
        {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
    ],
    "seo": {"title": "SEO Title", "description": "desc", "canonical_url": "https://example.com/article"},
}

# ──────────────────────────────────────────────────────────
# plan-backlinks edge cases
# ──────────────────────────────────────────────────────────

class TestPlanEdgeCases:
    def test_oversized_payload(self):
        """Oversized JSONL line must be rejected."""
        huge = _valid_seed()
        huge["topic"] = "x" * 100_000  # far over 64 KB line limit
        data = json.dumps(huge)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2
        assert stdout == ""

    def test_non_dict_json(self):
        """A JSON array instead of object must be rejected."""
        data = '[1, 2, 3]\n'
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2
        assert stdout == ""

    def test_missing_target_url(self):
        """Missing target_url must fail."""
        seed = _valid_seed()
        del seed["target_url"]
        data = json.dumps(seed)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2
        assert "target_url" in stderr.lower()

    def test_invalid_main_domain_url(self):
        """Non-URL main_domain must fail."""
        seed = _valid_seed({"main_domain": "not-a-url"})
        data = json.dumps(seed)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2

    def test_unsupported_language(self):
        """Unsupported language must fail."""
        seed = _valid_seed({"language": "de"})
        data = json.dumps(seed)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2
        assert "language" in stderr.lower()

    def test_seed_keywords_not_list(self):
        """seed_keywords must be a list."""
        seed = _valid_seed({"seed_keywords": "not-a-list"})
        data = json.dumps(seed)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 2

    def test_topic_empty_string(self):
        """Empty topic falls back to default."""
        seed = _valid_seed({"topic": ""})
        data = json.dumps(seed)
        stdout, stderr, code = _run(data, plan_main)
        assert code == 0
        payload = json.loads(stdout.strip())
        assert len(payload["title"]) > 0  # fallback topic used


# ──────────────────────────────────────────────────────────
# validate-backlinks edge cases
# ──────────────────────────────────────────────────────────

class TestValidateEdgeCases:
    def test_links_array_not_list(self):
        """links field must be a list."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = "not-a-list"
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2

    def test_link_missing_url_field(self):
        """Each link dict must have url."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = [{"anchor": "x", "kind": "supporting", "required": False}]
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2
        assert "url" in stderr.lower()

    def test_seo_not_dict(self):
        """seo field must be a dict."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["seo"] = "not-a-dict"
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2

    def test_too_many_links(self):
        """More than 8 links must fail."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = [
            {"url": f"https://site{i}.com", "anchor": f"{i}", "kind": "supporting", "required": False}
            for i in range(9)
        ]
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2

    def test_exactly_6_links(self):
        """Exactly 6 links is the minimum valid (schema requires 6-8)."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = p["links"][:6]
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 0

    def test_exactly_5_links_rejected(self):
        """Fewer than 6 links is invalid per schema."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = p["links"][:5]
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2

    def test_exactly_8_links(self):
        """Exactly 8 links is the maximum valid."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"] = [
            {"url": f"https://site{i}.com", "anchor": f"{i}", "kind": "supporting", "required": False}
            for i in range(8)
        ]
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 0

    def test_invalid_url_in_links(self):
        """Invalid URL format in links must fail."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["links"][0]["url"] = "ftp://invalid-scheme.com"
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2

    def test_empty_excerpt(self):
        """Empty excerpt must fail (it's a required string field in output schema)."""
        p = copy.deepcopy(VALID_OUTPUT)
        p["excerpt"] = ""
        data = json.dumps(p)
        stdout, stderr, code = _run(data, validate_main, ["--no-check-urls"])
        assert code == 2  # empty excerpt is now rejected


# ──────────────────────────────────────────────────────────
# publish-backlinks edge cases
# ──────────────────────────────────────────────────────────

class TestPublishEdgeCases:
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup", return_value=None)
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    def test_publish_platform_from_row(self, mock_pub, mock_verify):
        """Platform is read from row when --platform not specified."""
        from backlink_publisher.adapters.base import AdapterResult
        p = copy.deepcopy(VALID_OUTPUT)
        p["platform"] = "blogger"
        data = json.dumps(p)

        mock_pub.return_value = AdapterResult(
            status="drafted",
            adapter="blogger-api",
            platform="blogger",
            draft_url="https://blogger.example.com/edit/abc",
        )

        stdout, stderr, code = _run(data, __import__(
            "backlink_publisher.cli.publish_backlinks", fromlist=["publish_backlinks"]
        ).main, ["--mode", "draft"])

        assert code == 0
        out = json.loads(stdout.strip())
        assert out["platform"] == "blogger"

    def test_publish_invalid_row_schema(self):
        """Invalid row schema causes exit 2."""
        data = json.dumps({"id": "x", "platform": "medium"})  # missing many fields
        stdout, stderr, code = _run(data, __import__(
            "backlink_publisher.cli.publish_backlinks", fromlist=["publish_backlinks"]
        ).main, ["--platform", "medium"])
        assert code == 2


# ──────────────────────────────────────────────────────────
# Integration: full pipeline edge case
# ──────────────────────────────────────────────────────────

class TestPipelineIntegration:
    @patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
    @patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
    def test_full_pipeline_mixed_languages(self, mock_verify, mock_pub):
        """Pipeline handles mixed languages in a single batch."""
        from backlink_publisher.cli.publish_backlinks import main as publish_main

        seeds = [
            _valid_seed({"language": "en", "topic": "Cloud"}),
            _valid_seed({"language": "zh-CN", "topic": "人工智能"}),
            _valid_seed({"language": "ru", "topic": "Облако"}),
        ]

        # Stage 1: plan
        sys_stdin = sys.stdin
        sys_stdout = sys.stdout
        sys_stderr = sys.stderr
        try:
            sys.stdin = StringIO("\n".join(json.dumps(s) for s in seeds))
            out1 = StringIO()
            sys.stdout = out1
            sys.stderr = StringIO()
            try:
                plan_main([])
            except SystemExit:
                pass
            planned = out1.getvalue()
        finally:
            sys.stdin, sys.stdout, sys.stderr = sys_stdin, sys_stdout, sys_stderr

        assert planned.strip() != "", "plan-backlinks should produce output"
        planned_rows = [json.loads(l) for l in planned.strip().split("\n")]
        assert len(planned_rows) == 3

        # Stage 2: validate
        try:
            sys.stdin = StringIO(planned)
            out2 = StringIO()
            sys.stdout = out2
            sys.stderr = StringIO()
            try:
                validate_main(["--no-check-urls"])
            except SystemExit:
                pass
            validated = out2.getvalue()
        finally:
            sys.stdin, sys.stdout, sys.stderr = sys_stdin, sys_stdout, sys_stderr

        assert validated.strip() != "", "validate-backlinks should produce output"

        # Stage 3: publish dry-run
        from backlink_publisher.adapters.base import AdapterResult
        mock_pub.return_value = AdapterResult(
            status="draft",
            adapter="medium-api",
            platform="medium",
            _dry_run=True,
            _command="publish to medium --mode draft (dry-run)",
        )
        try:
            sys.stdin = StringIO(validated)
            out3 = StringIO()
            sys.stdout = out3
            sys.stderr = StringIO()
            try:
                publish_main(["--mode", "draft", "--dry-run"])
            except SystemExit:
                pass
            published = out3.getvalue()
        finally:
            sys.stdin, sys.stdout, sys.stderr = sys_stdin, sys_stdout, sys_stderr

        assert published.strip() != "", "publish-backlinks --dry-run should produce output"
        pub_rows = [json.loads(l) for l in published.strip().split("\n")]
        assert len(pub_rows) == 3
        for r in pub_rows:
            assert r["status"] == "draft"
            assert r.get("error") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])