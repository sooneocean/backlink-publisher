---
title: "fix: Ensure all main_domain mentions in article templates are hyperlinks"
type: fix
status: completed
date: 2026-05-12
---

# fix: Ensure all main_domain mentions in article templates are hyperlinks

## Overview

Every occurrence of `main_domain` (e.g., `https://51acgs.com`) in generated article content must be a proper Markdown hyperlink `[anchor](url)`. Currently 22 occurrences across 9 body template functions and 9 excerpt template strings are plain text. CommonMark (used by markdown-it-py) does not auto-link bare URLs — they render as unclickable text in the published Blogger/Medium article, defeating the core backlink purpose.

## Problem Frame

The backlink pipeline's core value is creating dofollow hyperlinks from external articles back to a target domain. When `main_domain` appears as raw text (`https://51acgs.com`) rather than a Markdown link (`[51acgs.com](https://51acgs.com)`), `render_to_html()` produces plain text with no `<a>` tag. The published article passes validation (the URL string is present in content) but delivers zero SEO link value.

Confirmed via browser inspection of a live published Blogger draft: body paragraphs contained clickable text only for the explicit `[anchor](url)` occurrences but raw text for all others.

## Requirements Trace

- R1. Every occurrence of `main_domain` in `content_markdown` must be wrapped as `[{domain}]({main_domain})` — a Markdown hyperlink that `render_to_html` converts to `<a href="…">`.
- R2. The fix applies to all 3 languages (EN, ZH, RU) and all 3 URL modes (A, B, C).
- R3. No existing hyperlinks (`[text](url)` already present in some templates) are double-wrapped or broken.
- R4. The rendered HTML must contain `<a href="{main_domain}">` for every prose mention of the domain, not just the References section.

## Scope Boundaries

- Only `main_domain` URL occurrences in body/excerpt templates. The `## References` section already produces correct `[anchor](url)` via `links_to_markdown()` — do not touch it.
- No changes to `render_to_html()`, `links_to_markdown()`, or the validation logic.
- No new template content — only the hyperlink format of existing `{main_domain}` interpolations changes.
- `seo_title`, `seo_desc`, and `tags` fields are not part of `content_markdown` — do not fix those.

## Context & Research

### Relevant Code and Patterns

- **`src/backlink_publisher/markdown_utils.py`** — 9 body functions: `_en_body_a/b/c`, `_zh_body_a/b/c`, `_ru_body_a/b/c`. Each receives `(domain: str, main_domain: str)`. `domain` = clean label (e.g., `51acgs.com`), `main_domain` = full URL (e.g., `https://51acgs.com`).
- **`src/backlink_publisher/cli/plan_backlinks.py`** — `TEMPLATES` dict contains `excerpt` keys for EN/ZH/RU × A/B/C. Formatted via `.format(main_domain=main_domain, domain=domain_label, topic=topic_val)`. `domain` is available as `{domain}` in the format string.
- **`content_markdown` assembly** (plan_backlinks.py) — `excerpt` IS included as the second paragraph of `content_markdown`. Body paragraphs are the third. Both must be fixed.
- **Correct pattern already used in some templates** — `_en_body_a` and `_zh_body_a/c` already have `[{main_domain}]({main_domain})` at the end of the paragraph. The fix unifies all occurrences to `[{domain}]({main_domain})` (cleaner anchor text).
- **Test file** — `tests/test_plan_backlinks.py` asserts `payload["main_domain"] in payload["content_markdown"]` (string presence only, not hyperlink format). New test needed.
- **`tests/test_markdown_render.py`** — tests `render_to_html`; the pattern `test_link_no_nofollow` shows how to assert `href` in rendered HTML.

### Bare URL Inventory (22 instances)

| File | Location | Count |
|------|----------|-------|
| `markdown_utils.py` | `_en_body_a` line 2 | 1 |
| `markdown_utils.py` | `_en_body_b` lines 2, 3 | 2 |
| `markdown_utils.py` | `_en_body_c` line 2 | 1 |
| `markdown_utils.py` | `_zh_body_a` line 2 | 1 |
| `markdown_utils.py` | `_zh_body_b` lines 1, 3 | 2 |
| `markdown_utils.py` | `_zh_body_c` line 2 | 1 |
| `markdown_utils.py` | `_ru_body_a` line 4 | 1 |
| `markdown_utils.py` | `_ru_body_b` lines 2, 4 | 2 |
| `markdown_utils.py` | `_ru_body_c` lines 2, 4 | 2 |
| `plan_backlinks.py` | EN excerpt A/B/C | 3 |
| `plan_backlinks.py` | ZH excerpt A/B/C | 3 |
| `plan_backlinks.py` | RU excerpt A/B/C | 3 |

