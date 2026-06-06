"""Tests for ``plan-backlinks`` banner integration — Plan 2026-05-20-001 Unit 4.

When ``Config.image_gen`` is set and the frw-token file is present,
``plan-backlinks`` calls ``ImageGenAdapter.generate``, persists the
banner via ``image_gen.storage.save_banner``, and emits a ``banner``
dict in each output JSONL row.  Body markdown stays unchanged —
no ``![](url)`` prepending — so older backlinks don't break when an
upstream CDN's TTL expires (the per-platform CDN upload happens in
Unit 5).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher.cli.plan_backlinks import main as plan_main
from backlink_publisher.cli.plan_backlinks._banners import _generate_banner_for_payload
from backlink_publisher.config.types import ImageGenConfig
from backlink_publisher.publishing.adapters.image_gen.types import BannerArtifact


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Tracker:
    disabled = False

    def record_success(self):
        pass

    def record_failure(self):
        pass


class _Store:
    def append(self, *args, **kwargs):
        pass

    def query(self, *args, **kwargs):
        return [{"n": 0}]


class _PromptRecorder:
    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> BannerArtifact:
        self.prompts.append(prompt)
        return BannerArtifact(
            data=_PNG,
            mime="image/png",
            source_url=None,
            prompt_sha="promptsha1234567",
        )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setenv("BACKLINK_PUBLISHER_CACHE_DIR", str(cache))
    return tmp_path


def _seed_token(config_dir: Path) -> None:
    from backlink_publisher._util.secrets import write_frw_token
    write_frw_token("sk_test_unit4")


def _seed_config_with_image_gen(config_dir: Path, **overrides) -> None:
    """Write a config.toml with [image_gen] populated."""
    defaults = dict(
        base_url="https://gateway.example.com/v1",
        model="banner-m",
        banner_size="1200x630",
        daily_cap=50,
        per_run_cap=10,
        timeout_s=5.0,
        max_retries=2,
    )
    defaults.update(overrides)

    body = "[image_gen]\n"
    for k, v in defaults.items():
        if isinstance(v, str):
            body += f'{k} = "{v}"\n'
        elif isinstance(v, bool):
            body += f"{k} = {'true' if v else 'false'}\n"
        else:
            body += f"{k} = {v}\n"

    (config_dir / "config.toml").write_text(body)


def _seed_input(tmp_path: Path) -> Path:
    seeds = tmp_path / "seeds.jsonl"
    rows = [
        {
            "main_domain": "https://example.com/",
            "target_url": "https://example.com/post-1",
            "platform": "telegraph",
            "language": "en",
            "url_mode": "A",
            "topic": "test topic",
            "publish_mode": "draft",
        },
    ]
    with seeds.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return seeds


def _capture_outputs(stdout_path: Path) -> list[dict]:
    if not stdout_path.exists():
        return []
    return [json.loads(line) for line in stdout_path.read_text().splitlines() if line.strip()]


def _run_plan_backlinks(seeds_path: Path, out_path: Path) -> int:
    """Invoke plan_main with stdin/stdout redirected to files.

    Returns the exit code (or 0 on clean completion).
    """
    import sys
    with seeds_path.open() as fin, out_path.open("w") as fout:
        old_stdin, old_stdout = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = fin, fout
        try:
            try:
                plan_main([])
            except SystemExit as exc:
                return int(exc.code or 0)
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
    return 0


def _post_ok(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = "OK"
    resp.raise_for_status = MagicMock()
    return resp


def _get_ok_bytes(content: bytes, mime: str) -> MagicMock:
    """Mock ``requests.get`` returning bytes (url-mode follow-up)."""
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"Content-Type": mime}
    resp.raise_for_status = MagicMock()
    return resp


def test_banner_uses_ai_cover_prompt(monkeypatch, tmp_path):
    recorder = _PromptRecorder()
    runtime = {
        "adapter": recorder,
        "tracker": _Tracker(),
        "store": _Store(),
        "config": ImageGenConfig(
            base_url="https://image.test/v1",
            model="banner-m",
        ),
        "run_counter": [0],
    }
    monkeypatch.setattr(
        "backlink_publisher.publishing.adapters.image_gen.storage.save_banner",
        lambda artifact: tmp_path / "banner.png",
    )

    banner = _generate_banner_for_payload(
        {
            "title": "Fallback Title",
            "content_markdown": "Body",
            "cover_prompt": "Use this exact AI cover prompt.",
        },
        runtime=runtime,
        llm_provider=None,
    )

    assert recorder.prompts == ["Use this exact AI cover prompt."]
    assert banner["path"] == str(tmp_path / "banner.png")


# ── banner = None when image_gen not configured ─────────────────────────────


def test_banner_none_when_image_gen_section_absent(isolated, tmp_path):
    """No ``[image_gen]`` section → ``banner: null`` in every row."""
    # config.toml left absent / blank
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    _run_plan_backlinks(seeds, out)
    rows = _capture_outputs(out)
    assert rows, "expected at least one output row"
    for row in rows:
        assert row.get("banner") is None, f"expected banner=None, got {row.get('banner')!r}"


def test_banner_none_when_use_image_gen_false(isolated, tmp_path):
    """``[image_gen] use_image_gen = false`` → ``banner: null``,
    no adapter invocation."""
    _seed_config_with_image_gen(isolated, use_image_gen=False)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
    ) as mock_post:
        _run_plan_backlinks(seeds, out)

    assert mock_post.call_count == 0, "adapter must not be called when use_image_gen=False"
    for row in _capture_outputs(out):
        assert row.get("banner") is None


# ── banner = full dict on success ───────────────────────────────────────────


def test_banner_dict_emitted_on_success(isolated, tmp_path):
    """Successful generation → ``banner = {path, alt, mime, sha}`` with
    file actually present at ``path``."""
    _seed_config_with_image_gen(isolated)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    b64 = base64.b64encode(_PNG).decode()
    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
        return_value=_post_ok({"data": [{"b64_json": b64}]}),
    ):
        _run_plan_backlinks(seeds, out)

    rows = _capture_outputs(out)
    assert rows
    banner = rows[0].get("banner")
    assert isinstance(banner, dict), f"expected dict, got {banner!r}"
    assert banner.get("path"), "banner.path missing"
    assert banner.get("mime") == "image/png"
    assert banner.get("sha")
    assert banner.get("alt") == rows[0]["title"]
    # R12 (Plan 2026-05-20-004 Unit 1): source_url MUST be present
    # in the emitted dict so the publish-time dispatcher can fall
    # back to it.  b64_json mode → value is None; url mode → URL
    # string.  Either way, the KEY must exist (a missing key would
    # be indistinguishable from a pre-R12 emission and break the
    # source_url-fallback path documented in AGENTS.md).
    assert "source_url" in banner, "R12: source_url key missing from banner dict"
    assert banner["source_url"] is None  # b64_json mode → no upstream URL


def test_banner_source_url_emitted_for_url_mode_response(isolated, tmp_path):
    """R12 (Plan 2026-05-20-004 Unit 1): when the image-gen provider
    returns a ``url`` (not ``b64_json``), that URL flows through to
    the JSONL ``banner.source_url`` field so the publish-time
    dispatcher can use it as a Medium-style fallback."""
    _seed_config_with_image_gen(isolated)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    upstream_url = "https://provider.cdn.example/banner-fixture.png"

    # url-mode response: the adapter fetches bytes from the URL.
    # We need TWO mocks: requests.post to /images/generations
    # returning the URL, and requests.get against that URL
    # returning the PNG bytes.
    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
        return_value=_post_ok({"data": [{"url": upstream_url}]}),
    ), patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_get",
        return_value=_get_ok_bytes(_PNG, "image/png"),
    ):
        _run_plan_backlinks(seeds, out)

    rows = _capture_outputs(out)
    banner = rows[0]["banner"]
    assert banner["source_url"] == upstream_url


def test_banner_does_not_alter_body(isolated, tmp_path):
    """Banner is emitted as separate field; ``content_markdown`` must
    NOT be prepended with ``![](...)``."""
    _seed_config_with_image_gen(isolated)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    b64 = base64.b64encode(_PNG).decode()
    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
        return_value=_post_ok({"data": [{"b64_json": b64}]}),
    ):
        _run_plan_backlinks(seeds, out)

    rows = _capture_outputs(out)
    body = rows[0]["content_markdown"]
    assert not body.lstrip().startswith("!["), (
        f"content_markdown must not be prepended with banner image: {body[:120]!r}"
    )


# ── Degraded paths ──────────────────────────────────────────────────────────


def test_banner_status_capped_per_run(isolated, tmp_path):
    """``per_run_cap = 0`` blocks immediately → ``banner.status =
    'capped:per_run_cap'`` and adapter is never invoked."""
    _seed_config_with_image_gen(isolated, per_run_cap=0)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
    ) as mock_post:
        _run_plan_backlinks(seeds, out)

    assert mock_post.call_count == 0
    rows = _capture_outputs(out)
    banner = rows[0].get("banner")
    assert banner == {"path": None, "status": "capped:per_run_cap"}


def test_banner_status_auth_failed_on_401(isolated, tmp_path):
    """Adapter raises ``RuntimeError`` with 'frw-login' on 401 →
    ``banner.status = 'auth_failed'``; body still emits."""
    import requests
    _seed_config_with_image_gen(isolated)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    bad = MagicMock()
    bad.status_code = 401
    bad.text = "no auth"
    bad.json.return_value = {"error": "bad key"}
    bad.raise_for_status = MagicMock(side_effect=requests.HTTPError("401"))

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
        return_value=bad,
    ):
        _run_plan_backlinks(seeds, out)

    rows = _capture_outputs(out)
    assert rows  # row still emitted (degraded mode)
    banner = rows[0].get("banner")
    assert banner == {"path": None, "status": "auth_failed"}


def test_banner_none_when_token_file_missing(isolated, tmp_path):
    """``[image_gen]`` configured but ``frw-token.json`` absent → the
    feature is silently disabled for the run (operator-actionable
    warning logged, but no crash).

    NOTE: This is graceful-degradation — the operator may have
    configured the section in advance of running ``frw-login``.
    """
    _seed_config_with_image_gen(isolated)
    # NO token file
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.http_post",
    ) as mock_post:
        _run_plan_backlinks(seeds, out)

    assert mock_post.call_count == 0
    rows = _capture_outputs(out)
    assert rows[0].get("banner") is None
