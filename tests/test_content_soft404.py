"""Unit tests for content._soft404.is_soft_404_title.

Covers: English, Chinese (simplified + traditional), Japanese, Russian patterns;
site-suffix tolerance; case-insensitivity; empty-string guard; true-positive
rejection (legitimate titles must not match).
"""

from __future__ import annotations

import pytest

from backlink_publisher.content._soft404 import is_soft_404_title


# ── Positive cases (should return True) ───────────────────────────────────────

@pytest.mark.parametrize("title", [
    # Bare numeric
    "404",
    "404 Not Found",
    # English
    "Page Not Found",
    "Not Found",
    "Page does not exist",
    "Error 404",
    "This page can't be found",
    "This page cannot be found",
    "This page could not be found",
    # English + site suffix
    "Page Not Found - My Site",
    "404 | Example.com",
    "Not Found · GitHub",
    "Page Not Found – Some Blog",
    "Page does not exist — SiteName",
    # Chinese simplified
    "页面不存在",
    "页面未找到",
    "找不到页面",
    "404错误",
    "404 错误",
    # Chinese traditional
    "頁面不存在",
    "頁面未找到",
    "找不到頁面",
    "404錯誤",
    # Japanese
    "ページが見つかりません",
    "お探しのページは見つかりません",
    # Russian
    "Страница не найдена",
    "СТРАНИЦА НЕ НАЙДЕНА",
    # Case variants
    "page not found",
    "PAGE NOT FOUND",
    "Not found",
])
def test_soft_404_positive(title: str) -> None:
    assert is_soft_404_title(title) is True, f"Expected soft-404 for {title!r}"


# ── Negative cases (should return False) ──────────────────────────────────────

@pytest.mark.parametrize("title", [
    # Legitimate article titles
    "How to fix 404 errors on your site",
    "Understanding HTTP status codes",
    "My Blog Post About 404 Pages",
    "Top 10 SEO Tips",
    "Welcome to My Site",
    "About Us",
    "Contact",
    "Homepage",
    # Contains "not found" mid-title (not anchored start)
    "What to do when content is not found",
    # Non-empty legitimate titles with numbers
    "Chapter 404: The Lost Chapter",
    # Empty / whitespace handled separately
])
def test_soft_404_negative(title: str) -> None:
    assert is_soft_404_title(title) is False, f"Expected non-soft-404 for {title!r}"


def test_empty_string_returns_false() -> None:
    assert is_soft_404_title("") is False


def test_whitespace_only_returns_false() -> None:
    assert is_soft_404_title("   ") is False


def test_suffix_alone_does_not_trigger() -> None:
    """Site name appended to a valid title must not match."""
    assert is_soft_404_title("Welcome - 404 Page Gone") is False
