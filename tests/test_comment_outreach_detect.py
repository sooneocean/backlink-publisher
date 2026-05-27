"""Comment-region detector tests + the Unit 4 accuracy spike / go-no-go gate.

The spike (``test_detector_accuracy_gate``) is the plan's measurable gate: across the
six fixture classes, the detector must hit **100% recall on the four open classes**
(WordPress, Disqus, native form, forum reply) with **zero false positives** on the
no-comment page. The JS-lazy page is recorded as a **known false-negative** — a page that
mounts comments purely client-side has no server-side marker, so detecting it ``False`` is
the documented limitation, not a gate failure. Below this floor, escalate before
broadening signatures (or descope ``discover`` to import-only).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backlink_publisher.comment_outreach.detect import detect_comment_region

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "comment_outreach"

# fixture name -> expected tri-state (on a successful fetch, so never None here).
_OPEN_CLASSES = ("wordpress", "disqus", "native_form", "forum_reply")
_CLOSED_CLASSES = ("no_comment",)
_KNOWN_FALSE_NEGATIVE = ("js_lazy",)  # client-side mount, no static marker


def _load(name: str) -> bytes:
    return (_FIXTURES / f"{name}.html").read_bytes()


@pytest.mark.parametrize("name", _OPEN_CLASSES)
def test_open_fixtures_detected_true(name: str):
    assert detect_comment_region(_load(name)) is True, name


@pytest.mark.parametrize("name", _CLOSED_CLASSES)
def test_closed_fixtures_detected_false(name: str):
    assert detect_comment_region(_load(name)) is False, name


@pytest.mark.parametrize("name", _KNOWN_FALSE_NEGATIVE)
def test_js_lazy_is_known_false_negative(name: str):
    # Documented limitation: no server-side marker -> False even though comments exist.
    assert detect_comment_region(_load(name)) is False, name


def test_unfetchable_page_is_tristate_none():
    assert detect_comment_region(None) is None


def test_detector_accuracy_gate():
    """Go/no-go: 100% recall on open classes, 0 false positives on closed classes."""
    open_hits = sum(detect_comment_region(_load(n)) is True for n in _OPEN_CLASSES)
    recall = open_hits / len(_OPEN_CLASSES)
    false_positives = sum(detect_comment_region(_load(n)) is True for n in _CLOSED_CLASSES)

    assert recall == 1.0, f"recall {recall:.2f} below 1.0 floor — escalate, do not broaden blindly"
    assert false_positives == 0, f"{false_positives} false positive(s) on closed pages"


def test_html_comment_marker_does_not_false_positive():
    # A bare HTML comment / the word "comment" in prose must not trip detection.
    body = b"<html><body><h1>Post</h1><!-- comment --><p>Comments are closed.</p></body></html>"
    assert detect_comment_region(body) is False
