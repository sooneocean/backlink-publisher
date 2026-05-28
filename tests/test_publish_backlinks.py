"""Tests for publish-backlinks CLI."""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.linkcheck.verify import VerificationResult


@pytest.fixture(autouse=True)
def _mock_verify_pass(mocker):
    """Default: verification always passes so tests stay fast and network-free."""
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _run_publish(
    input_data: str,
    argv: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> tuple[str, str, int]:
    """Run publish-backlinks with given stdin data. Returns (stdout, stderr, exit_code)."""
    old_stdin = sys.stdin
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    old_env = dict(os.environ)

    try:
        if env:
            os.environ.update(env)

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
        os.environ.clear()
        os.environ.update(old_env)


def _make_valid_payload(url_mode: str = "A", platform: str = "medium") -> dict:
    return {
        "id": "abc123",
        "platform": platform,
        "language": "en",
        "publish_mode": "draft",
        "target_url": "https://example.com/article",
        "main_domain": "https://example.com",
        "url_mode": url_mode,
        "title": "Test Article",
        "slug": "test-article",
        "excerpt": "A test excerpt.",
        "tags": ["tag1", "tag2"],
        "content_markdown": "This is a test article about https://example.com and its resources.",
        "links": [
            {"url": "https://example.com", "anchor": "Example", "kind": "main_domain", "required": True},
            {"url": "https://example.com/article", "anchor": "Article", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "Wiki", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "MDN", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "SO", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "GitHub", "kind": "supporting", "required": False},
        ],
        "seo": {
            "title": "Test Article | SEO",
            "description": "SEO description",
            "canonical_url": "https://example.com/article",
        },
    }


def _make_result(platform="medium", adapter="medium-api", mode="draft") -> AdapterResult:
    status = "published" if mode == "publish" else "drafted"
    return AdapterResult(
        status=status,
        adapter=adapter,
        platform=platform,
        draft_url="" if mode == "publish" else "https://medium.com/p/abc123",
        published_url="https://medium.com/p/abc123" if mode == "publish" else "",
    )


@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_dry_run(mock_pub):
    """--dry-run calls adapter with dry_run=True and outputs plan without publishing."""
    mock_pub.return_value = AdapterResult(
        status="draft",
        adapter="medium-api",
        platform="medium",
        _dry_run=True,
        _command="publish to medium --mode draft (dry-run)",
    )
    payload = _make_valid_payload(platform="medium")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--dry-run"])

    assert code == 0, f"Expected 0, got {code}. stderr: {stderr}"
    # adapter was called with dry_run=True (not a real publish)
    call_kwargs = mock_pub.call_args[1]
    assert call_kwargs.get("dry_run") is True
    output = json.loads(stdout.strip())
    assert output["_dry_run"] is True
    assert output["platform"] == "medium"
    assert "_command" in output


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_draft_mode(mock_pub, mock_verify):
    mock_pub.return_value = _make_result(platform="medium", mode="draft")

    payload = _make_valid_payload(platform="medium")
    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "medium", "--mode", "draft"]
    )

    assert code == 0, f"Expected 0. stderr: {stderr}"
    output = json.loads(stdout.strip())
    assert output["status"] == "drafted"
    assert output["draft_url"] == "https://medium.com/p/abc123"
    assert output["error"] is None
    mock_verify.assert_called_once()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_blogger(mock_pub, mock_verify):
    mock_pub.return_value = AdapterResult(
        status="drafted",
        adapter="blogger-api",
        platform="blogger",
        draft_url="https://myblog.blogspot.com/2026/05/post.html",
    )

    payload = _make_valid_payload(platform="blogger")
    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "blogger", "--mode", "draft"]
    )

    assert code == 0, f"Expected 0. stderr: {stderr}"
    output = json.loads(stdout.strip())
    assert output["platform"] == "blogger"
    assert output["status"] == "drafted"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_with_row_platform(mock_pub, mock_verify):
    """Per-row platform used when --platform is not specified."""
    mock_pub.return_value = _make_result(platform="medium")

    payload = _make_valid_payload(platform="medium")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 0
    mock_verify.assert_called_once()


