---
title: Homepage 三層 URL 輸入結構（主網域 + 分類頁 + 漫畫頁）
type: feat
status: completed
date: 2026-05-14
completed: 2026-05-14
origin: docs/brainstorms/2026-05-14-homepage-three-tier-url-requirements.md
---

# Homepage 三層 URL 輸入結構

## Overview

實作 brainstorm 中定義的三層 URL 輸入結構在 homepage `/`：主網域 + 分類頁 + 漫畫頁。三個結構化欄位取代原本的「target_url + url_new (free-form extras)」單一輸入。提交後：3 個 URL 全部 fetch metadata（沿用既有 `/ce:plan` flow）、過 Plan 007 content-gate、自動寫入 `[sites.<main>.url_categories]` + 升級到 `[targets.<main>]` ThreeUrlConfig schema。

3 個產品決策已在 brainstorm 階段鎖定 — 本 plan 直接套用，不再 deferred。

## Problem Frame

Brainstorm doc 第一節已展開完整 problem statement。要點：

- 操作員心智模型是「主網域 → 分類頁 → 漫畫頁」三層，但表單沒結構化。
- 所有非主 URL 被丟進同一個 `url_new` 文本框，後端無法區分角色。
- 每次提交都要從零打三個 URL，沒有持久化、沒有自動派生入口。

本 plan 提供結構化入口 + 持久化通道，承襲 Plan 007 content-gate 在 form-save 時拒絕無效 URL。

## Requirements Trace

來自 brainstorm doc 的 F1–F6（功能）+ NFR：

- R1（F1）：Homepage `<form action="/ce:plan">` 渲染三個結構化 URL 欄位（main_url / category_url / work_url），各有 badge 標籤。
- R2（F2）：main_url 必填 + https；category_url / work_url 可空但填了須 https。
- R3（F3）：3 個 URL 全部過 Plan 007 content-fetch gate（HTTP 200 + 非空 title）；`BACKLINK_NO_FETCH_VERIFY=1` 旁路。
- R4（F4 + 已鎖定的 Q2）：main_url 對應的 `[targets.<main>]` 若是舊 anchor_keywords schema，自動升級到 ThreeUrlConfig（anchor_keywords → branded_pool；其他 5 字段按 Plan 006 派生）。
- R5（F4 + 已鎖定的 Q3）：寫 `[sites.<main>.url_categories]` 時只 set `home` + `category`（home 自動填 main_url）；不動 hot/animate/topic 等其他既有鍵。
- R6（已鎖定的 Q1）：3 個 URL 都 fetch metadata，沿用既有 `/ce:plan` flow 在 `/ce:generate` 預覽頁渲染 TDK。
- R7（F5）：提交後 session 設 `url_inputs = [main_url]`，生 1 篇文章（category / work 不另開 article）。
- R8（F6）：舊 form-data 名（target_url / url_new）+ 新名（main_url / category_url / work_url）並存；新名優先。
- R9（NFR-觀測）：失敗 422 在頁面上明確顯示哪個欄位 + reason。

## Scope Boundaries

承襲 brainstorm doc 全部 scope boundaries。額外的實作層面排除：

- **不在範圍**：對 `/sites/save-three-url` 的任何改動（Plan 006 的職責）。
- **不在範圍**：原 `url_new` 自由 extras 文本框移除。為向後兼容保留。
- **不在範圍**：homepage 上的 work_urls 多值輸入（單值即可）。
- **不在範圍**：升級流程的「dry-run preview」（顯示 will write 預覽再讓使用者確認）。一步落地。
- **不在範圍**：Plan 008（content-gate perf/observability）的依賴。本 plan 用既有 Plan 007 gate 即可，TTL/stats 可後續單獨繼續。

## Context & Research

### Relevant Code and Patterns

