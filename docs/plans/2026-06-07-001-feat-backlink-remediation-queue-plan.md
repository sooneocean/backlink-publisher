---
title: "feat: 外鏈發佈質量與活鏈運營管理優化 — Phase A: Remediation Queue"
type: feat
status: active
date: 2026-06-07
origin: docs/brainstorms/2026-06-07-backlink-quality-and-remediation-requirements.md
claims:
  paths:
    - src/backlink_publisher/recheck/events_io.py
    - src/backlink_publisher/recheck/verdicts.py
    - src/backlink_publisher/events/kinds.py
    - webui_app/routes/health.py
    - webui_app/templates/health.html
    - webui_app/health_metrics.py
    - src/backlink_publisher/cli/recheck_backlinks.py
  shas:
    - 32c4bc2
---

# feat: 外鏈發佈質量與活鏈運營管理優化

## Overview

本計劃是「外鏈發佈質量提高 + 活鏈運營管理優化」功能的總體規劃。分三個 Phase 逐步落地：

- **Phase A (當前)**: **Remediation Queue** — 把目前只 observability 的衰減閉環變成 operator 可 ack/resolve 的工作流。
- **Phase B (後續)**: **死鏈自動補發 pipeline** — recheck 檢測到死鏈後自動跑 plan-gap 生成補發 seed。
- **Phase C (後續)**: **Pre-publish Quality Gate** — 發佈前內容品質把關。

本 plan doc 涵蓋全部三個 Phase 的設計，但 **claims block 只鎖 Phase A 涉及的源碼文件**。Phase B/C 在各自落地時追加 claims。

## Problem Frame

### 現狀

`recheck-backlinks` 已經交付了週期性存活複查能力（5 verdict、events.db 時間序列、/ce:health 衰減橫幅）。但 operator 看到衰減橫幅後：

1. **無處標記「已處理」** — 衰減計數是 raw decay count，不區分 operator 是否已知曉/已補發。
2. **無後續自動化** — 死鏈被檢測到後，operator 需手動跑 `equity-ledger | plan-gap` 再補發。
3. **發佈前無質量門** — AI 生成內容的 anchor density、content uniqueness 無檢查，部分平台因此隱藏/刪除內容。

這三個 gap 的 ROI 排序是：**先關閉 observability open-loop (A) → 再自動化補發 (B) → 再預防低質量發佈 (C)**。

## Phase A: Remediation Queue

### Requirements

- **R1**: 新增 `remediation.event` event kind，operator 可對一條死鏈標記 ack（已知曉但未處理）或 resolved（已補發/已移除 target）。
- **R2**: 新增 `remediation-queue` CLI verb，列出/ack/resolve 死鏈。支援 `--list`、`--ack <live_url>`、`--resolve <live_url>`、`--snooze <live_url> --days N`。
- **R3**: `/ce:health` 衰減橫幅改為顯示 **unresolved decay count**（排除已 resolved 的死鏈）。
- **R4**: `/ce:health` 新增一個 remediation 面板：列出 unresolved 死鏈，每條附操作按鈕（ack、resolve、snooze）。
- **R5**: CLI exit 0 default（advisory）；`--fail-on-unresolved` opt-in 非零退出。
- **R6**: 所有操作寫 events.db（非 history_store），與 recheck 同存儲背書。
- **R7**: 不引進新存儲後端/新 schema bump。

### Non-Goals

- 不自動補發（Phase B）。
- 不改變 recheck 的 verdict 分類。
- 不改變 equity-ledger 的 liveness 列（仍延後到 plan-007 R6）。
- 不引進 scheduled snooze timer（snooze 只是標記 + days，CLI 不做定時恢復）。

### Key Technical Decisions