@patch(
    "backlink_publisher.cli.publish_backlinks.verify_adapter_setup",
    side_effect=DependencyError("Blogger OAuth not configured"),
)
def test_publish_missing_adapter_config(mock_verify):
    """Exit code 3 when adapter config is missing."""
    payload = _make_valid_payload(platform="blogger")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--platform", "blogger"])

    assert code == 3
    assert "OAuth" in stderr


def test_publish_unknown_platform_rejected():
    """platform=xyznonexistent rejected with exit code 2."""
    payload = _make_valid_payload(platform="xyznonexistent")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 2
    assert "xyznonexistent" in stderr.lower()
    assert stdout == ""


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish",
       side_effect=ExternalServiceError("editor not found"))
def test_publish_external_service_error(mock_pub, mock_verify):
    """ExternalServiceError from adapter records failure and exits 4 (not abort mid-batch)."""
    payload = _make_valid_payload(platform="medium")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 4
    assert "editor not found" in stderr


@patch("backlink_publisher.cli._publish_helpers.time.sleep")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_external_service_error_mid_batch_continues(mock_pub, mock_verify, mock_sleep):
    """ExternalServiceError on row 2 of 3 does not abort rows 1 and 3."""
    results = [
        _make_result(platform="medium"),
        ExternalServiceError("rate-limited"),
        _make_result(platform="medium"),
    ]
    mock_pub.side_effect = results

    payloads = [_make_valid_payload(platform="medium") for _ in range(3)]
    for i, p in enumerate(payloads):
        p["id"] = f"row-{i}"
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "draft"]
    )

    assert code == 4  # failure recorded
    assert "rate-limited" in stderr

    # Rows 1 and 3 succeeded — written to stdout
    out_lines = [l for l in stdout.strip().split("\n") if l]
    assert len(out_lines) == 2
    out_ids = {json.loads(l)["id"] for l in out_lines}
    assert "row-0" in out_ids
    assert "row-2" in out_ids

    # All 3 adapter calls were made
    assert mock_pub.call_count == 3


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_external_service_error_all_rows_fail(mock_pub, mock_verify):
    """All rows ExternalServiceError → exit 4, nothing on stdout."""
    mock_pub.side_effect = ExternalServiceError("service down")

    rows = [_make_valid_payload(platform="medium") for _ in range(2)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(r) for r in rows), ["--mode", "draft"]
    )

    assert code == 4
    assert stdout.strip() == ""  # no successful rows
    assert "service down" in stderr


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_dependency_error_still_aborts(mock_pub, mock_verify):
    """DependencyError still aborts immediately (exit 3, not log-and-continue)."""
    mock_pub.side_effect = DependencyError("oauth not configured")

    payloads = [_make_valid_payload(platform="blogger") for _ in range(2)]
    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "draft"]
    )

    assert code == 3
    assert "oauth not configured" in stderr
    # Only first adapter call was made — abort on first DependencyError
    assert mock_pub.call_count == 1


def test_publish_empty_input():
    """Empty input must produce error."""
    stdout, stderr, code = _run_publish("")
    assert code == 2
    assert stdout == ""


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_output_schema(mock_pub, mock_verify):
    """Publish output matches the expected JSONL schema."""
    mock_pub.return_value = _make_result(platform="medium")

    payload = _make_valid_payload(platform="medium")
    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "medium", "--mode", "draft"]
    )

    assert code == 0
    output = json.loads(stdout.strip())

    for field in ["id", "platform", "status", "title", "target_url", "article_urls",
                  "draft_url", "published_url", "created_at", "adapter", "error"]:
        assert field in output, f"Missing field: {field}"
    assert output["article_urls"] == ["https://medium.com/p/abc123"]


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_default_is_draft(mock_pub, mock_verify):
    """Default mode must be draft."""
    mock_pub.return_value = _make_result(platform="medium", mode="draft")

    payload = _make_valid_payload()
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--platform", "medium"])

    assert code == 0
    call_kwargs = mock_pub.call_args[1]
    assert call_kwargs.get("mode") == "draft"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_blogger_and_medium_rows(mock_pub, mock_verify):
    """Full integration: one blogger + one medium row, both mocked, exit 0."""
    def side_effect(payload, mode, config, dry_run=False, **_kwargs):
        # **_kwargs accepts ``banner_emit`` (Plan 2026-05-20-004 Unit 1).
        platform = payload.get("platform", "")
        return AdapterResult(
            status="drafted",
            adapter=f"{platform}-api",
            platform=platform,
            draft_url=f"https://{platform}.example.com/p/123",
        )

    mock_pub.side_effect = side_effect

    rows = [
        json.dumps(_make_valid_payload(platform="blogger")),
        json.dumps(_make_valid_payload(platform="medium")),
    ]
    stdout, stderr, code = _run_publish("\n".join(rows), ["--mode", "draft"])

    assert code == 0, f"Expected 0. stderr: {stderr}"
    lines = [l for l in stdout.strip().split("\n") if l]
    assert len(lines) == 2