- `webui.py:2795 @app.route('/ce:plan', methods=['POST']) def ce_plan()` — 主要修改點。讀新 form 欄位，3-URL 並發 fetch metadata + content-gate。
- `webui.py:2771 @app.route('/') def index()` — render homepage template；改 `<form>` 結構。
- `webui.py` 的 `_SITES_HTML` 模板的三 URL 表單渲染（PR #9）— 範本，但 simplification 可參考。
- `webui.py:_verify_urls_or_error(urls, field_label)` — Plan 007 Unit 4 新加的 helper，本 plan 直接複用。
- `src/backlink_publisher/config.py:84 ThreeUrlConfig` + `:155 _parse_target_three_url` — schema + 解析；自動升級邏輯需要寫一個對應的「from-anchor-keywords」工廠函式。
- `src/backlink_publisher/config.py:save_config` — 寫入 path。PR #12 Config Safety Net 的 `_preserve_unknown_sections` 保證未寫鍵不會被覆蓋。
- `webui.py:2345 fetch_full_tdk(url)` — 沿用，循環 fetch 3 個 URL。

### Institutional Learnings

- `feedback_test-autouse-verify-mock.md` — 新加的 HTTP 路徑用既有 autouse `_mock_content_fetch` 自動 mock；自動升級邏輯的測試要 mock fetch_full_tdk 路徑（per `feedback_python-mock-datetime-patterns.md` 在 consumer reference）。
- `feedback_config-save-overwrite-pattern.md` — `[sites.<main>.url_categories]` 寫入時只動 `home`/`category` 兩鍵，依靠 PR #12 保留邏輯處理 hot/animate/topic。
- `feedback_standalone-page-vs-retrofit.md` — webui.py 已 4500+ 行，避免新開 sibling page；本 plan 在現有 `/` index + `/ce:plan` POST handler 內局部 retrofit。
- `feedback_brainstorm-prompt-as-desired-state.md` — brainstorm 已鎖定產品決策，避免在 plan 階段再回溯。
- `feedback_recon-level-for-always-on-signals.md` — 自動升級事件用 `plan_logger.recon` 記錄（"target_upgraded_to_threeurl"）。

### External References

無。所有元件都是現有 stack（Flask + jinja2 + bs4 + python-stdlib + 自家 ThreeUrlConfig）。

## Key Technical Decisions

- **HTML 模板採共享 helper 渲染**：不複製 _SITES_HTML 的 form 結構，而是直接在 index 頁的內聯 HTML 中加三個 input field + badge。Rationale: homepage 是輕量入口，不需要完整錯誤訊息 grid，所以 simpler markup 足夠。
- **後端 form 欄位讀取支援雙名（新+舊）**：`main_url := form.get('main_url') or form.get('target_url')`。Rationale: F6 向後兼容。
- **3 個 URL 並發過 content-gate**：直接呼叫 `_verify_urls_or_error([main, category, work], 'URL')`，得到一個總錯誤訊息。Rationale: 簡單；Plan 007 helper 已支援多 URL 批驗證。
- **升級到 ThreeUrlConfig 集中在新 helper `_upgrade_target_to_threeurl(cfg, main_url, category_url, work_url) -> ThreeUrlConfig`**。Rationale: 自動升級邏輯複雜（從舊 anchor_keywords 派生 branded_pool + Plan 006 派生其他 5 字段），需獨立可測單元。
- **寫 `url_categories` 用 merge 而非 replace**：保留既有的 hot/animate/topic 條目。Rationale: brainstorm Q3 鎖定。
- **content-gate 失敗 → re-render homepage 不 redirect**：保持與 `/ce:plan` 失敗路徑既有行為一致（行 2819 `return _render(HTML, error=...)`）。Rationale: 維持向後相容。

## Open Questions

### Resolved During Planning

- **新欄位命名 `main_url` / `category_url` / `work_url` 是否與 `/sites/save-three-url` 表單衝突**？不衝突 —— 兩個路由 form-data 命名空間獨立，僅 mental load 略增。可接受。
- **TDK 預覽 3 個 URL 全 fetch 是否會超時**？3 個 URL 並發 fetch 各 timeout 15s，最壞 case 約 15s wall-clock（並發）；可接受。實作時用 ThreadPoolExecutor 並發。
- **work_url 多值是否支持**？brainstorm 已鎖定單值。後端寫 `work_urls = [work_url]` （長度 1 list）。

