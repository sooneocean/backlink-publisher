# 全面系統優化審計報告 — backlink-publisher

- **日期**: 2026-06-07
- **版本**: 1.0
- **範圍**: 全系統 7 維度掃描

---

## Executive Summary

backlink-publisher 整體健康度非常良好。專案包含 302 個 source 檔案（58,851 LOC）、410 個測試檔案（~6,270 個測試函式），零裸 `except:` 區塊、嚴格遵守 monolith budget、具有 plan claims、adapter dofollow gate 等自動化治理機制。

**核心發現**：WebUI 是優化潛力最大的領域——缺乏 JS 測試框架、部分經典 script 尚未遷移至 ESM、29 個 route 模組分散缺乏 lifecycle 管理、WebUI 測試覆蓋偏低（僅 47 個測試函式）。代碼品質方面，5 個檔案超過 640 行需要關注。整體而言這是一個治理成熟度遠高於同類專案的 codebase，優化建議集中在可持續性與防護網補強。

| 維度 | 評級 | 關鍵問題 | 建議優先級 |
|---|---|---|---|
| WebUI | B+ | 無 JS 測試、遺留 classic script、store lifecycle | P0 |
| 代碼品質 | A | 5 個檔案 >640 行、C(18) 複雜度 | P1 |
| 測試架構 | B+ | WebUI 測試不足、無 E2E | P0-P1 |
| CLI 管道 | A- | argparse 重複、pipe E2E 測試缺乏 | P2 |
| 治理與文檔 | A | 203 份 plan/brainstorm 需清理 | P3 |
| 依賴與工具鏈 | B+ | 無 Dependabot、style drift 風險 | P2 |
| 系統架構 | A- | adapter error taxonomy 不一致、store 肥大 | P2 |

---

## Dimension 1: WebUI（優先焦點）

### 現狀

WebUI 是 Flask 應用作業儀表板，採用零建置原生 ESM 架構（無 bundler/framework）：

- **Routes**: 29 個 route 模組，大小從 106 到 670 LOC
  - `routes/` 目錄下 20 個，`api/` 下 2 個，獨立檔案 7 個
  - 最大：`drafts_api.py` (670)、`settings_api.py` (552)
- **Templates**: 33 個 Jinja2 模板，9 個 `extends base.html`、24 個 partials
- **JS**: 14 個檔案，2,844 LOC 總量
  - 6 個 page module（ESM）、3 個 lib/ 共享層（`api.js`、`dom.js`、`profiles.js`）
  - 4 個非 module script（Bootstrap CDN non-defer、`fetch_json.js` classic、`bind_channel.js defer`、`channel-binding.js defer`）
  - `static/js/package.json {type:module}` 標記目錄為 ESM（瀏覽器忽略，供 node 層級檢查）
- **CSS**: 5 個檔案，1,303 LOC
  - `tokens.css` — 38 行，19 個設計 token
  - 94 個 `var(--)` 引用（53 index.css、33 copilot.css、8 settings.css）
- **Stores**: 11 個 webui_store 模組（module-level singleton 模式）
- **Services**: 5 個 service 模組 + 8 個 helper 模組
- **無障礙**：109 個 aria/role 模式（良好基準）
- **CSRF**：global guard 強制所有 POST/PUT/PATCH/DELETE
- **測試**：122 個測試檔案參照 webui 內容，但僅 47 個 webui 專用測試函式

### 優勢

- 零建置架構極簡，部署即執行（double click → run）
- 一致的 ESM module 模式（`import` from `lib/`）
- 全域 CSRF 防護完整無遺漏
- aria/role 模式採用率高
- tokens.css 設計 token 被各頁面 CSS 廣泛引用

### 問題與建議

#### P0: 缺乏 JS 測試框架（風險隨複雜度增長）

**現狀**：2,844 LOC 的 JS 僅有一個 `node` 層級的 `esc()` 檢查（`tests/js/lib_dom_check.mjs`），無正式測試框架。專案文檔明確標記 "JS interaction has no test framework yet (deferred)"。

**建議**：
- 導入 Vitest（輕量、與 ESM 相容最佳）或 node:test（零依賴）
- 優先覆蓋 `lib/` 共享層（`api.js`、`dom.js`）
- 目標：3-5 天，達到 ~80% lib/ 覆蓋率

#### P0: Module-level singleton 無 lifecycle 管理

**現狀**：11 個 store 模組全部使用 module-level singleton 模式。在長期運行的 WebUI 進程中，這些 singleton 累積狀態無清理機制。

**建議**：
- 導入 warm/cold state 概念：閒置 N 分鐘後釋放資源
- 加入定期 cleanup hook（30 分鐘週期清理過期 cache）
- 目標：2 天

#### P1: 遺留 Classic Script 未遷移