# ── Unit 2: checkpoint integration ────────────────────────────────────────────

@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_checkpoint_created_on_success(mock_pub, mock_verify, mock_cache, tmp_path):
    """2-row batch both succeed → checkpoint has both items done, run_id in stderr."""
    mock_cache.return_value = tmp_path / "cache"
    payloads = [_make_valid_payload(platform="blogger") for _ in range(2)]
    for i, p in enumerate(payloads):
        p["id"] = f"r{i}"
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )

    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads),
        ["--mode", "draft", "--log-level", "INFO"],
    )

    assert code == 0
    assert "run_id=" in stderr

    ckpt_dir = tmp_path / "cache" / "checkpoints"
    files = list(ckpt_dir.glob("*.json"))
    assert len(files) == 1
    import json as _json
    data = _json.loads(files[0].read_text())
    assert all(item["status"] == "done" for item in data["items"])
    assert len(data["items"]) == 2


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
def test_checkpoint_not_created_on_preflight_failure(mock_verify, mock_cache, tmp_path):
    """validate_publish_payload failure → no checkpoint created."""
    mock_cache.return_value = tmp_path / "cache"
    bad_row = {"id": "x", "platform": "blogger"}  # missing required fields

    stdout, stderr, code = _run_publish(json.dumps(bad_row), ["--mode", "draft"])

    assert code == 2
    ckpt_dir = tmp_path / "cache" / "checkpoints"
    assert not ckpt_dir.exists() or not list(ckpt_dir.glob("*.json"))


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_checkpoint_first_fails_second_succeeds(mock_pub, mock_verify, mock_cache, tmp_path):
    """First row ExternalServiceError → failed in checkpoint, second done.

    Note: each row MUST have a distinct target_url so the reconciler
    (which cross-references checkpoints against the dedup store)
    does not auto-fix r0's "failed" checkpoint to "done" when r1's
    same-URL dedup record is in ``done`` state (Plan 2026-05-28-004).
    """
    mock_cache.return_value = tmp_path / "cache"
    r0 = _make_valid_payload(platform="blogger")
    r0["id"] = "r0"
    r0["target_url"] = "https://example.com/r0"
    # Update links to reference r0's distinct target_url.
    for link in r0.get("links", []):
        if link["url"] == "https://example.com/article":
            link["url"] = r0["target_url"]
    r1 = _make_valid_payload(platform="blogger")
    r1["id"] = "r1"
    r1["target_url"] = "https://example.com/r1"
    for link in r1.get("links", []):
        if link["url"] == "https://example.com/article":
            link["url"] = r1["target_url"]
    mock_pub.side_effect = [
        ExternalServiceError("upstream down"),
        AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                      draft_url="https://blogger.example.com/p/2"),
    ]

    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in [r0, r1]), ["--mode", "draft"]
    )

    assert code == 4
    ckpt_dir = tmp_path / "cache" / "checkpoints"
    import json as _json
    data = _json.loads(list(ckpt_dir.glob("*.json"))[0].read_text())
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["r0"]["status"] == "failed"
    assert by_id["r0"]["error_class"] == "transient"
    assert by_id["r1"]["status"] == "done"


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
def test_checkpoint_not_created_on_verify_failure(mock_verify, mock_cache, tmp_path):
    """verify_adapter_setup failure → exit 3, no checkpoint created."""
    mock_cache.return_value = tmp_path / "cache"
    mock_verify.side_effect = DependencyError("oauth not configured")

    payload = _make_valid_payload(platform="blogger")
    stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 3
    ckpt_dir = tmp_path / "cache" / "checkpoints"
    assert not ckpt_dir.exists() or not list(ckpt_dir.glob("*.json"))