### Deferred to Implementation

- **`_upgrade_target_to_threeurl` helper 的 5 個派生字段細節**（list_url / partial_pool / exact_pool / work_anchor_templates / insecure_tls）：實作時參考 Plan 006 派生邏輯，按 main_url 域名 label + main_url 自身作為 fallback。具體閾值與默認看實際 TDK 結果調整。
- **homepage HTML 的 badge 圖示與顏色**：留給實作者按既有 `_SITES_HTML` 的設計語言（type-badge）統一處理。
- **若 main_url 已存在 ThreeUrlConfig 但 work_urls 不空且不同**：覆寫單一第一項 vs append？實作時看更直觀的 UX 決定。傾向覆寫第一項（簡單 + 與 brainstorm 「homepage 是輕量入口」精神一致）。

## High-Level Technical Design

```
POST /ce:plan
       │
       ▼
   read form: main_url (or target_url fallback), category_url, work_url, url_new (legacy)
       │
       ▼
   validate: main_url required + https; category/work optional but https if present
       │
       ▼
   ┌─── content_fetch.verify_urls_batch([main, category, work])
   │   gate fail → 422 + field-level errors (sibling to /sites/save-three-url)
   │
   ▼
   fetch_full_tdk for each (concurrent) → meta_info list (rendered at /ce:generate)
       │
       ▼
   ┌── if category_url: write [sites.<main>.url_categories] {home, category} (merge)
   ├── if work_url:     _upgrade_target_to_threeurl(cfg, main, category, work) → save_config
   └── session['urls_json'] = [main_url] (1-article generation)
       │
       ▼
   redirect / render /ce:generate preview
```

`_upgrade_target_to_threeurl` decision tree:

```
existing = config.target_three_url.get(main_key)
if existing:
    # already ThreeUrlConfig — overwrite work_urls[0], keep rest
    return ThreeUrlConfig(..., work_urls=[work_url])
else:
    # check anchor_keywords legacy
    keywords = config.target_anchor_keywords.get(main_key, [])
    if keywords:
        # migrate: anchor_keywords → branded_pool
        branded = keywords
    else:
        # bootstrap: derive from domain label (Plan 006 fallback)
        branded = [domain_label(main_url)]
    return ThreeUrlConfig(
        main_url=main_url,
        list_url=category_url or main_url,  # fallback if no category
        branded_pool=branded,
        partial_pool=[domain_label(main_url)],  # Plan 006 default
        exact_pool=[domain_label(main_url)],
        work_urls=[work_url] if work_url else [],
        # work_anchor_templates, list_path_blocklist, insecure_tls = defaults
    )
```

## Implementation Units

- [ ] **Unit 1: Homepage HTML 三層輸入結構**

**Goal:** Index 頁的 `<form action="/ce:plan">` 從「target_url + url_new」改為「main_url + category_url + work_url + (optional) url_new」，三個主欄位帶 badge 標籤。

**Requirements:** R1, R8

**Dependencies:** 無。

**Files:**
- Modify: `webui.py`（index() 函式 + 嵌入 HTML，或對應的 jinja2 template 字串）
- Test: `tests/test_webui_three_url.py`（新加 GET / render 測試）

**Approach:**
- 在 homepage HTML 的 `<form method="POST" action="/ce:plan">` 內：
  - 移除既有 `<input name="target_url">` 之外的 main 欄位 markup
  - 加 3 個 `<div class="url-item">`，分別 `name="main_url"` / `name="category_url"` / `name="work_url"`
  - 每個欄位有 badge：「主」/「類」/「漫」
  - main_url 加 HTML `required`；其他兩個不加
  - 保留既有 `name="url_new"` textbox 在「+ 添加更多」區（向後兼容）
