"""Unit 7: txt.fyi form-POST adapter (Plan 2026-05-25-001).

Covers publish happy/draft paths, missing content, missing hidden fields,
redirect failure, anti-bot challenge propagation, and the fire-and-forget
link verification hook. All HTTP is mocked — no real network.
"""

from __future__ import annotations

from unittest import mock

import pytest

from backlink_publisher._util.errors import AntiBotChallengeError, ExternalServiceError
from backlink_publisher.config import Config
from backlink_publisher.publishing.adapters.txtfyi_api import (
    TxtfyiFormPostAdapter,
)

_ADAPTER = "backlink_publisher.publishing.adapters.txtfyi_api"

_PAYLOAD = {
    "id": "txt-1",
    "title": "Hello txt.fyi",
    "content_markdown": "# Hi\n\nA [link](https://example.com) here.\n",
    "target_url": "https://example.com",
    "main_domain": "https://example.com/",
}

_FORM_HTML_WITH_TOKENS = (
    '<form action="edit.php" method="post">'
    '<input name="nonce" type="hidden" value="a1b2c3,1234567890,def456">'
    '<input name="form_time" type="hidden" value="1234567890">'
    '<input name="url" type="url">'
    '<textarea name="txt"></textarea>'
    '<input name="go" type="submit" value="PUBLISH">'
    "</form>"
)

_FORM_HTML_MISSING_NONCE = (
    '<form action="edit.php" method="post">'
    '<input name="form_time" type="hidden" value="1234567890">'
    '<textarea name="txt"></textarea>'
    '<input name="go" type="submit" value="PUBLISH">'
    "</form>"
)


def _mock_response(*, status=200, text="", url="https://txt.fyi/"):
    """Build a requests.Response-like mock."""
    resp = mock.MagicMock()
    resp.status_code = status
    resp.text = text
    resp.url = url
    resp.headers = {"server": "cloudflare"}
    return resp


@pytest.fixture(autouse=True)
def _zero_submit_delay(monkeypatch):
    """Neutralize the txt.fyi anti-spam dwell-time wait in unit tests.

    Production waits a few seconds before POSTing (see ``_SUBMIT_DELAY_ENV``);
    forcing 0 here keeps the mocked-HTTP tests instant. The dwell-time test
    below ``delenv``s this to exercise the real default.
    """
    monkeypatch.setenv("BACKLINK_TXTFYI_SUBMIT_DELAY_SECONDS", "0")


# ── happy paths ────────────────────────────────────────────────────────────


def test_publish_happy_returns_published_url():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/abcd1234/",
    )

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ) as mock_fetch, mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit, mock.patch(
        f"{_ADAPTER}.attach_link_verification",
        return_value={"link_attr_verification": {"verification": "ok"}},
    ):
        res = TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())

    assert res.status == "published"
    assert res.published_url == "https://txt.fyi/+/abcd1234/"
    assert res.adapter == "txtfyi-form-post"
    assert res.platform == "txtfyi"
    assert res._provider_meta["link_attr_verification"]["verification"] == "ok"

    # Verify the form was fetched and submitted with the right data.
    mock_fetch.assert_called_once_with("https://txt.fyi/")
    assert mock_submit.call_args.args[0] == "https://txt.fyi/edit.php"
    data = mock_submit.call_args.args[1]
    assert "nonce" in data
    assert "form_time" in data
    assert "txt" in data
    assert "# Hello txt.fyi" in data["txt"]
    assert data["go"] == "PUBLISH"


def test_publish_draft_mode_returns_draft_url():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/draft123/",
    )

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit, mock.patch(
        f"{_ADAPTER}.attach_link_verification",
    ) as mock_verify:
        res = TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="draft", config=Config())

    assert res.status == "drafted"
    assert res.draft_url == "https://txt.fyi/+/draft123/"
    assert res.published_url == ""
    # Draft mode does not call the verify hook.
    assert res._provider_meta is None
    mock_verify.assert_not_called()


def test_publish_without_title_prepends_no_heading():
    """When title is empty, the body should NOT get a '# ' prefix."""
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/+/no-title/",
    )
    payload_no_title = dict(_PAYLOAD, title="")

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ) as mock_submit:
        TxtfyiFormPostAdapter().publish(payload_no_title, mode="publish", config=Config())

    sent_body = mock_submit.call_args.args[1]["txt"]
    assert sent_body.startswith("# Hi"), (
        "Body should not get '# ' prefix when title is empty"
    )


