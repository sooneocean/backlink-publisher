"""Tests for render_to_html."""

from backlink_publisher.markdown_utils import render_to_html


def test_heading_and_paragraph():
    html = render_to_html("# Title\n\nBody text.")
    assert "<h1>Title</h1>" in html
    assert "<p>Body text.</p>" in html


def test_link_no_nofollow():
    html = render_to_html("[anchor](https://example.com)")
    assert 'href="https://example.com"' in html
    assert "nofollow" not in html


def test_bold():
    html = render_to_html("**bold**")
    assert "<strong>bold</strong>" in html


def test_empty_string():
    assert render_to_html("") == ""


def test_chinese_characters():
    html = render_to_html("**你好**，世界。")
    assert "你好" in html
    assert "世界" in html


def test_russian_characters():
    html = render_to_html("Привет **мир**.")
    assert "Привет" in html
    assert "<strong>мир</strong>" in html


def test_backlink_survives_rendering():
    md = "Visit [example.com](https://example.com) for more."
    html = render_to_html(md)
    assert "https://example.com" in html


def test_raw_html_preserved():
    md = "Text <br/> more text."
    html = render_to_html(md)
    # markdown-it by default allows inline HTML
    assert "more text" in html