- 若實作發現 index 模板過於膨脹，考慮抽 jinja2 template 字串到 module-level constant（但 scope 內不強制）

**Test scenarios:**
- Happy path: GET `/` 回應 HTML 含 `name="main_url"` + `name="category_url"` + `name="work_url"` 三個 input。
- Edge case: 三個欄位都有對應 badge label「主」「類」「漫」(via class 或 text)。
- Edge case: 只有 main_url 帶 HTML `required` attribute；其他兩個不帶。
- Regression: 既有 `name="target_url"` 仍在 HTML 中（向後兼容），或顯式從 server-side 接受 fallback 名。

**Verification:**
- 瀏覽器手工檢視 http://127.0.0.1:8888/ 看到三欄結構。
- `pytest tests/test_webui_three_url.py -k "homepage"` 新測試綠。

---

- [ ] **Unit 2: ce_plan handler 接收新欄位 + 3-URL content-gate**

**Goal:** `/ce:plan` POST handler 讀新 form-data 名（fallback 到舊名）、並發 fetch metadata、批 content-gate 驗證、422 + 字段錯誤上呈失敗。

**Requirements:** R1, R2, R3, R6, R8, R9

**Dependencies:** Unit 1（HTML 結構）。

**Files:**
- Modify: `webui.py`（ce_plan handler）
- Test: `tests/test_webui_three_url.py`（新增 `/ce:plan` POST 測試）

**Approach:**
- ce_plan 開頭：
  ```
  main_url := form.get('main_url') or form.get('target_url') or ''
  category_url := form.get('category_url') or ''
  work_url := form.get('work_url') or ''
  ```
- 維持既有 `url_new` 處理（合併進 extras）。
- 主驗證：main_url 空 → render with error "請輸入主網域"；非 https → render with error "主網域必須 https"。
- 副驗證：category_url 非空但非 https → 422 + field 錯誤；work_url 同樣。
- 集 main + category + work 進一個 list（去 None / empty）→ 過 `_verify_urls_or_error(...)`；fail → render with composite error。
- 對 3 個 URL 並發 fetch_full_tdk（Python `concurrent.futures.ThreadPoolExecutor`, workers=3）→ 結果合進現有 `meta_info` 列表 → session 寫入。
- session 設 `urls_json = json.dumps([main_url])`（只 1 篇）。

**Test scenarios:**
- Happy path：POST 三 URL 全填 + 全 gate 過 → 200 render `/ce:generate` 預覽，session 含三 URL 的 meta_info。
- Happy path：POST 只填 main_url → 200，category/work 不在 meta_info。
- Error path：POST 缺 main_url → render index with error "請輸入主網域"。
- Error path：POST main_url 非 https → render with error。
- Error path：POST main_url gate fail → render with error 含 URL + reason。
- Error path：POST work_url 非 https → 422 + 字段錯誤。
- Backward compat：POST 用舊 `target_url` 名 → 仍工作（fallback 邏輯）。
- Bypass：`BACKLINK_NO_FETCH_VERIFY=1` → gate skip，含 stale URL 也通過。

**Verification:**
- 新測試全綠 + 既有 `/ce:plan` 測試（如有）保持綠。

---

- [ ] **Unit 3: `_upgrade_target_to_threeurl` 升級 helper**

**Goal:** 新 helper 函式（在 config.py 或 webui.py，傾向 config.py 因為它操作 Config）封裝「舊 anchor_keywords → 新 ThreeUrlConfig」遷移邏輯。決策樹見 High-Level Technical Design。

**Requirements:** R4

**Dependencies:** 無（純函式，可獨立測）。

**Files:**
- Modify: `src/backlink_publisher/config.py`（新 helper）
- Test: `tests/test_config_three_url.py`（新測試 class）

