"""Unit 7c — manifest force-flags (R11, R14). On an enforce run, --force-manifest
honors force-flagged rows from a preview manifest: re-publish a held (uncertain)
key. Guards: store-token binding, --confirm count, --reason; a force on a done key
is a surfaced conflict (R11). Each honored force writes a --forget-parity audit
entry.

Plan: docs/plans/2026-05-27-005-feat-cross-run-publish-idempotency-plan.md (U7).
"""

from __future__ import annotations

import json
import os
import sys
from io import StringIO
from unittest.mock import patch

import pytest

from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.cli.publish_backlinks import main
from backlink_publisher.idempotency import DedupKey, DedupStore
from backlink_publisher.idempotency import audit_log
from backlink_publisher.linkcheck.verify import VerificationResult

_ENFORCE = "BACKLINK_PUBLISHER_DEDUP_ENFORCE"
_TARGET = "https://example.com/article"


@pytest.fixture(autouse=True)
def _fresh_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv(_ENFORCE, raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def _verify_pass(mocker):
    mocker.patch(
        "backlink_publisher.cli._publish_helpers.verify_published",
        return_value=VerificationResult(ok=True, reason=""),
    )


def _payload(target=_TARGET, item_id="r1", platform="medium"):
    return {
        "id": item_id, "platform": platform, "language": "en",
        "publish_mode": "draft", "target_url": target,
        "main_domain": "https://example.com", "url_mode": "A",
        "title": "T", "slug": "t", "excerpt": "e", "tags": ["a", "b"],
        "content_markdown": f"About {target} and https://example.com.",
        "links": [
            {"url": "https://example.com", "anchor": "E", "kind": "main_domain", "required": True},
            {"url": target, "anchor": "A", "kind": "target", "required": True},
            {"url": "https://wikipedia.org", "anchor": "W", "kind": "supporting", "required": False},
            {"url": "https://mdn.dev", "anchor": "M", "kind": "supporting", "required": False},
            {"url": "https://stackoverflow.com", "anchor": "S", "kind": "supporting", "required": False},
            {"url": "https://github.com", "anchor": "G", "kind": "supporting", "required": False},
        ],
        "seo": {"title": "S", "description": "d", "canonical_url": target},
    }


def _drafted():
    return AdapterResult(
        status="drafted", adapter="medium-api", platform="medium",
        draft_url="https://medium.com/p/new", published_url="",
    )


def _seed(state, *, platform="medium", target=_TARGET, live_url=None):
    store = DedupStore()
    key = DedupKey(platform=platform, target_url=target)
    store.intent_write(key)
    if state != "attempting":
        store.transition(key, state, live_url=live_url)
    return key


def _write_manifest(tmp_path, *, platform="medium", target=_TARGET, force=True, token=None):
    if token is None:
        token = DedupStore().store_token()
    key = DedupKey(platform=platform, target_url=target)
    entry = {
        "id": "r1", "platform": key.platform, "account": key.account,
        "target_url": key.target_url, "key_digest": "x", "state": "uncertain",
        "verdict": "HOLD-UNCERTAIN", "live_url": None, "run_id": None,
        "force": force, "store_token": token,
    }
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(entry) + "\n")
    return str(path)


def _run(rows, argv, enforce=True):
    if enforce:
        os.environ[_ENFORCE] = "1"
    old = (sys.stdin, sys.stdout, sys.stderr)
    try:
        sys.stdin = StringIO("\n".join(json.dumps(r) for r in rows))
        out, err = StringIO(), StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return out.getvalue(), err.getvalue(), code
    finally:
        sys.stdin, sys.stdout, sys.stderr = old
        os.environ.pop(_ENFORCE, None)