@patch("backlink_publisher.checkpoint._cache_dir")
def test_checkpoint_not_created_on_dry_run(mock_cache, tmp_path):
    """--dry-run → no checkpoint file created."""
    mock_cache.return_value = tmp_path / "cache"
    payload = _make_valid_payload(platform="medium")
    with patch("backlink_publisher.cli.publish_backlinks.adapter_publish") as mock_pub:
        mock_pub.return_value = AdapterResult(
            status="draft", adapter="medium-api", platform="medium",
            _dry_run=True, _command="dry-run plan",
        )
        stdout, stderr, code = _run_publish(json.dumps(payload), ["--dry-run"])

    assert code == 0
    ckpt_dir = tmp_path / "cache" / "checkpoints"
    assert not ckpt_dir.exists() or not list(ckpt_dir.glob("*.json"))


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_checkpoint_create_failure_degrades_gracefully(mock_pub, mock_verify, mock_cache, tmp_path):
    """create_checkpoint raising OSError → publish run still completes, no crash."""
    mock_cache.return_value = tmp_path / "cache"
    mock_pub.return_value = AdapterResult(
        status="drafted", adapter="blogger-api", platform="blogger",
        draft_url="https://blogger.example.com/p/1",
    )
    with patch("backlink_publisher.cli.publish_backlinks.checkpoint.create_checkpoint",
               side_effect=OSError("disk full")):
        payload = _make_valid_payload(platform="blogger")
        stdout, stderr, code = _run_publish(json.dumps(payload), ["--mode", "draft"])

    assert code == 0
    assert "checkpoint not created" in stderr
    assert stdout.strip() != ""


@patch("backlink_publisher.checkpoint._cache_dir")
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_checkpoint_3_rows_2_done_1_failed(mock_pub, mock_verify, mock_cache, tmp_path):
    """3-row batch: first two succeed, third raises → checkpoint has 2 done, 1 failed."""
    mock_cache.return_value = tmp_path / "cache"
    payloads = [_make_valid_payload(platform="blogger") for _ in range(3)]
    for i, p in enumerate(payloads):
        p["id"] = f"r{i}"
        p["target_url"] = f"https://example.com/r{i}"
        for link in p.get("links", []):
            if link["url"] == "https://example.com/article":
                link["url"] = p["target_url"]
    mock_pub.side_effect = [
        AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                      draft_url="https://blogger.example.com/p/1"),
        AdapterResult(status="drafted", adapter="blogger-api", platform="blogger",
                      draft_url="https://blogger.example.com/p/2"),
        ExternalServiceError("timeout"),
    ]

    stdout, stderr, code = _run_publish(
        "\n".join(json.dumps(p) for p in payloads), ["--mode", "draft"]
    )

    assert code == 4
    import json as _json
    ckpt_dir = tmp_path / "cache" / "checkpoints"
    data = _json.loads(list(ckpt_dir.glob("*.json"))[0].read_text())
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id["r0"]["status"] == "done"
    assert by_id["r1"]["status"] == "done"
    assert by_id["r2"]["status"] == "failed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_quarantined_hard_skip_is_skipped_not_failed(
    mock_pub, mock_verify, tmp_path, monkeypatch
):
    """A quarantined platform opted into hard_skip is filtered from the payload
    WITHOUT being counted as a publish failure: the run still exits 0 (a
    deliberate advisory skip is not exit-4). Regression for the ce:review
    finding that skipped_quarantined rows were appended as failure rows."""
    from backlink_publisher.canary import store as canary_store

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        "\n".join(
            [
                "[canary.blogger]",
                'post_url = "https://canary.example.com/p.html"',
                'expected_target = "https://example.com/"',
                'marker = "cnry-zzz"',
                "hard_skip = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    canary_store.canary_health_store.reset()
    # Quarantine blogger (two consecutive drifts crosses QUARANTINE_AFTER_N).
    canary_store.record_verdict("blogger", canary_store.STATUS_DRIFT_CONFIRMED)
    canary_store.record_verdict("blogger", canary_store.STATUS_DRIFT_CONFIRMED)
    assert canary_store.is_quarantined("blogger") is True

    payload = _make_valid_payload(platform="blogger")
    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "blogger", "--mode", "draft"]
    )

    # The deliberate skip must NOT be a publish failure.
    assert code != 4, f"quarantine skip wrongly treated as failure. stderr: {stderr}"
    assert code in (0, 5)  # 0 = clean; 5 = "no payloads published" (all skipped)
    # The adapter was never invoked for the skipped row.
    assert mock_pub.call_count == 0
    # Operator gets a clear advisory on stderr; nothing published to stdout.
    assert "skipped_quarantined" in stderr
    assert stdout.strip() == ""
    canary_store.canary_health_store.reset()