**Approach:**
- 函式簽名：`upgrade_target_to_threeurl(config: Config, main_url: str, category_url: str | None, work_url: str | None) -> ThreeUrlConfig`
- Pure 函式：不寫盤、不修改 config，回傳新 ThreeUrlConfig 物件。
- 內部邏輯：
  - 如果 `config.target_three_url[main_key]` 存在 → 覆寫該 entry 的 `work_urls=[work_url]`（如有 work_url）+ `list_url=category_url`（如有 category）；其他字段繼承。
  - 否則：派生新 entry。`branded_pool = config.target_anchor_keywords.get(main_key, [])` if non-empty else `[domain_label]`；`partial_pool=[domain_label]`；`exact_pool=[domain_label]`；`work_urls=[work_url]` if work_url else `[]`；其他用 ThreeUrlConfig defaults。
- `domain_label(main_url) -> str`：抽 main_url 的 host 第一段去 `www.` 前綴（Plan 006 同邏輯，可放 url_utils.py 或內聯）。
- `plan_logger.recon("target_upgraded_to_threeurl", main=main_url, source="anchor_keywords|bootstrap|merge_existing")` 一次 per 升級。

**Test scenarios:**
- Happy path：main_url 已有 ThreeUrlConfig → 升級後 work_urls 覆寫，其餘字段保留。
- Happy path：main_url 只有 anchor_keywords（舊 schema）→ branded_pool 從 anchor_keywords 派生，三 pool 非空。
- Happy path：main_url 既無 ThreeUrlConfig 也無 anchor_keywords → 全部 fallback 到 domain_label。
- Edge case：category_url=None + work_url=None → list_url 用 main_url 兜底，work_urls=[]。
- Edge case：domain_label("https://www.x.com/") == "x"（去 www 前綴）。
- Integration：函式回傳值通過 `_parse_target_three_url` 的 schema 校驗（pool 非空）。

**Verification:**
- 函式測試 + 既有 ThreeUrlConfig schema 測試全綠。

---

- [ ] **Unit 4: ce_plan handler 持久化整合 (sites + targets)**

**Goal:** Unit 2 的 handler 在 gate + fetch 成功後，呼叫 Unit 3 的 helper + 寫 `[sites.<main>.url_categories]`，最終透過 `save_config` 落盤。

**Requirements:** R4, R5, R7

**Dependencies:** Unit 2, Unit 3。

**Files:**
- Modify: `webui.py`（ce_plan handler 持久化區塊）
- Test: `tests/test_webui_three_url.py`（端到端持久化測試）

**Approach:**
- 在 Unit 2 handler 的尾端，在 session 寫入之前：
  - `cfg = load_config()`
  - 如果 `category_url` 非空：
    - 取 `sites = dict(cfg.site_url_categories)`；`sites.setdefault(main_key, {}).update({"home": main_url, "category": category_url})`
    - 將 sites 傳給 save_config（如果 save_config 不支援 site_url_categories 寫入，本 plan 加一個對應參數 — 但傾向直接利用 _preserve_unknown_sections 機制，因為 [sites.*] 不在 _SAVE_CONFIG_KNOWN_ROOTS）
  - 如果 `work_url` 非空（或 main_url 已是 ThreeUrlConfig）：
    - `upgraded = upgrade_target_to_threeurl(cfg, main_url, category_url, work_url)`
    - `target_three_url = dict(cfg.target_three_url); target_three_url[main_key] = upgraded`
    - `save_config(cfg, target_three_url=target_three_url)`
- recon event `homepage_form_persisted` 一次，含 wrote_category=bool, wrote_work=bool, upgraded_from=str（"anchor_keywords"/"bootstrap"/"merge"）。
- 注意：`[sites.*]` 寫入若 save_config 沒支援，需要先擴展 `save_config` API 或直接動 TOML（傾向擴展 save_config，但這擴展屬於本 Unit）。

**Test scenarios:**
- Happy path：POST 三 URL 全填 → load_config 後 `~/.config/backlink-publisher/config.toml` 出現新段：
  - `[sites."<main>".url_categories]` 含 `home = "<main>"` 和 `category = "<category>"`
  - `[targets."<main>"]` 含三 pool + work_urls = ["<work>"]
