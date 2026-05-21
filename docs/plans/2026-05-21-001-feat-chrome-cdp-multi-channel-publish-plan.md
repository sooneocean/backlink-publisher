---
title: "feat: Chrome/CDP multi-channel publish via BrowserPublisher abstraction"
type: feat
status: completed
date: 2026-05-21
deepened: 2026-05-21
claims: {}
---

# Chrome/CDP multi-channel publish via BrowserPublisher abstraction

## Overview

抽出一個 BrowserPublisher 共用層，讓 Hashnode / Velog / Dev.to / Mastodon 等渠道可以用 **attach 真實 Chrome via CDP** 的模式發帖，繞過 API paywall（Hashnode）與 Cloudflare 反爬（共通痛點）。同步整理 Settings WebUI 的 channel 卡片分組，讓「bind backend」與「publish backend」這兩個獨立維度都能在 UI 顯式控制。

不重寫 `medium_browser.py`；它走 Playwright + cookies.json 注入的舊路徑繼續工作。新抽象專為未實作 browser publish 的渠道服務。

## Problem Frame

目前狀態：
- **Bind 階段** 已有 Chrome/CDP backend（`src/backlink_publisher/cli/_bind/chrome_backend.py`），velog / telegraph 走 chrome，medium 因 `required_backend="playwright"` 即使 UI 勾 chrome 也會回退（`driver.py:336`）。
- **Publish 階段** 每個渠道各自為政：medium 用 Playwright ephemeral + cookies 注入（`medium_browser.py`，420 SLOC），其餘渠道全走 API。
- **Hashnode** GraphQL `publishPost` 自 2026-05-13 paywall（[[hashnode-graphql-paywall-2026-05-13]]），免費帳號發帖唯一路徑就是 browser publish。
- **Velog / Dev.to / Mastodon** 走 API 仍能發但脆弱：velog GraphQL 不時抽風，Dev.to/Mastodon 即便 nofollow 仍有發帖需求（operator 已明確要）。
- **WebUI Settings dashboard** 把 medium/velog/telegraph 一起塞進「Chrome DevTools 授權」群組，UI 語義不準確（velog/telegraph publish 走 API，跟 Chrome 無關）。

痛點：每加一個 browser publish 渠道，operator 要寫 ~400 SLOC + 一整套 selectors + cookie injection + auth-expired path + screenshot path + retry handling，且每家都會踩同樣的 race。

## Requirements Trace

- **R1.** 提供 `BrowserPublishRecipe` 抽象，新增 channel 走 browser publish 只需提供 `compose_url + publish_flow + selectors`，不重抄 cookies / retry / screenshot / error mapping。
- **R2.** 提供 `ChromeAttachSession` 連線層，可 attach 既有 CDP-enabled Chrome（沿用 bind 階段啟動的同一個 port 與 profile），無 CDP port 時自行啟動 Chrome。
- **R3.** 為 Hashnode 落地首個 chrome publish recipe，作為抽象的 first concrete consumer，並 unblock 免費帳號 publish。
- **R4.** 為 Velog / Dev.to / Mastodon 落地第二批 chrome publish recipes（Mastodon 與 Dev.to 仍標 `_DOFOLLOW_BY_CHANNEL = False`，operator 要在 UI 看見 nofollow 警示後才能啟用）。
- **R5.** WebUI Settings 把 channel 卡片的 bind backend 與 publish backend 拆成兩個獨立維度。Chrome publish 啟用時，dofollow=False 的渠道要顯示 "nofollow — 不貢獻 SEO" 紅字警示。
- **R6.** Publish flow 必須產出 `AdapterResult`，與既有 `Publisher` ABC（`publishing/registry.py:52`）契合，dispatch 與 throttle 機制零改動。
- **R7.** Auth-expired path 必須走既有 `mark_expired(channel)` + raise `AuthExpiredError(channel, reason)` 契約（複用 medium_browser:_safe_mark_expired pattern）。

## Scope Boundaries

**Out of scope（明確不做）**：
- 重寫 `medium_browser.py`。Medium 走 Playwright + cookies.json 的現行路徑保留；只在第二期才考慮遷移。
- Cloudflare bot fingerprint 模擬升級（UA / JA3 / HTTP/2 settings）。Chrome attach 比 Playwright ephemeral headless 顯著降低反偵測風險，但**不是「invisible」**：Playwright `connect_over_cdp` 仍可能因 CDP 探測（`window.cdc_*`、`navigator.webdriver`、Runtime.evaluate timing）被指紋。下層指紋對齊延後，但 recipes 必須將 JA3/CDP-detection 失敗 surface 為可診斷的 `ExternalServiceError` 子型別（per adversarial-review F2）。
- Hashnode Pro tier 自動偵測 / 訂閱導引。Hashnode 即使 paywall 也允許 browser publish — 我們直接走 browser，不引導購買。
- Mastodon / Dev.to 自動 dofollow 升級。兩家在 source level 寫死 nofollow，Chrome publish 不會改變 SEO 屬性。
- Bind 階段擴充。Chrome/CDP bind 已 work，不在本 plan 動 `cli/_bind/`。
- 新增 schema.py / CLI argparse 變動。Per `tests/test_r9_extension_readiness.py`，新平台只在 `publishing/adapters/__init__.py::register()` 一行落地。

## Context & Research

### Relevant Code and Patterns

- **Adapter registry**：`src/backlink_publisher/publishing/registry.py:52` `Publisher` ABC + `register()` + `dispatch()`。新 BrowserPublishDispatcher 直接 subclass `Publisher`，每個 chrome publish channel `register("hashnode", HashnodeBrowserAdapter)`。
- **Medium browser 範例**：`src/backlink_publisher/publishing/adapters/medium_browser.py` 是 cookies + Playwright ephemeral 的參考實作。新抽象的 lifecycle / error mapping / screenshot helper 全部拷貝其骨架，差別在 connection method（ephemeral vs attach）。
- **Bind chrome backend**：`src/backlink_publisher/cli/_bind/chrome_backend.py` 已有 `_chrome_port()` / `_profile_dir()` / `discover_chrome_binary()` / `_cdp_available()` 工具函式。Publish 階段共用這些常數與 helper（移至 shared module 或從 bind module re-export）。
- **Bind recipe pattern**：`src/backlink_publisher/cli/_bind/recipes/__init__.py::ChannelRecipe` 是「value object + post_persist hook」的乾淨樣板。BrowserPublishRecipe 完全鏡像它。
- **Cookie sanity gate**：`recipes/medium.py::_cookie_sanity_passes` 是 Cloudflare-aware cookie 真偽判定的參考；新 recipe 若需要驗證 publish 前 session 仍 alive，重用此 pattern（見 [[medium-httponly-auth-cookies-spike-3a]]）。
- **AGENTS.md 「Adding a new publisher adapter」**（AGENTS.md:249）：契約契合的 reference recipe。

### Institutional Learnings

- `[[probe-then-pivot-when-api-unverifiable]]` — Hashnode 與 Velog 都驗證過：在 API 不穩或 paywall 時 pivot 到 browser publish 是正確路徑；plan 不要為 API mutation 設計 fallback dead code。
- `[[playwright-framenavigated-orphaned-during-cross-origin-sso]]` — Chrome attach 後若 publish 流程中觸發跨 origin（OAuth gate, captcha popup），`page.on('framenavigated')` 可能 orphan。Publish flow 偵測 success 不能只靠 framenavigated，要併 URL 直 poll + DOM 出現 selector 雙保險。
- `[[chrome-devtools-cdp-4-traps]]`（[[chrome-devtools-cdp-traps]]）— `--remote-allow-origins=*` (Chrome 111+) 必加、port 動態、stderr 不能 DEVNULL、pytest fixture 殺乾淨。
- `[[bind-channel-diagnostic-playbook]]` — Chrome attach 失敗診斷 5 鐵律（profile lock、port 占用、idle timeout、operator 自報不算成功），publish 階段同樣適用。
- `[[grep-dofollow-map-before-shipping-adapter]]` — PR #108 → #109 revert 教訓：ship 新 publisher 前 grep `_DOFOLLOW_BY_CHANNEL`，nofollow 平台必須在 UI 紅字警示。
- `[[hidden-from-ui-pattern-for-retiring-channels]]` — Settings 上若需暫關某 channel 卡片，用 `binding_status.HIDDEN_FROM_UI`，不刪 adapter source。
- `[[fetch-json-must-guard-content-type]]` — WebUI 新增的 polling endpoint 要回 JSON 並設正確 content-type。

### External References

僅必要時補充，多數設計可從本 repo 既有 pattern 推導：
- Playwright 官方 `browser.connect_over_cdp(ws_url)` API（CDP attach 公認解法，medium_browser 目前未用是因為其用 ephemeral）。
- Chrome DevTools Protocol `/json/version` 探活、`/json/new?url=` 開 tab — 已被 `chrome_backend.py` 驗證可用。

## Key Technical Decisions

