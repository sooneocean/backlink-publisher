"""Unit 6: LiveJournal XML-RPC adapter (Plan 2026-05-25-001) — dofollow keystone.

Covers publish happy/draft paths, credential at-rest security (0o600 + fail-loud
load), XML-RPC fault classification (auth → DependencyError, transport →
ExternalServiceError), the no-credential-leak-in-logs contract, the
fire-and-forget verify hook, and concurrent bootstrap-vs-rotation credential
writes. XML-RPC is fully mocked; no sockets.
"""

from __future__ import annotations

import json
import os
import threading
from unittest import mock
from xmlrpc.client import Fault, ProtocolError

import pytest

from backlink_publisher.config import Config
from backlink_publisher._util.errors import DependencyError, ExternalServiceError
from backlink_publisher.publishing.adapters import livejournal_api as lj
from backlink_publisher.publishing.adapters.livejournal_api import (
    LivejournalAPIAdapter,
    store_credentials,
)

_ADAPTER = "backlink_publisher.publishing.adapters.livejournal_api"

PAYLOAD = {
    "id": "lj-1",
    "title": "Hello LiveJournal",
    "content_markdown": "# Hi\n\nA [link](https://51acgs.com) here.\n",
    "target_url": "https://51acgs.com",
    "main_domain": "https://51acgs.com/",
}


@pytest.fixture
def isolated_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKLINK_PUBLISHER_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _mock_proxy(*, url="https://user.livejournal.com/123.html", postevent_side=None):
    """Build a MagicMock ServerProxy: getchallenge + postevent."""
    proxy = mock.MagicMock()
    proxy.LJ.XMLRPC.getchallenge.return_value = {"challenge": "CHAL-XYZ"}
    if postevent_side is not None:
        proxy.LJ.XMLRPC.postevent.side_effect = postevent_side
    else:
        proxy.LJ.XMLRPC.postevent.return_value = {"url": url, "itemid": 123, "anum": 7}
    return proxy


# ── happy paths ──────────────────────────────────────────────────────────────


def test_publish_happy_returns_published_url(isolated_config_dir):
    store_credentials(Config(), "tester", "pw")
    proxy = _mock_proxy(url="https://user.livejournal.com/999.html")
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy), mock.patch(
        f"{_ADAPTER}.attach_link_verification", return_value={"link_attr_verification": {"verification": "ok"}}
    ):
        res = LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())
    assert res.status == "published"
    assert res.published_url == "https://user.livejournal.com/999.html"
    assert res.adapter == "livejournal-api"
    assert res.platform == "livejournal"
    assert res._provider_meta["link_attr_verification"]["verification"] == "ok"


def test_publish_computes_challenge_response(isolated_config_dir):
    # auth_response must be md5(challenge + hpassword); assert the value the
    # adapter actually sends rather than trusting the happy-path URL alone.
    import hashlib

    store_credentials(Config(), "tester", "secretpw")
    hpassword = hashlib.md5(b"secretpw").hexdigest()
    expected = hashlib.md5(("CHAL-XYZ" + hpassword).encode()).hexdigest()
    proxy = _mock_proxy()
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy), mock.patch(
        f"{_ADAPTER}.attach_link_verification", return_value={}
    ):
        LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())
    sent = proxy.LJ.XMLRPC.postevent.call_args.args[0]
    assert sent["auth_response"] == expected
    assert sent["auth_method"] == "challenge"
    # The literal password must never be put on the wire.
    assert "secretpw" not in json.dumps(sent)


def test_publish_draft_mode_returns_draft_url(isolated_config_dir):
    store_credentials(Config(), "tester", "pw")
    proxy = _mock_proxy(url="https://user.livejournal.com/5.html")
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        res = LivejournalAPIAdapter().publish(PAYLOAD, mode="draft", config=Config())
    assert res.status == "drafted"
    assert res.draft_url == "https://user.livejournal.com/5.html"
    assert res.published_url == ""
    # Draft mode does not run the verify hook.
    assert res._provider_meta is None


# ── error paths ──────────────────────────────────────────────────────────────


def test_auth_fault_raises_dependency_error_no_password_leak(isolated_config_dir, caplog):
    # An XML-RPC fault whose faultString echoes the submitted password must NOT
    # leak it into the raised message or the logs (scrubber is extra-only).
    store_credentials(Config(), "tester", "hunter2pw")
    fault = Fault(101, "Invalid password for user tester: hunter2pw")
    proxy = _mock_proxy(postevent_side=fault)
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        with caplog.at_level("INFO"):
            with pytest.raises(DependencyError) as exc:
                LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())
    assert "hunter2pw" not in str(exc.value)
    assert "hunter2pw" not in caplog.text