- Happy path：POST 只填 main_url → 上述兩段都不被寫入（無 deltā）。
- Happy path：既有 anchor_keywords 升級 → 寫盤後 anchor_keywords 已不在 raw TOML，branded_pool 包含原 anchor_keywords 全部。
- Edge case：既有 [sites.<main>.url_categories.hot] = "..."（無關鍵）→ 寫入後 hot 鍵保留（PR #12 _preserve_unknown_sections）。
- Edge case：兩次 POST 連續同 main_url → save_config 冪等，內容相同。
- Integration：寫盤後 `load_config()` 讀回的 cfg.target_three_url + cfg.site_url_categories 同寫入內容。

**Verification:**
- 端到端測試：mock content_fetch (autouse default-pass) + 真實 save_config + load_config 雙向 round-trip。

---

- [ ] **Unit 5: 操作員手工 smoke + recon log 驗證**

**Goal:** 真實瀏覽器 smoke 確認 form 渲染 + 提交 + config.toml 寫入 + plan-backlinks 後續看到新 config 段。

**Requirements:** R1–R9（整合驗證）

**Dependencies:** Unit 1–4。

**Files:** 無代碼變更；只是 smoke checklist。

**Approach:** 操作員（或維護者）按下列流程跑一遍：

1. webui 重啟（拉新代碼）→ 開 http://127.0.0.1:8888/。
2. 看 form：三個結構化欄位 + badge「主」「類」「漫」+ 既有 url_new 在進階區。
3. 填 main_url = `https://example.com/`, category_url = `https://example.com/category`, work_url = `https://example.com/article` → 提交。
4. 預期：5–15s 後 redirect 到 `/ce:generate` 預覽頁；session 含 3 URL 的 TDK。
5. `cat ~/.config/backlink-publisher/config.toml` → 看到 `[sites."https://example.com".url_categories]` 含 home + category；`[targets."https://example.com"]` 含三 pool + work_urls。
6. 後續：`echo '{...}' | plan-backlinks` 走 url_mode B → 看 `link_dropped_no_content` 是否含 category URL 對應的 reason（若 example.com/category 真 404 → drop；若不 → 仍出現在 links）。
7. recon log grep：`homepage_form_persisted` + `target_upgraded_to_threeurl` 應各出現 1 次。

**Test scenarios:**
- Test expectation: none — 操作員手工 smoke checklist，不寫成 pytest case。

**Verification:** 6 個 checkbox 全勾。

## System-Wide Impact

