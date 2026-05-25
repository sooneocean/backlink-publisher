---
title: "feat: Canonical URL contract + Notion/Dev.to adapters + IndexNow push"
type: feat
status: completed
date: 2026-05-21
completed: 2026-05-21
claims: {}
origin: docs/brainstorms/2026-05-21-canonical-contract-and-platform-expansion-requirements.md
---

# Canonical URL Contract + Platform Expansion (Notion / Dev.to / IndexNow)

## Overview

把外部顧問的 SEO 補充建議落地為 **2 個獨立 PR**（PR-C 已 defer，見下）：

- **PR-A**：把已存在但只有 `medium_api.py` 消費的 `seo.canonical_url` schema 契約**推廣到既有 dofollow adapter**（Hashnode / Blogger / GHPages / Writeas），補 schema URL validator + adapter per-context escape（per doc-review P0-2 security），補 cross-adapter 回歸測試（含 injection fixture）。Telegraph + Velog 結構性無 canonical 欄位，docstring 註明跳過。
- **PR-B**：新增 **Notion + Dev.to** adapter（reuse R9 extension readiness 路徑，含 canonical opt-in + per-context escape），補 **Hashnode paywall 偵測**（per doc-review P0-1：移進 `publish()` 進入點 raise `ExternalServiceError`，**不**改 `available()` 契約）。
- ~~**PR-C**~~：**DEFERRED**（per doc-review P0-3 feasibility）— IndexNow protocol 要求 operator 對 `<host>/<key>.txt` 部署金鑰，但本系統 published_url 是平台 host（medium.com/notion.so/dev.to/etc），operator 無寫入權。需在新 brainstorm 釐清推送對象是「operator 自站變動」還是「新生 backlink 頁」再決定 protocol。本期不 ship 推送層。

Medium adapter family（含 PR #138/#141 Chrome backend OPEN 工作）**不動**。

## Problem Frame

顧問送來 6 平台排程 + SEO 規範 + 主動推送建議（see origin: `docs/brainstorms/2026-05-21-canonical-contract-and-platform-expansion-requirements.md`）。對照現況有 3 個張力：

1. 顧問「全平台都帶 canonical_url 指自站」會把外站頁標為 syndication copy → 外站頁退出 SERP → 與本項目 dofollow gate / anchor proportions / footprint regression 的 pure-backlink 戰略相反。
2. 顧問建議下線 Medium API 走「Dev.to → 手動 Import」，與 PR #138/#141 Chrome backend OPEN 投資衝突。
3. 顧問的 Google Indexing API 官方僅支援 JobPosting/BroadcastEvent，一般 URL 推送實效近 0；IndexNow 才是實際可用標準。

本計畫採 **Mixed canonical 策略**（per-row opt-in：`payload.seo.canonical_url` 帶就走 syndication，未帶走 pure backlink），保留 Medium 投資，用 IndexNow 取代 Google Indexing API。

## Requirements Trace

對應 origin doc R1–R15：

- **R1**：`payload.seo.canonical_url` opt-in 契約（schema 已具備，僅需 adapter wiring）→ Unit 1, 4
- **R2**：所有 dofollow 平台 adapter 讀同一欄位 → Unit 2
- **R3**：不強制 `canonical_url` → Unit 1
- **R4**：cross-adapter canonical 回歸測試 → Unit 3
- **R5**：Notion adapter → Unit 6
- **R6**：Dev.to adapter，標 NoFollow → Unit 7
- **R7**：兩個新 adapter 必過 R9 extension readiness → Unit 6, 7
- **R8**：Hashnode `available()` Pro tier 偵測 → Unit 8
- **R9**：WebUI Hashnode 卡片顯示 paywall 狀態 → Unit 8
- **R10–R11**：**[DEFERRED]** IndexNow 主推送 + 新 CLI（per doc-review P0-3 feasibility — PR-C 整個 defer，IndexNow protocol 與本系統 published_url 結構性衝突，重新 brainstorm 推送對象）
- **R12**：**[OUT-OF-SCOPE]** Google Indexing API 預留 opt-in（默認不開）→ scope-boundary，本期不實作
- **R13**：**[DEFERRED]** GSC sitemap ping → 隨 PR-C 一起 defer
- **R14–R15**：**[OUT-OF-SCOPE]** Medium 戰略保留 → scope-boundary，無 unit（詳見 Scope Boundaries §Medium 戰略保留）

### Execution Structure (auto-clarify per doc-review F004)

**2 PRs → 2 Phases → 8 Implementation Units**（post doc-review fold-ins + PR-C defer）：

- **PR-A = Phase 1**: Units 1-3（canonical contract cross-adapter wiring + URL validator + per-context escape）
- **PR-B = Phase 2**: Units 4-9（Notion + Dev.to adapter + Hashnode paywall via publish-entry + WebUI wiring）
- ~~**PR-C**~~: **DEFERRED**（IndexNow + GSC ping，重新 brainstorm）

每 unit 為獨立可測試單位。**PR-A 與 PR-B 在合併順序上沒有硬技術相依性**（per adversarial F6）：PR-B 的 Notion/Devto canonical 可直接在新 adapter 內含；PR-A blocked 不阻塞 PR-B ship。**PR-A 內部 Blogger 風險亦獨立**：feasibility 已驗 Blogger Posts v3 schema 無 post-level head-meta 欄位，body 內 `<link rel=canonical>` 不被 Google head-only spec 認可 — Unit 2 內 Blogger 以「best-effort cosmetic marker, no SEO impact expected」docstring 落地，或可在 PR-A 內子分（Blogger 抽出 follow-up PR）。

### Terminology Note (auto-clarify per doc-review F003)

所有 adapter 從**單一 schema 源** `payload.seo.canonical_url` 讀取。各 adapter 的平台特定欄位名（Hashnode `input.originalArticleURL`、Jekyll `canonical_url:`、Dev.to `article.canonical_url`、HTML `<link rel="canonical">`）是同一個值的**平台 translation**，不是不同 schema 欄位。

## Scope Boundaries

- ❌ 不動 Medium adapter family（4 路徑 + PR #138/#141 Chrome backend OPEN）。
- ❌ 不採用 Google Indexing API 作為主推送。`JobPosting` opt-in 屬於 R12 預留，本期不實作。
- ❌ 不強制全平台 canonical（per-row opt-in only）。
- ❌ 不換 storage backend（不採用顧問 Google Sheets 建議）。
- ❌ 不做 IndexNow key auto-rotation；單 key 持久化即可。
- ❌ 不做 Bing Webmaster URL Submission API（IndexNow 已覆蓋 Bing）。
- ⚠️ Hashnode adapter 保留但 free-tier paywall 後不可用；`available()` 返 False → dispatcher raise DependencyError（**不**靜默 skip — 詳見 Unit 8 dispatcher 訊息傳遞設計，per doc-review P0-1）。
- ⚠️ Dev.to v1 單帳號；multi-publication 等需求出現再擴。
- ❌ **Velog canonical wiring 移出本期**：feasibility 已驗 `velog_graphql.py` `WRITE_POST_MUTATION` 結構性為固定 7 欄位無 canonical 等價（per doc-review P1）；Velog 與 Telegraph 並列為「結構性不支援 canonical」，docstring 註明，不修改代碼，不計入 Unit 2 file list。

## Context & Research

### Relevant Code and Patterns