- **D1. Publish 走 Playwright `connect_over_cdp` 而非延伸 `_CdpPage`**。`_CdpPage`（chrome_backend.py 內最小 CDP 包裝）只支援 bind 必要的 `goto / url / cookies / evaluate`。Publish 需要 keyboard typing / clipboard paste / locator clicks / screenshot — 重新實作太重，且 medium_browser.py 已 import Playwright，新模組額外引入 `connect_over_cdp` 零成本。Rationale: 維護兩條獨立 CDP 包裝是技術債放大器。
- **D2. Chrome lifecycle 由 ChromeAttachSession 依「擁有權」對稱處理**。Operator 可能 bind 後關閉 Chrome、或從未 bind 過、或別的 process 已占 port 9222。`ChromeAttachSession.__enter__` 探活：CDP up → attach（記 `owned=False`）；CDP down → 啟新 Chrome reuse `_profile_dir()`，記 `owned=True` 並把 PID 寫到 `<config_dir>/real-chrome-publish.pid`。`__exit__`：`owned=True` → terminate（與 launch 對稱）；`owned=False` → leave alone（attach 者無權關 attacher 不擁有的 process）。Webui 啟動時讀 pid 檔，若 pid 仍 alive 且我方 webui process 不識則 reap（mirror bind backend 的 orphan reap 模式）。Rationale: Finding 2 — symmetry 是避免 orphan Chrome 占 profile lock 阻塞下次 bind 的唯一可靠 invariant。
- **D3. Per-channel profile isolation（沿用 bind 既有行為，REVISED）**。Bind backend `_profile_dir()`（`cli/_bind/chrome_backend.py:75-83`）已支援 `BACKLINK_PUBLISHER_BIND_CHANNEL` env var → 回傳 `<config_dir>/real-chrome-profile/<channel>` 子目錄；env 未設才 fallback 共用 root。Plan 初稿誤認為「共用 profile 是已落實 invariant」（feasibility F2 + adversarial F5 + security F4 三人指出），實際上 bind 已 per-channel 隔離 — publish 階段沿用該語義即可，零新增基礎建設。**Named invariant（修訂）**：`ChromeAttachSession(channel=...)` 在 `_profile_dir()` 解析時設定 `BACKLINK_PUBLISHER_BIND_CHANNEL=<channel>` 強制走 per-channel subdir，避免 channels 共用導致的 anti-bot cascade contamination。Cookies.json 路徑保留為 medium ephemeral 模式專用，不擴散到新 channels。Operator 第一次啟用 N 個 chrome-attach channels 需 bind N 次（一次性成本，per-channel）；後續每個 channel 的 anti-bot trip 不影響其他 channels（採 adv-review F5 + sec-review F4 共識方案）。
- **D4. `BrowserPublishRecipe` 與 bind `ChannelRecipe` 是兩個獨立 dataclass，不合併**。雖然形態相近，但 lifecycle 完全不同（bind = 等使用者登入；publish = 機器人式 type/click/wait）、selectors 互斥、`required_backend` 概念對 publish 無意義。合併會做出一個 protocol 對兩者都不合身。
- **D5. Per-channel `BrowserPublishRecipe` 寫在 `publishing/browser_publish/recipes/<name>.py`**，不放 `publishing/adapters/<name>_browser.py` 避免跟 medium_browser.py 風格混淆。`adapters/__init__.py` 直接 `register("hashnode", BrowserPublishDispatcher.for_channel("hashnode"))`，不另立 wrapper module。
- **D6. 用「單一 `BrowserPublishDispatcher` class + classmethod factory」取代「動態 class 生成」**。原方案 `type(f"{Channel}BrowserAdapter", ...)` 動態建類（Finding 1 review 指出）有 cost 無 gain：stack trace 顯示合成名稱無 source、`isinstance` 變脆、IDE/grep 失能、pickling 邊界 bug。改採：`BrowserPublishDispatcher.for_channel(channel) -> Publisher` 是 classmethod factory，回傳 `BrowserPublishDispatcher` 實例（持 `channel` + `recipe` 兩個 ctor 參數）。`registry.register` 已支援 instance 註冊（檢查 `_REGISTRY: dict[str, list[type[Publisher]]]` 簽名是 type，需確認 — 若 registry 只吃 class，則 register 簽名擴一格 callable factory 比 type() hack 乾淨）。Stack trace 一律落在 `BrowserPublishDispatcher.publish`，medium_browser.py 的 static-class 風格亦不被破壞（它仍是獨立 class，不走 dispatcher）。
- **D7. UI 上 publish backend 分四檔**：`api`（預設、有官方 API 的 channel）、`chrome-attach`（attach 真實 Chrome）、`browser-playwright`（medium 現行 ephemeral，僅 medium 可見）、`none`（disabled / hidden）。Unit 5 升維 `dashboard_binding_methods` → `dashboard_channel_methods`（nested `{"bind", "publish"}`），不新增 sibling dict（per pattern-reviewer Gap 2）。
- **D8. Dofollow guardrail 在 UI + adapter 雙層**：UI 在 `_DOFOLLOW_BY_CHANNEL[channel] is False` 時對「啟用 Chrome publish」按鈕加紅字 + confirm dialog；adapter 層 `BrowserPublishDispatcher.publish()` 啟動時 log `dofollow=<value>`，operator 在 webui 日誌可審計（但不阻擋發帖 — operator 已知情）。

## Open Questions

### Resolved During Planning

- **Q. Chrome attach 時若 Chrome 是 operator 一般使用中的視窗（混雜其他 tab）會出事？** A. `_profile_dir()` 解析到 `<config_dir>/real-chrome-profile`，與 operator 個人 Chrome profile 完全分離（已 by `chrome_backend.py:65-69`）。publish 啟動的 Chrome 是專屬實例，不會干擾。
- **Q. Hashnode Pro tier 對 browser publish 有限制嗎？** A. **未驗證 — 載入 Unit 3 前必跑 manual probe**（per adversarial F1：本 plan 寫的「不觸碰 paywall」是 untested premise，Hashnode 完全可能在 web UI 編輯器 publish button 後彈 Pro modal）。Probe protocol：使用 throwaway free-tier 帳號 在 `hashnode.com/new` 手動發一篇 throwaway post → 記錄結果（成功 URL / Pro modal / 部分功能 gated）到 plan + `docs/refs/` 備忘 → 結果決定 Unit 3 命運：(a) 成功 publish → Unit 3 正常推進；(b) Pro modal → Unit 3 pivot 到「devto 為 first concrete consumer」或 plan 暫停。dofollow 屬性 publish 後 verify_link_attributes 量測，記入 [[probe-then-pivot-when-api-unverifiable]]。
- **Q. Hashnode 若量測到 nofollow 怎辦？**（per adversarial F6）A. 決策樹：(a) `_DOFOLLOW_BY_CHANNEL["hashnode"] = False` hotfix，hashnode UI 卡片走 devto/mastodon 同款 nofollow 警示路徑；(b) 從預設 `--platforms` list 移除 hashnode，僅 explicit opt-in；(c) `binding_status.HIDDEN_FROM_UI` 不加（adapter source 保留 — 未來若 Hashnode 改 dofollow 可回切）。Unit 3 在量測完成前不進 default platforms。
- **Q. Medium 是否一起換到 chrome-attach？** A. **不換 — 但理由是遷移成本，不是技術不可行**。Medium 走 Playwright ephemeral + cookies 已在 production 穩定，遷移會耗 ~1 unit 工作量且觸及 medium_browser.py 既有 PR #138 Cloudflare workaround；同時 [[playwright-framenavigated-orphaned-during-cross-origin-sso]] 對 attach 模式同樣是風險（見 Risks 第一列），需要 Medium-specific mitigation。本 plan 不負擔此成本，留 follow-up plan：若 Medium 因 Cloudflare 加劇而需 attach，那 plan 屆時實作 Medium-specific framenavigated 防護。
- **Q. WebUI Settings groups 怎麼改名？** A. 原 "Chrome DevTools 授權" 群組（含 medium / telegraph / velog）拆兩部分：(a) Bind 維度仍叫 "Chrome DevTools 授權"，(b) 新增 "Chrome 發帖（Browser publish）" 維度作每張卡內部子欄。group 標題不換，卡片內部加 publish backend 欄位。
- **Q. Bind 階段 chrome_backend.py 的 `_chrome_port()` / `_profile_dir()` / `discover_chrome_binary()` 如何被 publish 重用？** A. 抽到 `publishing/browser_publish/chrome_session.py`（或 `_util/chrome_paths.py`）作 shared module；`cli/_bind/chrome_backend.py` 改為從 shared module 匯入。Bind 既有測試（`test_bind_channel_chrome_backend.py`）不破壞。

### Deferred to Implementation

- 各 channel `publish_flow` 的 exact selectors（hashnode.com/new 編輯器 DOM、velog 編輯頁、dev.to /new、mastodon compose 表單）— 需在實機開瀏覽器拍 selectors。實作時參照 `_medium_selectors.py` 風格抽出 `_<channel>_selectors.py`。
- Publish 後驗證 URL 模式 regex（每家不同）— 用 `verify_link_attributes()` 後處理。
- Chrome attach 啟動時若使用者 Chrome 主程式已開啟，profile lock 競爭如何處置 — `chrome_profile_locked` error_code 已在 `BIND_ERROR_MESSAGES` 有 Chinese 提示，publish 階段沿用即可，但是否 retry 一次留實作時決定。
- 各 channel publish 是否需要 `post_publish_delay_seconds`（如 Medium 30s）— 量測後填入。

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