**現狀**：
- `fetch_json.js` (2KB, 23 行) 仍以 classic `<script>` 載入
- Bootstrap CDN bundle 以 non-defer 方式在 `<head>` 載入（區塊渲染）

**建議**：
- `fetch_json.js` → 改為 ESM，`import` 到相關 page module
- Bootstrap：評估是否可改為 `defer`（需檢查 `window.bootstrap` 依賴鏈）
- 目標：1-2 天

#### P2: Route 模組肥大

**現狀**：`drafts_api.py` (670)、`settings_api.py` (552) 超過可維護閾值。

**建議**：
- 將 >400 LOC 的 route 模組中的 business logic 提取到 service 層
- 目標：2 天

#### P3: 前端監控

**現狀**：無任何前端性能監控或 timing instrumentation。

**建議**：
- 加入 Navigation Timing API 的基本采集
- 僅記錄到 events 系統，不阻塞 UX

---

## Dimension 2: 代碼品質

### 現狀

| 指標 | 數值 | 評級 |
|---|---|---|
| Source files | 302 | — |
| Total LOC | 58,851 | — |
| bare `except:` blocks | **0** | ⭐ 卓越 |
| `type: ignore` | 29 (across 16 files) | 優秀 |
| `except Exception` blocks | 243 | 合理（network-heavy） |
| 最大 cyclomatic complexity | C(18) — phase0_seal.py `_handle_reseal` | 需關注 |
| Monolith budget compliance | 14/14 files under ceiling | ⭐ 合規 |
| 最大檔案 | idempotency/store.py (758) | 需關注 |

### 優勢

- **零裸 except** — 在 302 個 source 檔案中完全找不到 `except:`（不指定例外類型），極少見
- Monolith budget 14 個檔案全部低於上限（例如 `generate_backlink_text.py` 357/390 SLOC）
- 型別提示普及率高

### 問題與建議

#### P1: 5 個檔案 >640 行

```
idempotency/store.py       758
velog_graphql.py           757
_manifests.py              698
mcp/server.py              678
telegraph_api.py           648
```

**建議**：
- `idempotency/store.py` — 提取領域子 store（publish store、citation store、probe store）
- `velog_graphql.py` — 分離 GraphQL query 定義與 adapter 邏輯
- `_manifests.py` — 每個 manifest 類別獨立檔案

#### P2: Cyclomatic Complexity C(18)

`phase0_seal.py::_handle_reseal` 的 C(18) 複雜度來自多種 reseal 路徑的條件分支。

**建議**：提取各 reseal 路徑為獨立方法（`_reseal_by_domain`、`_reseal_by_platform` 等）

#### P2: 29 個 type:ignore 缺乏註解

**建議**：為每個 `type: ignore` 加上 `# reason:` 註解追蹤

#### P2: 243 個 except Exception

雖然合理（大量 network I/O），但部分可收窄為 `requests.RequestException`、`aiohttp.ClientError` 等。

**建議**：審計並目標縮減 30% 為具體例外類型

---

## Dimension 3: 測試架構

### 現狀

| 指標 | 數值 |
|---|---|
| 測試檔案數 | 410 |
| 測試函式數 | ~6,270 |
| autouse 網路 mocking fixture | 4 |
| 自訂 marker | real_ssrf_check, real_content_fetch, real_image_gen, real_browser_publish_smoke |
| WebUI 測試函式 | 47 |
| JS 測試框架 | 無 |

### 優勢

- 大規模測試套件（6,270 tests）
- 嚴謹的 mocking 紀律：4 個 autouse fixture 預設封鎖網路
- 自訂 marker 允許選擇性執行真實 API 呼叫
- `PYTHONHASHSEED=0` 強制 footprint regression 可重現
- fixture 層次清晰（conftest 鏈）

### 問題與建議

#### P0: 缺乏 JS 測試

2,844 LOC 的 JS 完全沒有測試框架覆蓋。唯一的防護是單一的 `node` 層級 `esc()` 檢查。

**TC0**：導入 Vitest 並覆蓋 `lib/` 模組（`api.js`、`dom.js`、`profiles.js`）

#### P1: WebUI Route 測試不足

29 個 route 模組僅有 47 個專用測試函式（平均 < 2/route）。

**TC1**：增加至 80+ 個測試函式，確保每個 route handler 至少 2-3 個測試（happy path + error case）

#### P1: 缺乏 WebUI E2E 測試

無 Playwright/in-browser 測試。Critical flows（settings 頁面 channel 綁定、publish history 瀏覽、health dashboard）無法自動驗證。

**TC2**：加入 5-8 個關鍵 E2E 測試腳本（Playwright），優先覆蓋 binding flows 與 CSRF 防護

#### P3: 缺乏 Property-based 測試

複雜資料轉換（config load/save、event dedup、JSONL 解析）缺乏隨機 fuzz 測試。

