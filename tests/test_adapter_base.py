"""Tests for AdapterResult base type."""

from backlink_publisher.adapters.base import AdapterResult


def test_adapter_result_defaults():
    r = AdapterResult(status="drafted", adapter="blogger-api", platform="blogger")
    assert r.draft_url == ""
    assert r.published_url == ""
    assert r.error is None
    assert r._dry_run is False


def test_adapter_result_failed_allows_empty_urls():
    r = AdapterResult(
        status="failed",
        adapter="medium-api",
        platform="medium",
        draft_url="",
        published_url="",
        error="something went wrong",
    )
    assert r.error == "something went wrong"
    assert r.draft_url == ""


def test_to_publish_output_shape():
    r = AdapterResult(
        status="drafted",
        adapter="blogger-api",
        platform="blogger",
        draft_url="https://blog.example.com/p/123",
    )
    row = {"id": "abc123", "title": "My Post"}
    out = r.to_publish_output(row, "2026-05-11T00:00:00+00:00")
    assert out["id"] == "abc123"
    assert out["title"] == "My Post"
    assert out["status"] == "drafted"
    assert out["draft_url"] == "https://blog.example.com/p/123"
    assert out["adapter"] == "blogger-api"
    assert out["error"] is None