### Module layout

```
src/backlink_publisher/publishing/browser_publish/   ← new
├── __init__.py                  # BrowserPublishRecipe + RECIPES dict
├── chrome_session.py            # ChromeAttachSession + shared chrome paths
├── dispatcher.py                # BrowserPublishDispatcher + _make_browser_adapter()
└── recipes/
    ├── __init__.py
    ├── hashnode.py              # Unit 4
    ├── velog.py                 # Unit 5
    ├── devto.py                 # Unit 5
    └── mastodon.py              # Unit 5
```

### Recipe shape

```
BrowserPublishRecipe(
  channel="hashnode",
  compose_url="https://hashnode.com/new",
  publish_flow=callable(page, payload) -> str (final_url),
  expects_dofollow=None,  # mirrors _DOFOLLOW_BY_CHANNEL — for adapter audit log
)
```

`publish_flow` 是 channel-specific 機器人腳本：拿到 attached page + payload，回傳 published URL（或 raise AuthExpiredError / ExternalServiceError）。Adapter 層負責 screenshot、cookies refresh（attach 模式下 noop）、retry、AdapterResult 組裝。

### Session lifecycle

```
publish(payload, mode, config):
  with ChromeAttachSession(channel=self.channel) as page:
    final_url = recipe.publish_flow(page, payload)
    return AdapterResult(status="published", platform=self.channel, published_url=final_url, ...)
```

`ChromeAttachSession` 內部：
1. `_cdp_available(port)` 探活 → attach existing → else 啟動 Chrome（共用 `_profile_dir()`）
2. 等 CDP `/json/version` ready
3. `playwright.connect_over_cdp(ws_url)` 取 BrowserContext
4. `context.new_page()` + `page.goto(recipe.compose_url)`
5. 偵測 login redirect → `mark_expired + AuthExpiredError`
6. 偵測 captcha / Cloudflare challenge → `ExternalServiceError` (non-retryable)
7. yield page → recipe.publish_flow
8. cleanup：close page；**不關 Chrome**（保留給後續 publish 重用）

### Registration

`adapters/__init__.py` 改：
- Hashnode：刪除既有 `register("hashnode", HashnodeAPIAdapter)`（line 51）— GraphQL 已 paywall，API adapter 為 dead path；同時刪除 dead `from .hashnode import HashnodeAPIAdapter` import。改為：
  ```
  register("hashnode", BrowserPublishDispatcher.for_channel("hashnode"))
  ```
- Velog：append browser fallback 到既有 chain：
  ```
  register("velog", VelogGraphQLAdapter, BrowserPublishDispatcher.for_channel("velog"))
  ```
- Devto / Mastodon：新增 single-adapter chain：
  ```
  register("devto", BrowserPublishDispatcher.for_channel("devto"))
  register("mastodon", BrowserPublishDispatcher.for_channel("mastodon"))
  ```
- `_verify_hashnode_live` 既有 dashboard verify 函式（adapters/__init__.py:675-780 區段）對 paywalled GraphQL 仍會 401 — Unit 3 一併 disable 或 redirect 到 browser verify 路徑，避免 dashboard "live verify" 按鈕誤導。

### Decision matrix: publish backend per channel

| Channel | api adapter | chrome-attach? | playwright-ephemeral? | dofollow |
|---|---|---|---|---|
| medium | MediumAPIAdapter | — | **MediumBrowserAdapter（現行）** | True |
| velog | VelogGraphQLAdapter | new (fallback) | — | True |
| hashnode | — (paywalled) | **new (primary)** | — | None → measure |
| devto | — | new (opt-in) | — | False（nofollow） |
| mastodon | — | new (opt-in) | — | False（nofollow） |
| telegraph | TelegraphAPIAdapter | — | — | True |
| ghpages | GitHubPagesAPIAdapter | — | — | True |
| writeas | WriteAsAPIAdapter | — | — | True |
| blogger | BloggerAPIAdapter | — | — | True |

## Implementation Units

- [x] **Unit 0: Chrome lifecycle spike (pre-Unit-1 validation)**

**Goal:** 在 commit Unit 1 設計前，落地驗證 4 項 Chrome lifecycle 假設（feasibility F1/F5 + security F1/F2/F3 五人標 high）。不寫 production code，產 `docs/refs/2026-05-2N-chrome-lifecycle-spike.md` report。

**Requirements:** 預驗 R2 的 ChromeAttachSession 設計

**Dependencies:** None

**Files:**
- Create: `scripts/spike_chrome_lifecycle.py`（throwaway，不進 production）
- Create: `docs/refs/2026-05-2N-chrome-lifecycle-spike.md`（report，Unit 1 依此寫實作）

**Approach（spike protocol — 1-2 day budget）:**
1. **macOS Chrome process group teardown**：用 `subprocess.Popen([chrome_bin, ...], start_new_session=True)` 啟 Chrome → 觀察 `pgrep -P <pid>` + `ps -o pgid= -p <pid>` 看 helper tree → `os.killpg(pgid, SIGTERM)` 5s → `os.killpg(pgid, SIGKILL)` → 立即重啟新 Chrome 看 `--user-data-dir` SQLite lock 是否釋放。重複 5 次量穩定性。Report：是 / 否；若否，記錄 lock 釋放需多少額外延遲。
2. **CDP listener identity verification**：`lsof -i:<port> -sTCP:LISTEN -Fp` parse PID → `ps -o comm=,command= -p <pid>` parse executable + cmdline → 比對 `discover_chrome_binary()` 路徑與 `--user-data-dir` 期望值。Report：API 是否 macOS + Linux 可移植；whitespace / SIP 隔離下是否仍 work。
3. **Profile permission verification**：`stat()` profile dir 看 `st_uid` 與 `st_mode`；測試 `chmod 0700` fix path 在 SIP 與 sandbox 下行為。Report：fail-soft 條件。
4. **`BACKLINK_PUBLISHER_BIND_CHANNEL` per-channel profile**：用該 env var 啟兩個 Chrome（不同 channel），確認 `<config_dir>/real-chrome-profile/<a>/` 與 `<b>/` 各自獨立寫 cookie；同時觀察 `chrome-profile.lock` flock 跨 channel 是否 reasonable。Report：plan D3 修訂後行為與既有 bind backend 是否真的對齊。

**Patterns to follow:**
- `cli/_bind/chrome_backend.py` Chrome 啟動 sequence
- `scripts/medium_bind_spike.py`（既有 spike 風格 — throwaway script + docs/refs/ report）

**Test scenarios:** N/A — spike 不寫 production test。所有觀察直接寫進 report。

**Verification:**
- `docs/refs/2026-05-2N-chrome-lifecycle-spike.md` 落地，內含 4 項實證結果與每項對 Unit 1 設計的 implications。
- Unit 1 Approach 文字依 spike report 微調（若 macOS helper tree 不可靠 → Unit 1 加 fallback retry；若 listener verification 在某 OS 失效 → Unit 1 提供 opt-out env var）。

**Exit criteria：** 4 項全部至少一個明確「實證 work」或「實證需 fallback」結論。任何一項 inconclusive → 加長 spike，**不** ship Unit 1 帶猜測。

---

- [x] **Unit 1: BrowserPublishRecipe + ChromeAttachSession foundation**

**Goal:** 抽 `BrowserPublishRecipe` dataclass + `ChromeAttachSession` context manager + 共用 chrome path helpers。為後續 unit 提供 plumbing。

**Requirements:** R1, R2

