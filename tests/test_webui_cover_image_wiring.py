"""Unit 1b of Plan 2026-05-19-007: contract test between plan dict
(``cover_image_url`` / ``cover_image_warning``) and webui Jinja rendering.

These tests do not need full ``index.html`` context — they exercise the
filter registration and the templated fragments that consume the new
plan-dict fields in isolation. Any regression that drops the filter,
renames a field, or breaks render_markdown will fail here.
"""

from __future__ import annotations

import os
import sys

import pytest

# webui_app lives at repo root, not inside src/ — mirror test_webui_route_contract.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from webui_app import create_app  # noqa: E402


@pytest.fixture
def app():
    return create_app(start_scheduler=False)


def test_render_markdown_filter_is_registered(app) -> None:
    assert "render_markdown" in app.jinja_env.filters


def test_render_markdown_converts_cover_image_to_img(app) -> None:
    """Happy path: ``![title](url)`` becomes ``<img>`` so preview can show
    the cover image instead of literal markdown."""
    tpl = app.jinja_env.from_string("{{ md | render_markdown | safe }}")
    md = "![Foo](https://cdn.example.com/cover.png)\n\nBody text here."
    out = tpl.render(md=md)
    assert '<img src="https://cdn.example.com/cover.png"' in out
    assert 'alt="Foo"' in out
    # raw markdown image syntax must be gone
    assert "![Foo](" not in out


def test_render_markdown_renders_headings_and_emphasis(app) -> None:
    """F1 win: switching to shared renderer means preview matches publish
    for the whole article body, not just images."""
    tpl = app.jinja_env.from_string("{{ md | render_markdown | safe }}")
    md = "# Title\n\n**bold** and *italic*\n\n- item1\n- item2"
    out = tpl.render(md=md)
    assert "<h1>" in out
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out
    assert "<ul>" in out
    assert "<li>item1</li>" in out


def test_render_markdown_anchor_links_carry_target_blank(app) -> None:
    """Existing preview behavior (external links open new tab) preserved via
    ``markdown_utils.render_to_html`` itself (CommonMark + custom link_open)."""
    tpl = app.jinja_env.from_string("{{ md | render_markdown | safe }}")
    md = "Check [anchor text](https://target.example/page) here."
    out = tpl.render(md=md)
    assert '<a href="https://target.example/page"' in out
    assert 'target="_blank"' in out
    assert 'rel="noopener"' in out


def test_render_markdown_neutralizes_script_in_image_alt(app) -> None:
    """XSS contract: a ``<script>`` payload inside an image alt-text markdown
    title must not survive as a live ``<script>`` tag in the rendered HTML."""
    tpl = app.jinja_env.from_string("{{ md | render_markdown | safe }}")
    md = '![<script>alert(1)</script>](https://cdn.example.com/x.png)'
    out = tpl.render(md=md)
    assert "<script>" not in out
    assert "</script>" not in out


def test_render_markdown_handles_empty_input(app) -> None:
    """Old JSONL with no markdown body should not crash."""
    tpl = app.jinja_env.from_string("{{ md | render_markdown | safe }}")
    assert tpl.render(md="") == ""


def test_cover_image_warning_badge_renders_when_present(app) -> None:
    """The conditional badge block (mirrored from index.html) shows only on
    failure and exposes the warning via tooltip ``title=`` (autoescaped)."""
    fragment = (
        "{% if plan.cover_image_warning %}"
        "<span data-bs-toggle=\"tooltip\" title=\"{{ plan.cover_image_warning }}\">封面降级</span>"
        "{% endif %}"
    )
    tpl = app.jinja_env.from_string(fragment)
    out = tpl.render(plan={"cover_image_warning": "TimeoutError('network')"})
    assert "封面降级" in out
    # Jinja autoescape converts the apostrophes in the repr
    assert "TimeoutError" in out


def test_cover_image_warning_badge_absent_when_none(app) -> None:
    """No badge when generation succeeded or was disabled."""
    fragment = (
        "{% if plan.cover_image_warning %}"
        "<span>封面降级</span>"
        "{% endif %}"
    )
    tpl = app.jinja_env.from_string(fragment)
    assert "封面降级" not in tpl.render(plan={"cover_image_warning": None})
    # Missing field (old JSONL) — Jinja Undefined is falsy
    assert "封面降级" not in tpl.render(plan={})


def test_cover_image_warning_html_is_escaped_in_tooltip(app) -> None:
    """If an upstream error string contains HTML, it must not break out of
    the ``title=`` attribute (autoescape protects us; no ``|safe`` on it)."""
    fragment = '<span title="{{ plan.cover_image_warning }}">x</span>'
    tpl = app.jinja_env.from_string(fragment)
    out = tpl.render(plan={"cover_image_warning": '" onmouseover="alert(1)'})
    assert 'onmouseover="alert(1)' not in out
    assert "&#34;" in out or "&quot;" in out
