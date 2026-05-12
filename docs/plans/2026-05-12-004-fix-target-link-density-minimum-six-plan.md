---
title: "fix: Ensure target-site links appear ≥6 times per article (A+B+C ≥ 6)"
type: fix
status: completed
date: 2026-05-12
---

# fix: Ensure target-site links appear ≥6 times per article (A+B+C ≥ 6)

## Overview

Every generated article must contain at least 6 hyperlinks pointing to the target site (main_domain + target_url + extra_urls combined). Mode A currently produces only 4–5, which fails the core backlink density requirement. Mode B (6) and C (7) already pass. The fix adds a short injected paragraph in `_generate_payload` when the projected target-site link count is below 6.

## Problem Frame

SEO backlinks derive value from the number and context of hyperlinks pointing to the target domain. The current pipeline generates:

| Mode | target==domain | A (main) | B (target) | C (extra) | Total | Status |
|------|---------------|----------|------------|-----------|-------|--------|
| A | yes | 4 | 0 | 0 | **4** | ❌ |
| A | no | 4 | 1 | 0 | **5** | ❌ |
| B | no | 4 | 1 | 1 | **6** | ✓ |
| C | no | 4 | 1 | 2 | **7** | ✓ |

The 4 existing A links come from: excerpt (1) + body paragraph (2) + references main_domain entry (1). The fix targets Mode A exclusively.

## Requirements Trace

- R1. For every generated article, the count of distinct hyperlinks whose href starts with `main_domain` or `target_url` domain must be ≥ 6.
- R2. When `target_url == main_domain`, all 6+ hyperlinks use `main_domain`. When they differ, the injected paragraph includes both.
- R3. Mode B and C are unchanged — they already meet the threshold.
- R4. Supporting links (Wikipedia, MDN, etc.) are NOT counted toward the target-site total; they remain separate.
- R5. All injected target-site links must use proper Markdown hyperlink syntax `[anchor](url)`, never bare URLs.

## Scope Boundaries

- Mode B and C: no changes — they already satisfy A+B+C ≥ 6.
- No changes to the `links[]` array structure or `_build_links()` logic.
- No changes to `validate-backlinks` or `schema.py` link-count validation (that checks 6–8 total links including supporting, which remains satisfied).
- No changes to the 9 body template functions in `markdown_utils.py` — only the assembly step in `plan_backlinks.py` changes.
- The injected paragraph adds context naturally — it does not look like a raw link dump.

## Context & Research

### Relevant Code and Patterns

- **`_generate_payload()` in `plan_backlinks.py` (lines 230–395)**: All article assembly happens here. The body is generated at `body = body_tmpl(...)`, then content_parts are assembled. Injection point is after `body` is finalized and before `content_parts.append(f"\n{body}\n")`.
- **`_build_links()` in `plan_backlinks.py` (lines 118–227)**: Builds the `links[]` references list; the Mode B/C category/detail URLs come from here. This function is NOT changed.
- **`extra_urls` handling (lines 297–345)**: Currently adds extra URL inline lists only when `extra_urls` is provided. The new density injection is separate and mode-conditional.
- **Body template signatures**: All 9 functions take `(domain: str, main_domain: str)`. The injection is done in `_generate_payload` after calling `body_tmpl`, so no signature changes needed.
- **`links_to_markdown()` pattern**: Existing anchor link format in references uses `[anchor](url)` — follow same convention in injected paragraph.
- **Language templates** (`zh-CN`, `en`, `ru`): Injection must use the article's `language` variable (already available in `_generate_payload` scope).

### Institutional Learnings

- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`: Confirmed pipeline is: `plan-backlinks` → `validate-backlinks` → `publish-backlinks`. Injected content must pass `validate-backlinks` validation (presence check passes as long as the URL string appears in `content_markdown`).
- `docs/plans/2026-05-12-003-fix-bare-url-hyperlinks-in-templates-plan.md`: Established pattern: use `[{domain}]({main_domain})` not bare `{main_domain}`.

## Key Technical Decisions

- **Inject at assembly time in `_generate_payload`, not in body template functions**: The 9 body functions are already correctly updated. Modifying them again would require 9 × 3-language changes. A single injection point in the assembly step handles all modes uniformly.
- **Inject only when projected count < 6**: Compute expected count before injecting: `expected = 4 + (1 if target_url != main_domain else 0)`. For Mode A same-URL: 4 < 6, inject 2. For Mode A diff-URL: 5 < 6, inject 1. For Mode B/C: 6+, skip.
- **Use `target_url` in injected links when it differs from `main_domain`**: The user's requirement is A+B ≥ 6. When they're the same, A alone suffices. When different, both URLs should appear to maximise SEO diversity.
- **Short paragraph (1–2 sentences), not a list**: A natural contextual sentence is less likely to trigger spam detection on publishing platforms than a raw list of repeated links.
- **Language-aware injection text**: Three short templates (EN / ZH / RU) are added inside `_generate_payload` using the existing `language` variable. They follow the same prose style as the body templates.

## Implementation Units

- [ ] **Unit 1: Add `_build_link_density_paragraph` helper and call it in `_generate_payload`**

**Goal:** Inject a 1–2 sentence paragraph that adds exactly the links needed to reach A+B ≥ 6 for Mode A articles.

**Requirements:** R1, R2, R3, R4, R5

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks.py`
- Test: `tests/test_plan_backlinks.py`

**Approach:**

Add `_build_link_density_paragraph(domain, main_domain, target_url, language, extra_count)` that:
1. Receives the current target-site link count after body/excerpt/extra sections
2. Returns an empty string if count ≥ 6
3. Returns a short paragraph that contributes the missing links if count < 6