def test_non_auth_fault_raises_external_service_error(isolated_config_dir):
    fault = Fault(211, "Client error: Some unexpected server condition")
    store_credentials(Config(), "tester", "pw")
    proxy = _mock_proxy(postevent_side=fault)
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        with pytest.raises(ExternalServiceError):
            LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


def test_protocol_error_raises_external_service_error(isolated_config_dir):
    store_credentials(Config(), "tester", "pw")
    err = ProtocolError("livejournal.com/interface/xmlrpc", 503, "Service Unavailable", {})
    proxy = _mock_proxy(postevent_side=err)
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        with pytest.raises(ExternalServiceError):
            LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


def test_empty_url_in_response_raises(isolated_config_dir):
    store_credentials(Config(), "tester", "pw")
    proxy = _mock_proxy()
    proxy.LJ.XMLRPC.postevent.return_value = {"itemid": 1}  # no url
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        with pytest.raises(ExternalServiceError, match="no url"):
            LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


def test_missing_credentials_raises_dependency_error(isolated_config_dir):
    proxy = _mock_proxy()
    with mock.patch(f"{_ADAPTER}.ServerProxy", return_value=proxy):
        with pytest.raises(DependencyError, match="credentials not found"):
            LivejournalAPIAdapter().publish(PAYLOAD, mode="publish", config=Config())


# ── credential at-rest security ──────────────────────────────────────────────


def test_store_credentials_writes_0600_and_hashes_password(isolated_config_dir):
    path = store_credentials(Config(), "tester", "plaintextpw")
    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600
    data = json.loads(path.read_text())
    assert data["username"] == "tester"
    # Plaintext password is never written; only the md5 hpassword.
    assert "plaintextpw" not in path.read_text()
    import hashlib

    assert data["hpassword"] == hashlib.md5(b"plaintextpw").hexdigest()


def test_load_credentials_rejects_world_readable_file(isolated_config_dir):
    path = store_credentials(Config(), "tester", "pw")
    os.chmod(path, 0o644)
    with pytest.raises(DependencyError, match="0600"):
        lj._load_credentials(Config())


def test_store_credentials_empty_inputs_raise(isolated_config_dir):
    with pytest.raises(DependencyError):
        store_credentials(Config(), "", "pw")
    with pytest.raises(DependencyError):
        store_credentials(Config(), "tester", "")


def test_store_credentials_resets_perms_on_preexisting_loose_file(isolated_config_dir):
    # A pre-existing loose-mode file must be tightened to 0600 after rotation.
    path = lj._credentials_path(Config())
    path.write_text(json.dumps({"username": "old", "hpassword": "x"}))
    os.chmod(path, 0o666)
    store_credentials(Config(), "tester", "newpw")  # rotation overwrite
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_concurrent_bootstrap_and_rotation_no_torn_write(isolated_config_dir):
    # bootstrap and rotation are the two credential mutation sites; run them
    # concurrently and assert the file is never torn and ends 0600 with one
    # of the two writers' values (os.replace atomicity, single write primitive).
    cfg = Config()
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def writer(username: str, password: str):
        try:
            barrier.wait(timeout=5)
            store_credentials(cfg, username, password)
        except Exception as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    t1 = threading.Thread(target=writer, args=("userA", "pwA"))
    t2 = threading.Thread(target=writer, args=("userB", "pwB"))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert not errors
    path = lj._credentials_path(cfg)
    data = json.loads(path.read_text())  # parses cleanly == not torn
    assert data["username"] in ("userA", "userB")
    assert (os.stat(path).st_mode & 0o777) == 0o600


# ── registry integration ─────────────────────────────────────────────────────


def test_registered_false_with_referral_and_rationale():
    # Canary 2026-05-29: nofollow confirmed; flipped from "uncertain" to False.
    from backlink_publisher.publishing import registry as R

    assert "livejournal" in R.registered_platforms()
    assert R.dofollow_status("livejournal") is False
    assert R.referral_value("livejournal") == "high"
    assert len((R.dofollow_rationale("livejournal") or "").strip()) >= 80


def test_load_credentials_corrupt_json_omits_raw_exception(isolated_config_dir):
    """A corrupt credentials file must surface a generic DependencyError, not the
    raw JSONDecodeError text (which echoes a snippet of the file contents)."""
    path = store_credentials(Config(), "tester", "pw")
    path.write_text("{ this is not valid json — hpassword leak risk ")
    os.chmod(path, 0o600)
    with pytest.raises(DependencyError) as exc:
        lj._load_credentials(Config())
    msg = str(exc.value)
    assert msg == "Cannot parse LiveJournal credentials: file corrupt or unreadable"
    # raw JSONDecodeError detail (position/snippet) must NOT leak
    assert "Expecting" not in msg
    assert "char" not in msg