- **Schema 既有 SEO 契約**：`src/backlink_publisher/schema.py:80-86` 把 `seo` 列入 OUTPUT_FIELD_TYPES；`schema.py:316-321` 對 `seo` block 內 `title` / `description` / `canonical_url` 三個 string 必填欄位驗證。**已存在，不需新增，只需各 adapter 消費。**
- **唯一消費者**：`src/backlink_publisher/publishing/adapters/medium_api.py:136` 讀 `payload.get("seo", {}).get("canonical_url")` → 寫到 Medium API 的 `canonicalUrl` body field。**這是本計畫所有 adapter 要 mirror 的 reference pattern。**
- **Adapter 註冊 pattern**：`src/backlink_publisher/publishing/adapters/__init__.py:46-52` 每平台一行 `register("x", XAdapter)`。R9 extension readiness（`tests/test_r9_extension_readiness.py`）禁止改 `cli/*.py` 或 `schema.py`。
- **Publisher ABC**：`src/backlink_publisher/publishing/registry.py` — 子類實作 `publish(payload, mode, config) -> AdapterResult`；`available(cls, config)` opt-in 排除（reference: `MediumBraveAdapter` macOS-only）。
- **AdapterResult**：`src/backlink_publisher/publishing/adapters/base.py:21-46` — `status / adapter / platform / draft_url / published_url / error / post_publish_delay_seconds / _provider_meta`。canonical 不需新欄位，per-row 寫入平台即可。
- **Hashnode adapter reference**：`src/backlink_publisher/publishing/adapters/hashnode.py:1-50` — GraphQL 單 endpoint，`Authorization: <pat>`（**無 Bearer 前綴**），publication-scoped。
- **Token 儲存 pattern**：`src/backlink_publisher/config/tokens.py:64-109` — 每平台一對 `load_X_token` / `save_X_token`。`telegraph_api.py` 是 canonical credential-rotation reference（per memory: `[[telegraph-adapter-credential-rotation-pattern]]`）。
- **Ghpages Jekyll front-matter**：`src/backlink_publisher/publishing/adapters/ghpages.py:124-139` — 已有 `front_matter_lines` 構造，加 `canonical_url:` 一行即可。
- **Telegraph node tag whitelist**：`src/backlink_publisher/publishing/adapters/telegraph_node.py` — Telegraph 不支援 `<link rel="canonical">`（不在 native tag 集合）。Telegraph 平台只能放棄 canonical 注入（顧問建議 R2 對 Telegraph 不適用）。
- **WebUI token-paste pattern**：`webui_app/routes/token_paste.py:38-41` `_ALLOWED` dict 加 entry；`webui_app/templates/_channel_card_macro.html` reuse 卡片；wire 5 站點（per memory: `[[wire-token-paste-channel-five-sites]]`）。
- **link_attr_verifier**：`src/backlink_publisher/publishing/adapters/link_attr_verifier.py` — runtime 抓 published URL 驗 `rel=nofollow` 注入，本計畫 Unit 6 用它驗證 Notion dofollow 狀態。

### Institutional Learnings

- `[[probe-then-pivot-when-api-unverifiable]]` — 顧問建議的 API 在落地前要 probe；Hashnode paywall 是已知 case，Notion dofollow status 未驗證需 plan 內 probe。
- `[[grep-dofollow-map-before-shipping-adapter]]` — R9 extension readiness 驗 registry pattern 不驗 link 屬性值；新 adapter 必須 grep dofollow 證據再 ship（U6 verification step）。
- `[[wire-token-paste-channel-five-sites]]` — 新 WebUI token-paste card 必須改 5 處（import / `_token_paste_status` / config_summary / return dict / template）。
- `[[fetch-json-must-guard-content-type]]` — WebUI JS `await resp.json()` 前必檢 `content-type`（U8 paywall status endpoint 注意）。
- `[[telegraph-adapter-credential-rotation-pattern]]` — credential write 走 path resolver + atomic write + flock；IndexNow key 持久化 mirror 此 pattern。
- `[[plan-doc-on-cutoff-needs-claims-block]]` — plan-claims gate 2026-05-20 cutoff；本 plan 日期 2026-05-21 > cutoff，需要 `claims:` 或對應 frontmatter；plan-check 階段確認（U13 verification）。
- `[[grep-all-legacy-import-forms]]` — 過去的 legacy import bridge 已删（PR #124）；新 adapter 用 canonical path `from backlink_publisher.publishing.adapters.X` 不用 flat。

### External References

未使用外部 research — 顧問 brief 已提供 API endpoint + payload shape；canonical 戰略決定在 brainstorm 完成；codebase pattern 充足。

## Key Technical Decisions

- **canonical = per-row opt-in via `payload.seo.canonical_url`**：理由是本項目戰略 = pure backlink builder（dofollow gate + anchor proportions + footprint regression 均指向此戰略）；全平台強制 canonical 會把外站頁標 syndication → 退出 SERP → 與 backlink 戰略相反。少數 syndication 場景仍能 per-row 帶。
- **Schema 加 URL format validator（per doc-review P0-2）**：`seo.canonical_url` 已是 schema 必填欄位（type string），但本 plan 額外加 URL regex 驗證（require `^https?://`，拒 control chars / quotes / angle-brackets）。**Defense in depth**：schema-layer 阻擋大部分注入 payload，但 adapter 在輸出層仍須 per-context escape（不純信任 schema gate）。
- **Per-context escape required at each adapter（per doc-review P0-2）**：Blogger/Writeas HTML body 注入須 HTML-escape；ghpages Jekyll front-matter 用 quoted YAML string；Hashnode 用 GraphQL variables 不用 string interpolation；Notion children block 經 `rich_text.link.url` 結構化欄位（已自帶 escape）。每 adapter Unit 2 verify step 含 injection fixture。
- **Hashnode paywall 偵測進 `publish()` 進入點（per doc-review P0-1）**：**不**改 `available()` 契約（保持 Publisher ABC 一致性 + 不影響其他 adapter）；`publish()` 進入後若帳號為 free-tier（GraphQL probe `me { publication }` 返回 null）直接 raise `ExternalServiceError("Hashnode GraphQL paywall — Pro plan required since 2026-05-13...")`。dispatcher 不 fallthrough（既有 ExternalServiceError 語意），publish-history `failure_reason` 收到 rich message。WebUI 卡片透過**獨立 status probe endpoint**（不走 dispatcher）顯示 paywall pill。
- **Telegraph 不注入 canonical**：Telegraph node API tag whitelist 不含 `<link>`，物理無法注入；Unit 2 跳過 Telegraph 並在 docstring 明示「Telegraph 結構性限制」。
- **Notion canonical 用 children block**：Notion Page 不支援 page-level meta，只能在 children block 內加「Original: <url>」段落（非 SEO `<link rel=canonical>`，但對閱讀者明示）。Notion dofollow 狀態本身待 U6 驗證。
- **Dev.to canonical_url 原生支援**：API 直送 `article.canonical_url` 欄位。Dev.to 是 NoFollow，不入 dofollow shortlist，**保留是為 entity 信號 / 收錄速度**，不作主力 backlink。
- **Hashnode paywall 偵測 = `me { publication }` GraphQL query**：Pro tier 帳號回傳 publication list；free-tier 帳號 GraphQL 仍接受 query 但業務層 `publishPost` mutation 會 403/limit。用 query 而非 dry-run mutation 避免污染。
- **IndexNow 推送觸發 = 獨立 CLI `report-indexing-push`**：契合既有 6-CLI 對等架構（plan-/validate-/publish-/report-anchors/footprint/phase0-seal）；避免併入 publish-backlinks 後 push 失敗污染 publish status。
- **IndexNow key 儲存 = `<config_dir>/indexnow.key`**：純 plaintext 單行 8-128 字元（per IndexNow spec）；mirror `telegraph_api.py` rotation pattern（path resolver + atomic write + flock）；自動生成 UUID4 若不存在。
- **Google Indexing API 駁回主推**：官方只支援 JobPosting/BroadcastEvent schema；本項目內容是 backlink 文章不是 JobPosting → 推送無效。R12 預留 opt-in（不實作）以保 design space 開放。
- **GSC sitemap ping = best-effort GET**：`https://www.google.com/ping?sitemap=...` zero auth、不阻塞 IndexNow 主路徑、失敗 silent。

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

**Canonical 注入分類矩陣**（per adapter）：

| Platform | 注入方式 | 來源欄位 | 既有狀態 |
|---|---|---|---|
| medium | API body `canonicalUrl` | `seo.canonical_url` | ✅ 已實作（reference） |
| hashnode | GraphQL `input.originalArticleURL` | `seo.canonical_url` | ❌ 補 |
| devto | API body `article.canonical_url` | `seo.canonical_url` | ❌ 新 adapter 直接含 |
| ghpages | Jekyll front-matter `canonical_url:` | `seo.canonical_url` | ❌ 補 |
| blogger | HTML body `<link rel="canonical" href=...>` 注入到 post content head | `seo.canonical_url` | ❌ 補 |
| velog | **N/A** — feasibility 已驗 `velog_graphql.py:74-87` `WRITE_POST_MUTATION` 固定 7 欄位無 canonical 等價（per doc-review P1） | — | 跳過 + docstring 註明（與 telegraph 並列）|
| notion | children block「Original article: <url>」段落（無 native meta） | `seo.canonical_url` | ❌ 新 adapter 直接含 |
| writeas | HTML `<link rel="canonical">` 注入 | `seo.canonical_url` | ❌ 補（writeas 已從 WebUI 退役但 adapter 保留） |
| telegraph | **N/A** — Telegraph node tag whitelist 結構性不支援 | — | 跳過 + docstring 註明 |

