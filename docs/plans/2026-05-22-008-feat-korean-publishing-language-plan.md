---
title: "feat: Add Korean (ko) to publishing language dropdown and content templates"
type: feat
status: completed
date: 2026-05-22
claims: {}
origin: docs/plans/2026-05-22-004-feat-korean-language-full-support-plan.md
---

# feat: Add Korean (ko) Publishing Language

## Overview

Add Korean (한국어) as a selectable publishing language in the WebUI dropdown and wire up the full content-generation layer so Korean seeds produce Korean-language article output rather than falling back to English.

## Problem Frame

`ko` is already registered in `SUPPORTED_LANGUAGES`, the CLI `--default-language` choices, and the anchor/resolver layers (PR #006 Unit 1 done 2026-05-18). However, the content-generation and WebUI layers were never implemented — plan 004 was incorrectly marked `completed` on 2026-05-22. Concretely:

- `webui_app/templates/_shared_config_selects.html` has no `<option value="ko">` — users cannot select Korean in the UI
- `_util/_body_templates.py` has no `_ko_body_a/b/c` functions — Korean rows fall through to English body paragraphs
- `cli/plan_backlinks/_templates.py` has no `_TEMPLATES["ko"]` entry — same fallback issue
- `cli/plan_backlinks/_links.py` and `_work_themed.py` have no `ko` branch — English prose leaks into Korean articles

## Requirements Trace

- R1. The WebUI target_language dropdown must include a `ko` option labelled `한국어 (韩文)`
- R2. A seed with `language:"ko"` must produce Korean-language article content (title, excerpt, body paragraphs, link-density paragraph, further-reading sentence)
- R3. No existing language output (zh-CN, en, ru) must regress
- R4. All existing tests must continue to pass

## Scope Boundaries

- Short-form scheduler for Korean (`_ko_short.py`) is **out of scope** — codebase explicitly defers this; the comment in `anchor/resolver.py:137-140` documents that no production caller invokes `language="ko"` through the scheduler path because Unit 7's scheduler activation was reverted as a pass-2 P0, making this a distinct future deliverable
- `content/themed_gen.py` (`_WORK_TEMPLATES`) is **out of scope** — work-themed Korean support requires its own spike. Note: `cli/plan_backlinks/_work_themed.py` (the `_further_reading_paragraph` helper) is **in scope** as Unit 3; these are two distinct files
- `tdk_title` / `tdk_description` seed fields inject a Chinese-language TDK section from `_payload.py:142-147` unconditionally — **out of scope** for this plan; operators should avoid these fields with Korean seeds until a follow-up addresses the language gate
- `url_meta.py` `.kr` TLD detection is **in scope** as a small, high-value bonus (1–2 lines) for the `.kr` TLD only; `/ko/` path-segment sniffing is explicitly **out of scope** (the two-character string "ko" appears in too many non-Korean URL segments like `/checkout/`, `/tokyo/`, `/stock/` to be safe as a bare substring match)

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/_util/_body_templates.py` — `_ru_body_a/b/c` is the exact pattern to follow (single f-string functions with `domain`, `main_domain`, `anchors` list params)
- `src/backlink_publisher/cli/plan_backlinks/_templates.py` — `_TEMPLATES["ru"]` dict (lines 83–110) is the exact structure to copy for `"ko"`; `_TDK_TITLE_TMPL["ru"]` (line 22) is the pattern for the title meta template
- `src/backlink_publisher/cli/plan_backlinks/_links.py` — lines 236–266 show the `if language == "zh-CN": … if language == "ru": … else:` chain; add a `ko` branch before the `else`
- `src/backlink_publisher/cli/plan_backlinks/_work_themed.py` — lines 40–44 show the same two-branch pattern; add `ko` branch
- `webui_app/templates/_shared_config_selects.html` — lines 27–38 show the zh-CN/en/ru options with `selected` filter; add `ko` option after `ru`
- `webui_app/helpers/url_meta.py` — TLD heuristic block checks `.cn`, `.ru`, etc.; add `.kr` check (TLD only, no path sniffing)

### Institutional Learnings

- `_util/markdown.py` re-exports body template functions — any new `_ko_body_*` must be added to the imports in `markdown.py` so `_templates.py` can import them (same as `_ru_body_*` pattern). The import chain is: define in `_body_templates.py` → re-export via `markdown.py` → import into `_templates.py`. Missing the middle step causes `ImportError` at startup.
- `markdown.py` **IS** monitored by `monolith_budget.toml` with ceiling=240 / current=210. Adding three re-export lines costs ~3 SLOC (headroom: ~27 SLOC). Run `pytest tests/test_no_monolith_regrowth.py -x` after editing `markdown.py` to confirm the ceiling holds. `_body_templates.py` and `_templates.py` are **not** in the budget.
- `detect_language()` in `url_meta.py` currently returns codes outside `SUPPORTED_LANGUAGES` for some TLDs (`"zh-TW"`, `"ja"`) — these silently cause validate-backlinks to skip language+anchor gates. The `.kr` → `"ko"` addition is safe because `"ko"` is in `SUPPORTED_LANGUAGES`; the existing broken TLDs are a pre-existing issue out of scope here.

## Key Technical Decisions

- **Follow the ru pattern, not the zh pattern:** zh body functions use a `_ZH_BODY_X_POOL` list of lambdas with `random.choice`; ru uses a single f-string function. For Korean, follow ru (simpler, sufficient variety from A/B/C slots). Rationale: lower maintenance surface, same output quality, and the pool pattern requires mocking `random.choice` in tests to produce deterministic assertions — the single-function pattern allows plain containment checks.
- **No new test file needed:** Add Korean parametrized cases to existing test files. Specifically, add `"ko"` to: (a) the language loop at `test_plan_backlinks.py:~488`, (b) the `(lang, mode)` parametrize at `test_plan_backlinks.py:~547`, and (c) the 9-combination comprehension at `test_plan_backlinks.py:~764`. For `_further_reading_paragraph`, add `ko` variant to `tests/test_plan_backlinks_work_themed.py`. For `_build_link_density_paragraph`, add `ko` to the `test_target_site_link_density` parametrize at `test_plan_backlinks.py:~601`.
- **WebUI option label:** `한국어 (韩文)` — consistent with `Русский (俄文)` label style (native name + parenthetical Chinese for operator readability).

## Implementation Units

- [ ] **Unit 1: Korean body paragraph functions**

  **Goal:** Add `_ko_body_a`, `_ko_body_b`, `_ko_body_c` to the body templates module and re-export them through `markdown.py`.

  **Requirements:** R2

  **Dependencies:** None

  **Files:**
  - Modify: `src/backlink_publisher/_util/_body_templates.py`
  - Modify: `src/backlink_publisher/_util/markdown.py` (add re-exports to imports block)

  **Approach:**
  - Add three functions each with signature `(domain: str, main_domain: str, anchors: list[str]) -> str`
  - Each function indexes into `anchors[0]` and `anchors[1]` inside the function body — these are list indexing operations inside the f-string, not additional call parameters. The call signature is 3 arguments, same as `_ru_body_a/b/c`
  - Each returns a distinct Korean-language paragraph: A = platform overview + anchor links, B = navigation/category guidance, C = analytical depth / expert context
  - Add `_ko_body_a`, `_ko_body_b`, `_ko_body_c` to `markdown.py` imports block alongside the existing `_ru_body_*` imports — **this step is required before Unit 2 can import from markdown**

  **Patterns to follow:**
  - `_ru_body_a/b/c` in `_body_templates.py` (lines 109–143) — exact structural mirror
  - `_util/markdown.py` existing re-export block

  **Test scenarios:**
  - Happy path: `_ko_body_a("example.com", "https://example.com", ["앵커A", "앵커B"])` returns a non-empty string containing `example.com`, `https://example.com`, and at least one anchor
  - Happy path: all three functions (`a`, `b`, `c`) return distinct strings (no copy-paste identical output)
  - Edge case: `anchors` list with single-character strings — no crash, string still interpolates
  - Happy path: returned text contains Hangul characters (assert `any('가' <= c <= '힯' for c in result)`)

  **Verification:**
  - `python -m py_compile src/backlink_publisher/_util/_body_templates.py src/backlink_publisher/_util/markdown.py` exits 0
  - `pytest tests/test_no_monolith_regrowth.py -x` passes (confirms `markdown.py` ceiling holds)
  - `pytest tests/test_plan_backlinks.py -k ko -x` passes after Unit 2 adds the seed fixture

---

- [ ] **Unit 2: Korean entries in `_TEMPLATES` and `_TDK_TITLE_TMPL`**

  **Goal:** Add `_TEMPLATES["ko"]` and `_TDK_TITLE_TMPL["ko"]` so the payload builder no longer falls back to English for Korean seeds.

  **Requirements:** R2, R3

  **Dependencies:** Unit 1 must be complete first — `_ko_body_a/b/c` must exist in `markdown.py` before `_templates.py` imports them

  **Files:**
  - Modify: `src/backlink_publisher/cli/plan_backlinks/_templates.py`
  - Test: `tests/test_plan_backlinks.py`

  **Approach:**
  - Import `_ko_body_a`, `_ko_body_b`, `_ko_body_c` from `backlink_publisher._util.markdown` alongside existing imports
  - Add `_TDK_TITLE_TMPL["ko"]` — a Korean-language SEO title template string following the pattern `"{tdk}에 대한 완벽한 가이드: {domain} 심층 분석"` (mirror of the ru/en templates)
  - Add `_TEMPLATES["ko"]` dict with keys: `title` (A/B/C), `excerpt` (A/B/C), `seo_title`, `seo_desc`, `topic_fallback`, `tags`, `body_paragraphs` (A/B/C pointing to `_ko_body_a/b/c`)
  - All prose strings must be Korean-language

  **Patterns to follow:**
  - `_TEMPLATES["ru"]` block in `_templates.py` (lines 83–110) — exact structural mirror
  - `_TDK_TITLE_TMPL["ru"]` (line 22) — exact naming mirror

  **Test scenarios:**
  - Happy path: run `plan-backlinks` end-to-end with a `language:"ko"` seed (via `_run_plan()` helper) and verify `exit_code == 0` and stdout contains valid JSON
  - Happy path: output JSON row has `title` field containing Hangul characters (`any('가' <= c <= '힯' for c in payload['title'])`)
  - Happy path: output JSON row has `content_markdown` field containing Hangul characters (proves Korean prose, not English fallback)
  - Integration: `_TEMPLATES["ko"]["body_paragraphs"]["A"]` is callable and returns a string when invoked with typical args
  - Edge case: parametrize the language×mode loops at `test_plan_backlinks.py:~488`, `~547`, and `~764` to also include `ko`, confirming no structural differences in output shape
  - Integration (sweep): in the `ko` case of the `test_plan_all_languages` sweep, assert `any('가' <= c <= '힯' for c in payload['content_markdown'])` — confirms Korean script is produced, not English fallback

  **Verification:**
  - `pytest tests/test_plan_backlinks.py -x` passes
  - Spot-check: `echo '{"id":"k1","platform":"velog","language":"ko","target_url":"https://example.kr/page","main_domain":"https://example.kr","publish_mode":"A"}' | python -m backlink_publisher.cli.plan_backlinks` emits a JSON row with Korean text in `title` and `content_markdown`

---

- [ ] **Unit 3: Korean prose branches in `_links.py` and `_work_themed.py`, including `_build_links` anchor strings**

  **Goal:** Add `if language == "ko":` branches so the link-density paragraph, further-reading sentence, and `extra_urls` anchor strings are written in Korean rather than defaulting to English/Chinese.

  **Requirements:** R2

  **Dependencies:** None (no import dependency on Units 1–2 at module-load time; can be coded in parallel but must merge before an end-to-end ko seed test passes)

  **Files:**
  - Modify: `src/backlink_publisher/cli/plan_backlinks/_links.py`
  - Modify: `src/backlink_publisher/cli/plan_backlinks/_work_themed.py`
  - Test (link density + `_build_links`): `tests/test_plan_backlinks.py` (add `ko` to `test_target_site_link_density` parametrize at line ~601)
  - Test (further reading): `tests/test_plan_backlinks_work_themed.py`

  **Approach:**
  - In `_links.py` → `_build_link_density_paragraph()`: insert `if language == "ko":` block before the `else` (English fallback). Return a Korean-language sentence embedding the main domain link.
  - In `_links.py` → `_build_links()`: add a `language` parameter (default `"en"` for backwards compat); add `if language == "ko":` branches for the hardcoded Chinese anchor strings (`分页` → `페이지`, `分类` → `카테고리`, `归档` → `아카이브`, `相关` → `관련`, `详情页` → `상세 페이지`). The function signature change flows from callers — verify with `grep -rn "_build_links("` before patching callsites.
  - In `_work_themed.py` → `_further_reading_paragraph()`: insert `if language == "ko":` before the `else` block. Return a Korean-language "further reading" closing sentence.

  **Patterns to follow:**
  - `if language == "ru":` blocks in `_build_link_density_paragraph` and `_further_reading_paragraph` — exact same structure
  - Caller pattern for `_build_links` — grep all callers to pass `language` through

  **Test scenarios:**
  - Happy path: call `_further_reading_paragraph()` with `language="ko"` and verify returned string contains Hangul and is non-empty
  - Happy path: call `_build_link_density_paragraph()` with `language="ko"` and verify returned string contains Hangul and contains the passed `main_domain`
  - Happy path: call `_build_links()` with `language="ko"` and verify the extra-URL anchor labels contain Hangul (not Chinese characters)
  - Regression: call all three functions with `language="en"` and `language="ru"` and verify outputs unchanged (guard against accidental fallthrough)

  **Verification:**
  - `pytest tests/test_plan_backlinks_work_themed.py tests/test_plan_backlinks.py -x` passes
  - `python -m py_compile src/backlink_publisher/cli/plan_backlinks/_links.py src/backlink_publisher/cli/plan_backlinks/_work_themed.py` exits 0

---

- [ ] **Unit 4: WebUI dropdown option and `.kr` TLD auto-detection**

  **Goal:** Make Korean selectable in the UI and auto-detected for `.kr` domain URLs.

  **Requirements:** R1

  **Dependencies:** Unit 2 should land first or in the same deployment — if this unit lands without Unit 2, users can select Korean but will receive English-language content (silent regression)

  **Files:**
  - Modify: `webui_app/templates/_shared_config_selects.html`
  - Modify: `webui_app/helpers/url_meta.py`

  **Approach:**
  - In `_shared_config_selects.html`: add `<option value="ko" {% if config is defined and config.target_language == 'ko' %}selected{% endif %}>한국어 (韩文)</option>` immediately after the `ru` option block (lines 35–38), following the identical `selected`-filter pattern
  - In `url_meta.py` → `detect_language()`: add `.kr` TLD check returning `"ko"` for `.kr` hostnames (TLD match only — no path-segment sniffing; see Scope Boundaries)

  **Patterns to follow:**
  - `<option value="ru" …>Русский (俄文)</option>` block in `_shared_config_selects.html`
  - `.ru` branch in `url_meta.py:detect_language()` for TLD matching only

  **Test scenarios:**
  - Automated: use the Flask test client to GET a route that renders `_shared_config_selects.html` and assert `b'value="ko"'` appears in the response — runs in CI without a live WebUI. Manual WebUI startup is a supplementary check.
  - Happy path: `detect_language("https://example.kr/page")` returns `"ko"`
  - Regression: `detect_language("https://example.ru/page")` still returns `"ru"`, `detect_language("https://example.cn/")` still returns `"zh-CN"`
  - Regression: `detect_language("https://shop.example.com/checkout/cart")` does NOT return `"ko"` (guard against `'ko' in path` substring false positive)
  - Regression: `detect_language("https://example.kr/page")` returns a value in `SUPPORTED_LANGUAGES` (i.e., `"ko"` is valid, unlike the pre-existing `"zh-TW"` / `"ja"` returns that are not)

  **Verification:**
  - `pytest tests/ -x -q` — full suite green (no regressions)
  - Start the WebUI (`python webui.py`) and confirm the dropdown renders `한국어 (韩文)` as an option

## System-Wide Impact

- **Interaction graph:** `_payload.py` (line 80) will resolve `_TEMPLATES["ko"]` directly after Unit 2 instead of falling back to `_TEMPLATES["en"]`; no other callsites affected
- **Unchanged invariants:** `SUPPORTED_LANGUAGES`, anchor/lang/resolver `ko` rules, and CLI `--default-language ko` choice are already correct — this plan does not touch them
- **State lifecycle risks:** None — template dicts are module-level constants; no persistent state involved
- **API surface parity:** `seeds.jsonl` schema already accepts `language:"ko"` — no contract change needed
- **Known gap (partial, tracked):** Korean seeds that include `extra_urls` now receive Korean anchor labels (fixed in Unit 3). Seeds that include `tdk_title`/`tdk_description` still receive the hardcoded Chinese TDK section from `_payload.py:142-147` — operators should avoid these fields with Korean seeds until a follow-up plan addresses that path.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Korean prose quality (grammaticality) | Generated prose is informational boilerplate; native-speaker review is desirable but not blocking for functionality |
| `markdown.py` SLOC ceiling (240) | Current SLOC is ~210; adding 3 re-export lines is safe. Run `pytest tests/test_no_monolith_regrowth.py -x` to confirm |
| Unit 4 landing before Unit 2 | UI users select Korean but get English content. Mitigation: land Units 2 and 4 in the same PR/deployment |
| Plan 004 marked `completed` causing confusion | This plan supersedes 004 for the implementation; update plan 004's status to `archived` and add `superseded_by: 2026-05-22-008-feat-korean-publishing-language-plan.md` after all units land |

## Sources & References

- **Origin document (incorrectly completed):** [docs/plans/2026-05-22-004-feat-korean-language-full-support-plan.md](docs/plans/2026-05-22-004-feat-korean-language-full-support-plan.md)
- Related code — anchor layer already done: `src/backlink_publisher/anchor/lang.py`, `anchor/resolver.py`
- Pattern reference — ru body templates: `src/backlink_publisher/_util/_body_templates.py:109–143`
- Pattern reference — ru template dict: `src/backlink_publisher/cli/plan_backlinks/_templates.py:83–110`
