---
title: "feat: Adapter Contract Canary — 主動偵測平台靜默契約漂移"
type: feat
status: completed
shipped: 7bbaf119 (#268)
date: 2026-05-27
origin: docs/brainstorms/2026-05-27-adapter-contract-canary-requirements.md
claims: {}
---

# feat: Adapter Contract Canary

## Overview

新增一個只讀的 `canary-targets` CLI verb + 持久化 per-platform health store,定期/on-demand 重抓每個 dofollow 平台上一篇長存的「canary 貼」,斷言**目標 backlink 自己的錨點**還在、還是 dofollow、頁面無明顯 noindex,漂移時以 **advisory 為預設**(響亮告警,不靜默停發)surface;per-platform opt-in 才升級為 hard-skip。

研究已解答 origin 的 Q2(刪除能力):唯 `blogger`、`ghpages` 乾淨可刪,其餘平台無/不確定刪除能力。故 **v1 走 evergreen 只讀**(re-fetch 既有貼,零刪貼工程、零帳號污染、全 cohort 通用);L3「真發+清」往返縮到 blogger+ghpages 兩平台,列為 Phase 2 follow-up。

本計畫**本質是自動化既有的手動 dofollow canary 流程**(operator 跑 `verify_link_attributes` 把 `uncertain→True`),不是發明新機制 — 見 origin 與 `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`。

## Problem Frame

發布 adapter 依賴每平台的契約(選擇器/schema/rel)。平台靜默改動→adapter 不報錯但鏈接被改 nofollow / 被剝離 / 發空,跨 31 adapter 幾天到幾週才被發現,期間 SEO campaign 在無效產出。現有零件(`link_attr_verifier`、`_preflight_fetch`、`audit-state`)都 reactive,沒組成主動偵測防線。(see origin: `docs/brainstorms/2026-05-27-adapter-contract-canary-requirements.md`)

**威脅覆蓋矩陣(誠實邊界 — 關鍵):** evergreen 重抓的是 seeding 時(舊契約下)已發布的靜態頁,因此它能抓「平台**回溯改寫**既有頁的 rel/noindex」,但**結構上 blind 於「前向發布路徑漂移」**(明天 adapter 發新貼才會踩到的 auth/schema/selector 破),而後者正是 Problem Frame 的頭號威脅。

| 失敗模式 | evergreen (v1 全 cohort) | L3 真發+清 (Phase 2, 限 blogger/ghpages) |
|---|---|---|
| 既有頁 rel 被回溯改 nofollow / 被剝離 | ✅ 抓得到 | ✅ |
| 既有頁被加 noindex | ✅ 抓得到 | ✅ |
| **前向發布路徑破**(auth/schema/selector,新貼發空/失敗) | ❌ **抓不到** | ✅ |
| 平台死亡 / canary 頁 rot | ⚠️ 表現為 advisory(見 R9/not-configured) | ✅ |

故 v1 的 operator-facing verdict 用 **`link-alive`** 而非 `healthy`,使「綠」永不被誤讀成「發布管線正常」。頭號威脅(前向漂移)在 v1 僅對未來 Phase 2 的 2 個平台覆蓋;v1 主要交付「既有 dofollow 鏈接的回溯保護 + 早期告警基建」。

## Requirements Trace

- **R1** 目標錨點斷言:重抓 live URL,斷言 (a) **目標 backlink href 存在於頁面**、(b) **該目標錨點** dofollow、(c) 頁面無明顯 noindex(meta/X-Robots-Tag,necessary-not-sufficient)。**禁用全頁 `nofollow_detected`** 作判據。
- **R2** L3(真發+清)僅對核實可刪的平台(blogger/ghpages)啟用;刪除失敗/無 handle→記 orphan+告警+STOP,不靜默判綠 → **Phase 2**。
- **R3** evergreen 模式:重抓長存 marked 貼重斷言;報告明示「只存活覆蓋,非發布路徑覆蓋」。**v1 全 cohort 默認此模式**。
- **R4** 抓取走 SSRF-guarded 路徑;dofollow 判定擴充 `link_attr_verifier` 為**目標 href 比對**;indexability 復用 `_preflight_fetch`。
- **R5** cohort 動態 = `registry.dofollow_status(name) is True`(blogger/medium/telegraph/velog/ghpages/livejournal);**禁用 `referral_value` 謂詞**(正交軸,對 dofollow tier 恆 None→空集)。
- **R6** 啟動斷言 cohort 非空(fail-loud)。
- **R7** `uncertain` 平台 v1 不納入,列 Phase 2 promotion 偵測(見 Scope Boundaries)。
- **R8** per-platform health 狀態持久化,跨 run 存活,攜帶去抖信號。
- **R9** 去抖 + 紅因區分:auth-過期/限流紅不計入契約 quarantine;**ambiguous(soft-404/null/ssrf-blocked)→advisory 不 quarantine**;只確認的契約漂移才升級。
- **R10** 預設 advisory(告警+儀表板紅+發布時 WARNING);hard-skip per-platform opt-in。
- **R11** 抗 flap re-arm(連綠 M≥2 / 冷卻窗);flap 升人工告警。
- **R12** SSRF 硬驗收:重抓含 redirect hop 走 `_make_ssrf_opener`+post-redirect 重檢;`real_ssrf_check` 測試。
- **R13** canary 抓取沿用既有 preflight 的獨立 UA(便於 target 分開限流)。**誠實限制**:獨立 UA 可被平台 UA-cloaking(對 canary 餵 dofollow、對真實流量餵 nofollow),故 canary 的 dofollow verdict 是「契約漂移信號」而非「真實訪客/爬蟲所見保證」——與「不是 indexability oracle」同級的免責。(取代原 R13 自相矛盾的「絕不用專屬 UA」表述。)
- **R14** marker **私有且跨所有 footprint 維度 varying**(`attr_order/rel/target/preceding_char` + 周邊結構,非僅一個高熵 token);過 `footprint` 稽核斷言每維度 <95% prevalence。
- **R15** 排程 canary 載入憑證只走既有 0o600/atomic_write 路徑;receipt/health/告警 payload 為**非敏感欄位白名單**(平台名/verdict/failed-check 名/時間戳/去抖計數),**禁**含憑證/token/cookie/raw HTML/帶憑證 query string。
- **R16** health 寫入可被 health-dashboard/TG 消費;**勿用 bind-scoped `channel_status_store`**(只認 velog/medium/blogger,會拒 telegraph/ghpages/livejournal)。
- **R17** verb 純 on-demand(exit 0,JSONL);排程由 harness Cron/RemoteTrigger 疊加,**不 baked-in**;cadence × N 須滿足偵測延遲上限。

## Scope Boundaries

- 只覆蓋 `dofollow_status is True` 平台;退役/`dofollow=False` 排除;`uncertain` 平台 v1 不納入(列 Phase 2 promotion 偵測)。
- 預設不做 hard auto-skip(advisory 預設;hard-skip per-platform opt-in)。
- 不是 indexability oracle(只查頁面層 noindex,necessary-not-sufficient)。
- 不做 adapter 契約靜態宣告 / typed `AdapterContractError`。
- **v1 不建刪貼原語**;L3 真發+清整體列 Phase 2,且只限 blogger+ghpages。
- 排程 cron 不寫進 verb。
- canary 貼的初始 seeding(每平台發一篇長存 marked 貼)是 operator 一次性動作,不在自動化範圍。

## Context & Research

### Relevant Code and Patterns

- **CLI verb 範本**:`cli/preflight_targets.py` — 逐字鏡像。`argparse` + **post-parse 閉集合校驗**(`UsageError` exit 1,**非** `choices=` exit 2,見 `feedback_argparse_choices_vs_usage_error_exit_code_clash`)、`read_jsonl`/`write_jsonl`、`PipelineLogger("canary-targets")`+`logger.recon` stderr summary、`try/except PipelineError: handle_error`、`main()` 返 None=exit 0(advisory 天然姿態)。`_failed_checks()`(全集)+ `_classify()`(first-match ladder)+ `_build_receipt()`(one fact→one key)+ 模塊級 `VERDICTS`。
- **註冊 verb**:`pyproject.toml [project.scripts]` 加一行(現 line 45 是 `preflight-targets`);`cli/__init__.py` 是 stub 不動。
- **cohort 讀法**:`import backlink_publisher.publishing.adapters  # noqa: F401 populate registry` 後 `registry.registered_platforms()` 過濾 `registry.dofollow_status(p) is True`。
- **SSRF-guarded 抓取**:`content/_preflight_fetch.py::fetch_target(url) -> PreflightFacts`(never-raise;`noindex`/`x_robots_tag` 現成給 R1c)。`_PREFLIGHT_OPENER=_make_ssrf_opener(5)`;per-hop + post-redirect 重檢。`_util/net_safety._check_url_for_ssrf`(never-raise on malformed IPv6,見 `feedback_urlparse_raises_on_malformed_ipv6`)。
- **dofollow 檢測原語**:`publishing/adapters/link_attr_verifier.py::verify_link_attributes(url) -> dict`。⚠️ 現只聚合全頁錨點(`nofollow_detected = any nofollow`),**不比對目標 href**,且走**未 SSRF-guard 的 `backlink_publisher.http.get`**。兩個 gap 都在 Unit 2 修。
- **state store 範本**:`webui_store/base.py::JsonStore`(`load/save/update(fn)`,per-instance Lock,`safe_write.atomic_write` 0o600)+ `_LazyStore`(`BACKLINK_PUBLISHER_CONFIG_DIR` re-resolve)。鏡像 `webui_store/channel_status.py` 結構但 key=registry 平台名。config dir 走 `config.loader._resolve_config_dir()`,**never hardcode `Path.home()`**(`feedback_webui_store_config_dir_frozen`/`feedback_config_paths_must_respect_env_var`)。
- **quarantine gate 點**:CLI pre-dispatch filter(advisory,低 blast)>`available()`(env-capability 語義,非 health;`feedback_patch_available_when_mocking_chain_publish` 顯示已 test-sensitive)。
- **delete 能力盤點(Q2 解答)**:blogger=`posts().delete`(需從 insert response 抽 post_id,現丟棄);ghpages=GitHub `DELETE contents`(file-path handle 可得);telegraph=editPage 有、delete 無;velog=introspection 關;livejournal=XML-RPC 可能但 secret 不可撤銷;medium=API 棄。→ L3 只 blogger+ghpages。

### Institutional Learnings

- `docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md` — **必查目標錨點 rel,非全頁 flag**(已踩過的 bug);verdict 須 fresh publish 寫入非 resume;「surface data, don't auto-decide」是 advisory 預設的先例。
- `docs/runbooks/2026-05-25-dofollow-canary-closeout.md` + `docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md` — uncertain→True 工作流;**throwaway-account gate**(livejournal secret 不可撤銷);「canary closed ≠ in production」→ health 狀態分 registered/healthy vs in-rotation;flip 仍是人工 PR。
- `docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md` — 三態分類器(`kind|NO_EMIT|QUARANTINE`)= healthy/advisory-debounced/quarantine 的形狀;**mass-quarantine ratio guard**(一片紅不算 clean run,單點不 quarantine);WAL 巢狀連線死鎖→寫延後到 commit 之後。
- `docs/brainstorms/2026-05-26-destination-page-preflight-requirements.md` — SSRF 原語非協商;verdict ladder=verdict+failed-check list+timestamp(no durability claim);誠實措辭「not obviously dead」≠「indexable」。
- `docs/brainstorms/2026-05-27-cross-channel-blast-radius-requirements.md`(今日)+ `docs/plans/2026-05-18-007-feat-footprint-regression-gate-plan.md` — footprint byte-signature 維度 `attr_order/rel/target/preceding_char` ≥95% 告警;**marker 必須 varying**(常量 sentinel=自己的 SpamBrain cluster key);gate 先 advisory exit-0 再校準;`OVERRIDE.md` break-glass 模式。
- `docs/solutions/best-practices/credential-rotation-tests-cover-bootstrap-race-2026-05-19.md` + telegraph rotation ref — canary 與 publish loop 並發→憑證觸碰走單一 `credential_mutation_lock`,寫 `threading.Barrier(2)` 測試。
- `docs/solutions/best-practices/probe-then-pivot-when-api-unverifiable-2026-05-20.md` + ContentRejectedError sibling — ambiguous re-fetch(null/soft-404/ssrf-blocked)→classify uncertain/advisory,**「讀不到」≠「漂移」**。
- 寫 state JSON 走 `safe_write.atomic_write` 0o600(`feedback_atomic_write_canonical_for_secrets`);讀 events.db 用 snapshot-copy read-only(`reference_events_db_readonly_wal_snapshot`)。

## Key Technical Decisions

- **Q2 research 結果**:核實 6 個 dofollow 平台刪除能力 → blogger(`posts().delete`,需從 insert response 抽現被丟棄的 post_id)、ghpages(GitHub `DELETE contents`,file-path handle 可得)= 唯二乾淨可刪;telegraph(匿名頁無 delete)、velog(introspection 關)、livejournal(XML-RPC 可能但 secret 不可撤銷)、medium(API 棄)= 不可/不確定。
- **v1 = evergreen 只讀,不 L3**:據上,re-fetch 對全 cohort 通用且零帳號污染/零刪貼工程;L3 加 API 設計+orphan 清理只對 2 平台划算 → 縮 Phase 2。
- **drift 信號**:**新增 sibling 函式**(如 `inspect_target_anchor`)做目標 href 比對 + SSRF-guarded 抓取,**不改 `verify_link_attributes` 既有 fetch**(後者被 6 個 post-publish caller 正調用,換 HTTP client 會改它們的 timeout/redirect/異常語義)。既有 bug post-mortem 硬教訓:查目標錨點自己的 rel,全頁 `nofollow_detected` 必誤報。
- **drift-confirmed 嚴格門檻**:只當「HTTP 200 + 可解析 body + **marker 在頁上**(證明確是 canary 頁)+ 目標錨點 rel 翻 nofollow / href 消失」才算 drift-confirmed。**marker 在但錨點消失=最強漂移信號,絕不可被吞進 advisory**;讀不到(soft-404/null/ssrf-blocked/auth/限流/marker 缺)→advisory。解決誤報(interstitial)與漏報(真漂移被當 ambiguous)兩面。
- **interstitial-unwrap**:目標 href 比對前先解 redirect-shim(如 juejin `link.juejin.cn?target=`):抽 `target=`/`url=` query 或經 SSRF-opener 跟一跳比對 post-redirect 終點;無法證明「在」不得當成「已消失」(降 advisory)。
- **not-configured 為第一級 verdict**:cohort 動態含未 seed 的平台 → not-configured(非 advisory);未 seed 平台須**響亮列出**(coverage 斷言),別靜默缺席。canary 頁 rot(連 K 輪 advisory)→升級 `canary-stale/needs-reseed` 告警,不留永久 advisory 噪音。
- **advisory 預設,quarantine gate 走 publish_backlinks.py 逐行 loop pre-filter**(~line 140,仿既有 `_check_row_reachability`),不碰 `available()`(env 語義);per-platform opt-in 才 hard-skip。先 advisory 校準再談自動 quarantine。
- **health store = 專屬 `JsonStore`**(`canary-health.json` 0o600),key=registry 平台名;不用 `channel_status_store`(bind-scoped 拒非 bind 平台)。**注**:`JsonStore`/`_LazyStore` 在 repo-root `webui_store/`(import `from webui_store.base import ...`),非 `src/` 下;新 store 在 `src/backlink_publisher/canary/` 須確認可 import root-level 包,或將 JsonStore re-export。config dir 用 `_config_dir()`(channel_status.py 實際用的 accessor)。
- **SSRF**:canary 抓取**復用 `_preflight_fetch` 的 opener**(繼承 per-hop + post-redirect 重檢),非自建 `_make_ssrf_opener`(只檢初始 URL = SSRF bypass)。
- **排程外置**:verb 純 CLI exit-0;cron 由 harness routine 疊加。verb 輸入**config-driven**(registry cohort + `[canary.<platform>]` config),非 stdin JSONL — preflight 範本只有 receipt/verdict helper + exit-0/recon 契約可轉,read_jsonl spine 不轉。

## Open Questions

### Resolved During Planning

- **Q2 刪除能力(origin Resolve-Before-Planning)**:已盤點 → blogger/ghpages 可刪,餘不可/不確定 → v1 evergreen,L3 縮 Phase 2/2 平台。
- **health 存哪**:專屬 `JsonStore` `canary-health.json`,非 `channel_status_store`(bind-scoped 確認會拒)。
- **quarantine 接線點**:CLI pre-dispatch filter(advisory),非 `available()`。
- **抓取/indexability 復用**:`fetch_target`(facts+noindex/x_robots)+ 擴充 `link_attr_verifier`(target-href dofollow)雙路;`link_attr_verifier` 改走 SSRF-guarded fetch。

### Deferred to Implementation

- **最大可容忍偵測延遲上限**(如 ≤48h)— operator 定;cadence × N 反推須滿足之。這是 Success Criteria 可驗證性的前提,須在排程上線前定(非純技術問題,需 operator 輸入)。
- 去抖 N、re-arm M、冷卻窗、cadence 的具體默認值 — 先 advisory 跑真實分布再校準,且與上面延遲上限聯動(origin `[R9/R11][User decision]`)。
- varying marker 的具體生成法(跨維度變化策略)— 實作時跑 `footprint` extractor 驗證每維度不觸 95% 告警。
- v1 ship 時是否有平台 opt-in hard-skip — 決定 Unit 4 re-arm/flap 邏輯有無 v1 消費者(無則純 advisory)。
- Phase 2:blogger post_id / ghpages file-path handle 抽取的精確欄位;delete optional capability 契約形狀;`credential_mutation_lock` 是否須 retrofit 既有 publish 路徑取鎖。
- (config schema `[canary.<platform>]` `{post_url, expected_target, hard_skip}` 已在 Unit 1 定,不再 deferred。)

## High-Level Technical Design

> *以下說明意圖方向供審查,非實作規格;實作 agent 當 context 不照抄。*

```
canary-targets verb (config-driven; 借 preflight 的 receipt/verdict helper + exit-0/recon 契約,非其 read_jsonl spine)
  cohort = [p for p in registered_platforms() if dofollow_status(p) is True]   # R5/R6 非空斷言
  for platform in cohort (jittered inter-platform delay, preflight UA):
      if platform not in config[canary]: verdict = not-configured; continue   # 第一級 verdict, 響亮列出
      facts  = fetch_target(canary_post_url)                  # 復用 _preflight_fetch opener: per-hop+post-redirect SSRF (R4/R12/R1c)
      anchor = inspect_target_anchor(url, target_url=...)     # NEW sibling; interstitial-unwrap 後比對目標 href (R1a/R1b)
      verdict = classify(facts, anchor)                       # 嚴格門檻 (R9)
          ├─ 200 + 可解析 body + marker 在頁 + 目標錨點存在 dofollow + 無 noindex → link-alive
          ├─ 200 + 可解析 body + marker 在頁 + 目標錨點 rel=nofollow / href 確消失   → drift-confirmed
          └─ 其餘(soft404/null/ssrf-blocked/auth/限流/marker 缺/interstitial 無法證)→ advisory(NEVER quarantine)
      health_store.update(platform, verdict)                  # 去抖計數, atomic 0o600 (R8)
      if platform advisory >K 連輪: escalate canary-stale/needs-reseed       # 防 rot 永久噪音
      receipt = build_receipt(platform, verdict, failed_checks, timestamp)   # 非敏感欄位白名單 (R15)
  write_jsonl(receipts); return None  # exit 0, advisory (R10/R17)

publish_backlinks.py 逐行 loop pre-filter (~line 140, 仿 _check_row_reachability) (R10):
  platform = args.platform or row.get("platform")
  if health_store.is_degraded(platform): WARN (advisory default, dedup/cooldown 防疲勞)
  if platform in HARD_SKIP_OPTIN and quarantined: 從 payload 移除 + 明示原因
```

## Implementation Units

```mermaid
graph TB
    U1[Unit 1: canary health store + config schema]
    U2[Unit 2: target-href link inspection + SSRF fix]
    U3[Unit 3: canary-targets CLI verb + 三態 ladder]
    U4[Unit 4: advisory surfacing + opt-in hard-skip pre-filter]
    U5[Unit 5: scheduling 外置 + seeding runbook]
    U6[Unit 6 (Phase 2, deferred): L3 round-trip blogger+ghpages]
    U1 --> U3
    U2 --> U3
    U1 --> U4
    U3 --> U4
    U5 -. seeding 須先於 U3 驗收 .-> U3
    U3 --> U5
    U3 -.deferred.-> U6
```

> **依賴注記:** U5 的「seeding canary 貼」是 operator 動作,**須先於 U3 的真實抓取驗收**(沒 seed 就沒東西可抓);U5 的「排程/cadence 文件」才是 U3 之後。U4 同時讀 U1(health store)與 U3(verdict)。U4 的 gate 在 seeding + 數輪 verb run 清完去抖前是 fail-open inert。

- [ ] **Unit 1: Canary health store + config schema**

**Goal:** 持久化 per-platform canary health(advisory 去抖所需的最小欄位)與 per-platform canary post 配置。

**Requirements:** R8, R15, R16

**Dependencies:** None

**Files:**
- Create: `backlink-publisher/src/backlink_publisher/canary/__init__.py`
- Create: `backlink-publisher/src/backlink_publisher/canary/store.py`
- Modify: `backlink-publisher/config.example.toml`(加 `[canary.<platform>]` schema:`post_url`、`expected_target`、`hard_skip`(bool,默認 false))
- Test: `backlink-publisher/tests/test_canary_store.py`

**Approach:**
- 鏡像 `webui_store/channel_status.py`:`_LazyStore(lambda: JsonStore(_config_dir()/"canary-health.json", default_factory=dict))`,key=registry 平台名。
- **v1 最小欄位**(advisory 預設只需這些):`{status, consecutive_failures, last_ok_at, last_drift_at}`。**`quarantined`、`consecutive_oks`(re-arm 計數)移到 Unit 4** — 與讀它們的 opt-in hard-skip/re-arm machinery 同單元落地,不在閾值校準前先建狀態。
- 讀-改-寫用 `update(fn)`;寫經 `safe_write.atomic_write` 0o600(JsonStore.save 已是)。
- **store 路徑注記**:`JsonStore`/`_LazyStore` 在 repo-root `webui_store/`(import `from webui_store.base import JsonStore, _LazyStore`),**非** `src/` 下;新 `canary/store.py` 在 `src/backlink_publisher/` 須確認可 import root-level `webui_store` 包,否則將 JsonStore re-export 進 `src/`。config dir 用 `config.loader._config_dir()`(channel_status.py 實際用的,honor `BACKLINK_PUBLISHER_CONFIG_DIR`;`_resolve_config_dir()` 是同義 delegate,擇一即可)。
- 並發模型:per-instance 記憶體 Lock + atomic_write 即足(canary-targets 與 publish-backlinks 是**獨立進程**,同進程多 update 不會發生;cross-process 安全靠 atomic_write 的 TOCTOU 保護,非 flock)。
- **不**用 `channel_status_store`(bind-scoped 拒非 {velog,medium,blogger})。

**Patterns to follow:** `webui_store/channel_status.py`、`webui_store/base.py::JsonStore`、`persistence/safe_write.py`。

**Test scenarios:**
- Happy: 新平台首次寫 link-alive → load 回讀一致。
- Edge: 連續 drift → consecutive_failures 累加;link-alive 後歸零(去抖計數正確)。
- Edge: `BACKLINK_PUBLISHER_CONFIG_DIR` 改變 → store 路徑跟隨 re-resolve(用 `monkeypatch.setenv` 非 `del`,`feedback_del_os_environ_poisons_later_tests`)。
- Error: 檔案 0o600 權限;atomic write 中斷不留半檔(模擬)。
- Edge: config `[canary.<platform>]` 解析(post_url/expected_target/hard_skip)round-trip。

**Verification:** health store 可被獨立讀寫、跨進程持久、權限 0o600、env 路徑正確;欄位集僅 advisory 所需。

---

- [ ] **Unit 2: 目標錨點檢測 sibling 函式(SSRF-guarded)**

**Goal:** **新增** 一個目標 href 比對 + SSRF-guarded 抓取的 sibling 函式,**不改** `verify_link_attributes` 既有 fetch(避免衝擊其 6 個 post-publish caller)。

**Requirements:** R1(a)(b), R4, R12, R13

**Dependencies:** None

**Files:**
- Modify: `backlink-publisher/src/backlink_publisher/publishing/adapters/link_attr_verifier.py`(**新增** `inspect_target_anchor(url, target_url, *, opener=...)`,不動既有 `verify_link_attributes` 的 `backlink_publisher.http.get`)
- Test: `backlink-publisher/tests/test_inspect_target_anchor.py`

**Approach:**
- **新增 sibling 函式**(非改既有):`verify_link_attributes(url)` 被 6 個 post-publish caller(dispatcher/velog/medium_browser/medium_brave/http_form_post/medium_api)正調用,換它的 HTTP client 會改它們的 timeout/redirect-cap/異常語義 → 故 canary 走獨立新函式。沿用 `_A_TAG_RE`/`_tag_has_nofollow` 解析邏輯。
- 捕捉完整 `<a ...href=...>`,**先 interstitial-unwrap**(抽 `link.juejin.cn?target=`/`?url=` 類 query,或經 SSRF-opener 跟一跳取 post-redirect 終點)再經 `_util.url.canonicalize_url` 比對期望目標;回 `{target_anchor_found, target_rel, target_is_nofollow, page_readable, marker_present}`,**never-raise + dict-return**。
- **禁用全頁 `nofollow_detected` 作 drift 判據**(既有 bug 教訓);只看目標錨點自己的 rel。
- 抓取**復用 `_preflight_fetch._PREFLIGHT_OPENER`**(繼承 per-hop + post-redirect SSRF 重檢),**非**自建 `_make_ssrf_opener`(只檢初始 URL=bypass);每個 `urlparse` 站點 guard malformed-IPv6。
- UA:沿用既有 preflight 獨立 UA(target 可分開限流)。**誠實限制**:獨立 UA 可被 cloaking,verdict 是漂移信號非真實訪客保證(R13)。

**Patterns to follow:** `link_attr_verifier._A_TAG_RE`/`_tag_has_nofollow`、`_preflight_fetch._PREFLIGHT_OPENER`(直接復用,別新建)、`_util/net_safety._check_url_for_ssrf`、`_util/url.canonicalize_url`。

**Test scenarios:**
- Happy: 頁含目標錨點 dofollow → `target_anchor_found=True, target_is_nofollow=False`。
- Drift: 目標錨點帶 `rel=nofollow` → `target_is_nofollow=True`(即使其他無關錨點 dofollow)。
- Drift: 目標 href 確從頁面消失(頁可讀)→ `target_anchor_found=False`。
- Edge(關鍵反例 1):nav/footer 有 nofollow 錨點但**目標錨點仍 dofollow** → 不得誤報。
- Edge(關鍵反例 2 — interstitial):平台把 href 包成 `link.juejin.cn?target=https%3A%2F%2Fexample.com` → unwrap 後比對成功,**不得誤報 href 消失**。
- Edge: 同 href 多錨點 / 相對 URL / trailing slash / utm 參數 → canonicalize 比對正確。
- Error: 抓取失敗 / 非 HTML / 空 body → never-raise,`page_readable=False`。
- Security: 平台 redirect 到私網/link-local(**含 redirect hop,非僅初始 URL**)→ SSRF 拒絕(`real_ssrf_check` 標記測試,R12)。
- Regression: 既有 `verify_link_attributes` 簽名/fetch 不變(6 caller 不受影響)。

**Verification:** 能隔離「我的 backlink 的 rel 是否變」於全頁噪音 + interstitial 之外;抓取走 SSRF-guard 含 redirect hop;既有 verifier 零回歸。

**Execution note:** 先寫兩個關鍵反例(全頁 nofollow 但目標 dofollow;interstitial-wrapped href)的失敗測試,再實作。

---

- [ ] **Unit 3: `canary-targets` CLI verb + 三態分類**

**Goal:** 只讀 verb:逐 cohort 平台重抓 canary 貼,三態分類,去抖更新 health,JSONL 輸出,exit 0。

**Requirements:** R1, R3, R5, R6, R9, R17

**Dependencies:** Unit 1, Unit 2

**Files:**
- Create: `backlink-publisher/src/backlink_publisher/cli/canary_targets.py`
- Modify: `backlink-publisher/pyproject.toml`(`[project.scripts]` 加 `canary-targets = "backlink_publisher.cli.canary_targets:main"`)
- Test: `backlink-publisher/tests/test_cli_canary_targets.py`

**Requirements:** R1, R3, R5, R6, R9, R15, R17

**Approach:**
- 借 `cli/preflight_targets.py` 的 **receipt/verdict helper + exit-0/recon 契約**(`_failed_checks`/`_classify`/`_build_receipt`/`VERDICTS`/`PipelineLogger`+`recon`/argparse post-parse 校驗/`try/except PipelineError`/`main()` 返 None)。**輸入 config-driven**(registry cohort + `[canary.<platform>]`),**非** preflight 的 `read_jsonl` stdin spine。
- cohort = `registered_platforms()` 過濾 `dofollow_status is True`;**啟動斷言非空否則 fail-loud**(R6)。
- per-platform:`fetch_target`(facts/noindex)+ Unit 2 的 `inspect_target_anchor`;`_classify()` first-match ladder(嚴格門檻 R9):
  - **not-configured**(第一級 verdict):平台不在 `[canary]` config → 標 not-configured + 響亮列出(coverage 斷言),**非** advisory、非 drift。
  - **drift-confirmed**:HTTP 200 + 可解析 body + **marker 在頁** + 目標錨點 rel 翻 nofollow / href 確消失。**marker 在但錨點消失=最強漂移,絕不可吞進 advisory**。
  - **advisory**:其餘讀不到(soft404/null/ssrf-blocked/auth/限流/marker 缺/interstitial 無法證在)→ never quarantine。
- 連 K 輪 advisory(canary 頁 rot)→ 升 `canary-stale/needs-reseed`(別留永久噪音)。
- `_build_receipt()` one-fact-one-key + timestamp(no durability claim);receipt/recon **僅非敏感欄位白名單**(平台/verdict/failed-check 名/時間戳),禁憑證/token/cookie/raw HTML(R15);更新 health store。
- 平台間 jittered delay(複用 `_sleep_with_throttle` 精神),尊重 throttle。
- evergreen 模式:每平台明示 `mode=evergreen`,verdict `link-alive`(非 `healthy`)= 「鏈接存活」非「能發新貼」。

**Patterns to follow:** `cli/preflight_targets.py`(helper 部分)、`feedback_argparse_choices_vs_usage_error_exit_code_clash`、`feedback_content_rejected_error_sibling_pattern`(ambiguous→advisory)、`feedback_probe_then_pivot_when_api_unverifiable`。

**Test scenarios:**
- Happy: cohort 全 link-alive → receipt verdict=link-alive,`main()` 返 None。
- Drift(關鍵正例):200+可解析+**marker 在**+目標錨點 nofollow → drift-confirmed,consecutive_failures++,exit 0。
- 漏報反例(關鍵):200+marker 在+目標錨點 href 確消失 → **drift-confirmed**(不得因「anchor not found」被降 advisory)。
- 誤報反例:soft-404 / marker 缺 → advisory(非 drift)。
- Ambiguous:null / ssrf-blocked / auth-過期 / 限流 → advisory,**不標 quarantine**。
- not-configured:平台無 `[canary]` 條目 → not-configured verdict + coverage 警示,非 drift。
- Rot:同平台連 K 輪 advisory → 升 canary-stale 告警。
- 安全:receipt 不含任何 token/cookie 子串(即使 fetch 帶 auth)(R15)。
- Edge: cohort 為空 → fail-loud(R6)。
- Integration: 跑完一輪 health store 真被更新(patch `fetch_target`+`inspect_target_anchor` at verb 引用 + tmp config dir;`feedback_mock_patch_paths_after_extraction`)。
- Contract: stdout 純 JSONL、stderr 是 recon、exit 0(`assert main(...) is None`)。

**Verification:** cohort 動態正確、drift 嚴格門檻(marker 在才判 drift)、ambiguous/not-configured 不誤 quarantine、receipt 無敏感欄位、永 exit 0。

**Execution note:** 先寫漏報反例(marker 在+anchor 消→drift)、誤報反例(marker 缺→advisory)、cohort 非空 fail-loud 的失敗測試。

---

- [ ] **Unit 4: Advisory surfacing + per-platform opt-in hard-skip**

**Goal:** publish/plan 路徑讀 canary health:degraded 出 WARNING(advisory 預設,帶 dedup/cooldown);僅 opt-in 平台 quarantine 時 hard-skip。本單元亦落地 Unit 1 移出的 `quarantined`/`consecutive_oks` 欄位與 re-arm machinery(與其消費者同處)。

**Requirements:** R10, R11, R15, R16

**Dependencies:** Unit 3(+ Unit 5 seeding 須先發生,gate 才非 inert)

**Files:**
- Modify: `backlink-publisher/src/backlink_publisher/cli/publish_backlinks.py`(逐行 loop **~line 140**,`platform = args.platform or row.get("platform")` 解析後,仿既有 `_check_row_reachability` gate 插 WARN/skip)
- Modify: `backlink-publisher/src/backlink_publisher/cli/_publish_helpers.py`(可選:放可複用的 `is_degraded`/`is_quarantined` helper)
- Modify: `backlink-publisher/src/backlink_publisher/cli/plan_backlinks/core.py`(plan 階段 advisory nudge,參 preflight nudge 先例)
- Modify: `backlink-publisher/src/backlink_publisher/canary/store.py`(加 `quarantined`/`consecutive_oks` 欄位 + re-arm 邏輯)
- Modify: health-dashboard 讀取點(`webui_app/` 對應 route / `/ce:health`,read-side join)
- Test: `backlink-publisher/tests/test_canary_advisory_gate.py`

**Approach:**
- `publish_backlinks.py` 逐行 loop pre-filter 讀 canary health:`is_degraded(platform)` → stderr WARNING(advisory,**不跳過**,dedup/cooldown 防每 run 重複刷);`platform in HARD_SKIP_OPTIN(config hard_skip=true) and quarantined` → 過濾出 payload + stderr 明示「因 canary 漂移已隔離」。
- **drift-confirmed→quarantine** 與**抗 flap re-arm**(R11):連綠 M≥2(或冷卻窗)才解 quarantine;flap(快速振盪)升人工告警。**不碰 `available()`**(env 語義;`feedback_patch_available_when_mocking_chain_publish`)。
- **operator action loop**(寫進 Unit 5 runbook,此處接線):advisory WARNING 的消費者、複查 cadence、決策樹(investigate adapter / re-seed canary / flip 成 opt-in hard-skip);dedup/cooldown 避免 alert fatigue(repo 有 L1/L2/L3 告警疲勞前例)。
- **v1 ship 時是否有平台 opt-in hard-skip?** 若無,re-arm/flap 邏輯無 v1 消費者——在 Approach 明示:hard-skip + re-arm 隨**首個 opt-in 平台**啟用;無 opt-in 則 v1 gate 僅 advisory WARNING。
- health-dashboard/TG read-side join(R16),非寫進 bind store;告警 payload 僅非敏感欄位(R15)。

**Patterns to follow:** `publish_backlinks.py::_check_row_reachability`(逐行 gate 先例)、`preflight_targets`→`plan-backlinks` advisory nudge、`feedback_patch_available_when_mocking_chain_publish`。

**Test scenarios:**
- Happy(advisory 預設):degraded 平台 → WARNING 出 stderr,publish **仍進行**(不跳過)。
- Dedup:同 degraded 平台多 run → WARNING 受 cooldown 不每次刷。
- Opt-in hard-skip:平台 config `hard_skip=true` 且 quarantined → 從 payload 移除 + 明示原因。
- Edge: 連綠 M 達閾值 → quarantine 解除(re-arm);flap(快速振盪)→ 升人工告警 receipt。
- Edge: 平台無 canary health(從未跑過/未 seed)→ 視為 unknown,不阻擋(fail-open advisory)。
- 安全:WARNING/告警 payload 不含憑證/URL query secret(R15)。
- Integration: health store 標 quarantine 後,publish 路徑確實讀到並按授權模式處置(注入 tmp store)。

**Verification:** 預設不靜默停發;opt-in 才硬跳;re-arm 抗 flap;advisory 有 dedup 不疲勞;告警無敏感欄位。

---

- [ ] **Unit 5: 排程外置 + canary 貼 seeding runbook**

**Goal:** 文件化排程(harness Cron/RemoteTrigger 疊加,非 baked-in)、每平台 canary 貼一次性 seeding 步驟、與 operator action loop。

**Requirements:** R14, R17

**Dependencies:** None for seeding 文件本身;**seeding 動作須先於 Unit 3 真實抓取驗收**(沒 seed 就沒東西可抓);cadence 文件部分在 Unit 3 之後。

**Files:**
- Create: `backlink-publisher/docs/runbooks/2026-05-27-canary-targets-operations.md`
- Modify: `backlink-publisher/AGENTS.md`(verb 表 + env 表加 canary)

**Approach:**
- runbook 寫:(1) 每平台 canary 貼**必須經真實 adapter publish 路徑發**(非手貼 UI),否則 canary 驗的是手造 artifact 非 adapter 輸出,publish-path 漂移即使回溯也照不出;(2) marker **跨所有 footprint 維度 varying**(`attr_order/rel/target/preceding_char`+周邊結構),過 `footprint` extractor 斷言每維度 <95%;(3) URL+expected_target 記進 `[canary.<platform>]` config;(4) 排程經 harness Cron routine 觸發 `canary-targets`(參 `reference_phase0_remote_routines`);(5) cadence × N 對齊**明確的最大偵測延遲上限**(待 operator 定,如 ≤48h);(6) **operator action loop**:誰看 advisory、複查 cadence、決策樹(investigate / re-seed / flip opt-in hard-skip);(7) throwaway-account gate(尤其 livejournal secret 不可撤銷)。
- **誠實風險**:平台可能對舊貼/休眠帳號貼施加與新 campaign 貼不同的 link policy → evergreen 信號未必泛化;runbook 註明。
- 不在 verb 內寫 cron。

**Patterns to follow:** Telegraph Phase 0 remote routines、footprint `OVERRIDE.md` break-glass。

**Test scenarios:** Test expectation: none — 純文件/runbook,無行為變更。

**Verification:** operator 能照 runbook 經真實 adapter seed canary 貼並排程;marker 跨維度過 footprint 稽核;有明確 action loop。

---

- [ ] **Unit 6 (Phase 2 — Deferred): L3 真發+清往返(僅 blogger + ghpages)**

**Goal:** 對唯二乾淨可刪平台補刪貼能力,真發拋棄貼→驗→刪,覆蓋發布路徑漂移。

**Requirements:** R2

**Dependencies:** Unit 3(及真實 advisory 分布校準後)

**Files:**
- Modify: `publishing/adapters/blogger_api.py`(從 insert response 抽 post_id 進 `_provider_meta`)
- Modify: `publishing/adapters/ghpages.py`(file-path handle 進 meta + `http_delete` 接線)
- Modify: `publishing/adapters/base.py` / `registry.py`(delete 作 **optional capability**,非強制 ABC method,避免破全 adapter)
- Modify: `cli/canary_targets.py`(L3 模式分支:可刪平台真發+清)
- Test: `tests/test_canary_l3_roundtrip.py`

**Approach:**
- delete 設為**可選能力**(capability flag / optional method),只 blogger+ghpages 實作;其餘維持 evergreen。**`livejournal` 硬排除真發**(secret 不可撤銷)——列為硬不變式,Unit 6 MUST NOT real-publish livejournal。
- L3:真發 varying-marked 拋棄貼→`fetch_target`+`inspect_target_anchor` 驗→刪;刪失敗/無 handle→記 orphan + 告警 + STOP(不靜默判綠 R2);**orphan store 走 `safe_write.atomic_write` 0o600**;orphan 告警只攜不透明 `平台+時間戳+orphan-id`,**非**帳號 handle / 帶憑證 URL(R15);sweep 重試刪除 + 對超時仍 live 的 orphan 告警。
- **throwaway-account gate 為 fail-closed**:config 須有 allowlisted throwaway 帳號才允許真發;未配置 → 不真發;非 throwaway/production-marked 帳號 → 拒絕。
- 憑證觸碰走單一 **file lock(flock,仿 `telegraph_api.py` rotation-under-flock)非 in-process `threading.Lock`**(canary-targets 與 publish-backlinks 是**獨立進程**,thread lock 無法跨進程序列化);**且 publish 路徑也須取同一鎖**(否則 canary 旋轉 vs 進行中 publish 讀的真 race 仍開)。

**Patterns to follow:** `telegraph_api.py` rotation-under-flock;`credential-rotation-tests-cover-bootstrap-race` 的並發測試;`feedback_content_rejected_error_sibling_pattern`。

**Test scenarios:**
- Happy: blogger 真發→驗 dofollow→刪成功,health=link-alive,無 orphan。
- Error: 刪除失敗 → 記 orphan(0o600)+ 告警(僅不透明 ref)+ 不判綠(STOP)。
- Edge: 無 delete handle(post_id 抽取失敗)→ 視為 orphan。
- Invariant: livejournal 永不真發(斷言 Unit 6 路徑拒 livejournal)。
- Gate: 未配置 throwaway 帳號 → 拒絕真發(fail-closed);production-marked 帳號 → 拒絕。
- Integration: canary 與 publish loop 並發觸碰憑證 → flock 跨進程序列化(cross-process lock 測試,或文件化為何 Barrier 足夠)。
- Security: orphan 告警/receipt 無帳號 handle/憑證(R15)。

**Verification:** blogger/ghpages 能完整 L3 且不留 orphan;livejournal 硬排除;throwaway fail-closed;跨進程憑證安全。

## System-Wide Impact

- **Interaction graph:** 新 verb 讀 `registry`(只讀)+ 新 canary store;Unit 4 在 `publish_backlinks.py` 逐行 loop(~line 140)+ `plan_backlinks/core.py` 加讀取點;health-dashboard read-side join。不改 `dispatch()`/`available()` 語義。
- **Error propagation:** verb 永 exit 0(advisory);ambiguous/讀不到/not-configured → advisory 不 quarantine;只「marker 在頁 + 錨點漂移」驅動 drift 計數。Unit 4 fail-open(無 health=不阻擋)。
- **State lifecycle risks:** canary-health.json atomic 0o600;Unit 6 並發 publish loop 觸碰憑證需 **flock 跨進程鎖(且 publish 路徑也取)**;orphan store 0o600;讀 events.db(若用)走 snapshot-copy + 寫延後 commit。
- **API surface parity:** verb 遵循既有 stdout=data/stderr=diag/exit-0 契約(同 preflight-targets/audit-state/footprint),但**輸入 config-driven 非 stdin**。
- **Integration coverage:** patch `fetch_target`+`inspect_target_anchor` 雙路 + 注入 tmp config dir 才能證 health 真被更新(unit mock 不夠)。
- **Unchanged invariants:** `dispatch()`/`available()`/registry register 形狀不變;不新增 `register()` kwarg(故不碰三個 registry-isolation fixtures);`channel_status_store` 不動。**⚠️ 改動的不變式**:Unit 2 **新增** `inspect_target_anchor` sibling,**刻意不改** `verify_link_attributes` 既有 fetch/簽名——保 6 個 post-publish caller(dispatcher/velog/medium_browser/medium_brave/http_form_post/medium_api)零回歸(初稿「換 link_attr_verifier fetch」的寫法會衝擊它們,已撤)。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| 全頁 nofollow flag 誤報 drift(已踩過) | Unit 2 強制目標 href 比對 + 關鍵反例測試 |
| **evergreen v1 對前向發布路徑漂移 blind**(頭號威脅在 v1 多未覆蓋) | 威脅覆蓋矩陣明示;verdict 用 `link-alive` 非 `healthy`;前向覆蓋待 Phase 2(2 平台) |
| interstitial-wrapped href(juejin link.juejin.cn 類)誤報 href 消失 | Unit 2 比對前 interstitial-unwrap;無法證「在」降 advisory 非 drift |
| 真 drift 被當 ambiguous 漏報 | drift-confirmed 嚴格門檻:marker 在頁 + 錨點漂移才算;漏報反例測試鎖定 |
| 「讀不到」誤判成「漂移」→誤鎖 | ladder:ambiguous/not-configured→advisory 永不 quarantine;advisory 預設 |
| canary 頁 rot → 永久 advisory 噪音 | 連 K 輪 advisory 升 canary-stale/needs-reseed |
| 常量 marker 成 footprint cluster key | marker 跨**所有**維度 varying + footprint 稽核每維度 <95%(R14) |
| 新 SSRF fetch 衝擊 6 個 verify_link_attributes caller | Unit 2 **新增 sibling** 不改既有 fetch;regression 測試 |
| SSRF redirect-hop bypass | 復用 `_preflight_fetch` opener(per-hop+post-redirect)非自建;`real_ssrf_check` 測 redirect hop |
| 憑證跨進程 race(Unit 6) | **flock**(非 thread lock)+ publish 路徑也取同鎖 |
| 自動 quarantine 誤鎖最高價平台 | 預設 advisory;hard-skip 僅 opt-in;先校準分布再談自動 |
| livejournal secret 不可撤銷(Unit 6 真發) | **硬排除真發**;throwaway gate fail-closed;v1 不真發 |
| advisory 無人 action → 變死儀表板 | Unit 5 operator action loop + dedup/cooldown 防疲勞 |
| seeding 手貼非經 adapter → 驗錯 artifact | Unit 5 要求經真實 adapter publish 路徑 seed |
| receipt/告警洩漏憑證 | R15 非敏感欄位白名單 + 測試斷言無 token 子串 |
| health 塞 bind-scoped store 被拒 | 專屬 canary store,key=registry 平台名 |

## Documentation / Operational Notes

- Unit 5 runbook:seeding + 排程 + throwaway gate + cadence/延遲。
- `AGENTS.md` verb 表 + env 表更新。
- monolith budget:新 `cli/canary_targets.py` ~200 SLOC,確認未被加進 `monolith_budget.toml` 追蹤集(否則需 rationale)。

## Sources & References

- **Origin document:** `docs/brainstorms/2026-05-27-adapter-contract-canary-requirements.md`
- 範本代碼:`cli/preflight_targets.py`、`content/_preflight_fetch.py`、`publishing/adapters/link_attr_verifier.py`、`webui_store/{base,channel_status}.py`、`publishing/registry.py`
- Learnings:`docs/solutions/integration-issues/dofollow-canary-verdict-dropped-at-publish-output-seam-2026-05-25.md`、`docs/runbooks/2026-05-25-dofollow-canary-closeout.md`、`docs/solutions/logic-errors/projector-silent-drop-status-vocabulary-drift-2026-05-26.md`、`docs/brainstorms/2026-05-26-destination-page-preflight-requirements.md`、`docs/plans/2026-05-18-007-feat-footprint-regression-gate-plan.md`
- 相關 PR:#258(preflight-targets)、#238(audit-state)、#222(events projector)