- **D1 — 單 kind `remediation.event` 帶 `action` 字段**。floor = `{"action", "live_url"}`。action ∈ `{ack, resolve, snooze}`。snooze 帶 `snooze_until_utc`。
- **D2 — unresolved 視圖 = 從 `remediation.event` 推導**。對每個 live_url 取最新 action。若最新是 `resolve` → resolved；最新是 `ack`/`snooze`(未過期) → unresolved；無 remediation 記錄 → unresolved（未處理過）。
- **D3 — decay 計數過濾**：`derive_decay_counts` 增加 `exclude_resolved=True` 參數，在 latest-per-link 聚合中跳過 marked-resolved 的 live_url。dashboard 默認顯示 unresolved 計數。
- **D4 — WebUI 雙入口**：`/ce:health` 既保持衰減橫幅（unresolved-only），也新增 remediation 卡片（可操作列表）。`POST /ce:health/remediation` 路由（CSRF 保護）處理 ack/resolve/snooze。
- **D5 — CLI 四子命令**：`remediation-queue --list`、`remediation-queue --ack <live_url>`、`remediation-queue --resolve <live_url>`、`remediation-queue --snooze <live_url> --days 7`。輸出為 human-friendly 表格（stdout）和 JSONL（可 pipe）。
- **D6 — never-raises**：所有操作 fail-open（讀取失敗回退到 raw decay，寫失敗 log warning + continue），不影響 dashboard 渲染。

### Implementation Units

---

- [x] **Unit 1: `remediation.event` event kind + action taxonomy 基座**

**Files modified/created:**
- Modified: `src/backlink_publisher/events/kinds.py`（加 `REMEDIATION_EVENT` 到 `KINDS` + `REQUIRED_FIELDS`）
- Created: `src/backlink_publisher/remediation/__init__.py`
- Created: `src/backlink_publisher/remediation/actions.py`（action 常量 + unresolved 視圖推導）
- Created: `src/backlink_publisher/remediation/events_io.py`（emit/query remediation events）

**Status: ✅ Complete**
- `REMEDIATION_EVENT` registered in `KINDS` with floor `{"action", "live_url"}`
- `ACK`, `RESOLVE`, `SNOOZE` constants with `_ACTIONS` validation set
- `is_unresolved(store, live_url)` — latest action per link determines state
- `list_unresolved(store)` — returns all currently unresolved live_urls
- `emit_event(store, live_url, action, ...)` — WAL-safe append with flush-after-commit
- `resolved_live_urls(store)` — returns set of resolved live_urls for decay filtering

---

- [x] **Unit 2: CLI `remediation-queue` verb**

**Files modified/created:**
- Created: `src/backlink_publisher/cli/remediation_queue.py`
- Modified: `pyproject.toml`（加 `remediation-queue` entry point）

**Status: ✅ Complete**
- `remediation-queue --list` — human table (default) or `--json` JSONL
- `remediation-queue --ack <live_url> [--note ...]`
- `remediation-queue --resolve <live_url> [--note ...]`
- `remediation-queue --snooze <live_url> --days N [--note ...]`
- `--fail-on-unresolved` exits 6 when unresolved links exist
- URL scheme validation via post-parse `UsageError`

---

- [x] **Unit 3: `/ce:health` decay 橫幅改為 unresolved-only**

**Files modified:**
- Modified: `src/backlink_publisher/recheck/events_io.py`（`derive_decay_counts` 加 `exclude_resolved` 參數）
- Modified: `webui_app/health_metrics.py`（`decay_counts` 加 `exclude_resolved` 參數）
- Modified: `webui_app/routes/health.py`（`_decay_counts` 默認 exclude_resolved + `_total_decay_counts` helper）

**Status: ✅ Complete**
- `derive_decay_counts(store, exclude_resolved=True)` filters out resolved links
- Keyed by `live_url` (from payload) with `article_id` fallback
- `health_metrics.decay_counts` passes through `exclude_resolved`
- `_decay_counts()` defaults to resolved-excluded counts for dashboard
- `_total_decay_counts()` for "show all" toggle

---

- [x] **Unit 4: `/ce:health` remediation 面板 + 操作按鈕** ✅

**Files created/modified:**
- Created: `webui_app/routes/remediation.py`（`POST /ce:health/remediation` 路由）
- Modified: `webui_app/routes/health.py`（加 `_remediation_rows` helper + `_g_cache` 注入 + `remediation_rows` template context）
- Modified: `webui_app/templates/health.html`（加 remediation 卡片 + inline JS 按鈕 handler）
- Modified: `webui_app/routes/__init__.py`（註冊 remediation blueprint）

