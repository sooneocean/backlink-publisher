"""Hatena AtomPub adapter tests (mocked HTTP — no live API).

Covers the publish happy path, draft vs publish status, credential
failure modes (missing / loose-perm / corrupt → DependencyError, no secret
leak), HTTP 401 surfacing, WSSE digest correctness, XML escaping, and the
PR #323 invariant that a network error on the non-idempotent create POST is
NOT retried (which would risk a duplicate entry).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters.base import AdapterResult
from backlink_publisher.publishing.adapters.hatena_atompub import (
    HatenaAtomPubAdapter,
    _build_entry_xml,
    _build_wsse_header,
    _load_credentials,
    _parse_entry_url,
)

_POST = "backlink_publisher.publishing.adapters.hatena_atompub.requests.post"


@pytest.fixture
def config_with_creds(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "hatena-credentials.json"
    cred.write_text(
        json.dumps(
            {
                "hatena_id": "alice",
                "blog_id": "alice.hatenablog.com",
                "api_key": "supersecretkey",
            }
        )
    )
    os.chmod(cred, 0o600)
    return cfg


def _payload():
    return {
        "id": "a1",
        "title": "Hello",
        "content_markdown": "body [x](https://t.example)",
    }


def _created_resp():
    resp = MagicMock()
    resp.status_code = 201
    resp.text = (
        '<?xml version="1.0"?>'
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        "<title>Hello</title>"
        '<link rel="edit" href="https://blog.hatena.ne.jp/alice/x/atom/entry/99"/>'
        '<link rel="alternate" type="text/html" '
        'href="https://alice.hatenablog.com/entry/2026/05/29/120000"/>'
        "</entry>"
    )
    return resp


def test_publish_returns_published_url(config_with_creds):
    with patch(_POST, return_value=_created_resp()) as post:
        result = HatenaAtomPubAdapter().publish(
            _payload(), "publish", config_with_creds
        )
    assert isinstance(result, AdapterResult)
    assert result.status == "published"
    assert (
        result.published_url == "https://alice.hatenablog.com/entry/2026/05/29/120000"
    )
    assert result.platform == "hatena"
    # the request carried a WSSE header and atom+xml content type
    _, kwargs = post.call_args
    assert kwargs["headers"]["X-WSSE"].startswith("UsernameToken ")
    assert "atom+xml" in kwargs["headers"]["Content-Type"]
    assert b"<app:draft>no</app:draft>" in kwargs["data"]


def test_draft_mode_reports_drafted(config_with_creds):
    with patch(_POST, return_value=_created_resp()) as post:
        result = HatenaAtomPubAdapter().publish(_payload(), "draft", config_with_creds)
    assert result.status == "drafted"
    assert result.published_url == ""
    assert result.draft_url == "https://alice.hatenablog.com/entry/2026/05/29/120000"
    _, kwargs = post.call_args
    assert b"<app:draft>yes</app:draft>" in kwargs["data"]


def test_missing_credentials_raises_dependency_error(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    with pytest.raises(DependencyError):
        HatenaAtomPubAdapter().publish(_payload(), "publish", cfg)


def test_loose_permissions_raise(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "hatena-credentials.json"
    cred.write_text(json.dumps({"hatena_id": "a", "blog_id": "b", "api_key": "k"}))
    os.chmod(cred, 0o644)
    with pytest.raises(DependencyError) as exc:
        _load_credentials(cfg)
    assert "0600" in str(exc.value)


def test_corrupt_credentials_no_secret_leak(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "hatena-credentials.json"
    cred.write_text('{ "api_key": leaktokenvalue broken ')
    os.chmod(cred, 0o600)
    with pytest.raises(DependencyError) as exc:
        _load_credentials(cfg)
    msg = str(exc.value)
    assert msg == "Cannot read Hatena credentials: file missing, corrupt, or unreadable"
    assert "leaktokenvalue" not in msg


def test_incomplete_credentials_raise(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "hatena-credentials.json"
    cred.write_text(json.dumps({"hatena_id": "a", "blog_id": "b", "api_key": ""}))
    os.chmod(cred, 0o600)
    with pytest.raises(DependencyError) as exc:
        _load_credentials(cfg)
    assert "incomplete" in str(exc.value)


def test_401_raises_external_service_error(config_with_creds):
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "<error/>"
    with patch(_POST, return_value=resp):
        with pytest.raises(ExternalServiceError) as exc:
            HatenaAtomPubAdapter().publish(_payload(), "publish", config_with_creds)
    assert "401" in str(exc.value)
    assert "{resp.status_code}" not in str(exc.value)


def test_network_error_not_retried(config_with_creds):
    """PR #323: the create POST is non-idempotent — a network error must
    fail once, never retry (a retry could duplicate the live entry)."""
    with patch(_POST, side_effect=requests.ConnectionError("boom")) as post:
        with pytest.raises(ExternalServiceError):
            HatenaAtomPubAdapter().publish(_payload(), "publish", config_with_creds)
    assert post.call_count == 1


def test_wsse_header_digest_is_correct():
    nonce = b"0123456789abcdef"
    created = "2026-05-29T00:00:00Z"
    header = _build_wsse_header("alice", "secret", nonce=nonce, created=created)
    expected_digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + b"secret").digest()
    ).decode()
    assert f'PasswordDigest="{expected_digest}"' in header
    assert f'Nonce="{base64.b64encode(nonce).decode()}"' in header
    assert 'Username="alice"' in header
    assert f'Created="{created}"' in header


def test_entry_xml_escapes_title_and_content():
    xml = _build_entry_xml("A & B <tag>", "x < y & z", draft=False)
    assert "A &amp; B &lt;tag&gt;" in xml
    assert "x &lt; y &amp; z" in xml
    assert "<app:draft>no</app:draft>" in xml
    assert 'type="text/x-markdown"' in xml


def test_parse_entry_url_prefers_text_html_alternate():
    xml = (
        '<entry xmlns="http://www.w3.org/2005/Atom">'
        '<link rel="alternate" type="application/atom+xml" href="https://wrong/"/>'
        '<link rel="alternate" type="text/html" href="https://right/post"/>'
        "</entry>"
    )
    assert _parse_entry_url(xml) == "https://right/post"


def test_available_reflects_cred_file(config_with_creds, tmp_path):
    assert HatenaAtomPubAdapter.available(config_with_creds) is True
    empty = MagicMock()
    empty.config_dir = tmp_path / "nope"
    (tmp_path / "nope").mkdir()
    assert HatenaAtomPubAdapter.available(empty) is False