**IndexNow 推送流程**：

```
report-indexing-push <history.jsonl>
    │
    ├── parse status=="published" rows (last N days, default 7)
    ├── load/auto-gen IndexNow key from <config_dir>/indexnow.key
    │       (UUID4 if missing, atomic write + flock per telegraph_api pattern)
    ├── publish key at <site_root>/<key>.txt (best-effort GET verify)
    │       — 若用戶 root 不可寫，skip 此步並 warn；IndexNow 仍接受 host-key 雙驗
    ├── POST https://api.indexnow.org/indexnow
    │       { host, key, keyLocation, urlList: [...] }
    ├── parse response (200/202 OK；422 = invalid key；403 = key not found at location)
    └── stdout: JSONL { url, submitted_at, status, search_engines:[...] }
        stderr: human-readable summary
```

**Hashnode paywall 偵測**（U8）：

```
HashnodeAPIAdapter.available(config) →
    if hashnode_token absent → False (no behavior change)
    else GraphQL POST `query { me { publication { id } } }`
        if 200 + publication non-empty → True (Pro tier)
        if 200 + publication empty/null → False + log "free-tier paywall"
        if 4xx/5xx → False + log network/auth failure
    cache result for adapter instance lifetime
```

publish() 進入後若 `available()` 已回 False，dispatcher 跳到下一個 adapter；若無 fallback adapter（hashnode 單 adapter），raise `DependencyError("Hashnode GraphQL paywall — Pro plan required since 2026-05-13")`，publish-history `failure_reason` 寫入此訊息。

## Implementation Units

### Phase 1 (PR-A): Canonical URL Contract Cross-Adapter Wiring

- [x] **Unit 1: Schema canonical_url URL validator + docstring (REVISED per doc-review P0-2)**

**Goal:** 為 `seo.canonical_url` 加 URL format validator（require `^https?://`，拒 control chars / quotes / angle-brackets），形成 layered defense 第一層；補 docstring 註明 opt-in 契約。**Unit 1 + Unit 2 共同 ship 為 PR-A 的第一個 commit**。

**Requirements:** R1, R3

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/schema.py` — `validate_output_payload` SEO block 加 URL regex 驗證 + docstring 註解
- Test: `tests/test_schema_seo_canonical_contract.py`（新增；含 injection fixture）

**Approach:**
- schema.py:316-321 加 URL format check（`re.match(r"^https?://[^\s\"'<>\x00-\x1f]+$", canonical_url)`）；若 fail 加錯誤訊息 `"seo.canonical_url must be a valid https?:// URL without control chars or HTML metacharacters"`。
- 加 docstring：明示「`seo` block opt-in；帶了則 title/description/canonical_url 三欄必填且為 string；不帶 = pure backlink mode」。
- **此 Unit 是 layered defense 第一層**（schema validator）；adapter 在 Unit 2 加第二層（per-context escape）。

**Patterns to follow:** `schema.py` 既有 comment 風格（如 OUTPUT_ONE_OF_GROUPS 上方多行說明）。

**Test scenarios:**
- Happy path: row 有 `seo` block + valid https URL → `validate_publish_payload` 返回 `[]`。
- Happy path: row 無 `seo` block → `validate_publish_payload` 返回 `[]`（pure backlink mode）。
- Edge case: `seo.canonical_url = ""` → 接受（empty string 仍為 string，adapter 視同未提供 via `... or None`）。
- Edge case: `seo` 缺 canonical_url → 返回錯誤 `seo: missing field 'canonical_url'`。
- Edge case: 非 string → 錯 `seo.canonical_url must be a string`。
- **Security: HTML XSS injection** `'"><script>alert(1)</script>'` → reject (含 HTML metacharacters)。
- **Security: HTML attr injection** `'" onerror=alert(1) x="'` → reject。
- **Security: YAML injection** `"https://x\nmalicious: true"` → reject（含 newline/control char）。
- **Security: protocol injection** `"javascript:alert(1)"` → reject（不匹配 https?://）。
- **Security: GraphQL escape break** `'https://x"}{evil}'` → reject。
- Happy path: 正常 URL `https://example.com/post?q=1` → accept。

**Verification:**
- `pytest tests/test_schema_seo_canonical_contract.py` 全綠。
- `git grep "seo.canonical_url"` 文件級 reference 出現在 schema docstring + medium_api.py。

---

- [x] **Unit 2: Adapter canonical consumption — non-Medium dofollow platforms**

**Goal:** 把 `medium_api.py:136` 的 canonical pattern 推廣到**既有 dofollow adapter**：Hashnode / Blogger / GHPages / Writeas 四個 adapter；Telegraph + Velog 結構性跳過並 docstring 註明（兩者均不支援 canonical 欄位）；Notion / Dev.to 在 Unit 6/7 新 adapter 內直接含 canonical（不需 retrofit）。**Unit 1 已 fold into 此 Unit**（per doc-review SG-1）：本 Unit 第一個 commit 包含 schema docstring 註解 + 5 個 characterization 測試。

**Requirements:** R2