Language templates for the paragraph:

```
EN (target == domain):
  "For more resources, visit [{domain}]({main_domain}) and explore the
  wide range of content available at [{domain}]({main_domain})."

EN (target != domain):
  "Read more at [{domain}]({target_url}) and visit the main hub
  [{domain}]({main_domain}) for the full collection."

ZH (target == domain):
  "欲了解更多资源，请访问[{domain}]({main_domain})，探索
  [{domain}]({main_domain})提供的丰富内容。"

ZH (target != domain):
  "阅读更多请访问[{domain}]({target_url})，并前往
  [{domain}]({main_domain})获取完整内容。"

RU (target == domain):
  "Больше материалов на [{domain}]({main_domain}) —
  посетите [{domain}]({main_domain}) для полного каталога."

RU (target != domain):
  "Читайте подробнее на [{domain}]({target_url}) и
  посетите [{domain}]({main_domain}) для обзора всех материалов."
```

Injection happens right before `content_parts.append(f"\n{body}\n")` is called — append paragraph to `body` variable.

**Target link count estimation formula** (used to decide injection size):
```
base_count = 4   # excerpt(1) + body_template(2) + references_main(1)
if target_url != main_domain:
    base_count += 1  # references_target entry
if url_mode == "B":
    base_count += 1  # categories URL
elif url_mode == "C":
    base_count += 2  # categories + detail URLs
# inject if base_count < 6
```

**Patterns to follow:**
- `_zh_body_a` / `_en_body_a` in `markdown_utils.py` for anchor text style.
- `body_tmpl(domain=domain_label, main_domain=main_domain)` call pattern already in `_generate_payload`.

**Test scenarios:**
- Happy path: Mode A, `target_url == main_domain`, `language=zh-CN` → injected paragraph present, content has ≥ 6 target-site `[…](https://…)` links
- Happy path: Mode A, `target_url != main_domain`, `language=en` → injected paragraph present, both main_domain AND target_url appear as hyperlinks ≥ 1 time each; total ≥ 6
- Edge case: Mode B with diff target_url → no injection, link count stays at 6
- Edge case: Mode C → no injection, link count stays at 7
- Edge case: `language=ru` → Russian-language injection paragraph
- Integration: `plan-backlinks` + `validate-backlinks` end-to-end — all modes pass validation with ≥ 6 target-site links
- Integration: injected links use proper `[anchor](url)` format, no bare URLs (re-run `test_all_main_domain_occurrences_are_hyperlinked` pattern)

**Verification:**
- For all 3 language × all url_mode × same/different target_url combinations: `re.findall(r'\[[^\]]+\]\((https?://{target_domain}[^)]*)\)', content)` returns ≥ 6 results.
- Mode A: count increases from 4–5 to ≥ 6.
- Mode B/C: count is unchanged.
- `pytest tests/test_plan_backlinks.py` passes.

---

- [ ] **Unit 2: Add parametrized link-density test**

**Goal:** Add a test that verifies A+B+C ≥ 6 for all mode / language combinations.

**Requirements:** R1, R2

**Dependencies:** Unit 1

**Files:**
- Modify: `tests/test_plan_backlinks.py`

**Approach:**
- Parametrize over `[("en","A","same"), ("en","A","diff"), ("zh-CN","A","same"), ("zh-CN","A","diff"), ("ru","A","same"), ("ru","A","diff"), ("zh-CN","B","diff"), ("zh-CN","C","diff")]`.
- Use `main_domain = "https://example.com"` and `target_url = "https://example.com/article"` (diff) or same.
- For each: count `re.findall(r'\[[^\]]+\]\(https://example\.com[^)]*\)', content)` and assert `>= 6`.
- This test complements `test_all_main_domain_occurrences_are_hyperlinked` (which checks zero bare URLs) by adding a minimum-count assertion.

**Patterns to follow:**
- `test_all_main_domain_occurrences_are_hyperlinked` in `test_plan_backlinks.py` for parametrization style.

**Test scenarios:**
- Happy path: all 8 parameter combinations → each returns ≥ 6 target-site hyperlinks
- Edge case: Mode A same URL, only main_domain links present → still ≥ 6

**Verification:**
- `pytest tests/test_plan_backlinks.py::test_target_site_link_density` passes for all 8 param combos.

## System-Wide Impact

- **Interaction graph**: `plan-backlinks` stdout → `validate-backlinks`. The added links all use valid `https://` URLs already in the article's domain — `validate-backlinks` checks `main_domain in content_markdown` (string presence), which continues to pass. The `links[]` array count (6–8 total links for references) is unaffected.
- **Unchanged invariants**: `links[]` references array structure, `_build_links()`, `validate_publish_payload()`, body template function signatures, adapter layer.
- **API surface parity**: Both the webui (`ce_generate` → `plan-backlinks` subprocess) and the direct CLI path produce the same content — the injection is in `_generate_payload` which is used by both.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Injected paragraph feels repetitive (domain mentioned again) | Templates use different phrasing from body text; both include a forward-looking CTA to reduce repetitiveness |
| `validate-backlinks` `--check-urls` fails for `/categories` or `/detail` injected URLs | Injection only uses `main_domain` and `target_url` (real URLs provided by user), never constructed paths |
| Injection pushes Mode B/C over 8 | Injection is skipped when current count ≥ 6; Mode B/C are never injected |

## Sources & References

- Related code: `src/backlink_publisher/cli/plan_backlinks.py` `_generate_payload()`, `_build_links()`
- Test file: `tests/test_plan_backlinks.py`
- Prior fix: `docs/plans/2026-05-12-003-fix-bare-url-hyperlinks-in-templates-plan.md`