**建議**：對 config parser 與 event store 導入 `hypothesis`

---

## Dimension 4: CLI 管道

### 現狀

- **27 個 console_scripts entrypoints**，54 個 CLI 檔案
- **Pipeable 設計**：`plan-backlinks | validate-backlinks | publish-backlinks --mode draft`
- **Exit code contract**：0-6，語義明確
- **輸出規範**：stdout = clean JSONL，stderr = diagnostics
- 較新的 CLI 多為 read-only advisory（`gate-probe`、`canary-targets`、`channel-scorecard`），exit 0
- 最大 CLI：`phase0_seal` (572 LOC)、最小：`comment` (39 LOC)

### 優勢

- 一致的 pipe 設計哲學
- 明確的 exit code 語義（0=success, 3=auth expired, 6=dead links 等）
- 新 CLI 遵循「advisory first」模式

### 問題與建議

#### P2: argparse 重複

27 個 entrypoints 各自定義 `--help`、`--verbose`、`--dry-run` 等常見 flag。

**建議**：建立共享 argparse factory（`cli/_helpers.py`），目標減少 200+ 行重複代碼

#### P2: 缺乏 Pipe Chain E2E 測試

沒有測試驗證 `plan → validate → publish` 完整流程整合。

**建議**：加入 2-3 個 pipe chain 測試（使用 `--dry-run` 模式）

#### P3: Exit Code 文件分散

exit code 語義散見多處，可能 drift。

**建議**：集中定義在 `cli/_exit_codes.py`，其他位置 import

---

## Dimension 5: 治理與文檔

### 現狀

- **Plan docs**: 127 份
- **Brainstorm docs**: 76 份
- **docs/ 目錄**: 17 個（architecture、plans、brainstorms、solutions、runbooks 等）
- **docs/solutions/**: 11 個類別目錄（best-practices、test-failures、logic-errors 等）
- **Monolith budget**: TOML + radon CI 強制（14 個追蹤檔案）
- **Plan claims system**: YAML frontmatter + CI gate (`plan-claims-gate`) + overnight radar (`plan-claims-radar`)
- **Adapter dofollow gate**: `register()` 強制 `dofollow=` 關鍵字參數
- **AGENTS.md**: 規範性 SSoT（但 worktree 複本已過時）
- **Bugfix discipline**: 5 步驟協議（reproduce → root cause → classify → fix → evidence）
- **Claims cutoff**: 2026-05-20，之後 plan 必須有 `claims:` block

### 優勢

- 卓越的文件文化 — 127 份計畫 + 76 份 brainstorm + solutions 分類
- 多層自動化治理（budget、claims、dofollow）
- Bugfix discipline 完整記錄
- Plan claims 系統設計深思熟慮（grandfathering、schema validation、CI enforcement）

### 問題與建議

#### P3: 203 份 Plan/Brainstorm 需清理

大量歷史文件可能已過時（`status: shipped` 但從未歸檔）。

**建議**：
- 封存 2026-04 之前所有 `status: active → shipped` 的 plan docs
- 可移至 `docs/archived/` 子目錄

#### P3: Claims gate 仍在 14 天 soak 期

`plan-claims-gate` 目前為 non-required status。2026-06-02 之後建議轉為 required。

**建議**：到期後立即轉為 required check

#### P3: bp-*/AGENTS.md 過時

AGENTS.md 記錄了 worktree 複本為 stale copies。

**建議**：在 `prune-stale-worktrees.sh` 中加入 stale AGENTS.md 檢測

---

## Dimension 6: 依賴與工具鏈

### 現狀

- **Core deps (13)**：Flask 3.0+、Playwright、google-api-python-client、openai、beautifulsoup4、lxml 等
- **Optional groups**：
  - `dev`：pytest、radon==6.0.1
  - `dev-webwright`：webwright==0.0.7
  - `mcp-server`：MCP 相關
- **CI**：GitHub Actions，Python 3.11 + 3.12 matrix，all-blocking steps
- **Local lint**：Black + flake8
- **CI lint**：`py_compile` + `ast.parse`（非 Black/flake8）
- 現代化 pyproject.toml 構建

### 優勢

- 核心依賴精簡（13 個）
- 重量級依賴放在 optional groups
- radon 版本鎖定確保 monolith budget 可重現

### 問題與建議

#### P2: CI 無 style check

CI 使用 `ast.parse` 而不是 Black/flake8，可能導致 style drift。

**建議**：加入 minimal style check（至少 `black --check --diff`），只有 diff 的檔案

#### P2: 無 Dependabot/Renovate

依賴更新完全手動。

**建議**：加入 Dependabot 每週更新配置

#### P2: google-api-python-client 體積大

僅供 Blogger adapter 使用，但依賴體積很大。