**Dependencies:** Unit 1（schema 行為確認）

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/hashnode.py` — GraphQL `input.originalArticleURL` 帶入
- Modify: `src/backlink_publisher/publishing/adapters/blogger_api.py` — HTML body 注入 `<link rel="canonical">`（注入位置：post content 開頭）
- Modify: `src/backlink_publisher/publishing/adapters/ghpages.py` — front_matter_lines 加 `canonical_url:` 行（ghpages.py:124-139）
- Modify: `src/backlink_publisher/publishing/adapters/velog_graphql.py` — 確認 velog GraphQL `createPost` 是否支援 canonical 欄位；若支援帶入，若不支援在 docstring 註記跳過
- Modify: `src/backlink_publisher/publishing/adapters/writeas.py` — HTML body `<link rel="canonical">` 注入
- Modify: `src/backlink_publisher/publishing/adapters/telegraph_api.py` + `telegraph_node.py` — docstring 補「Telegraph 結構性不支援 canonical 注入」說明，不改邏輯
- Test: `tests/test_adapter_canonical_emission.py`（新增；per-adapter 參數化）

**Approach:**
- 每個 adapter 在 `publish()` 入口取 `canonical = payload.get("seo", {}).get("canonical_url") or None`，None 時走 pure backlink 路徑（不注入），有值時走平台對應注入。
- 不引入共用 helper（adapter 自治 + R9 extension readiness 不要求 cross-adapter shared canonical helper；若未來 ≥3 adapter 同模式可重構）。
- **Per-context escape（layered defense 第二層 per doc-review P0-2）**：
  - Blogger / Writeas HTML body：`html.escape(canonical_url, quote=True)` 後注入 `<link rel="canonical" href="...">`。
  - ghpages Jekyll front-matter：用 quoted YAML string `canonical_url: "..."`（已 schema 阻擋 quote/newline，雙重保險）。
  - Hashnode GraphQL：用 variables `$canonical: String` 不用 string interpolation。
  - Notion children block：透過結構化 `rich_text.text.link.url` 欄位（Notion SDK 自帶 JSON escape，免手動）。
  - 即便 schema 已 reject 多數注入 payload，adapter 仍須這層 escape — schema validator 偶有 regex 漏網 / 未來放寬 / 測試漏挑時是最後防線。
- **Blogger HTML body canonical 標記為 cosmetic-only（per doc-review feasibility F4）**：W3C / Google 規範 `<link rel="canonical">` 僅在 `<head>` 認可；body 注入 Google 不解析。Blogger Posts v3 API 無 post-level head-meta 欄位，無法注入正確位置。決定：Unit 2 仍實作 body 注入但 docstring 明示「best-effort cosmetic marker, no expected SEO impact — kept for explicit syndication intent rather than enforcement」。若 future Blogger 支援 head-meta 欄位，retrofit。

**Patterns to follow:** `medium_api.py:136-147`（canonical opt-in 模式）；`ghpages.py:124-139`（front_matter line append 模式）。

**Test scenarios:**
- Happy path (per adapter): payload 帶 `seo.canonical_url = "https://example.com/post"` → adapter 輸出（mocked API call body / front_matter / HTML body）含正確 canonical 標記。
- Happy path (per adapter): payload 無 `seo` → adapter 輸出**完全沒有** canonical 痕跡（grep `canonical` 應 0 hit）。
- Edge case: `seo.canonical_url` 為 `""` → 視同未提供，pure backlink mode。
- Edge case: Hashnode `originalArticleURL` mutation variable 缺欄位時 API 行為一致（API 接受 null）。
- Edge case: ghpages front_matter `canonical_url: "https://..."`格式正確 YAML（不破壞 Jekyll 解析）。
- Error path (per adapter): adapter 收到 canonical 帶非法 URL（非 https://）→ adapter **不**自己擋（schema.py 已做基本驗證；adapter 信任 schema gate）。
- Telegraph: payload 帶 canonical → adapter 不注入 + log warning（或保持 silent，per docstring 決定）。

**Verification:**
- `pytest tests/test_adapter_canonical_emission.py -v` per-adapter 全綠。
- `git grep "seo.canonical_url\|canonical_url\|originalArticleURL"` 在 publishing/adapters/ 下出現 ≥6 處（medium + hashnode + blogger + ghpages + velog + writeas，視 velog API 支援度可能 5 處）。
- `pytest tests/test_r9_extension_readiness.py` 仍綠（未動 cli/schema）。

---

- [x] **Unit 3: Cross-adapter canonical regression suite**

**Goal:** 建立**負向**回歸測試套件 — 確保沒有任何 adapter 在 `seo` 未提供時 default-on 注入 canonical（防未來 PR 不小心改 default）。

**Requirements:** R4

**Dependencies:** Unit 2

**Files:**
- Create: `tests/test_canonical_contract.py` — 對 `registered_platforms()` 動態列表參數化測試每 adapter 雙路徑（帶 / 不帶 canonical）。

**Approach:**
- 用 `pytest.mark.parametrize` + `from backlink_publisher.publishing.registry import registered_platforms` 取所有平台，**動態**生成測試 case。
- 每平台 mock 對應 HTTP / file 寫入，斷言：
  - 帶 canonical → 輸出 body/header/file 含 canonical URL 字串。
  - 不帶 canonical → 輸出**任何位置都不含** `"canonical"` 字串（case-insensitive grep）。
- Telegraph adapter 走 skip path（`pytest.skip("Telegraph 結構性不支援 canonical")`），但仍跑 negative test（確認不會誤注入）。

**Patterns to follow:** `tests/test_r9_extension_readiness.py` — 動態參數化 over `registered_platforms()` 的同款 pattern。

**Test scenarios:**
- Happy path: 動態 over 全平台 — 帶 canonical → 輸出含 canonical 字串（per-platform 預期定位點）。
- Happy path: 動態 over 全平台 — 不帶 canonical → 輸出 grep `canonical` (case-i) hit 0。
- Edge case: 新 adapter 加入 registry 後自動進入測試（防 default-on 退路 + 防漏網新平台）。
- Integration: 跑 `pytest tests/test_canonical_contract.py` 在加 Notion + Dev.to（U6/U7）之後自動覆蓋 8 個 adapter。

**Verification:**
- `pytest tests/test_canonical_contract.py -v` 全綠。
- 假動：暫時把某 adapter 改為「未帶 canonical 也注入空 canonical」，跑測試應紅。回滾。

---

### Phase 2 (PR-B): Notion + Dev.to Adapters + Hashnode Paywall

- [ ] **Unit 4: Token storage + config loaders for Notion + Dev.to**

**Goal:** 在 `config/tokens.py` 加 `{load,save}_notion_token` / `{load,save}_devto_token`，mirror 既有 7 個平台的 token loader pattern。

**Requirements:** R5, R6

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/config/tokens.py` — 加 4 個 function
- Modify: `src/backlink_publisher/config/__init__.py` — re-export
- Test: `tests/test_config_tokens_notion_devto.py`（新增）

**Approach:**
- Notion token 結構：`{"integration_token": str, "database_id": str}`（Notion 需要 database ID）。
- Dev.to token 結構：`{"api_key": str}`（單欄）。
- 檔案位置：`<config_dir>/notion-token.json`、`<config_dir>/devto-token.json`，0600 perm（mirror ghpages/hashnode pattern）。
- 用 `BACKLINK_PUBLISHER_CONFIG_DIR` env 重新解析 path（per memory: `[[config-paths-must-respect-env-var]]`），不寫死 `~/.config`。

**Patterns to follow:** `src/backlink_publisher/config/tokens.py:84-99`（ghpages / hashnode 範例）。

**Test scenarios:**
- Happy path: `save_notion_token({"integration_token": "secret_xxx", "database_id": "abc123"})` → 檔案存在 + 0600 perm + JSON 內容正確。
- Happy path: `load_notion_token()` 讀回 round-trip 一致。
- Happy path: Dev.to 同上 round-trip。
- Edge case: 檔案不存在 → load 返回 None（mirror ghpages 行為）。
- Edge case: `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/test123` env 設定 → token 寫入 `/tmp/test123/` 不寫 `~/.config/`。
- Error path: 寫入時磁碟滿 → IOError 傳遞，不靜默吞。

**Verification:**
- `pytest tests/test_config_tokens_notion_devto.py` 全綠。
- `pytest tests/test_save_config_new_channel_roots.py` 仍綠（既有測試可能 reference 全 token list）。

---

- [x] **Unit 5: ~~Schema platform allowlist extension~~ — FOLDED into Unit 6/7 verification**

**Status:** Removed per doc-review feasibility F6. Verified `schema.py:34-49 supported_platforms()` already delegates to `registered_platforms()` dynamically. Unit 6/7 verification step now includes a one-line check: `python -c "from backlink_publisher.schema import supported_platforms; assert 'notion' in supported_platforms() and 'devto' in supported_platforms()"`.

**Original Unit 5 content (for archaeology, do not implement as standalone):**

**Goal:** `schema.py:reject_unsupported_platform` 接受新平台名 `notion` + `devto`；驗證 R9 extension readiness 不被觸發。

**Requirements:** R5, R6, R7

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/schema.py:supported_platforms`（若該函式從 `registered_platforms()` 動態讀則 0 改動；若硬編碼則加 entry — **plan-time 假設動態**，U5 第一動作是 grep 驗證）
- Test: `tests/test_schema_platform_allowlist.py`（驗 reject_unsupported_platform 對 notion/devto 不 reject）

**Approach:**
- 先 `grep -n "supported_platforms\|reject_unsupported_platform" src/backlink_publisher/schema.py`；若已 dynamic delegate to `registered_platforms()` 則此 Unit 純驗證 + 退化為 0-LOC change。
- 若 hardcoded，加 entry 並在 PR description 註明此為已知設計問題；同時開 follow-up ticket 讓 schema 完全 R9 align。

**Patterns to follow:** AGENTS.md「Adding a new publisher adapter」recipe。

**Test scenarios:**
- Happy path: `reject_unsupported_platform("notion")` 返回 None（接受）。
- Happy path: `reject_unsupported_platform("devto")` 返回 None。
- Happy path: `reject_unsupported_platform("nonexistent")` 返回非空錯誤字串。

**Verification:**
- `pytest tests/test_r9_extension_readiness.py` 仍綠。
- `pytest tests/test_schema_platform_allowlist.py` 全綠。

---

- [ ] **Unit 6: Notion adapter**

**Goal:** 新增 `NotionAPIAdapter`，POST `https://api.notion.com/v1/pages`，創建公開 Page；register 一行進 adapter table。

**Requirements:** R5, R7

**Dependencies:** Unit 4 (notion token loader), Unit 5 (schema allow)