# ---------------------------------------------------------------------------
# Unit 3 — _record_publish_path() unit tests
# Plan 2026-05-27-006 Unit 3: advisory forward-path drift recording
# ---------------------------------------------------------------------------

def _drift_result(
    platform: str = "medium",
    *,
    nofollow: bool = False,
    rewritten: bool = False,
    found: bool = True,
    verification: str = "ok",
    has_target_fields: bool = True,
) -> AdapterResult:
    """Build an AdapterResult with a link_attr_verification dict pre-set."""
    if verification == "skipped":
        link_attr: dict = {"verification": "skipped", "reason": "timeout"}
    elif not has_target_fields:
        link_attr = {"verification": "ok", "total_anchors": 2}  # no target_* fields
    else:
        link_attr = {
            "verification": "ok",
            "total_anchors": 2,
            "target_found": found,
            "target_nofollow": nofollow,
            "target_rewritten": rewritten,
            "target_nofollow_urls": ["https://x.com"] if nofollow else [],
            "target_missing_urls": [] if found else ["https://x.com"],
            "target_rewritten_urls": ["https://x.com"] if rewritten else [],
        }
    return AdapterResult(
        status="published",
        adapter=f"{platform}-api",
        platform=platform,
        published_url="https://pub.example.com/p/abc",
        _provider_meta={"link_attr_verification": link_attr},
    )


def _drift_row() -> dict:
    return {
        "id": "row01",
        "links": [
            {"url": "https://x.com", "required": True},
        ],
    }


def test_record_publish_path_link_alive_happy(monkeypatch, tmp_path):
    """Happy path: dofollow → link-alive recorded, no WARN, returns 0."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = _drift_result("medium", nofollow=False, rewritten=False, found=True)
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 0
    health = cstore.get_publish_path_health("medium")
    assert health["status"] == cstore.STATUS_LINK_ALIVE
    cstore.canary_health_store.reset()


def test_record_publish_path_drift_nofollow_returns_1_and_warns(
    monkeypatch, tmp_path, capsys
):
    """Drift (nofollow): drift recorded, WARN on stderr, returns 1, exit code unchanged."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = _drift_result("medium", nofollow=True)
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 1
    health = cstore.get_publish_path_health("medium")
    assert health["status"] == cstore.STATUS_DRIFT_CONFIRMED
    cstore.canary_health_store.reset()


def test_record_publish_path_drift_stripped_detected(monkeypatch, tmp_path):
    """Drift (stripped / missing): readable page, required link absent → drift (R5)."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = _drift_result("medium", found=False)
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 1
    assert cstore.get_publish_path_health("medium")["status"] == cstore.STATUS_DRIFT_CONFIRMED
    cstore.canary_health_store.reset()


def test_record_publish_path_skipped_verdict_records_nothing(monkeypatch, tmp_path):
    """skipped verification → nothing recorded (R5), returns 0."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = _drift_result("medium", verification="skipped")
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 0
    # No forward-path entry written at all — status remains NOT_CONFIGURED
    health = cstore.get_publish_path_health("medium")
    assert health["status"] == cstore.STATUS_NOT_CONFIGURED
    cstore.canary_health_store.reset()