## Key Technical Decisions

- **Use `[{domain}]({main_domain})` not `[{main_domain}]({main_domain})`**: `domain` (e.g., `51acgs.com`) is cleaner anchor text than the full URL. It's already available in every template context. Existing `[{main_domain}]({main_domain})` occurrences in `_en_body_a`, `_zh_body_a`, `_zh_body_c` should also be updated to `[{domain}]({main_domain})` for consistency.
- **No prose restructuring**: Only the URL interpolation format changes. Word order, sentence structure, and content remain identical. This minimises risk of introducing new bugs.
- **excerpt templates use `.format(domain=domain_label, ...)` already**: `{domain}` is a valid placeholder in all excerpt strings — confirmed by reading `plan_backlinks.py` line 268. No signature change needed.

## Implementation Units

- [ ] **Unit 1: Fix body template functions in `markdown_utils.py`**

**Goal:** Replace every bare `{main_domain}` with `[{domain}]({main_domain})` in all 9 body functions. Update existing `[{main_domain}]({main_domain})` to `[{domain}]({main_domain})` for consistency.

**Requirements:** R1, R2, R3

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/markdown_utils.py`
- Test: `tests/test_markdown_render.py`

**Approach:**
- In each function, every f-string segment containing `{main_domain}` as plain interpolation becomes `[{domain}]({main_domain})`. Example: `f"at {main_domain} provides"` → `f"at [{domain}]({main_domain}) provides"`.
- Watch for existing `[{main_domain}]({main_domain})` — update to `[{domain}]({main_domain})`, not double-nested.
- After the fix, each function should have **zero** bare `{main_domain}` and **all** occurrences wrapped as `[{domain}]({main_domain})`.

**Patterns to follow:**
- `_en_body_a` line 84: existing `[{main_domain}]({main_domain})` is the model to follow — just swap `{main_domain}` anchor text to `{domain}`.

**Test scenarios:**
- Happy path: call `_zh_body_a("51acgs.com", "https://51acgs.com")` → returned string contains no bare `https://51acgs.com`, contains `[51acgs.com](https://51acgs.com)` at least twice
- Happy path: call `_ru_body_b("example.ru", "https://example.ru")` → no bare `https://example.ru`, all occurrences wrapped
- Happy path: `render_to_html(_en_body_b("example.com", "https://example.com"))` → rendered HTML contains `href="https://example.com"` at least twice, no unlinked `https://example.com` text node
- Edge case: domain with trailing slash — `_zh_body_a("51acgs.com", "https://51acgs.com/")` → link wraps correctly, anchor text uses `domain` not `main_domain`
- Integration: all 9 functions × assert no bare `{main_domain}` pattern using a regex check on the returned string

**Verification:**
- `grep -n '{main_domain}' src/backlink_publisher/markdown_utils.py` returns zero results (outside of existing `[{main_domain}]` → which should also be gone).
- `pytest tests/test_markdown_render.py` passes.

---

- [ ] **Unit 2: Fix excerpt template strings in `plan_backlinks.py`**

**Goal:** Replace every `{main_domain}` in the `excerpt` template strings with `[{domain}]({main_domain})` so the first paragraph of `content_markdown` also contains hyperlinks.

**Requirements:** R1, R2, R3

**Dependencies:** None (parallel with Unit 1)

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks.py`
- Test: `tests/test_plan_backlinks.py`

**Approach:**
- In the `TEMPLATES` dict, find the `"excerpt"` sub-dict for each language (EN/ZH/RU) and each mode (A/B/C). Replace every `{main_domain}` with `[{domain}]({main_domain})`.
- The format call already passes `domain=domain_label` — no signature change needed.
- Example: `"This article explores … {main_domain},"` → `"This article explores … [{domain}]({main_domain}),"`

**Patterns to follow:**
- Same `[{domain}]({main_domain})` pattern as Unit 1.

**Test scenarios:**
- Happy path: run full `plan-backlinks` CLI with `main_domain=https://example.com`, `language=en`, `url_mode=A` → `content_markdown` excerpt paragraph contains `[example.com](https://example.com)` and no bare `https://example.com`
- Happy path: `language=zh-CN`, `url_mode=B` → same assertion for Chinese excerpt
- Happy path: `language=ru`, `url_mode=C` → same for Russian excerpt
- Integration: the excerpt hyperlink survives `validate-backlinks --no-check-urls` without errors
- Integration: `render_to_html(content_markdown)` for a full zh-CN plan contains `href="https://example.com"` in the excerpt `<p>` tag (not just in the References section)