**Files:**
- Create: `src/backlink_publisher/publishing/adapters/notion_api.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — 加 `register("notion", NotionAPIAdapter)` 一行
- Test: `tests/test_notion_adapter.py`（新增）

**Approach:**
- HTTP client = `requests`（mirror hashnode/ghpages）。
- `Authorization: Bearer <integration_token>` + `Notion-Version: 2022-06-28` header。
- Body 結構：`{parent: {database_id}, properties: {Name: {title: [{text: {content: title}}]}}, children: [paragraph blocks for content + canonical block if seo.canonical_url]}`。
- `canonical_url` 注入 child block：`{type: "paragraph", paragraph: {rich_text: [{type: "text", text: {content: "Original: " + canonical, link: {url: canonical}}}]}}`（per origin doc 範例 + canonical 連結）。
- `Authorization` 缺失 → `DependencyError`；4xx/5xx → `ExternalServiceError`（reuse `_util/errors.py`）。
- `available(config)` → token 存在 = True。
- `post_publish_delay_seconds = 30`（保守，待 throttle test 後可調）。
- Markdown → Notion blocks 用簡單 paragraph-per-line strategy（不上 markdown→Notion rich-text 完整轉換；複雜度封閉在 v1）。

**Patterns to follow:** `hashnode.py`（GraphQL 端到端範例：token load、header build、retry、error mapping）；`ghpages.py`（REST 端到端）。

**Test scenarios:**
- Happy path: payload + valid token → POST 成功 → `AdapterResult(status="published", published_url="https://www.notion.so/...")`。
- Happy path: payload 帶 `seo.canonical_url` → request body `children` 含 canonical 段落。
- Happy path: payload 不帶 `seo` → request body `children` 不含 canonical 段落。
- Edge case: title 空 → schema 已 reject 在 publish-backlinks 入口；adapter 不重複驗。
- Edge case: database_id 缺失 → `DependencyError("Notion database_id missing")`。
- Error path: token expired/invalid (401) → `ExternalServiceError`，不 fallthrough。
- Error path: rate-limit (429) → retry via `retry.py` `RETRYABLE_HTTP_STATUSES`（mirror hashnode）。
- Integration: `tests/test_r9_extension_readiness.py` 跑 — Notion 自動加入 throttle gating / tier matrix。
- Integration: dispatch via `registry.dispatch("notion", payload, mode, config)` → 返回 AdapterResult，未動 cli/schema。

**Verification:**
- `pytest tests/test_notion_adapter.py` + `tests/test_r9_extension_readiness.py` 全綠。
- Notion **dofollow 狀態 probe**：實際建一個公開 Page（手動或 integration test），跑 `link_attr_verifier.verify_link_attributes` 對 Page 內 backlink → 確認 rel 屬性。**此驗證結果寫入 adapter docstring + 若 NoFollow 則在 U7 一併標記 entity-only**。

---

- [ ] **Unit 7: Dev.to adapter (NoFollow, entity signal only)**

**Goal:** 新增 `DevtoAPIAdapter`，POST `https://dev.to/api/articles`；明確標 NoFollow（adapter docstring + docs/solutions 註記）。

**Requirements:** R6, R7

**Dependencies:** Unit 4 (devto token loader), Unit 5 (schema allow)

**Files:**
- Create: `src/backlink_publisher/publishing/adapters/devto_api.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — 加 `register("devto", DevtoAPIAdapter)` 一行
- Test: `tests/test_devto_adapter.py`（新增）

**Approach:**
- `api-key: <key>` header（Dev.to 自定義 header，**不**用 `Authorization: Bearer`）。
- Body：`{article: {title, body_markdown, published, tags, canonical_url}}`，`canonical_url` 直接從 `payload["seo"]["canonical_url"]` 帶（空字串 → omit field 不傳）。
- `available(config)` → token 存在 = True。
- `post_publish_delay_seconds = 30`。
- **Adapter docstring 開頭明示**：「Dev.to 平台對外鏈強制 `rel=nofollow`。本 adapter 保留為 entity signal / 收錄速度價值，不作為 dofollow backlink 主力。link_attr_verifier 確認 nofollow 為預期狀態，不當作 anomaly。」
- 同時新建 `docs/solutions/dofollow-platform-shortlist.md`（若不存在則創建 placeholder）註明 Dev.to **不**在 dofollow shortlist。

**Patterns to follow:** `hashnode.py`（單 token + REST/GraphQL endpoint + retry 模式）；adapter docstring 範例見 `medium_api.py:1-30`。

**Test scenarios:**
- Happy path: payload 帶 canonical → request body `article.canonical_url = "..."`，response → `AdapterResult(status="published", published_url="https://dev.to/...")`。
- Happy path: payload 不帶 `seo` → request body 不含 `canonical_url` key。
- Edge case: tags 為空 list → Dev.to 接受。
- Edge case: tags 超過 4 個 → Dev.to API 422 → `ExternalServiceError`。
- Error path: 401 invalid key → `ExternalServiceError`。
- Error path: 422 validation error → `ExternalServiceError` 帶 server message。
- Integration: link_attr_verifier 對 Dev.to published_url 驗 → 預期 rel=nofollow（不視為失敗，per adapter docstring 規範）。
- Integration: r9 extension readiness 跑 — Devto 自動加入。

**Verification:**
- `pytest tests/test_devto_adapter.py` 全綠。
- `pytest tests/test_r9_extension_readiness.py` 仍綠。
- Adapter docstring grep `nofollow` ≥1 hit（確認警示有寫入）。

---

- [ ] **Unit 8: Hashnode paywall detection in `publish()` entry (REVISED per doc-review P0-1)**

**Goal:** Hashnode adapter `publish()` 進入點跑 GraphQL `me { publication }` probe；free-tier 帳號（`publication = null`）→ raise `ExternalServiceError("Hashnode GraphQL paywall ...")`（不 fallthrough，rich message 直達 publish-history `failure_reason`）。`available()` **不動**，保持 Publisher ABC 契約一致性。WebUI 透過**獨立 `/api/hashnode-paywall-status` endpoint**（不走 dispatcher）顯示卡片 pill。

**Requirements:** R8, R9

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/publishing/adapters/hashnode.py` — `publish()` 進入點加 paywall probe（**不**動 `available()`）；提取 `_probe_hashnode_paywall(token) -> Optional[str]` helper（返回錯誤訊息 or None）
- Create: `webui_app/routes/hashnode_status.py` — 新獨立 GET endpoint `/api/hashnode-paywall-status`，不經 dispatcher
- Modify: `webui_app/templates/_channel_card_macro.html` — Hashnode 卡片加 paywall pill（讀 endpoint via fetch + content-type guard per `[[fetch-json-must-guard-content-type]]`）
- Test: `tests/test_hashnode_paywall_detection.py`（新增）

**Approach:**
- `publish()` 進入點先跑 `_probe_hashnode_paywall(token)`：
  - POST `https://gql.hashnode.com/` `{"query": "{ me { publication { id name } } }"}`, timeout=10s, `Authorization: <pat>`（無 Bearer 前綴，per 既有 hashnode.py 慣例）
  - 200 + `data.me.publication.id` 非空 → return None（Pro tier，繼續 publish 主流程）
  - 200 + `data.me.publication` 為 null / 空 → return `"Hashnode GraphQL paywall — Pro plan required since 2026-05-13. See https://hashnode.com/changelog/2026-05-13-graphql-api-paid-access"`
  - 4xx/5xx/timeout → return None（network 不確定不誤判 paywall，讓 mutation 自己 4xx）
- `publish()` 若 probe 返回非 None：raise `ExternalServiceError(probe_result)` — dispatcher 不 fallthrough（既有 ExternalServiceError 語意），rich message 直達 publish-history `failure_reason`。
- WebUI status endpoint：獨立 GET `/api/hashnode-paywall-status` 呼叫同 `_probe_hashnode_paywall`，返回 JSON `{paywalled: bool, message: str|null, last_checked: ts}`。**JS fetch 必含 content-type guard**（per memory）。
- Cache：probe result 本 process 快取 5 分鐘（module-level dict keyed by token-hash，with TTL）— 避免每次 publish 都打 introspection；WebUI status endpoint 與 publish() 共用此 cache。
- 既有 hashnode 測試適配：所有呼叫 `publish()` 的測試需要 mock `requests.post` 對 `gql.hashnode.com` 至少返回 happy-path Pro tier JSON（per feasibility F3）。Test fixture `_mock_hashnode_pro_tier()` 提供 reusable mock。

**Patterns to follow:** `hashnode.py` 既有 GraphQL POST + auth header pattern；獨立 status endpoint 對 `webui_app/routes/token_paste.py` 同款 Blueprint 結構。