# ── error paths ────────────────────────────────────────────────────────────


def test_empty_content_raises_external_service_error():
    payload_empty = dict(_PAYLOAD, content_markdown="")
    with pytest.raises(ExternalServiceError, match="has no content_markdown"):
        TxtfyiFormPostAdapter().publish(payload_empty, mode="publish", config=Config())


def test_missing_hidden_fields_raises_external_service_error():
    form_resp = _mock_response(text=_FORM_HTML_MISSING_NONCE)

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp):
        with pytest.raises(ExternalServiceError, match="missing hidden fields"):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_no_redirect_raises_external_service_error():
    """If submit_form returns the submit URL instead of a redirect, raise."""
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    # .url stays at the submit endpoint — no redirect happened.
    submit_resp = _mock_response(url="https://txt.fyi/edit.php")

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ):
        with pytest.raises(ExternalServiceError, match="did not redirect"):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_anti_spam_tarpit_page_raises_actionable_error():
    """A 200 'Thank you' tarpit (no redirect) → clear anti-spam error, not the
    generic no-redirect message, so the operator knows to raise the dwell time."""
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(
        url="https://txt.fyi/edit.php",
        text="<p>Thank you for your submission!<p># Hi\n\nbody echoed back",
    )

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ):
        with pytest.raises(ExternalServiceError, match="anti-spam dwell-time"):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_publish_waits_to_clear_anti_spam_gate_before_submit(monkeypatch):
    """Publish sleeps a positive, gate-clearing dwell time BEFORE submitting."""
    # Exercise the real default rather than the autouse 0-override.
    monkeypatch.delenv("BACKLINK_TXTFYI_SUBMIT_DELAY_SECONDS", raising=False)
    order: list[str] = []
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(url="https://txt.fyi/+/ok/")

    with mock.patch(
        f"{_ADAPTER}.fetch_form", return_value=form_resp
    ), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=lambda *a, **k: (order.append("submit"), submit_resp)[1],
    ), mock.patch(
        f"{_ADAPTER}.time.sleep", side_effect=lambda *_: order.append("sleep")
    ) as mock_sleep, mock.patch(
        f"{_ADAPTER}.attach_link_verification", return_value={}
    ):
        TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())

    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args.args[0]
    assert waited >= 3.0, f"dwell time {waited}s too short to clear txt.fyi gate"
    assert order == ["sleep", "submit"], "must wait BEFORE submitting, not after"


def test_submit_delay_env_override_respected(monkeypatch):
    """The dwell time is operator-tunable via env (here: a custom 9s)."""
    monkeypatch.setenv("BACKLINK_TXTFYI_SUBMIT_DELAY_SECONDS", "9")
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    submit_resp = _mock_response(url="https://txt.fyi/+/ok/")

    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form", return_value=submit_resp
    ), mock.patch(f"{_ADAPTER}.time.sleep") as mock_sleep, mock.patch(
        f"{_ADAPTER}.attach_link_verification", return_value={}
    ):
        TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())

    mock_sleep.assert_called_once_with(9.0)


def test_anti_bot_challenge_on_fetch_form_propagates():
    """AntiBotChallengeError from fetch_form must propagate (not catch)."""
    with mock.patch(
        f"{_ADAPTER}.fetch_form",
        side_effect=AntiBotChallengeError("bot challenge on GET txt.fyi"),
    ):
        with pytest.raises(AntiBotChallengeError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_anti_bot_challenge_on_submit_form_propagates():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=AntiBotChallengeError("bot challenge on POST txt.fyi"),
    ):
        with pytest.raises(AntiBotChallengeError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())


def test_external_service_error_from_submit_propagates():
    form_resp = _mock_response(text=_FORM_HTML_WITH_TOKENS)
    with mock.patch(f"{_ADAPTER}.fetch_form", return_value=form_resp), mock.patch(
        f"{_ADAPTER}.submit_form",
        side_effect=ExternalServiceError("HTTP 500 from txt.fyi"),
    ):
        with pytest.raises(ExternalServiceError):
            TxtfyiFormPostAdapter().publish(_PAYLOAD, mode="publish", config=Config())