**建議**：評估改為 `google-auth` + raw HTTP requests

#### P3: webwright 0.0.7 pinned

Niche 套件，維護狀態需定期評估。

**建議**：在 CI 中加入 webwright 健康檢查（每週確認 pypi 活躍度）

---

## Dimension 7: 系統架構

### 現狀

```
CLI (plan | validate | publish | ...)
  → Services (config, content, anchor, events, geo, ...)
    → Adapters (30+ publishing platforms)
      → External APIs (Blogger, Medium, Velog, Telegraph, ...)
    → WebUI (Flask dashboard)
      → webui_store (singletons)
      → Templates + JS
    → Events system (idempotent store, projectors)
    → MCP Server (multi-agent orchestration)
```

### 優勢

- 清晰的分層架構：CLI → services → adapters → platforms
- Events 系統：idempotent store + projector pattern + state machine
- Config 系統：hierarchical TOML + 5-class save_config taxonomy
- MCP server 為 multi-agent 設計預留
- WebUI 作為 operational dashboard，不暴露到公網

### 問題與建議

#### P2: Adapter Error Taxonomy 不一致

Adapter 錯誤處理混用 `DependencyError`、`ExternalServiceError`、`AuthExpiredError`，缺乏統一的錯誤分類層級。

**建議**：
- 建立正式 error hierarchy（`PublishError > AuthError > NetworkError > ContentError > RateLimitError`）
- 每個 adapter 必須實作 `error_map` classmethod

#### P2: Events Store 肥大

`idempotency/store.py` 758 LOC 包含多個關注點（dedup、projector dispatch、state machine）。

**建議**：分拆為 `store.py`（核心 + dedup）、`projectors.py`、`state_machine.py`

#### P3: 缺乏統一 Health Endpoint

沒有聚合所有 adapter 狀態的健康檢查端點。

**建議**：建立 `/api/health` 回傳每個 adapter 的 last_publish / auth_status / rate_limit_remaining

#### P3: Architecture 文件分散

架構描述散見 AGENTS.md、多份 plan docs 與 brainstorm docs。

**建議**：建立正式的 `ARCHITECTURE.md`，作為架構決策的 SSoT

---

## Priority Summary

| 優先級 | 領域 | 行動 | 預估工時 | 影響 |
|---|---|---|---|---|
| **P0** | WebUI | 導入 JS 測試框架 + 遷移 classic script 至 ESM | 2-3d | 高 — 防止 JS 回歸 |
| **P0** | WebUI | Singleton store lifecycle 管理 | 2d | 高 — 防止 memory leak |
| **P1** | 測試 | Playwright E2E 測試（關鍵 WebUI 流程） | 3-5d | 高 — UI 回歸安全網 |
| **P1** | 代碼品質 | Refactor idempotency/store.py (758 LOC) | 2-3d | 中 — 可維護性 |
| **P2** | CLI | 建立共享 argparse factory | 1d | 中 — 減少重複 |
| **P2** | 測試 | 增加 WebUI route handler 測試（47→80+） | 2d | 中 — 覆蓋率 |
| **P2** | 架構 | 正式化 adapter error taxonomy | 1d | 中 — 一致性 |
| **P2** | 依賴 | 加入 Dependabot 配置 | 0.5d | 低 — 依賴安全 |
| **P3** | 治理 | 封存 2026-04 前過時文檔 | 0.5d | 低 — 文檔衛生 |
| **P3** | 架構 | 建立 ARCHITECTURE.md | 1d | 中 — 知識集中 |
| **P3** | CI | 加入 minimal style check | 0.5d | 低 — style 一致性 |

---

## 結論與下一步

**整體評級：A-（健康，有明確改善空間）**

backlink-publisher 在代碼品質、測試紀律、治理成熟度方面顯著優於同類專案。核心瓶頸在於 WebUI 的測試覆蓋與架構現代化，這是唯一具有 P0 建議的領域。

**建議的三個 Sprint 規劃**：

### Sprint 1: JS 基礎設施與 Store 生命週期（3-5 天）
1. 導入 Vitest，覆蓋 `lib/` 共用模組
2. `fetch_json.js` → ESM 遷移
3. Singleton store lifecycle 管理實作
4. 為每個 `type:ignore` 加上註解

### Sprint 2: E2E 與大檔案重構（4-6 天）
1. Playwright E2E 測試（5-8 個腳本）
2. WebUI route 測試增加至 80+
3. `idempotency/store.py` 分拆
4. Adapter error taxonomy 統一

### Sprint 3: CLI 與基礎設施（2-3 天）
1. 共享 argparse factory
2. Dependabot 配置 + style check CI
3. 封存過時文檔
4. `ARCHITECTURE.md` 建立

---

*Report generated 2026-06-07 via comprehensive system scan.*