**Dependencies:** Unit 0（spike report 必先落地；Unit 1 Approach 文字依 spike 結果微調）

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/__init__.py`
- Create: `src/backlink_publisher/publishing/browser_publish/chrome_session.py`
- Create: `tests/test_browser_publish_chrome_session.py`
- Modify: `src/backlink_publisher/cli/_bind/chrome_backend.py` — 把 `_chrome_port` / `_profile_dir` / `discover_chrome_binary` / `_cdp_available` 改為從新 shared module 匯入（保留 bind 既有公開符號，向後相容）
- Modify: `webui_app/__init__.py::create_app` — 註冊 startup hook 呼叫 `chrome_session.reap_orphan_publish_chrome(cfg)`（PID 檔讀取 + executable path + start-time 驗證 + SIGTERM/SIGKILL；per D2）
- Pre-implementation audit: `rg "mock\.patch.*chrome_backend|mock\.patch.*_chrome_port|mock\.patch.*_profile_dir" tests/` — 把任何 string-mock target 移到 shared module 路徑，否則既有測試會 patch 失效（[[grep-all-legacy-import-forms]]）

**Approach:**
- `BrowserPublishRecipe` 是 frozen dataclass：`channel: str`, `compose_url: str`, `publish_flow: Callable[[Page, dict], str]`。鏡像 `cli/_bind/recipes/__init__.py::ChannelRecipe` 風格。
- `ChromeAttachSession(channel: str, config: Config)` 為 context manager，依 D2 對稱 lifecycle：
  - `__enter__` 探活：CDP up → **先驗證 listener 身份**（per security-review F1，避免 local port squatting）：`lsof -i:<port> -sTCP:LISTEN -Fp` 取 listener PID → `ps -o comm=,command= -p <pid>` 確認 executable == `discover_chrome_binary()` AND cmdline 含我方 `--user-data-dir` path → 通過後 attach（`self._owned = False`）；任何驗證失敗 → `DependencyError("chrome_cdp_foreign_listener")` 不 attach。CDP down → 先取 cross-phase mutex（flock on `<config_dir>/chrome-profile.lock`，避免 bind 與 publish 同時 launch；per feasibility F6）→ launch Chrome（reuse `_profile_dir()`），記 `self._owned = True`，把 PID + start-time 寫到 `real-chrome-publish.pid`。
  - Profile permission verification：launch / attach 前 `stat(_profile_dir())` 確認 `st_uid == os.geteuid()` 且 `st_mode & 0o077 == 0`（per security-review F2）；mismatch 時 attempt fix（chmod 0700），fix 失敗 → `DependencyError("chrome_profile_unsafe_perms")`。
  - Playwright `connect_over_cdp(ws_url)` → `new_page()` → return page。
  - `__exit__`：close page；若 `self._owned` → terminate launched Chrome（`os.killpg(pgid, SIGTERM)` with 5s wait, fallback `os.killpg(pgid, SIGKILL)`），unlink pid 檔，release mutex；若 `not self._owned` → leave alone（無關閉權）。
- Orphan reap（**注意：bind backend 目前無 pid 檔機制，本 plan 從零實作此模式，feasibility-review F1 指出 plan 原述「mirror bind backend」是 false premise**）：webui `create_app` startup hook 讀 `real-chrome-publish.pid`，**reap 前必驗證 PID 身份**（避免 PID reuse 攻擊 — `ps -o comm=,lstart= -p <pid>` 確認 (a) executable basename == Chrome binary、(b) `--user-data-dir` cmdline 含 profile path、(c) launch start-time 與 pid 檔同時寫入的 timestamp 比對，per security-review F3）；通過驗證後 SIGTERM 整 process group。pid 檔同時記錄 launched-by-webui PID + start-time（two-field tuple，atomic write 0600）。
- Chrome launch flags 與 bind backend 同：`--remote-allow-origins=*`、`--user-data-dir=<profile_path>`（profile path 解析見下）、`--no-first-run`，stderr 寫 `.last-launch.stderr` 並 chmod 0600（不 DEVNULL，[[chrome-devtools-cdp-traps]]；stderr tail 落 WebUI 錯誤訊息前先 redact `Authorization:` / `Cookie:` / `?code=` / `?state=` patterns，per security-review F6）。
- Chrome launch 用 `subprocess.Popen(start_new_session=True)`，建立獨立 process group；`__exit__` 對 owned process 走 `os.killpg(pgid, SIGTERM)` → 5s wait → `os.killpg(pgid, SIGKILL)`，避免 macOS Chrome helper subprocess 殘留 hold 住 profile SQLite lock（per feasibility F5）。
- 錯誤 code 與 bind 對齊：`chrome_not_available` / `chrome_cdp_unavailable` / `chrome_profile_locked` / `chrome_launch_failed`，全部 map 成 `DependencyError`（dispatcher 可 fall through 到下個 adapter）。
- Playwright `connect_over_cdp` lazy import；無 Playwright → `DependencyError("Playwright not installed")`。

**Patterns to follow:**
- `cli/_bind/chrome_backend.py::RealChromeBrowserRunner.launch_and_wait` — Chrome 啟動序列與 port 探活
- `publishing/adapters/medium_browser.py::_save_screenshot` — screenshot helper（複用）

**Test scenarios:**
- Happy path（attach mode）：CDP port up → attach 成功、`session._owned == False`、`__exit__` 關 page 不關 browser（fake popen 驗證 proc 仍 alive、pid 檔不寫入）。
- Happy path（launch mode）：CDP port down → 啟動 Chrome stub → `/json/version` ready → attach 成功、`session._owned == True`、`real-chrome-publish.pid` 落地 0600；`__exit__` 觸發 SIGTERM 並 unlink pid 檔。
- Edge case：launched Chrome 5s 內未響應 SIGTERM → SIGKILL 觸發、pid 檔仍 unlink。
- Edge case：port up 但 `BACKLINK_PUBLISHER_REAL_CHROME_ATTACH != "1"` → `DependencyError("chrome_cdp_unavailable")`（防止意外接管 operator 個人 Chrome）。
- Error path：`discover_chrome_binary()` 回 None → `DependencyError("chrome_not_available")`。
- Error path：Chrome 啟動 20s 內 `/json/version` 不通 → `DependencyError("chrome_launch_failed")` 且 stderr tail 被附入 error message；pid 檔不殘留。
- Edge case：profile lock — `OSError` mention "profile" → `DependencyError("chrome_profile_locked")`。
- Orphan reap：模擬 stale pid 檔指向 alive 但非 webui process tree 的 PID → startup hook terminate + unlink pid。
- Orphan reap：pid 檔指向已死 PID → unlink pid（不 raise）。
- Integration：與 `cli/_bind/chrome_backend.py` 從 shared module 匯入後 `test_bind_channel_chrome_backend.py` 全綠（不破 bind path）。

**Verification:**
- `pytest tests/test_browser_publish_chrome_session.py tests/test_bind_channel_chrome_backend.py` 全綠。
- Shared 模組無循環 import：`python -c "import backlink_publisher.publishing.browser_publish.chrome_session; import backlink_publisher.cli._bind.chrome_backend"` 0 退出。

---

- [x] **Unit 2: BrowserPublishDispatcher + classmethod factory**

**Goal:** 用單一 `BrowserPublishDispatcher`（Publisher subclass）+ `for_channel(channel)` classmethod factory 把 recipe 包成可註冊實例（per D6 — 不動態建類）。

**Requirements:** R1, R6, R7

**Dependencies:** Unit 1

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/dispatcher.py`
- Modify: `src/backlink_publisher/publishing/browser_publish/__init__.py` — export `BrowserPublishDispatcher`, `RECIPES`
- Modify: `src/backlink_publisher/publishing/registry.py` — `register(platform, *publishers: type[Publisher] | Publisher)` 簽名擴充（明確擴，非 conditional）；`_REGISTRY: dict[str, list[type[Publisher] | Publisher]]` 同步調型
- Modify: `src/backlink_publisher/publishing/registry.py::dispatch` — line 149-153 區段 `cls()` 改為「若 type → `cls()`；若 instance → 直接用」分支邏輯（per feasibility F3：原 `cls()` 對 instance 會 raise TypeError）
- Pre-implementation audit: `rg "_REGISTRY|registered_platforms" src/ tests/ webui_app/` 列出所有 reader；確認沒有人對 `_REGISTRY` value 呼叫 `.__name__` / `issubclass()` 等 type-only 操作（per adversarial F8）。若有，先 normalize reader 端
- Create: `tests/test_browser_publish_dispatcher.py`

**Approach:**
- `BrowserPublishDispatcher(Publisher)` 以 ctor 接 `channel: str` 與 `recipe: BrowserPublishRecipe`。Instance attribute（非 class attribute）— 避免 D6 那種 class-level mutable state。
- `@classmethod def for_channel(cls, channel: str) -> "BrowserPublishDispatcher"`: 從 `RECIPES[channel]` 撈 recipe 並回 `cls(channel, recipe)`。
- `publish(payload, mode, config)`：
  1. `ChromeAttachSession(self.channel, config)` open。
  2. Dispatcher 層只負責 **首頁載入後 URL-level** 偵測（page.url 含 `/signin` / `/login` / `/m/signin` pattern）→ `AuthExpiredError(channel=self.channel)`。**DOM-level captcha / Cloudflare challenge 偵測由 recipe 負責**（per feasibility F8 — dispatcher 用通用 `[id*=signin]` selector 會在 devto signup banner / mastodon footer 誤報）。
  3. `final_url = self.recipe.publish_flow(page, payload)`：recipe 內部處理 channel-specific 的 captcha selectors、Cloudflare challenge interactive wait、login redirect。Captcha policy（per security-review F5）：Cloudflare interactive challenge → recipe 等 30s 自解；hCaptcha / reCAPTCHA → raise `ExternalServiceError("captcha_requires_operator")`，UI 顯示「請在 Chrome 視窗手動解決 captcha 並重試」（attach 模式 Chrome 視窗 visible 給 operator）。
  4. `verify_link_attributes(final_url)` 後處理（reuse），attach `link_attr_verification` meta。
  5. 失敗截圖、`_safe_mark_expired(self.channel)`、`raise`。若 `mark_expired` 本身失敗（IO error），dispatcher 仍 raise 原 `AuthExpiredError`，但 result meta 加 `state_sync_degraded=True` flag 讓 operator UI 知道 dashboard 狀態可能未同步（per security-review F7）。
  6. 回 `AdapterResult(status="published", adapter=f"{self.channel}-browser-attach", platform=self.channel, published_url=final_url, post_publish_delay_seconds=...)`。
