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


_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


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
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
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
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
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
    # File actually written
    assert Path(banner["path"]).exists()
    assert Path(banner["path"]).read_bytes() == _PNG


def test_banner_does_not_alter_body(isolated, tmp_path):
    """Banner is emitted as separate field; ``content_markdown`` must
    NOT be prepended with ``![](...)``."""
    _seed_config_with_image_gen(isolated)
    _seed_token(isolated)
    seeds = _seed_input(tmp_path)
    out = tmp_path / "out.jsonl"

    b64 = base64.b64encode(_PNG).decode()
    with patch(
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
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
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
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
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
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
        "backlink_publisher.publishing.adapters.image_gen.adapter.requests.post",
    ) as mock_post:
        _run_plan_backlinks(seeds, out)

    assert mock_post.call_count == 0
    rows = _capture_outputs(out)
    assert rows[0].get("banner") is None
