"""Unit 4: pure-HTTP form-POST helper (Plan 2026-05-25-001).

Covers the transport contract for the credential-less form-POST helpers:
GET/POST happy paths, hidden-field extraction (CSRF nonce + timestamps),
challenge detection (including the Cloudflare-beacon false-positive guard),
the AntiBotChallengeError-vs-DependencyError distinction, and the
fire-and-forget post-publish link verification hook. All network is mocked —
no sockets, no ``real_*`` marker needed.
"""

from __future__ import annotations

from unittest import mock

import pytest

from backlink_publisher._util.errors import (
    AntiBotChallengeError,
    DependencyError,
    ExternalServiceError,
)
from backlink_publisher.publishing.adapters import http_form_post as hfp


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, *, status_code: int = 200, text: str = "", server: str = "") -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"server": server} if server else {}


# --------------------------------------------------------------------------- #
# detect_challenge
# --------------------------------------------------------------------------- #


def test_detect_challenge_cloudflare_503_just_a_moment() -> None:
    resp = _Resp(status_code=503, text="<title>Just a moment...</title>", server="cloudflare")
    assert hfp.detect_challenge(resp) is True


def test_detect_challenge_captcha_marker_on_200() -> None:
    # Some challenges return 200 HTML — body marker alone is enough.
    resp = _Resp(status_code=200, text='<div class="g-recaptcha"></div>')
    assert hfp.detect_challenge(resp) is True


def test_detect_challenge_ignores_cloudflare_beacon_on_real_200_form() -> None:
    # txt.fyi serves a real publish form on a 200 page that ALSO carries the
    # /cdn-cgi/challenge-platform/ beacon script. That must NOT be flagged.
    body = (
        '<form method="post" action="edit.php"><textarea name="txt"></textarea>'
        '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script></form>'
    )
    resp = _Resp(status_code=200, text=body, server="cloudflare")
    assert hfp.detect_challenge(resp) is False


def test_detect_challenge_plain_403_without_cf_or_marker_is_not_challenge() -> None:
    # A bare 403 from a non-edge server with no interstitial markers is a normal
    # HTTP error, surfaced as ExternalServiceError by the callers, not a challenge.
    resp = _Resp(status_code=403, text="forbidden", server="nginx")
    assert hfp.detect_challenge(resp) is False


# --------------------------------------------------------------------------- #
# fetch_form
# --------------------------------------------------------------------------- #


def test_fetch_form_happy_path_returns_response() -> None:
    resp = _Resp(status_code=200, text="<form></form>")
    with mock.patch.object(hfp.requests, "get", return_value=resp) as mget:
        out = hfp.fetch_form("https://txt.fyi/")
    assert out is resp
    assert mget.call_args.kwargs["headers"]["User-Agent"].endswith("backlink-publisher")


def test_fetch_form_challenge_raises_antibot() -> None:
    resp = _Resp(status_code=503, text="Attention Required! | Cloudflare", server="cloudflare")
    with mock.patch.object(hfp.requests, "get", return_value=resp):
        with pytest.raises(AntiBotChallengeError):
            hfp.fetch_form("https://txt.fyi/")


def test_fetch_form_http_error_raises_external_service() -> None:
    resp = _Resp(status_code=404, text="not found")
    with mock.patch.object(hfp.requests, "get", return_value=resp):
        with pytest.raises(ExternalServiceError):
            hfp.fetch_form("https://txt.fyi/")


def test_fetch_form_network_error_raises_external_service_no_body_leak() -> None:
    with mock.patch.object(hfp.requests, "get", side_effect=OSError("connection reset to 10.0.0.1")):
        with pytest.raises(ExternalServiceError) as exc:
            hfp.fetch_form("https://txt.fyi/secret-path")
    msg = str(exc.value)
    assert "secret-path" not in msg and "10.0.0.1" not in msg
    assert "txt.fyi" in msg


# --------------------------------------------------------------------------- #
# extract_hidden_fields
# --------------------------------------------------------------------------- #


def test_extract_hidden_fields_pulls_nonce_and_form_time() -> None:
    html = (
        '<form method="post" action="edit.php">'
        '<input name="url" type="url" value="">'
        '<textarea name="txt"></textarea>'
        '<input name="nonce" type="hidden" value="abc,123,deadbeef"/>'
        '<input name="form_time" type="hidden" value="1779679194"/>'
        "</form>"
    )
    fields = hfp.extract_hidden_fields(html, ["nonce", "form_time"])
    assert fields == {"nonce": "abc,123,deadbeef", "form_time": "1779679194"}