- Registry interaction：`adapters/__init__.py` 寫 `register("hashnode", BrowserPublishDispatcher.for_channel("hashnode"))`。需確認 `register` 接受 instance — 既有簽名 `*publishers: type[Publisher]` 偏向 class，調整為 `*publishers: type[Publisher] | Publisher`，`dispatch()` 內部處理：若是 type 則 instantiate，若是 instance 直接用。所有現有 `register("blogger", BloggerAPIAdapter)` 等 call sites 保持不變（type 路徑）。
- `available(cls, config)`：要求 `playwright` 已 importable + 共用 profile dir 存在或可建。注意：因 `available` 是 classmethod，所有 chrome publish channels 共用同一個 availability gate（合理 — 條件相同）。

**Patterns to follow:**
- `publishing/adapters/medium_browser.py::publish` — phase 結構、`_json_log`、screenshot path、`_safe_mark_expired` 模式
- `publishing/registry.py::Publisher` ABC contract

**Test scenarios:**
- Happy path：recipe.publish_flow 回 valid URL → `AdapterResult.status == "published"`、`published_url` 正確、adapter 標 `hashnode-browser-attach`（或任 channel）。
- Auth-expired path：page lands on signin URL → `mark_expired` 被呼叫 + `AuthExpiredError(channel=...)`。
- Error path：recipe.publish_flow raise `ExternalServiceError` → 截圖寫入 `screenshot_dir` + error propagate。
- Edge case：Playwright 未安裝 → `available()` 回 False，dispatcher 走下一 adapter。
- Integration：在 dispatcher chain 內 `dispatch({"platform": "hashnode", ...})` 觸發 `BrowserPublishDispatcher(channel="hashnode").publish` 並回 result（用 fake recipe）。
- Edge case：dofollow=False（mastodon）→ `publish` 在 log 標 `dofollow=False`，但不 raise（adapter 不該否決 operator）。
- Registry contract：`register("hashnode", BrowserPublishDispatcher.for_channel("hashnode"))` 後 `_REGISTRY["hashnode"]` 含 instance；`dispatch()` 不 re-instantiate（既有 type 路徑 instantiate；instance 路徑直接呼叫 `.publish`）。既有 `register("blogger", BloggerAPIAdapter)` 等 type-only 註冊仍正常。

**Verification:**
- `pytest tests/test_browser_publish_dispatcher.py tests/test_adapter_dispatcher.py` 全綠。
- `python -c "from backlink_publisher.publishing.browser_publish import BrowserPublishDispatcher; d = BrowserPublishDispatcher.for_channel('hashnode'); print(type(d).__name__, d.channel)"` 印 `BrowserPublishDispatcher hashnode`（同一 class、不同 instance — 確認非動態建類）。
- Stack trace smoke：故意讓 publish raise，traceback 落點為 `BrowserPublishDispatcher.publish` 而非 `HashnodeBrowserAdapter.publish`（合成名稱）。

---

- [x] **Unit 3: Hashnode chrome publish recipe + register**

**Goal:** 落地第一個 concrete recipe（hashnode），register 進 adapter chain，proving the abstraction。

**Requirements:** R3

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/__init__.py` — `RECIPES` dict
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/hashnode.py`
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/_hashnode_selectors.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — `register("hashnode", BrowserPublishDispatcher.for_channel("hashnode"))`
- Modify: `webui_app/binding_status.py` — `_DOFOLLOW_BY_CHANNEL["hashnode"]` 在 plan 不直接改，留 None 等 publish 後 verify_link_attributes 量測（per `[[probe-then-pivot-when-api-unverifiable]]`）
- Create: `tests/test_browser_publish_hashnode.py`

**Approach:**
- `compose_url = "https://hashnode.com/new"`。
- `publish_flow(page, payload)`：
  1. 探 signin URL → AuthExpiredError（dispatcher 處理）。
  2. wait_for selectors：title input、body editor、cover-image button（optional）、publish button。
  3. `page.locator(TITLE).fill(payload["title"])`。
  4. body：reuse `extract_publish_html(payload, "hashnode")` → clipboard paste 或 markdown direct type（量測後決定）。
  5. tags：optional `payload.get("tags", [])[:5]`。
  6. click PUBLISH → `wait_for_url(...)` 偵測 redirect 到 `https://*.hashnode.dev/<slug>`。
  7. 回 final URL。
- Selectors 由實作時 operator 開瀏覽器拍出，命名類比 `_medium_selectors.py`。
- 整合 `link_attr_verifier` post-check（dispatcher 已做）。
- **Selector decay 偵測**（per adversarial F9）：新增 pytest marker `real_browser_publish_smoke`（mirror 既有 `real_ssrf_check` / `real_content_fetch` pattern）。每個 recipe 一個 opt-in smoke test：開啟 compose_url 並 assert key selectors 存在（不真發帖）。Operator 可週跑 `pytest -m real_browser_publish_smoke` 在 selector 失效時收到早期警示。Plan 003 提示 Hashnode 反 Cloudflare 可能 block smoke probe — fail-soft 設計（marker 失敗不破 CI）。

**Patterns to follow:**
- `_medium_selectors.py` 選擇器命名與 placeholder
- `medium_browser.py::publish` 的 fill-title / fill-body / publish 三段式

**Test scenarios:**
- Happy path：fake recipe driver 走完三段 → dispatcher 回 `AdapterResult(platform="hashnode", published_url="https://x.hashnode.dev/test")`。
- Auth-expired：goto landed on `hashnode.com/signin` → `AuthExpiredError(channel="hashnode")` + `mark_expired("hashnode")`。
- Error path：publish button click 後 wait_for_url 超時 → ExternalServiceError + screenshot。
- Edge case：payload 缺 title → dispatcher 在 fill 階段 raise（schema 層應已 catch；此為 defense-in-depth）。
- Integration：`register("hashnode", HashnodeBrowserAdapter)` 後 `registered_platforms()` 含 "hashnode"，且 `test_r9_extension_readiness.py` 全綠（無 CLI / schema 改動）。

**Verification:**
- `pytest tests/test_browser_publish_hashnode.py tests/test_r9_extension_readiness.py` 全綠。
- `python -m backlink_publisher.cli.plan_backlinks --platforms hashnode <minimal-seeds>` 在 schema 層通過 — hashnode 重新成為可規劃平台。

---

**Unit 4 split rationale**：原 Unit 4 把 velog + devto + mastodon 三個獨立 channel 工作壓在單一 unit / 單一 PR，被 adversarial-review F7 標 high — 每個 channel 都需 live DOM 探索、selectors、success heuristics、auth detection、test fixtures（mastodon 還需 config 路徑）— 是 Unit 3-sized 努力 × 3。改拆三個 sibling units，可獨立 land / 獨立 defer 若 selector discovery 觸雷。

- [x] **Unit 4a: Velog chrome publish recipe**

**Goal:** Velog chrome publish recipe，append 到 `register("velog", ...)` chain 為 auth-missing fallback（不是 API outage fallback — 見 Velog fallthrough semantics 注解）。

**Requirements:** R4

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/velog.py`
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/_velog_selectors.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — velog 改為 `register("velog", VelogGraphQLAdapter, BrowserPublishDispatcher.for_channel("velog"))` (chain 加 browser fallback)
- Create: `tests/test_browser_publish_velog.py`

**Approach:**
- `compose_url = "https://velog.io/write"`，title + body markdown + tags + publish dialog
- Fallback chain semantics（per feasibility F9）：registry 只 `DependencyError` fall through；`ExternalServiceError` propagate。本 unit scope 為 "auth-missing fallback"，不是 "API outage fallback"
- Audit `velog_graphql.py` 錯誤映射 — 確認 cookies 不存在 / Playwright 未裝 / token 過期皆 raise `DependencyError`

**Patterns to follow:** Unit 3 hashnode recipe

**Test scenarios:**
- Happy path：fake recipe 走完 → result `published_url` 為 velog URL pattern
- velog fallback chain integration：`dispatch({"platform": "velog", ...})` 先試 `VelogGraphQLAdapter`，raise `DependencyError` 則 fall through 到 browser
- Selector smoke：`real_browser_publish_smoke` marker 開 velog.io/write 確認 title/body/publish selectors 存在

**Verification:**
- `pytest tests/test_browser_publish_velog.py tests/test_adapter_dispatcher.py` 全綠

---

- [x] **Unit 4b: Dev.to chrome publish recipe**

**Goal:** Devto chrome publish recipe；nofollow 平台 UI 警示沿用 Unit 5 dofollow-warning macro。

**Requirements:** R4

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/devto.py`
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/_devto_selectors.py`
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — `register("devto", BrowserPublishDispatcher.for_channel("devto"))`
- Confirm: `webui_app/binding_status.py::_DOFOLLOW_BY_CHANNEL["devto"] = False`（既有值，本 unit 只 assert 不改）
- Create: `tests/test_browser_publish_devto.py`

**Approach:**
- `compose_url = "https://dev.to/new"`，markdown editor + tags 用 `#tag` syntax + publish

**Patterns to follow:** Unit 3 hashnode recipe + Unit 4a velog

**Test scenarios:**
- Happy path：fake recipe → result `published_url` 為 dev.to/<user>/<slug> pattern
- dofollow audit：result meta 紀錄 `dofollow=False`、`link_attr_verification.blank_ratio` 量到 nofollow，不阻擋發帖
- Selector smoke：`real_browser_publish_smoke` marker