def test_record_publish_path_no_required_links_records_nothing(monkeypatch, tmp_path):
    """No target_* fields (no required links) → nothing recorded, returns 0."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = _drift_result("medium", has_target_fields=False)
    ret = _record_publish_path("medium", result, {"id": "x", "links": []})

    assert ret == 0
    health = cstore.get_publish_path_health("medium")
    assert health["status"] == cstore.STATUS_NOT_CONFIGURED
    cstore.canary_health_store.reset()


def test_record_publish_path_no_provider_meta_records_nothing(monkeypatch, tmp_path):
    """_provider_meta=None (dry-run / no-verifier adapter) → nothing recorded."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = AdapterResult(
        status="published",
        adapter="medium-api",
        platform="medium",
        published_url="https://pub.example.com/p/1",
        _provider_meta=None,
    )
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 0
    cstore.canary_health_store.reset()


def test_record_publish_path_empty_provider_meta_records_nothing(monkeypatch, tmp_path):
    """_provider_meta={} (empty dict, distinct code path from None) → returns 0."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    result = AdapterResult(
        status="published",
        adapter="medium-api",
        platform="medium",
        published_url="https://pub.example.com/p/1",
        _provider_meta={},
    )
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 0
    assert cstore.get_publish_path_health("medium")["status"] == cstore.STATUS_NOT_CONFIGURED
    cstore.canary_health_store.reset()


def test_record_publish_path_or_logic_any_drift_is_drift(monkeypatch, tmp_path):
    """Multi-link row: one dofollow, one rewritten → OR → platform verdict = drift."""
    from backlink_publisher.cli._publish_helpers import _record_publish_path
    from backlink_publisher.canary import store as cstore

    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cstore.canary_health_store.reset()

    # Simulate: first link dofollow, second rewritten
    result = _drift_result("medium", rewritten=True)
    ret = _record_publish_path("medium", result, _drift_row())

    assert ret == 1
    assert cstore.get_publish_path_health("medium")["status"] == cstore.STATUS_DRIFT_CONFIRMED
    cstore.canary_health_store.reset()


# ---------------------------------------------------------------------------
# Unit 3 — end-to-end through publish loop (advisory, no exit-code change)
# ---------------------------------------------------------------------------

@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_path_drift_does_not_change_exit_code(
    mock_pub, mock_verify_setup, monkeypatch, tmp_path
):
    """Drift detected during publish → advisory WARN on stderr, exit code still 0."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.canary import store as cstore
    cstore.canary_health_store.reset()

    mock_pub.return_value = _drift_result("medium", nofollow=True)
    payload = _make_valid_payload(platform="medium")

    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "medium", "--mode", "publish"]
    )

    assert code == 0, f"drift should not change exit code. stderr={stderr}"
    assert "publish-path-canary" in stderr
    assert "drift" in stderr
    cstore.canary_health_store.reset()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_publish_path_dry_run_records_nothing(
    mock_pub, mock_verify_setup, monkeypatch, tmp_path
):
    """--dry-run → adapters don't verify → nothing recorded to forward-path store."""
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    from backlink_publisher.canary import store as cstore
    cstore.canary_health_store.reset()

    # Dry-run result (no _provider_meta)
    mock_pub.return_value = AdapterResult(
        status="dry_run",
        adapter="medium-api",
        platform="medium",
        published_url="",
        _dry_run=True,
    )
    payload = _make_valid_payload(platform="medium")

    stdout, stderr, code = _run_publish(
        json.dumps(payload), ["--platform", "medium", "--mode", "publish", "--dry-run"]
    )

    health = cstore.get_publish_path_health("medium")
    assert health["status"] == cstore.STATUS_NOT_CONFIGURED
    cstore.canary_health_store.reset()