def test_extract_hidden_fields_missing_name_absent_not_error() -> None:
    html = '<input name="nonce" value="x"/>'
    fields = hfp.extract_hidden_fields(html, ["nonce", "csrf"])
    assert fields == {"nonce": "x"}
    assert "csrf" not in fields


def test_extract_hidden_fields_value_absent_becomes_empty_string() -> None:
    html = '<input name="nonce" type="hidden">'
    assert hfp.extract_hidden_fields(html, ["nonce"]) == {"nonce": ""}


# --------------------------------------------------------------------------- #
# submit_form
# --------------------------------------------------------------------------- #


def test_submit_form_happy_path() -> None:
    resp = _Resp(status_code=200, text='<a href="https://txt.fyi/abc/def">link</a>')
    with mock.patch.object(hfp.requests, "post", return_value=resp) as mpost:
        out = hfp.submit_form("https://txt.fyi/edit.php", {"txt": "hi", "go": "PUBLISH"})
    assert out is resp
    assert mpost.call_args.kwargs["data"]["go"] == "PUBLISH"


def test_submit_form_challenge_raises_antibot_distinct_from_dependency() -> None:
    # The whole point of AntiBotChallengeError: a challenge must be
    # distinguishable from "platform not configured" (DependencyError).
    resp = _Resp(status_code=403, text="Just a moment...", server="cloudflare")
    with mock.patch.object(hfp.requests, "post", return_value=resp):
        with pytest.raises(AntiBotChallengeError) as exc:
            hfp.submit_form("https://txt.fyi/edit.php", {"txt": "hi"})
    assert not isinstance(exc.value, DependencyError)
    assert isinstance(exc.value, ExternalServiceError)


def test_submit_form_http_error_raises_external_service_no_body_leak() -> None:
    resp = _Resp(status_code=500, text="<html>stacktrace with secret token tok_123</html>")
    with mock.patch.object(hfp.requests, "post", return_value=resp):
        with pytest.raises(ExternalServiceError) as exc:
            hfp.submit_form("https://txt.fyi/edit.php", {"txt": "secret body content"})
    msg = str(exc.value)
    assert "tok_123" not in msg and "secret body content" not in msg
    assert "HTTP 500" in msg


def test_submit_form_retries_transient_network_error_then_succeeds() -> None:
    resp = _Resp(status_code=200, text="ok")
    calls = {"n": 0}

    def _post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise hfp.requests.exceptions.ConnectionError("transient")
        return resp

    with mock.patch.object(hfp.requests, "post", side_effect=_post):
        with mock.patch("backlink_publisher.publishing.adapters.retry.time.sleep"):
            out = hfp.submit_form("https://txt.fyi/edit.php", {"txt": "hi"})
    assert out is resp
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# attach_link_verification (R4 "measure" hook)
# --------------------------------------------------------------------------- #


def test_attach_link_verification_stores_result_in_meta() -> None:
    fake = {"verification": "ok", "nofollow_detected": False, "total_anchors": 3}
    with mock.patch.object(hfp, "verify_link_attributes", return_value=fake) as mv:
        meta = hfp.attach_link_verification("https://txt.fyi/abc/def")
    mv.assert_called_once_with("https://txt.fyi/abc/def")
    assert meta["link_attr_verification"] == fake


def test_attach_link_verification_preserves_existing_meta() -> None:
    with mock.patch.object(hfp, "verify_link_attributes", return_value={"verification": "ok"}):
        meta = hfp.attach_link_verification("https://x/y", {"existing": 1})
    assert meta["existing"] == 1
    assert "link_attr_verification" in meta


def test_attach_link_verification_empty_url_skips() -> None:
    with mock.patch.object(hfp, "verify_link_attributes") as mv:
        meta = hfp.attach_link_verification("", {"k": "v"})
    mv.assert_not_called()
    assert meta == {"k": "v"}


def test_attach_link_verification_never_raises_on_verifier_skip() -> None:
    # verify_link_attributes itself never raises; the hook must pass the
    # skipped sentinel through unchanged (fire-and-forget contract).
    skipped = {"verification": "skipped", "reason": "timeout"}
    with mock.patch.object(hfp, "verify_link_attributes", return_value=skipped):
        meta = hfp.attach_link_verification("https://x/y")
    assert meta["link_attr_verification"]["verification"] == "skipped"