**Test scenarios:**
- Happy path (Pro tier): mock `me { publication }` 返回 `{id: "abc", name: "..."}` → `publish()` 繼續 publishPost mutation，無 paywall error。
- Happy path (free tier): mock 返回 `{me: {publication: null}}` → `publish()` raise `ExternalServiceError("Hashnode GraphQL paywall ...")`；dispatcher 不 fallthrough；publish-history `failure_reason` 含完整訊息（含 changelog URL）。
- Edge case: token 不存在 → publish() 既有 path raise `DependencyError`（token missing，不發 paywall probe，由 `available()` 既有檢查在 dispatch 前 short-circuit）。
- Edge case: cache hit — 5 分鐘內第二次 `publish()` 同 token 不重發 probe（test via mock call_count）。
- Edge case: cache TTL 過 → 重新 probe。
- Error path: probe 401 invalid token → return None（不誤當 paywall），讓 publishPost mutation 自己 4xx。
- Error path: probe timeout/5xx → return None，讓 publishPost mutation 自己決定。
- Integration: WebUI render settings page，呼叫 `/api/hashnode-paywall-status` 顯示 paywall pill；content-type guard 防 HTML error 撞 `<!doctype` SyntaxError。
- Regression: 既有 `tests/test_adapter_hashnode.py` 全部 happy-path 測試補 `_mock_hashnode_pro_tier()` fixture，confirm 不退化（per feasibility F3）。
- Security: probe response timing 不 leak token validity（200 vs 200-with-empty 結構相同 size 量級；TLS 已加密，外部觀察者無從區分）。

**Verification:**
- `pytest tests/test_hashnode_paywall_detection.py` 全綠。
- 既有 `tests/test_*hashnode*.py` 不退化。
- WebUI 手測：bind free-tier 帳號 → 設定頁顯示 paywall pill；嘗試 publish 該平台 → exit code 3（DependencyError）+ history 寫入。

---

- [ ] **Unit 9: WebUI token-paste card wiring for Notion + Dev.to**

**Goal:** 在 WebUI token-paste 流程加 Notion + Dev.to 卡片，**完整 5 站點 wire**（per memory: `[[wire-token-paste-channel-five-sites]]`）。

**Requirements:** R5 (Notion bind), R6 (Devto bind)

**Dependencies:** Unit 4 (token loaders)

**Files:**
- Modify: `webui_app/routes/token_paste.py` — `_ALLOWED` dict 加 notion / devto entry
- Modify: `webui_app/helpers.py` — `_token_paste_status` 加 notion + devto path
- Modify: `webui_app/binding_status.py` — `config_summary` 加新平台 status return
- Modify: `webui_app/binding_status.py` 或 `helpers.py` — `dashboard_channels` return dict 加 entry
- Modify: `webui_app/templates/settings.html` 或 `_channel_card_macro.html` — 渲染新卡片
- Test: `tests/test_webui_token_paste.py` 擴充 parametrize 加 notion + devto

**Approach:**
- Notion 卡片需要兩個欄位（integration_token + database_id），與 ghpages（單 token）不同 → 卡片模板需要小擴展或新 macro variant。
- Dev.to 單欄與 ghpages 同形，可直接 reuse 卡片模板。
- 注意 `[[fetch-json-must-guard-content-type]]`：bind POST 後 redirect/refresh，**不**用 JSON polling endpoint 避免 content-type 陷阱。

**Patterns to follow:** ghpages / writeas / hashnode 既有 5 站點接法；macro `_channel_card_macro.html` 模板。

**Test scenarios:**
- Happy path: POST `/save-notion-token` with `integration_token` + `database_id` → token 檔寫入 + redirect 200 + 卡片顯示綠燈。
- Happy path: POST `/save-devto-token` with `api_key` → 同上。
- Edge case: 空表單 POST → 400 + 不寫檔（per `[[never-smoke-test-real-save-endpoints]]` 配對的 fail-safe）。
- Edge case: CSRF token 缺失 → 403。
- Integration: 渲染 settings 頁，notion + devto 卡片可見且狀態正確顯示 unbound/bound。

**Verification:**
- `pytest tests/test_webui_token_paste.py` 全綠。
- `pytest tests/test_settings_dashboard_rendering.py` 仍綠。
- WebUI 手測：本地 webui.py 啟動 → 設定頁見 notion + devto 卡片 → bind 走通 → publish-backlinks dry-run 確認可選為 platform。

---

### ~~Phase 3 (PR-C): IndexNow Push + GSC Sitemap Ping~~ — DEFERRED

**Status:** PR-C 整個 defer（per doc-review P0-3）。理由：IndexNow protocol 要求 operator 在 `<host>/<key>.txt` 部署金鑰，但本系統的 `published_url` 是平台 host（medium.com / notion.so / dev.to / hashnode.com / etc），operator 無對這些 host root 寫入權。Unit 10/11 設計與目的衝突，不可實作。

**下一步：** 開新 brainstorm `docs/brainstorms/<date>-indexing-push-strategy-requirements.md`，先釐清：
1. 推送對象是「operator 自站 main_domain 的變動頁面」還是「新生 backlink 外站頁」？
2. 若是 operator 自站 → IndexNow 適用，但與本項目 backlink-builder 目的脫鉤，可能是獨立 tool。
3. 若是新生 backlink 頁 → IndexNow 不適用；需評估 Google Indexing API（仍限 JobPosting/BroadcastEvent，需 2026-05 重新驗）/ Bing Webmaster URL Submission API（需 site ownership 驗證，與 IndexNow 同問題）/ 接受 organic discovery（不主動推送）。

<details>
<summary>Original Phase 3 content (archaeology — do not implement until brainstorm resolves design conflict)</summary>

- [ ] **Unit 10: IndexNow core module + key management**

**Goal:** 新增 `publishing/indexing/indexnow.py`，提供 `submit_urls(urls: list[str], host: str) -> SubmitResult`；key auto-gen + persist + host-file publish helper。

**Requirements:** R10

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/publishing/indexing/__init__.py`
- Create: `src/backlink_publisher/publishing/indexing/indexnow.py`
- Create: `src/backlink_publisher/config/indexing.py` 或在 `config/tokens.py` 加 `{load,save,ensure}_indexnow_key`
- Test: `tests/test_indexnow_core.py`（新增）

**Approach:**
- Key file: `<config_dir>/indexnow.key` — 單行 UUID4（去 `-`），8-128 字元 per IndexNow spec。
- `ensure_indexnow_key()` 不存在則 atomic write（mirror `telegraph_api.py` rotation pattern：path resolver + flock + temp+rename）。
- `submit_urls(urls, host)`:
  - POST `https://api.indexnow.org/indexnow` body `{host, key, keyLocation: f"https://{host}/{key}.txt", urlList: urls}`。
  - 200 / 202 → SubmitResult(status="accepted", count=len(urls))。
  - 422 → SubmitResult(status="invalid_key")。
  - 403 → SubmitResult(status="key_not_at_location") + 提示 user 將 key 檔放到 site root。
  - 400 → 解析 server error message 帶回。
- **Host key file 不主動上傳到 user 自站**（user 須自行 deploy）— Unit 10 只生成本地 key + 寫入 stderr 提示「請將 <key>.txt 部署到 https://<host>/<key>.txt」。
- 不引入新 HTTP 依賴 — 用 `requests`（既有依賴）。

**Patterns to follow:** `src/backlink_publisher/publishing/adapters/telegraph_api.py` credential-rotation pattern（per `[[telegraph-adapter-credential-rotation-pattern]]`）。

**Test scenarios:**
- Happy path: `ensure_indexnow_key()` 不存在 → 生成 UUID4 32 字元 → 第二次呼叫返回相同 key（不重生）。
- Happy path: `submit_urls(["https://example.com/a", ".../b"], "example.com")` mock 200 → SubmitResult(status="accepted", count=2)。
- Edge case: empty url list → submit_urls early return 不發 POST。
- Edge case: `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/xx` → key 寫入 `/tmp/xx/indexnow.key`。
- Edge case: concurrent ensure_indexnow_key() — flock 確保不雙寫（mock parallel write 不互覆）。
- Error path: 422 invalid key → SubmitResult(status="invalid_key")，不 raise。
- Error path: network timeout → SubmitResult(status="network_error", retry_allowed=True)。
- Error path: malformed urlList (non-URL) → IndexNow 422，per-URL grep server error。

**Verification:**
- `pytest tests/test_indexnow_core.py` 全綠。
- 手測：在本機 config dir 跑 `python -c "from backlink_publisher.publishing.indexing.indexnow import ensure_indexnow_key; print(ensure_indexnow_key())"` 兩次返回相同 key。

---