**Verification:**
- `grep -n "excerpt" src/backlink_publisher/cli/plan_backlinks.py` shows no bare `{main_domain}` in any excerpt string.
- `pytest tests/test_plan_backlinks.py` passes.
- New assertion in `test_plan_backlinks.py`: for every language and url_mode combination, `content_markdown` contains `[` before every `main_domain` URL occurrence (no bare URL).

---

- [ ] **Unit 3: Add hyperlink coverage test to `test_plan_backlinks.py`**

**Goal:** Add a parametrized test that proves every `main_domain` occurrence in generated `content_markdown` is a Markdown hyperlink, not bare text.

**Requirements:** R1, R2, R4

**Dependencies:** Units 1 and 2 (this test verifies the fix end-to-end)

**Files:**
- Modify: `tests/test_plan_backlinks.py`

**Approach:**
- Add `test_all_main_domain_occurrences_are_hyperlinked` parametrized over `[("en", "A"), ("en", "B"), ("en", "C"), ("zh-CN", "A"), ("zh-CN", "B"), ("zh-CN", "C"), ("ru", "A"), ("ru", "B"), ("ru", "C")]`.
- For each combination: run `plan-backlinks`, parse `content_markdown`, assert that no bare URL occurrence exists. Check: `re.search(r'(?<!\()(https?://example\.com)(?!\))', content)` returns None.
- Also assert at least 2 hyperlink occurrences: `len(re.findall(r'\[.*?\]\(https://example\.com[^)]*\)', content)) >= 2`.
- Use the existing `_run_plan(seed)` helper pattern from the test file.

**Patterns to follow:**
- `test_plan_all_languages` in `test_plan_backlinks.py` for the parametrized-across-languages pattern.

**Test scenarios:**
- Happy path: `(en, A)` → no bare `https://example.com` in content, 2+ markdown links
- Happy path: `(zh-CN, B)` → same
- Happy path: `(ru, C)` → same
- Edge case: `main_domain` with trailing slash → `https://example.com/` also not bare

**Verification:**
- `pytest tests/test_plan_backlinks.py::test_all_main_domain_occurrences_are_hyperlinked` passes for all 9 parameter combinations.

## System-Wide Impact

- **Interaction graph**: `content_markdown` flows through `validate-backlinks` (string-presence check passes for `[domain](url)` since the URL is still present) → `publish-backlinks` → `render_to_html()` → Blogger/Medium API. No breakage in the pipeline.
- **Unchanged invariants**: `links_to_markdown()`, `render_to_html()`, `validate_publish_payload()` — none touched. The existing `schema.py` main_domain-in-content check (uses `.rstrip("/")`) continues to work correctly since the URL string is still present inside `[anchor](url)`.
- **API surface parity**: The `content_markdown` field in JSONL output gains more `<a>` tags but its structure is otherwise unchanged. Downstream consumers (Blogger API, Medium API) accept HTML with any number of links.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Double-wrapping existing `[{main_domain}]({main_domain})` | Read each function carefully before editing; the existing pattern must be replaced, not wrapped again. Test R3 explicitly. |
| `{domain}` placeholder unavailable in some format context | Confirmed: `plan_backlinks.py` L268 passes `domain=domain_label` to all excerpt `.format()` calls. No risk. |
| Validate-backlinks fails after fix | The URL string is still present inside `[anchor](url)` — `.rstrip("/")` check matches the domain portion. Covered by integration test. |

## Sources & References

- Related code: `src/backlink_publisher/markdown_utils.py`, `src/backlink_publisher/cli/plan_backlinks.py`
- Test files: `tests/test_plan_backlinks.py`, `tests/test_markdown_render.py`
- CommonMark spec: bare URLs are not autolinked; `<URL>` or `[text](URL)` required