- **Interaction graph:** Homepage `/` form → `/ce:plan` handler → content_fetch.verify_urls_batch + fetch_full_tdk + (新) upgrade_target_to_threeurl + save_config。下游：既有 `/ce:generate` 預覽頁不變；plan-backlinks 讀新 config 段（PR #19 + #21 已支援）。
- **Error propagation:** content-gate fail → 422 同 `/sites/save-three-url` 模式（render index with error）；config write fail → 既有 save_config 例外傳遞（操作員看到 500，需查日誌）。
- **State lifecycle risks:** save_config 內 PR #12 atomic-write + snapshot 保證寫盤安全；[sites.*] 段透過 _preserve_unknown_sections 不被誤覆寫。
- **API surface parity:** `/sites/save-three-url` 接收的 form-data 名（main_url / list_url / work_urls / branded_pool / partial_pool / exact_pool） vs `/ce:plan` 接收的 form-data 名（main_url / category_url / work_url / url_new）— 兩 form 命名空間獨立，但同樣的概念有不同命名（list_url vs category_url）需在 user-facing docs 對齊。
- **Integration coverage:** Unit 4 端到端測試覆蓋寫盤 → 讀回；plan-backlinks 後續對新 config 段的消費由 PR #21 既有測試覆蓋（regression）。
- **Unchanged invariants:** ThreeUrlConfig schema（三 pool 非空）、PR #12 config preserve 邏輯、Plan 007 content-gate 契約、`/ce:generate` / `/ce:validate` / `/ce:publish` flow、舊 form-data 名 backward compat。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 自動升級邏輯破壞既有 ThreeUrlConfig schema（pool 非空契約）。 | Unit 3 helper 保證每個 pool fallback 到至少 `[domain_label]`；Unit 4 整合測試 load_config 讀回校驗。 |
| `[sites.<main>.url_categories]` 寫入時 save_config 不支援該段，需擴展 API。 | 本 plan Unit 4 內包含 save_config API 擴展；若範圍超出，拆出新 PR。 |
| 三 URL 並發 fetch_full_tdk 的合計延遲讓表單看起來掛掉。 | ThreadPoolExecutor workers=3 並發；最壞 case 約 15s（單 URL timeout）；操作員需要在前端加 loading spinner（本 plan 不強制，留作 follow-up）。 |
| 升級後操作員後悔（想保留 anchor_keywords 不變）。 | PR #12 snapshot 寫入 `.config-history/<ts>.toml`，可回滾；recon `target_upgraded_to_threeurl` 事件記錄遷移時點。 |
| `_upgrade_target_to_threeurl` 在多 main_url 並發提交時 race（兩 form submit 同時改同一個 cfg）。 | webui 是單進程 Flask debug server，無真並發。生產 deployment 改 WSGI 時再加 file lock 或 cas。本 plan 不處理。 |
| 既有測試對 homepage HTML form 的斷言因為新結構失敗。 | Unit 1 加新 GET / render 測試；既有測試若依賴 target_url 名仍可工作（向後兼容）。 |

## Documentation / Operational Notes

- **CHANGELOG**：「Homepage 表單從單 URL 結構升級到 三層結構（主網域 + 分類頁 + 漫畫頁）；提交後自動寫入 sites + targets config，舊 anchor_keywords schema 自動升級到 ThreeUrlConfig。」
- **Operator runbook**：加 `target_upgraded_to_threeurl` + `homepage_form_persisted` 到 recon grep 列表。
- **`.config-history/` 提示**：操作員 doc 應提到「如果意外升級了 target schema，可在 .config-history/ 找回升級前的 TOML」。
- **與 Plan 006 關係**：Plan 006（/sites 表單極簡化）尚未實作。本 plan 完成後，操作員從 homepage 入口走快速 path；若要進階配置，仍走 /sites（Plan 006 後續可優化 /sites 為操作員減負）。
- **與 Plan 008 關係**：Plan 008（content-gate perf/observability）正在做但未完成。本 plan 用 Plan 007 既有 gate，不依賴 Plan 008 任何 unit。Plan 008 完成後，本 plan 自動受益（cache TTL + cross-row prefetch）。

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-14-homepage-three-tier-url-requirements.md](../brainstorms/2026-05-14-homepage-three-tier-url-requirements.md)
- Related plans:
  - `docs/plans/2026-05-14-006-feat-sites-form-minimal-input-plan.md`（/sites 表單極簡化 — 互補但獨立）
  - `docs/plans/2026-05-14-007-feat-url-content-fetch-gate-plan.md`（content-gate；本 plan 直接複用 PR #22 helper）
  - `docs/plans/2026-05-13-004-feat-work-themed-backlinks-plan.md`（ThreeUrlConfig + work_urls schema 原始 plan）
- Related code:
  - `webui.py:2795 ce_plan`, `:2771 index`, `:4264 sites_save_three_url`（模式參考）
  - `src/backlink_publisher/config.py:84 ThreeUrlConfig`, `:155 _parse_target_three_url`, `:save_config`
- Related PRs/issues: #9, #12, #19, #20, #21, #22
- Memory: `feedback_test-autouse-verify-mock.md`, `feedback_config-save-overwrite-pattern.md`, `feedback_standalone-page-vs-retrofit.md`, `feedback_brainstorm-prompt-as-desired-state.md`, `feedback_recon-level-for-always-on-signals.md`