- [ ] **Unit 11: New CLI `report-indexing-push`**

**Goal:** 新 CLI `report-indexing-push <history.jsonl>` — 讀 publish-history，提取最近 N 天 success URL，呼叫 IndexNow + GSC sitemap ping，stdout JSONL 結果。

**Requirements:** R10, R11

**Dependencies:** Unit 10 (indexnow core), Unit 12 (sitemap ping helper)

**Files:**
- Create: `src/backlink_publisher/cli/report_indexing_push.py`
- Modify: `backlink-publisher/pyproject.toml` — 新 entrypoint `report-indexing-push = "backlink_publisher.cli.report_indexing_push:main"`
- Test: `tests/test_cli_report_indexing_push.py`（新增）
- Test: `tests/test_cli_python_m_entrypoints.py` — 增加 `report-indexing-push` 到 5-CLI parametrize 表（per memory `[[python-m-missing-main-guard]]`）

**Approach:**
- argparse: `--since-days N`（default 7）、`--host <site_host>`（required，e.g. example.com）、`--dry-run`、`--include-sitemap-ping` flag（default True）。
- 讀 history.jsonl → filter `status=="published"` + `created_at >= cutoff` → 取 `published_url` list → group by host → 對每 host 跑 indexnow.submit_urls + sitemap ping。
- stdout JSONL per URL: `{url, host, submitted_at, indexnow_status, sitemap_ping_status}`。
- stderr human summary: `Submitted N URLs to IndexNow (accepted=K, errors=E). Sitemap ping: status=...`。
- Exit code: 0 success, 1 usage error, 3 dependency missing（IndexNow key 無法生成）, 5 partial failure（部分 URL 422）。
- **必含 `if __name__ == "__main__": main()` guard**（per `[[python-m-missing-main-guard]]`）。

**Patterns to follow:** `src/backlink_publisher/cli/report_anchors.py`（既有 single-file CLI 範例 + JSONL stdout + stderr summary 模式）。

**Test scenarios:**
- Happy path: history with 5 published rows last 3 days → submit_urls called once with 5 URLs → stdout 5 JSONL lines + exit 0.
- Happy path: `--dry-run` → 不呼叫 IndexNow → stdout 顯示 would-submit list + exit 0。
- Happy path: `--since-days 1` → 只 1 row in window → submit 1 URL。
- Edge case: history 無 published row → exit 0 + stderr "no URLs to submit"。
- Edge case: history file 不存在 → exit 1 + stderr usage error。
- Edge case: `--host` 缺失 → argparse exit 2。
- Error path: IndexNow key gen 失敗 → exit 3。
- Error path: 部分 URL 422 → exit 5 + stderr 列出失敗 URL。
- Integration: `python -m backlink_publisher.cli.report_indexing_push --help` 不退 0 之外的 code（per `[[python-m-missing-main-guard]]` tripwire）。

**Verification:**
- `pytest tests/test_cli_report_indexing_push.py` + `tests/test_cli_python_m_entrypoints.py` 全綠。
- 既有 6 CLI 對等性確認：`pyproject.toml` 7 entrypoint。
- 手測：dry-run mode 對既有 publish-history 跑通並顯示 will-submit 清單。

---

- [x] **Unit 12: ~~GSC sitemap ping helper~~ — FOLDED into Unit 11 as private helper**