**Verification:**
- `pytest tests/test_browser_publish_devto.py` 全綠；devto 在 `registered_platforms()` 中

---

- [x] **Unit 4c: Mastodon chrome publish recipe + config plumbing**

**Goal:** Mastodon recipe + 新 config field `[mastodon] instance_url`（feasibility F10 指出本 plan 原文跳過 config 路徑 — 必補）。

**Requirements:** R4

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/mastodon.py`
- Create: `src/backlink_publisher/publishing/browser_publish/recipes/_mastodon_selectors.py`
- Modify: `src/backlink_publisher/config/loader.py::Config` dataclass — 加 `mastodon: MastodonConfig | None = None` 與 `MastodonConfig(instance_url: str)`
- Modify: `src/backlink_publisher/config/writer.py::save_config` — 補 `[mastodon]` section round-trip（已知 bug：save_config 不 round-trip [targets.*]/[sites.*]/[anchor_alarm]/[anchor.proportions]/[llm.anchor_provider]；本 unit 必修並補 test）
- Modify: `config.example.toml` — 加 `[mastodon] instance_url = "https://mastodon.social"` sample 註解
- Modify: `src/backlink_publisher/publishing/adapters/__init__.py` — `register("mastodon", BrowserPublishDispatcher.for_channel("mastodon"))`
- Confirm: `webui_app/binding_status.py::_DOFOLLOW_BY_CHANNEL["mastodon"] = False`
- Create: `tests/test_browser_publish_mastodon.py`
- Create: `tests/test_config_round_trip_mastodon.py`

**Approach:**
- `compose_url = config.mastodon.instance_url + "/publish"` (lazy resolve at publish time per [[embed-banner-lazy-config-load]] pattern)
- 多 instance：本 unit 鎖單一 instance；多 instance 留 follow-up（per-instance worktree 各 bind）
- Security warning（per security-review F4）：plan 文件 + AGENTS.md runbook 標明「禁用 personal Mastodon 帳號做 backlink publish — 用專屬 throwaway 帳號」；profile 即使 per-channel 隔離，operator 不應在 publishing profile 內手動瀏覽 personal Mastodon

**Patterns to follow:** Unit 3 hashnode recipe + [[embed-banner-lazy-config-load]]（config field lazy resolve）

**Test scenarios:**
- Happy path：fake recipe → result `published_url` 為 instance URL pattern
- Edge case：`mastodon_instance_url` 未設 / 空字串 → `DependencyError("mastodon instance URL not configured; set [mastodon] instance_url in config.toml")`
- Config round-trip：`save_config(load_config(toml))` 對含 `[mastodon]` section 的 TOML 不丟失（test 寫 `[mastodon] instance_url = ...` → load → save → assert TOML 仍含該 section）
- dofollow audit：log `dofollow=False`，不阻擋發帖
- Selector smoke：`real_browser_publish_smoke` marker（操作員自行 set `MASTODON_SMOKE_INSTANCE_URL`，否則 marker skip）

**Verification:**
- `pytest tests/test_browser_publish_mastodon.py tests/test_config_round_trip_mastodon.py` 全綠
- `python -c "from backlink_publisher.publishing.registry import registered_platforms; print(sorted(registered_platforms()))"` 含 hashnode, devto, mastodon, velog

---

- [x] **Unit 5: WebUI Settings publish-backend UI + dofollow guardrail + Chrome profile health**

**Goal:** Settings dashboard 每張 channel 卡片底部新增「Publish backend」pill（read-only this unit）；dofollow=False channel 紅字警示（dofollow 知識從 `status.dofollow` 取，不重複）；新增 `chrome_profile_health` 與 `chrome_publish_status` indicator 在 dashboard 顯示綁定狀態與 CDP port 活躍度 — 提供 D3 共用 profile contamination 的可視化偵測點。

**Requirements:** R5, R8（implicit — UI parity）

**Dependencies:** Unit 1（chrome_session probe API）、Unit 3 / Unit 4（channels 已 register）

**Files:**
- Modify: `webui_app/helpers.py` — 既有 `dashboard_binding_methods` 升維為 `dashboard_channel_methods`，shape `{channel: {"bind": {...}, "publish": {...}}}`；既有 consumers 同 Unit in-place 改 key 路徑（per pattern-reviewer Gap 2 — sibling dict 會 drift）。**Pre-implementation grep enumerated consumers**（per [[grep-before-writing-brainstorm-plan-claims]]）：`helpers.py:1023, 1041, 1145` + `templates/_channel_card_macro.html:46` — 共 4 site，scope 確定，全在本 unit 內遷移
- Modify: `webui_app/helpers.py::_settings_context` — return dict 加 `chrome_publish_status=_get_chrome_publish_status(cfg)` 與 `chrome_profile_health=_get_chrome_profile_health(cfg)`（per pattern-reviewer Gap 4 — 不走 per-route plumbing，沿 medium_status/velog_status 同位置）
- Modify: `webui_app/templates/_channel_card_macro.html` — 卡片底部加 publish backend pill；dofollow=False 時紅字警示由 template 計算（讀 `status.dofollow`，不從 `methods.publish` 重複取，per pattern-reviewer Gap 1）
- Create: `webui_app/helpers.py::_get_chrome_publish_status(cfg)` — filesystem + CDP probe：`<config_dir>/real-chrome-profile/Default/Cookies` 存在性、CDP port 探活（500ms timeout）、`<config_dir>/real-chrome-publish.pid` 是否健康。回 `{"profile_ready": bool, "cdp_port_alive": bool, "port": int|None, "pid_alive": bool}`
- Create: `webui_app/helpers.py::_get_chrome_profile_health(cfg)` — 反映 D3 invariant：profile dir 大小、cookie DB mtime、最近 N 筆 chrome publish 失敗計數（從既有 history store）。回 `{"last_bind_age_days": int|None, "cookie_db_size_mb": float, "recent_chrome_failures": int}` — 提供 operator 早期偵測 anti-bot contamination 的入口
- Create: `tests/test_webui_settings_publish_backend_render.py`
- Create: `tests/test_webui_settings_chrome_publish_status.py`
- Create: `tests/test_webui_dashboard_channel_methods_drift.py` — 補 drift guardrail：`set(dashboard_channel_methods) == set(registered_platforms())` 確保新 channel 上線時 UI 必同步

**Out of scope this unit（per pattern-reviewer Gap 3）**：
- 不動 `_settings_channel_binding.html`（那是 bind action partial；publish backend 在本 unit 為 read-only，等後續 plan 開啟「per-channel 切換 publish backend」mutation flow 時再加 selector）。

**Approach:**
- `dashboard_channel_methods` 結構（單一 dict，per Gap 2）：

  ```
  {
    "hashnode": {"bind": {"kind": "chrome", "label": "Chrome DevTools 綁定", "backend": "chrome"}, "publish": {"kind": "chrome-attach", "label": "Chrome 發帖（唯一路徑 · paywall）"}},
    "velog":    {"bind": {"kind": "chrome", ...}, "publish": {"kind": "chrome-attach", "label": "Chrome 發帖（API fallback）"}},
    "devto":    {"bind": {"kind": "chrome", ...}, "publish": {"kind": "chrome-attach", "label": "Chrome 發帖 · nofollow"}},
    "mastodon": {"bind": {"kind": "chrome", ...}, "publish": {"kind": "chrome-attach", "label": "Chrome 發帖 · nofollow"}},
    "medium":   {"bind": {"kind": "chrome", ...}, "publish": {"kind": "browser-playwright", "label": "Playwright + cookies"}},
    "telegraph":{"bind": {"kind": "chrome", ...}, "publish": {"kind": "api"}},
    "ghpages":  {"bind": {"kind": "link", ...}, "publish": {"kind": "api"}},
    "writeas":  {"bind": {"kind": "link", ...}, "publish": {"kind": "api"}},
    "blogger":  {"bind": {"kind": "link", ...}, "publish": {"kind": "api"}},
  }
  ```

  per design-review D-5（AI slop）：每家 label 區分各自定位（hashnode 唯一路徑 / velog fallback / devto+mastodon nofollow / medium 走 Playwright），不全寫成 generic「Chrome 發帖」。

  注意：dict 內**不含 dofollow 任何欄位**（Gap 1）— dofollow 從 `status.dofollow` 讀（既有 `_DOFOLLOW_BY_CHANNEL` single source of truth）。
- Template 渲染（`_channel_card_macro.html`）：

  ```
  {% if methods.publish.kind == "chrome-attach" and status.dofollow is false %}
    <span class="text-danger small" role="status" aria-label="dofollow 為否，不貢獻 SEO 權重">
      <i class="bi bi-exclamation-triangle" aria-hidden="true"></i>
      nofollow — 不貢獻 SEO
    </span>
  {% endif %}
  ```

  - **dofollow UI 去重（per design-review D-2）**：既有 card line 23-28 三態 dofollow badge（good/weak/unknown）仍渲染（top-of-card "狀態" 標籤）；新增的紅字警示是 publish-specific contextual 訊息（顯示在 publish backend pill 旁邊）。兩者不重複 — 一個是 "channel 整體 dofollow status"，一個是 "你要 chrome-attach publish，這個動作產出 nofollow"。template 寫註解解釋分工避免後人合併。
  - **無障礙（per design-review D-6）**：紅字配 `bi-exclamation-triangle` icon（不依賴色彩 — WCAG color-blind 友善）、`role="status"` + `aria-label` 完整文案。
  - **State enumeration（per design-review D-1）**：chrome publish pill 完整狀態表落 Unit 5 template 內部註解：
    - `profile_ready=False` → "尚未綁定" pill（grey）
    - `profile_ready=True, cdp_port_alive=False, pid_alive=False` → "待機" pill（藍）— 正常 pre-publish
    - `profile_ready=True, cdp_port_alive=True, pid_alive=True` (owned) → "Chrome 啟動中" pill（黃）
    - `profile_ready=True, cdp_port_alive=True, pid_alive=False` → "Attached（非我方啟動）" pill（綠）
    - `cdp_port_alive=False, pid_alive=True` → "卡住，等待 reap" pill（橙）— 觸發 orphan reap 提示
    - `recent_chrome_failures > 5 in 24h` → 紅字 banner「Chrome profile 可能被反爬偵測」+「重新綁定 <channel>」CTA 按鈕（per design-review D-1/D-3 — 不是 text-only dead end）
- `_get_chrome_publish_status()` 與 `_get_chrome_profile_health()` 走 stat + 500ms CDP probe；fail-soft，回 default-False struct 而不 raise（[[fetch-json-must-guard-content-type]] — Settings render 不能因 probe 異常爆炸）。
- 注意 [[grep-before-writing-brainstorm-plan-claims]]：實作前需 `grep -n "dashboard_binding_methods\|_settings_context\|_channel_card_macro" webui_app/` 確認 callers + line 範圍 + 升維後 consumer 路徑（如 `.bind.kind`）全改到。

**Patterns to follow:**
- `webui_app/helpers.py::dashboard_binding_methods`（升維基礎，consumers 對照 in-place 改）
- `webui_app/helpers.py::_get_medium_browser_status`（filesystem-only probe pattern — sibling helper 即將出現 _get_chrome_publish_status 與 _get_chrome_profile_health）
- `webui_app/binding_status.py::_DOFOLLOW_BY_CHANNEL`（dofollow single source of truth — 讀，不複製）
- `_channel_card_macro.html:23-28` 既有 dofollow badge 三態（good/weak/unknown）— 警示文案沿這個 status.dofollow 維度

**Test scenarios:**
- Happy path：settings render 含 `dashboard_channel_methods["hashnode"]["publish"]["kind"] == "chrome-attach"`，template 渲染含 Chrome 發帖 pill。
- Happy path：CDP port up（fake stat + fake http）→ `chrome_publish_status.cdp_port_alive == True`，UI 顯示 "Chrome 待命中"。
- Edge case：profile dir 不存在 → `chrome_publish_status.profile_ready == False`，UI 顯示 "尚未綁定，請先完成 Chrome DevTools 綁定"。
- Edge case：dofollow=False channel（devto/mastodon）卡片 render 含紅字 `nofollow — 不貢獻 SEO` 字串，且該字串由 template 從 `status.dofollow` 計算（不從 methods.publish 取，drift 防護）。
- Error path：CDP probe timeout（fake socket）→ settings 仍正常 render（不掛），`cdp_port_alive == False`。
- Integration：render endpoint GET `/settings` 200，HTML 含全部 channel cards 與 publish backend pill；context inject 路徑正確（在 `_settings_context` return dict，不在 per-route）。
- Drift guard：`set(dashboard_channel_methods) == set(registered_platforms())` — 新 channel 加 registry 必須補 methods dict，反之亦然。**執行時序：Unit 5 自身先寫好 dict 鏡像當前 `registered_platforms()` 結果；待 Unit 3 + 4a/4b/4c 完成後此 test 自動驗 4 個新 channel 是否進 dict**（不在 Unit 5 isolation 跑該 test — 標 `pytest.mark.integration` 由 CI integration job 觸發，per coherence Coh-9）。
- Profile health：`chrome_profile_health.recent_chrome_failures > 5` 時 UI 顯示「Chrome profile 可能被反爬偵測，建議重新登入」提示 — D3 contamination 可視化驗證點。
- Bind methods consumer migration：既有 dashboard template 讀 `dashboard_binding_methods["medium"]` 改為 `dashboard_channel_methods["medium"]["bind"]`；同 commit 內所有 caller 對齊，無 `KeyError` 殘留。

**Verification:**
- `pytest tests/test_webui_settings_publish_backend_render.py tests/test_webui_settings_chrome_publish_status.py tests/test_settings_render*.py` 全綠。
- `python webui.py` 手動 smoke：開啟 :8888/settings，每個 chrome publish channel 卡片底部能看到 backend pill；devto/mastodon 卡片紅字警示出現；Chrome publish 狀態指示器顯示 profile 與 port 狀態。

## System-Wide Impact

- **Interaction graph：**
  - `dispatch()`（registry.py）走 channel chain；新 channel 走 `BrowserPublishDispatcher.publish` → `ChromeAttachSession` → recipe.publish_flow。velog 既有 GraphQL adapter 仍在 chain head，browser 為 fallback；hashnode chain 只有 browser；devto/mastodon 同。
  - WebUI Settings render 透過 `_render` auto-inject 注入 `publish_backend_methods` 與 `chrome_publish_status`，不靠 per-route plumbing。
  - `mark_expired(channel)`（webui_store.channel_status）在 chrome publish 失敗時被呼叫，與 medium_browser 共用同一個 store，dashboard "已過期" 卡片狀態正常反映。
- **Error propagation：**
  - `DependencyError`（Playwright 未裝 / Chrome 不可用 / profile lock）→ dispatcher fall through 到下個 adapter；hashnode 無下個 adapter → exit 3。
  - `AuthExpiredError`（login redirect / cookie 失效）→ `mark_expired` + propagate，dispatcher 不 fall through（已 mark expired，operator 須重 bind）。
  - `ExternalServiceError`（captcha / Cloudflare challenge / 網路）→ non-retryable，propagate 給上層 `publish-backlinks` 處理。
- **State lifecycle risks：**
  - Chrome process 由 `ChromeAttachSession` 啟動但不關閉 — long-running webui 場景需 operator 手動關 Chrome（與 bind 階段同模型，可接受）。pytest 必殺乾淨（[[chrome-devtools-cdp-traps]]）— 在 `conftest.py` fixture 補殺。
  - `<config_dir>/real-chrome-profile` 共用 across bind + publish + cross-channel — cookies 累積。`cookie_host_filter`（bind 階段已 enforce）僅影響 storage_state.json，不影響 attached Chrome 中的真 profile cookies。可接受：profile 為 operator 私有。
  - Multiple concurrent publish（同 channel）競爭同 Chrome profile：`chrome_profile_locked` 已是 DependencyError。Single-publish-process 假設成立（CLI 與 webui 都 single-row publish）。
- **API surface parity：**
  - `Publisher` ABC 契約零改動；`AdapterResult` shape 零改動。
  - `schema.validate_publish_payload` 自動接受新平台（registry-driven，per R9）。
  - CLI `--platforms` argparse 自動接受新平台（registry-driven）。
- **Integration coverage：**
  - `test_adapter_dispatcher.py` 補 chrome publish fall-through scenario（velog API fail → browser fallback）。
  - `test_r9_extension_readiness.py` 必綠 — 整個 plan 不該觸發任何 CLI / schema 改動。
  - `test_no_monolith_regrowth.py` — `medium_browser.py` SLOC 保持，新 module 在新檔不增加既有 6 個 ceiling files SLOC。
- **Unchanged invariants：**
  - `medium_browser.py` 行為與 SLOC 不動（Playwright ephemeral + cookies.json 注入照舊）。
  - `cli/_bind/*` 行為不動，只搬 chrome path helpers 到 shared module（functions 仍可從 `cli/_bind/chrome_backend` 匯入 — backward compat）。
  - `_DOFOLLOW_BY_CHANNEL` value 不在本 plan 改動（hashnode 仍 None，待實證；devto/mastodon 仍 False）— **dofollow knowledge single source of truth 保持在 `binding_status.py`，Unit 5 的 UI 不複製此資訊**。
- **Named invariants（明確記錄）：**
  - **Per-channel profile invariant（D3 修訂）**：`<config_dir>/real-chrome-profile/<channel>/` 是每個 chrome-attach channel 私有的 identity surface。`ChromeAttachSession` 透過設定 `BACKLINK_PUBLISHER_BIND_CHANNEL=<channel>` 強制 `_profile_dir()` 回傳 per-channel subdir。一個 channel 的 anti-bot trip 不影響其他 channels。
  - **Lifecycle ownership invariant**：`ChromeAttachSession` 只關自己 launch 的 Chrome（owned=True）；attach 到既存 CDP 的 process（owned=False）不關 — attacher 對 attachee 無關閉權。Crash-orphan 由 startup reap 處理（含 PID + executable + start-time 三驗證避免 PID reuse 攻擊）。
  - **Listener identity invariant**：CDP attach 前必驗證 listener PID 對應的 executable 與 `--user-data-dir` cmdline，拒絕 attach 到我方未啟動的 Chrome（防 local port squatting）。
  - **Cross-phase mutex invariant**：bind 與 publish 對同一 channel 的 Chrome profile 透過 `<config_dir>/chrome-profile.lock` flock 互斥；同時觸發時後到者 raise `chrome_profile_locked`，operator UI 顯示「正在 bind / 正在 publish，請稍候」。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `playwright.connect_over_cdp` 在 attach 模式下 framenavigated 仍 orphan（同 SSO 跨 origin 痛點，[[playwright-framenavigated-orphaned-during-cross-origin-sso]]） | publish_flow 偵測 success 不只靠 framenavigated；用 `wait_for_url(...)` regex + DOM `wait_for_selector("已發布")` 雙保險。每個 recipe 寫絕對 timeout（90s/15min idle + 20min absolute，mirror bind 策略）。 |
| Chrome profile lock — operator 個人 Chrome 已開 → `_profile_dir()` 雖獨立，但 Chrome process 啟動仍可能 EAGAIN | `ChromeAttachSession` catch OSError 並 retry 1 次（500ms）；仍失敗 → `DependencyError("chrome_profile_locked")`，UI 顯示 `BIND_ERROR_MESSAGES["chrome_profile_locked"]` 文案。 |
| Crash-orphan Chrome — webui 在 publish 中崩潰，launched Chrome 仍 alive 占住 profile lock 阻塞下一次 bind/publish | D2 設計的 PID 檔 + startup reap：`<config_dir>/real-chrome-publish.pid` 在 launch 時寫入，`__exit__` 時 unlink；webui `create_app` startup hook 讀 pid 檔，alive 但不在 webui process tree → terminate + unlink（Unit 1 test scenario 涵蓋）。 |
| Per-channel anti-bot contamination — Cloudflare 對 channel A 的 anti-bot trip 在 profile 留下 `cf_clearance` / captcha cookies | **D3 修訂後不再 cascade**：每個 channel 用獨立 `real-chrome-profile/<channel>/` subdir（沿用 bind 既有 `BACKLINK_PUBLISHER_BIND_CHANNEL` 機制），單 channel trip 不影響其他 channels。Operator 需重 bind 該 channel 即可。 |
| CDP port squatting — local 其他 process 佔住 9222 → publish 誤 attach 到非我方 Chrome | Unit 1 attach 前驗證 listener PID 的 executable + `--user-data-dir` cmdline；驗證失敗 → `DependencyError("chrome_cdp_foreign_listener")` 不 attach（per security-review F1）。 |
| PID reuse — webui crash + reboot 後 PID 被 OS recycle 給無關 process，reap 誤殺 | PID 檔同時記錄 launched-by-webui PID + start-time；reap 前 `ps -o lstart=` 比對 start-time，executable + cmdline 三驗證後才 kill（per security-review F3）。 |
| macOS Chrome helper subprocess tree — SIGTERM 父 PID 不一定回收 helper processes，留下 SQLite profile lock | Unit 1 用 `Popen(start_new_session=True)` 建獨立 process group，terminate 走 `os.killpg`；test 涵蓋 launch + 立即 kill + 再 attach 確保 profile lock 釋放（per feasibility F5）。 |
| Bind 與 publish 對同 channel 並發 — operator 在 webui 點 bind 同時 publish-backlinks subprocess 跑 → 兩個 Chrome 競爭 profile lock | `<config_dir>/chrome-profile.lock` flock cross-phase mutex（per feasibility F6）；UI 顯示「正在 publish，請稍候」。 |
| 抽 chrome path helpers 到 shared module 破壞 bind 既有測試 | Unit 1 在動 shared module 時 `from .._util.chrome_paths import _chrome_port as _chrome_port` 等 re-export 保留 bind 階段 import 路徑；跑 `test_bind_channel_chrome_backend.py` 作 gate。 |
| Dev.to / Mastodon nofollow trade-off 被 operator 忽視，最後爆「為什麼鏈接沒效果」 | UI 紅字 + 確認 modal；Adapter publish log 也記 `dofollow=False`；`docs/refs/` 新增備忘列出每家 dofollow 狀態。 |
| Hashnode web UI DOM selectors 不穩 / 重排 | `_hashnode_selectors.py` 單檔集中（mirror `_medium_selectors.py`），改 selectors 不動 recipe.publish_flow；每次失敗截圖落 `screenshot_dir`，operator 看截圖即可拍新 selectors。 |
| 並發 publish 跨 channel 同 profile 競爭 | 本 plan 預設 single-row publish；若未來開 concurrent，需加 per-profile lock（defer 到 future plan，不在本 plan 阻塞）。 |
| Playwright 版本與 Chrome 主版本不匹配（CDP protocol drift） | `chrome_session` 啟動時 log `chromium_version` + `playwright.__version__`；test 加 smoke 確保 connect_over_cdp 不 raise import-time。 |
| `tests/test_no_monolith_regrowth.py` 超預算 | 全部新邏輯落新 module（`publishing/browser_publish/...`），既有 6 個 ceiling files 零行新增；如需動 medium_browser.py 任何行，**本 plan 不動**。 |

## Documentation / Operational Notes

- 更新 `AGENTS.md` 「Adding a new publisher adapter」附加段落：「若新平台為 browser publish 而非 API，撰寫一個 `BrowserPublishRecipe` 在 `publishing/browser_publish/recipes/<name>.py`，然後 `register(name, BrowserPublishDispatcher.for_channel(name))` — 不寫 Adapter class」。
- 更新 `AGENTS.md` 加 operator security note（per security-review F4）：「Chrome publish 用 per-channel profile（`real-chrome-profile/<channel>/`），但 operator 不應在該 profile 內手動瀏覽 personal 站點 — 尤其 Mastodon 應使用 throwaway 帳號專供 backlink publish」。
- 不在本 plan 新增 user-facing 文檔（CLAUDE.md / README）— 等 chrome publish 首次成功 publish 並驗證 dofollow 後再 docs/solutions/best-practices 落筆。
- Operator runbook：「Chrome publish 失敗診斷」append [[bind-channel-diagnostic-playbook]] 補一節「publish 階段差異：connect_over_cdp 失敗時先 `lsof -i:<port>` 看 port 占用 + executable identity」。

### Follow-up plan placeholders

- **Per-channel publish backend mutation UI** — 本 plan Unit 5 為 read-only 顯示；後續 plan 開「operator 切換 publish backend」mutation flow 時，新 POST endpoint 必繼承既有 WebUI off-loopback gate（`BACKLINK_PUBLISHER_ALLOW_NETWORK`）+ `/save-*` 路由 CSRF middleware，**不**新增 ad-hoc state-change endpoint（per security-review F9）。
- **Medium migration to chrome-attach** — 留 follow-up；觸發條件：Medium Cloudflare 痛點加劇 OR cookies.json rotation 失敗率上升。
- **Hashnode dofollow downgrade** — 若 Unit 3 probe 量到 nofollow → hotfix `_DOFOLLOW_BY_CHANNEL["hashnode"] = False` 並從 default platforms 移除。
- **Performance**: `_get_chrome_publish_status` 500ms CDP probe 在每次 `/settings` GET 觸發；累積與 medium_status + velog_status 可能 1.5-3s render（per feasibility F11）。本 plan 接受 fail-soft；若實際 render latency 變問題，follow-up cache 5s in-process。

## Sources & References

- Related code:
  - `src/backlink_publisher/publishing/adapters/medium_browser.py`（既有 browser publisher 參考）
  - `src/backlink_publisher/cli/_bind/chrome_backend.py`（Chrome path helpers 來源）
  - `src/backlink_publisher/cli/_bind/recipes/__init__.py::ChannelRecipe`（recipe pattern 鏡像）
  - `src/backlink_publisher/publishing/registry.py::Publisher`（ABC 契約）
  - `webui_app/helpers.py::dashboard_binding_methods`（UI methods dict pattern）
  - `webui_app/binding_status.py::_DOFOLLOW_BY_CHANNEL`（dofollow audit single source of truth）
- Related PRs:
  - #138（PR Chrome/Playwright bind + medium pipeline repair，OPEN — 提供 chrome backend 與 cookies extraction 上下文）
  - #108 → #109（dofollow nofollow revert 教訓）
  - #123（embed_banner contract — `BannerUploadError` semantics 可借鏡）
- Institutional learnings:
  - `[[probe-then-pivot-when-api-unverifiable]]`
  - `[[playwright-framenavigated-orphaned-during-cross-origin-sso]]`
  - `[[chrome-devtools-cdp-traps]]`
  - `[[bind-channel-diagnostic-playbook]]`
  - `[[grep-dofollow-map-before-shipping-adapter]]`
  - `[[hidden-from-ui-pattern-for-retiring-channels]]`
  - `[[render-auto-inject-over-per-route]]`
  - `[[grep-before-writing-brainstorm-plan-claims]]`
- External docs:
  - Playwright `browser.connect_over_cdp(endpointURL)` — official CDP attach API
  - Chrome DevTools Protocol `/json/version` 與 `/json/new?url=` — 已被 `chrome_backend.py` 驗證
- AGENTS.md 段落：「Adding a new publisher adapter」（line 249）