**Status: ✅ Complete**
- `POST /ce:health/remediation` — JSON endpoint, CSRF protected
- Remediation card with unresolved table (Live URL, Latest Action, Note, Action buttons)
- Three buttons per row: Ack / Resolve / Snooze 7d
- Inline JS: `data-action` + delegated `addEventListener`, reads CSRF meta per-call
- Page reload on success, alert on error
- Fail-open: empty list + "No unresolved backlink decay" when all clear
- Badge shows unresolved count in card header

---

- [ ] **Unit 5: 行動閉環文檔 + AGENTS.md 更新** (pending)

**Files to modify:**
- Modify: `docs/operations/recheck-backlinks-runbook.md`
- Modify: `AGENTS.md`

---

## Phase B: 死鏈自動補發 pipeline (規劃, 尚未實現)

### 設計方向（實現時展開 plan doc）

- 新增 `replan-dead` CLI verb：讀 `link.rechecked` 的近期死鏈（host_gone/link_stripped）→ 提取 `target_url` → 對每個 under-linked target 生成 `plan-gap` seed JSONL → stdout。
- 可 pipe: `recheck-backlinks --probe | replan-dead | plan-backlinks | publish-backlinks --publish`
- `--days N`（最近 N 天的死鏈，default 7）、`--min-gap M`（少於 M 條存活外鏈才補，default 3）
- 不自動寫 pipeline（operator 自行決定 cron chain）

### 與 Phase A 的關係

Phase B 的輸入來自 `link.rechecked`（已有），讀 Phase A 的 `remediation.event` 來避免重複補發（已 resolved 的死鏈可排除）。因此 Phase A 是 Phase B 的前置。

## Phase C: Pre-publish Quality Gate (規劃, 尚未實現)

### 設計方向（實現時展開 plan doc）

- 新增 `quality-gate` 步驟（可集成進 `validate-backlinks` 或獨立 verb）：
  1. **Anchor density check** — per article 的外鏈密度（links / words）。超過閾值（default 5%）→ `quality.anchor_density_high`
  2. **Content uniqueness** — 與 events.db 已發佈內容對比（SHA256 模糊匹配），重複率 > 70% → `quality.duplicate_content`
  3. **AI-draft 合理性**（可選 `--quality-llm`）— 調 LLM 快速評分 content quality（0-100），< 閾值 → `quality.llm_rejected`
- 被 quality gate block 的 row：emit `publish.quality_blocked` event，跳過該 row（exit 0，不打斷 batch）
- 不引進新 LLM 依賴（可選 `--quality-llm`，默認只用 deterministic checks）

## Risk & Dependencies

| Risk | Mitigation |
|------|------------|
| Phase A 的 unresolved 視圖 SQL 複雜（JOIN recheck + remediation events）| 分兩步：先查 resolved 列表，再在 decay 聚合中排除。避免複雜 GROUP BY。|
| WebUI 操作按鈕無前端框架（native ES modules）| 用 `data-action` + delegated `addEventListener`，同現有 binding 模式。|
| `remediation.event` 與既有 `link.rechecked` 分歧（兩個 event kind 各自寫入）| 設計上二者 orthogonal：recheck 負責 verdict，remediation 負責 operator action。不一致不會導致數據損壞。|
| operator 大量點 ack（不 resolve）導致 unresolved 列表膨脹 | 列表始終按 live_url GROUP BY，只顯示最新 action。ack 不覆蓋 resolve。|
| Phase B 依賴 Phase A 的 resolved 過濾 | Phase B 落地時才需 resolve 標記；如果 Phase A 延後，Phase B 可先不 filter resolved。|
| Phase C 的 LLM 評分引入新依賴 | 默認關閉（`--quality-llm`），operator 自願啟用。|

## References

- `docs/plans/2026-05-29-004-feat-recheck-backlinks-survival-loop-plan.md` — 本 plan 的前置基礎設施
- `src/backlink_publisher/recheck/events_io.py` — WAL-safe emit 模式（本 plan 複用）
- `src/backlink_publisher/events/kinds.py` — event kind 註冊模式
- `webui_app/routes/health.py` — fail-open helper 模式
- `webui_app/templates/health.html` — 衰減橫幅渲染（需修改）