**Status:** Removed per doc-review scope-guardian SG-5. The 30-LOC `gsc_ping_sitemap()` is implemented as a private helper inside `cli/report_indexing_push.py` or alongside `indexnow.py` (implementer's choice), with its 4 test cases added to `tests/test_cli_report_indexing_push.py`. No separate module file.

**Original Unit 12 content (for archaeology, do not implement as standalone):**

**Goal:** 提供 `gsc_ping_sitemap(sitemap_url: str) -> PingResult` — zero-auth GET 推送 sitemap 到 Google。

**Requirements:** R13

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/publishing/indexing/sitemap_ping.py`
- Test: `tests/test_sitemap_ping.py`（新增）

**Approach:**
- GET `https://www.google.com/ping?sitemap=<urlencoded sitemap_url>` timeout=10s。
- 200 → PingResult(status="accepted")。
- Any non-200 / timeout → PingResult(status="error", message=...)，**silent**（best-effort，不阻塞 Unit 11）。
- Bing sitemap ping 過去也可走 IndexNow 替代，不另實作。
- Unit 11 內 `--include-sitemap-ping` flag 預設 True，--no-include-sitemap-ping 關閉。

**Patterns to follow:** 簡單 stdlib HTTP（`requests.get` + timeout）；無認證所以無 credential pattern。

**Test scenarios:**
- Happy path: mock 200 → PingResult(status="accepted")。
- Edge case: invalid sitemap_url（非 http/https）→ silently skip + log warn。
- Error path: timeout → PingResult(status="error", message="timeout")，不 raise。
- Error path: 5xx → PingResult(status="error", message="...")。

**Verification:**
- `pytest tests/test_sitemap_ping.py` 全綠。

---

</details>

### PR Ship Checklist (NOT a unit — per doc-review SG-2)

每個 PR (A/B/C) 合併前 verification gate（取代原 Unit 13）：

- `plan-check; echo $?` 確認 frontmatter / claims 合規（exit 0）
- `python -m radon raw -s src/backlink_publisher/cli/publish_backlinks.py` 確認未破 730 SLOC 上限
- `pytest tests/test_no_monolith_regrowth.py tests/test_r9_extension_readiness.py tests/test_cli_python_m_entrypoints.py` 全綠
- 新 CLI / adapter / token loader 不在 `monolith_budget.toml` 6 hot file 內，若超 200 SLOC 簡化或拆檔

<details>
<summary>Original Unit 13 content (archaeology — do not implement as unit)</summary>

- [x] **Unit 13: ~~Plan-claims gate + monolith budget verification~~ — FOLDED into PR Ship Checklist**

**Goal:** Plan 落地後對每個 PR 跑 plan-check + monolith budget 確認；本 Unit 不寫代碼，是 PR-A/B/C 各自 ship 前的 verification step。

**Requirements:** Plan-claims gate post-2026-05-20 cutoff（per memory `[[plan-doc-on-cutoff-needs-claims-block]]`）

**Dependencies:** PR-A, PR-B, PR-C 完成

**Files:**
- N/A（verification only）

**Approach:**
- 每 PR 開出後跑 `plan-check; echo $?` 確認 frontmatter / claims 合規。
- `python -m radon raw -s src/backlink_publisher/cli/publish_backlinks.py` 確認未破 730 SLOC 上限。
- 新 CLI `report_indexing_push.py` 不在 monolith_budget.toml 6 hot file 內，無需擴限；若 PR-C 過程中超 200 SLOC 簡化或拆檔。
- 新 adapter `notion_api.py` / `devto_api.py` 同樣不在 budget。

**Test expectation:** none — 本 unit 純為 PR ship 前 verification gate，無新 test 寫入。

**Verification:**
- `plan-check` 對本 plan + 3 PR commit 全返 exit 0。
- `pytest tests/test_no_monolith_regrowth.py` 仍綠。

</details>

## System-Wide Impact

- **Interaction graph：**
  - `publish_backlinks` CLI → `registry.dispatch(platform)` → 各 adapter `publish()` → 讀 `payload["seo"]["canonical_url"]`（Unit 2 共擴 5 adapter；Unit 6/7 新 adapter 含）→ 注入平台對應位置。
  - 新 `report-indexing-push` CLI → 讀 history.jsonl → IndexNow API + GSC sitemap ping。
  - WebUI `settings.html` 渲染 → `binding_status.config_summary` → 新增 notion/devto 卡片 + hashnode paywall pill。
- **Error propagation：**
  - Adapter canonical 注入失敗（不應發生 — 純字串拼接）不會 raise，但若 schema 已驗 canonical_url 為 string adapter 可信任。
  - Hashnode `available()` 返 False → dispatcher 對該平台 raise `DependencyError`（exit code 3）→ publish-history `failure_reason` 寫入清晰訊息（不靜默 success — 防 `[[probe-then-pivot-when-api-unverifiable]]` 重演）。
  - IndexNow 422/403 → CLI exit code 5（partial failure），不污染 publish 結果。
  - GSC sitemap ping 失敗 → silent，log 但不影響 indexnow 主路徑。
- **State lifecycle risks：**
  - IndexNow key 跨 process race：用 telegraph_api flock pattern 保 atomic write。
  - Hashnode `available()` cache lifetime = adapter instance；同次 publish-backlinks run 內不重發 probe。
  - Token files（notion/devto）對 BACKLINK_PUBLISHER_CONFIG_DIR env 響應 — 不踩 `[[webui-store-config-dir-frozen]]` 凍結陷阱（新 token loader 用 function re-resolve）。
- **API surface parity：**
  - 7 個既有 token loader + 2 個新（notion + devto）= 9 個對稱 loader → 必同步檢查 `tests/test_save_config_new_channel_roots.py`。
  - 6 個既有 CLI + 1 個新（report-indexing-push）= 7 entrypoint → 同步 `tests/test_cli_python_m_entrypoints.py` parametrize。
  - WebUI binding cards 既有 6 → 加 2 = 8 → `dashboard_channels` len + `HIDDEN_FROM_UI` 邏輯（per `[[hidden-from-ui-pattern-for-retiring-channels]]`）注意計數正確。
- **Integration coverage：**
  - canonical contract 跨 8 adapter — 由 Unit 3 動態參數化測試保證；新 adapter 加入 registry 自動納入。
  - Hashnode paywall 跨 CLI + WebUI — Unit 8 整合測試覆蓋 publish-history + 卡片 render。
  - IndexNow 全 8 平台 published_url — Unit 11 history-driven，與 publish-history 既有 schema 不破。
- **Unchanged invariants：**
  - `cli/*.py` 與 `schema.py` 不為新 adapter 改動（R9 extension readiness 持守）。
  - `_LegacyPathFinder` / `_REEXPORT_MAP` 不復活（PR #124 已 retire）。
  - Medium adapter family（4 路徑）零改動。
  - `monolith_budget.toml` 6 hot file SLOC 不破。
  - PYTHONHASHSEED=0 footprint 不退化。

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Notion 公開 Page 實為 NoFollow → dofollow 設計假設破 | Med | High | Unit 6 verification step 必 probe；若 NoFollow 立即降級為 entity-only（mirror Dev.to）並在 docstring + docs/solutions 註明 |
| canonical_url XSS injection 在 Blogger/Writeas HTML body / ghpages YAML | Med | **High** | **Layered defense（per doc-review P0-2）**：schema URL regex（Unit 1）+ adapter per-context escape（Unit 2）+ Unit 3 negative test 含 injection fixture |
| Hashnode introspection query 對 free-tier 仍返非空 publication（與假設不同）| Med | Med | Unit 8 probe 設計 fallback：若 `me { publication }` 不穩，pivot 到 dry-run mutation 抓 403 error code（per `[[probe-then-pivot-when-api-unverifiable]]`）；plan-time 已預留 design space |
| Blogger HTML body 內 `<link rel=canonical>` 不被 Google 解析（W3C 規範僅 head）| **High** | **Low**（已重新評估）| Unit 2 docstring 明示「cosmetic marker, no SEO impact expected」；接受並 ship。Blogger 真要 head 注入需 Posts v3 API 提供 head-meta 欄位（目前無），retrofit when 出現 |
| Schema 動態 vs hardcoded supported_platforms 假設錯 | **Resolved** | — | feasibility 已驗 `schema.py:34-49` 已 dynamic delegate to `registered_platforms()`；Unit 5 fold to 6/7 verification |
| Velog GraphQL `createPost` 不支援 canonical 欄位 | **Confirmed** | — | feasibility 已驗 `velog_graphql.py:74-87 WRITE_POST_MUTATION` 固定 7 欄位無 canonical 等價；Velog 移到 scope boundary（與 Telegraph 並列） |
| WebUI Notion 雙欄位卡片 macro 擴展破現有單欄卡片 render | Low | Med | Unit 9 新 macro variant 或 conditional template；既有 ghpages/writeas 卡片 render 測試 regress 確認 |
| 既有 Hashnode 測試 mock 不含 `me { publication }` probe → 全紅 | High | Med | Unit 8 verification 包含「對所有現有 hashnode publish() 測試補 `_mock_hashnode_pro_tier()` fixture」（per doc-review feasibility F3） |
| bp-hashnode-bind worktree（HEAD: a901f21）並發改 hashnode.py | Med | Med | PR-B 開工前必 `git worktree list` + 對 bp-hashnode-bind HEAD `git log --oneline origin/main..` 看是否觸及 hashnode.py available/publish；若有，rebase 或排在後（per `[[ce-work-must-check-concurrent-rebase-before-commit]]`）|
| PR-A 改 5 adapter 同時觸發外部 worktree concurrent 衝突 | Med | Med | per `[[worktree-concurrent-switching]]`：work 開始前 `git worktree list` + `git status --short`；用獨立 bp-canonical-contract worktree 隔離 |
| Hashnode adapter free-tier 帳號歷史 publish 在 paywall 後 retroactive 失效 | Low | Low | Unit 8 只改 `publish()` 進入點；既有已 publish 文章 URL 仍存（Hashnode 不撤稿）；history 不需 migration |
| ~~IndexNow rate-limit / `<key>.txt` 部署 friction~~ | — | — | **Risk removed**：PR-C deferred entirely |

## Documentation / Operational Notes

- **AGENTS.md update**：在「Adding a new publisher adapter」recipe 後加 sub-section「Adapter canonical_url contract」說明 opt-in semantics。
- **docs/solutions/**：建議 PR-A 同步 promote `docs/solutions/canonical-opt-in-strategy-2026-05-21.md`（戰略決定捕獲；per AGENTS.md「Lessons capture」）。
- **docs/solutions/dofollow-platform-shortlist.md**：Unit 7 新增或更新，明示 Dev.to 在 NoFollow shortlist，Notion 待 Unit 6 probe 結果填入。
- **WebUI operator 文檔**：Notion bind 需要 user 在 Notion 後台先建 Integration + Database + share Database 給 Integration；說明寫到 WebUI 卡片 tooltip 或單獨 docs page。
- **Operational**：IndexNow `<key>.txt` 部署到 site root 是 user 一次性手動動作；CLI 首次 run wizard 提示。
- **Monitoring**：新 `report-indexing-push` 加入既有 launcher cron（若有），或留作 user 手動觸發。

## Sources & References

- **Origin document：** `docs/brainstorms/2026-05-21-canonical-contract-and-platform-expansion-requirements.md`
- **Schema canonical contract：** `src/backlink_publisher/schema.py:80-86, 316-321`
- **Reference adapter（Medium canonical 消費）：** `src/backlink_publisher/publishing/adapters/medium_api.py:136-147`
- **Registry pattern：** `src/backlink_publisher/publishing/registry.py`
- **Adapter table：** `src/backlink_publisher/publishing/adapters/__init__.py:46-52`
- **R9 extension readiness test：** `tests/test_r9_extension_readiness.py`
- **Token loader pattern：** `src/backlink_publisher/config/tokens.py:64-109`
- **Ghpages Jekyll front-matter：** `src/backlink_publisher/publishing/adapters/ghpages.py:124-139`
- **WebUI token-paste pattern：** `webui_app/routes/token_paste.py:38-41`
- **Credential rotation reference：** `src/backlink_publisher/publishing/adapters/telegraph_api.py`（per memory `[[telegraph-adapter-credential-rotation-pattern]]`）
- **Memory-flagged traps**：
  - `[[wire-token-paste-channel-five-sites]]` — WebUI 5 站點接法
  - `[[python-m-missing-main-guard]]` — CLI entrypoint guard
  - `[[probe-then-pivot-when-api-unverifiable]]` — Hashnode paywall + Notion dofollow 都需 probe
  - `[[fetch-json-must-guard-content-type]]` — WebUI JSON endpoint 陷阱
  - `[[plan-doc-on-cutoff-needs-claims-block]]` — plan-claims gate post-2026-05-20
  - `[[telegraph-adapter-credential-rotation-pattern]]` — IndexNow key mirror
  - `[[hidden-from-ui-pattern-for-retiring-channels]]` — WebUI channel count 計算
- **External（顧問 brief 已提供）：** IndexNow spec `https://www.indexnow.org/`；Notion API `https://developers.notion.com/`；Dev.to API `https://developers.forem.com/api/`；Hashnode GraphQL `https://gql.hashnode.com/`；Hashnode paywall changelog `https://hashnode.com/changelog/2026-05-13-graphql-api-paid-access`。