# --------------------------------------------------------------------------- #
# Honored force
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_force_republishes_held_uncertain(mock_pub, _mv, _fresh_dir):
    _seed("uncertain")
    mock_pub.return_value = _drafted()
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "1",
         "--reason", "operator confirmed not-landed"],
    )
    assert code == 0, stderr
    mock_pub.assert_called_once()  # held key forced to dispatch
    # honored force writes a --forget-parity audit entry.
    entries = [e for e in audit_log.read_entries() if e["action"] == "force"]
    assert len(entries) == 1
    assert entries[0]["from_state"] == "uncertain"
    assert entries[0]["reason"] == "operator confirmed not-landed"


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_force_on_done_key_is_conflict(mock_pub, _mv, _fresh_dir):
    _seed("done", live_url="https://medium.com/p/old")
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "1",
         "--reason", "x"],
    )
    assert code == 1
    assert "conflict" in stderr.lower()
    mock_pub.assert_not_called()  # never re-publishes a live key


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_store_token_mismatch_rejected(mock_pub, _mv, _fresh_dir):
    _seed("uncertain")
    manifest = _write_manifest(_fresh_dir, token="deadbeefdeadbeef")
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "1",
         "--reason", "x"],
    )
    assert code == 1
    assert "store_token mismatch" in stderr
    mock_pub.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_confirm_count_mismatch_rejected(mock_pub, _mv, _fresh_dir):
    _seed("uncertain")
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "5",
         "--reason", "x"],
    )
    assert code == 1
    assert "--confirm 1" in stderr
    mock_pub.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_force_manifest_requires_reason(mock_pub, _mv, _fresh_dir):
    _seed("uncertain")
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "1"],
    )
    assert code == 1
    assert "reason" in stderr.lower()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_force_manifest_requires_enforce(mock_pub, _mv, _fresh_dir):
    _seed("uncertain")
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [_payload()],
        ["--platform", "medium", "--force-manifest", manifest, "--confirm", "1",
         "--reason", "x"],
        enforce=False,
    )
    assert code == 1
    assert "DEDUP_ENFORCE" in stderr
    mock_pub.assert_not_called()


@patch("backlink_publisher.cli.publish_backlinks.verify_adapter_setup")
@patch("backlink_publisher.cli.publish_backlinks.adapter_publish")
def test_unforced_held_key_still_holds_alongside_forced(mock_pub, _mv, _fresh_dir):
    """A held key NOT in the manifest stays held even when another is forced."""
    _seed("uncertain", platform="medium", target=_TARGET)
    _seed("uncertain", platform="velog", target="https://example.com/other")
    mock_pub.return_value = _drafted()
    manifest = _write_manifest(_fresh_dir)  # forces only the medium key
    _out, stderr, code = _run(
        [_payload(), _payload(target="https://example.com/other", item_id="r2", platform="velog")],
        ["--force-manifest", manifest, "--confirm", "1", "--reason", "x"],
    )
    # medium forced -> dispatched; velog not in manifest -> still held.
    assert mock_pub.call_count == 1
    assert DedupStore().get(
        DedupKey(platform="velog", target_url="https://example.com/other")
    ).state == "uncertain"


# --------------------------------------------------------------------------- #
# Manifest emits a store_token (binding source)
# --------------------------------------------------------------------------- #
def test_preview_manifest_emits_store_token(_fresh_dir):
    from backlink_publisher.cli.preview_manifest import emit_manifest

    old = sys.stdout
    try:
        out = StringIO()
        sys.stdout = out
        emit_manifest([_payload()], None)
    finally:
        sys.stdout = old
    entry = json.loads(out.getvalue().strip())
    assert entry["store_token"] == DedupStore().store_token()
    assert entry["force"] is False


def test_force_manifest_conflicts_with_resume(_fresh_dir):
    """--force-manifest is not honored on the resume seam, so the combination is
    rejected (exit 2) rather than silently dropping the operator's force-flags."""
    manifest = _write_manifest(_fresh_dir)
    _out, stderr, code = _run(
        [],
        ["--resume", "20260101T000000-deadbeef", "--force-manifest", manifest,
         "--confirm", "1", "--reason", "x"],
    )
    assert code == 2
    assert "mutually exclusive" in stderr
