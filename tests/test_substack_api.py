"""Substack adapter tests — P1#8 honest-draft-status regression.

The adapter only creates a draft via POST /api/v1/drafts; reporting
status="published" in publish mode was a lie the events projector would
miscount as a live backlink.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backlink_publisher._util.errors import ExternalServiceError
from backlink_publisher.publishing.adapters.substack_api import SubstackAPIAdapter
from backlink_publisher.publishing.adapters.base import AdapterResult


@pytest.fixture
def config_with_cookies(tmp_path):
    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "substack-credentials.json"
    cred.write_text(json.dumps({"cookies": [{"name": "sid", "value": "x"}]}))
    os.chmod(cred, 0o600)
    return cfg


def _payload():
    return {"id": "a1", "title": "Hi", "content_markdown": "<p>b</p>", "meta": {}}


def _draft_resp():
    resp = MagicMock()
    resp.status_code = 201
    resp.json.return_value = {"id": 555, "url": "https://x.substack.com/p/draft"}
    resp.text = ""
    return resp


@pytest.mark.parametrize("mode", ["draft", "publish"])
def test_status_always_drafted(config_with_cookies, mode):
    """P1#8: even in publish mode the adapter only drafts, so it must NOT
    claim 'published'."""
    with patch(
        "backlink_publisher.publishing.adapters.substack_api.requests.post",
        return_value=_draft_resp(),
    ):
        result = SubstackAPIAdapter().publish(_payload(), mode, config_with_cookies)
    assert isinstance(result, AdapterResult)
    assert result.status == "drafted"


def test_401_error_interpolates_status_code(config_with_cookies):
    """f-prefix regression: the error must contain '401', not the literal
    '{resp.status_code}'."""
    resp = MagicMock()
    resp.status_code = 401
    resp.text = "no"
    with patch(
        "backlink_publisher.publishing.adapters.substack_api.requests.post",
        return_value=resp,
    ):
        with pytest.raises(ExternalServiceError) as exc:
            SubstackAPIAdapter().publish(_payload(), "publish", config_with_cookies)
    assert "401" in str(exc.value)
    assert "{resp.status_code}" not in str(exc.value)


def test_load_cookies_corrupt_json_omits_raw_exception(tmp_path):
    """A corrupt cookies file must surface a generic DependencyError, not the raw
    JSONDecodeError text (which echoes a snippet of the cookie file contents)."""
    from backlink_publisher._util.errors import DependencyError
    from backlink_publisher.publishing.adapters.substack_api import _load_cookies

    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cred = tmp_path / "substack-credentials.json"
    cred.write_text('{ "cookies": [ broken sid=secretvalue ')
    os.chmod(cred, 0o600)
    with pytest.raises(DependencyError) as exc:
        _load_cookies(cfg)
    msg = str(exc.value)
    assert msg == "Cannot read Substack credentials: file missing, corrupt, or unreadable"
    assert "secretvalue" not in msg
    assert "Expecting" not in msg
