---
title: "feat: 外鏈發佈鏈路端到端優化 — 機制 × 性能"
type: feat
status: active
date: 2026-06-10
origin: docs/optimization-audit-2026-06.md
claims:
  paths:
    - src/backlink_publisher/cli/plan_backlinks/core.py
    - src/backlink_publisher/cli/plan_backlinks/_engine.py
    - src/backlink_publisher/cli/plan_backlinks/_templates.py
    - src/backlink_publisher/cli/validate_backlinks.py
    - src/backlink_publisher/cli/quality_gate.py
    - src/backlink_publisher/cli/_publish_throttle.py
    - src/backlink_publisher/cli/_publish_helpers.py
    - src/backlink_publisher/publishing/registry.py
    - src/backlink_publisher/publishing/reliability/policy.py
    - src/backlink_publisher/publishing/banner_dispatcher.py
    - src/backlink_publisher/cli/recheck_backlinks.py
    - src/backlink_publisher/cli/replan_dead.py
    - src/backlink_publisher/cli/remediation_queue.py
    - src/backlink_publisher/events/kinds.py
    - tests/test_cli_timing_regression.py
    - pyproject.toml
  shas:
    - a04949f
---

# feat: 外鏈發佈鏈路端到端優化 — 機制 × 性能

## Overview

backlink-publisher 的核心管線 `seed → plan → validate → quality-gate → publish → recheck → remediation` 已運作良好，但 `docs/optimization-audit-2026-06.md` 的全系統審計指出 **WebUI 之外、CLI/引擎/Adapter 鏈路同樣存在可觀的優化空間**：plan engine 缺純函數契約註解、validate 的 I/O 預算無上限、publish 為單 row 序列化、`recheck` 與 `remediation` 閉環缺觀測事件、Adapter 錯誤分類不一致。本企劃 **依序** 對每一個鏈路節點分 **機制（M*）** 與 **性能（P*）** 兩個維度提出優化，全部對齊既有治理：

- `docs/architecture/deterministic-planning-principle.md` — plan = 純函數、publish = 非確定
- `AGENTS.md` — `monolith_budget.toml`、`plan-claims-gate`、`bugfix discipline`、管線 `stdout=JSONL, stderr=diagnostics, exit code 0-6`
- `optimization-audit-2026-06.md` — 7 個維度已掃描，本企劃補上 D2/D3/D4/D7 中**CLI 與 adapter 鏈路層**的可執行條目

### 鏈路地圖

```
seed.jsonl
  └─ plan-backlinks         (純函數 kernel)
       └─ validate-backlinks (純函數 engine + 可選 URL probe)
            └─ quality-gate   (advisory, 阻塞級檢查)
                 └─ publish-backlinks
                      ├─ reliability.policy.publish_with_policy  (health gate / circuit / 觀測)
                      ├─ registry.dispatch → register("platform", *adapters, dofollow=…)
                      │     ├─ banner_dispatcher.apply
                      │     └─ adapter.publish
                      ├─ canary forward-path verdict (advisory)
                      └─ events.db  (link.published, banner.embedded…)
  └─ recheck-backlinks      (advisory)
       └─ replan-dead  →  plan-backlinks   (迴路閉合)
  └─ remediation-queue      (operator ack/resolve/snooze)
```

### 為何「依序」

每個環節的觀測事件（`schema.*`、`quality.*`、`publish.*`、`link.rechecked`、`remediation.*`）是**下個環節**的輸入；如果亂序（先做 P4-1 併發但 M3-1 觀測還沒出來）會失去 baseline。本企劃刻意按資料流方向排序：先固化 plan → validate 的純函數契約，再補 publish 的併發與觀測，最後閉合 recheck → replan → remediation。

## Requirements

> 編號慣例：R{Unit}{序}。每條都對應後文 Implementation 的某個 Unit 子項。

### Unit 1 — plan-backlinks

- **R1.1** plan kernel 補上「This module is PURE compute」docstring 對齊 `validate/engine.py` 與 `ledger.aggregate.build_ledger` 範式
- **R1.2** 統一 `plan-gap` 與 `plan-backlinks` 對 experimental / retired 平台的 fan-out 規則
- **R1.3** 為 `image_gen` 與 `llm` 加 per-run budget 硬上限
- **R1.4** 輸出 row 攜帶 `_schema_version`（"plan-2026-06"），下游 forward-compat
- **R1.5** `_engine.build_payload` 拆為 row 級 + 集合級兩層；對 `cell_id` 採 `multiprocessing.Pool` partition
- **R1.6** `_templates.py` 對 Jinja `Environment` 與 template 物件加 LRU cache
- **R1.7** `content.fetch` 提供 async wrapper，plan 階段 `asyncio.gather` 多 row
- **R1.8** prompt 模板改為 module-level 唯讀常量，dataclass 加 `__slots__`

### Unit 2 — validate-backlinks

- **R2.1** URL probe 加 `--max-probes N` / `--probe-timeout S` / `--probe-concurrency M` 預算；超限 emit `validate.truncated`
- **R2.2** 對未知 `_schema_version` 採 warn-pass（emit `schema.forward_compat_skipped`）
- **R2.3** `_validate_payload.py` 與 `validate/engine.py` 明確分層（I/O 殼 / 純函數），CLI 邏輯遷入 `validate_shell.py`
- **R2.4** `validate` 結束時 emit `validate.outcome` event 供 `quality-gate` 消費
- **R2.5** JSON Schema 用 `Draft202012Validator.check_schema` 預熱；`iter_errors` 流式 + early-exit
- **R2.6** URL probe 走 `ThreadPoolExecutor`，預設 `--probe-concurrency 8`
- **R2.7** process 內 `URL → ProbeResult` LRU cache
- **R2.8** anchor 重複度改用 `collections.Counter`

### Unit 3 — quality-gate

- **R3.1** LLM scoring 每次 emit `quality.llm_scored` event（latency, score, model_id）
- **R3.2** quality-gate 改為純下游消費者；`--quality-gate-after-plan` 觸發在 plan 收尾
- **R3.3** blocked rows 同步 emit `quality.blocked` event（reason enum）
- **R3.4** 對最近 N 天被 `replan-dead` 標記的 seed 加 TTL 去重
- **R3.5** LLM 評分採 batch API（一次 5 rows）
- **R3.6** 內容相似度改用 MinHash / shingles+jaccard
- **R3.7** events.db `(event_type, ts)` 複合索引 + `ANALYZE`

### Unit 4 — publish-backlinks

- **R4.1** reliability policy 改為 opt-OUT（預設啟用，`=0` 才關）；對齊 PR #279 observe→enforce
- **R4.2** 區分「平台問題」vs「payload 問題」對 circuit 的不同處理
- **R4.3** `_publish_throttle.py` 改為 adaptive（觀測最近 N 次失敗率 > 30% → band 1.5×）
- **R4.4** `AuthExpiredError` 翻 status 後加 `retry_after` hint 防止 flapping
- **R4.5** 抽 `AdapterError` 統一基類（`AuthError / NetworkError / ContentError / RateLimitError`），adapter 必填 `error_map` classmethod
- **R4.6** Banner fallback 引入 `banner.policy` 設定（per-platform `prefer: source_url / platform / skip`），`register()` 增 `banner_fallback` 覆寫
- **R4.7** 文件化 forward-path canary 與 evergreen canary disjoint key（`_publish_path` vs `canary`），contract test 防止 key 合併
- **R4.8** 引入 row 級 `asyncio.Semaphore(M)`，`M` 從 `policy.throttle_band` 推導
- **R4.9** `register()` 改 lazy resolve（`functools.cache`）
- **R4.10** events.db batch 寫入 250ms flush window
- **R4.11** stateless API adapter module-level cache，保留 `config` 變動 invalidate 鉤子
- **R4.12** `banner_dispatcher.apply` 用 `httpx.Limits` keep-alive
- **R4.13** throttle sleep 改 `await asyncio.sleep()` / `tenacity.AsyncRetrying`

### Unit 5 — recheck-backlinks

- **R5.1** 新增 `--probe=auto` 模式（env `BACKLINK_PUBLISHER_RECHECK_ALLOW_NETWORK=1` 才觸發）
- **R5.2** probe UA ≠ publish UA，CI 驗證
- **R5.3** `link.rechecked` event 結構加 schema（`recheck-2026-06`）
- **R5.4** `replan-dead` 為 recheck event 純函數 reader，無 network，exit 0
- **R5.5** `httpx.AsyncClient` + `asyncio.Semaphore(8)`
- **R5.6** `(target_url, last_check_hash) → result` 做 `cachetools.TTLCache`
- **R5.7** `read_events(event_type, since, until, batch=500)` 流式 API

### Unit 6 — remediation 閉環

- **R6.1** ack/resolve/snooze 全動作 emit `remediation.*` event
- **R6.2** 與 `channel-scorecard` / `cull-channels` 分層定義
- **R6.3** `/ce:health` 新增「Open remediations」card
- **R6.4** `replan-dead` 產出 seed row 寫 `parent_remediation_id`
- **R6.5** `remediation.event` 索引，pagination cursor 走 `ts, id`
- **R6.6** `snooze` 改 SQLite `JULIANDAY()` in-place 更新

### Unit 7 — 橫切

- **R7.1** `cli/_helpers.py::make_parser()` 工廠；200+ 行重複 → 一處
- **R7.2** `idempotency/store.py` 拆 `dedup / projectors / state_machine` 三檔
- **R7.3** 統一 `/api/health` 端點（adapter status、events.db 寫入延遲、circuit state）
- **R7.4** 引入 Vitest 覆蓋 `lib/` + `esc()`（既有 `node` check 升級）
- **R7.5** 29 處 `type:ignore` 全量補 `# reason:` 註解
- **R7.6** 243 處 `except Exception` 收窄 30% 為具體類型
- **R7.7** `AdapterError` 統一基類（與 R4.5 同一實作，從此處觸發）

## Non-Goals

- ❌ **不修改 exit code contract**（0-6 表）— 既有 `test_cli_exit_code_contract.py` 鎖定
- ❌ **不自動退役渠道** — 渠道 retire 維持手動 / cull-channels advisory
- ❌ **不修改現有 CLI 介面**（plan/validate/publish 旗標集合）— 全部以新旗標加入
- ❌ **不引入新的非確定依賴到 plan engine**（LLM、image-gen 仍為外部輸入，呼叫介面不變）
- ❌ **不啟動 channel-scorecard GA4/GSC/AI 軸** — 維持 `inert:not-landed`（Wave-0 DESCOPE）
- ❌ **不啟用 P4-1 publish 併發為預設** — 觀察期內 opt-in 旗標
- ❌ **不建立新的 WebUI 框架**（維持零建置原生 ESM）

## Implementation

> 格式：每個條目採「**現況 → 最小安全改動 → 觀測事件 → 風險/緩解**」四段式。所有改動符合 `AGENTS.md` bugfix discipline（reproduce → identify root cause → classify → smallest safe fix → evidence）。

---

### Unit 1 — plan-backlinks

#### M1-1 plan kernel 純函數契約註解
- **現況**：`src/backlink_publisher/cli/plan_backlinks/_engine.py` 缺乏「This module is PURE compute」docstring，與 `validate/engine.py` 範式不一致
- **改動**：補上 docstring，明列禁止 `print / set_log_level / SystemExit / stdin / stdout / events.emit`
- **觀測**：無（文件化契約）
- **風險**：低；如後續 reviewer 誤判 engine 為可 I/O，新增 docstring 後即對齊

#### M1-2 plan-gap / plan-backlinks fan-out 一致性
- **現況**：`plan-gap` 對 `active_platforms() | active+experimental` 處理未統一
- **改動**：抽 `cli/_publish_helpers.py::resolve_target_platforms(include_experimental: bool) -> frozenset[str]`，兩 CLI 共用
- **觀測**：emit `plan.fanout_resolved` event
- **風險**：低；改動局部，未破既有 `test_cli_plan_gap.py`

#### M1-3 LLM / image-gen 預算硬上限
- **現況**：`config.image_gen` 與 `config.llm` 無 per-run 上限
- **改動**：`Config` 加 `image_gen.budget_per_run: int` 與 `llm.budget_per_run: int`；超限 `quality-gate`-style downgrade（不生圖，改用現成 banner；LLM 改啟發式 anchor）
- **觀測**：`plan.budget_downgraded` event（reason: image_gen / llm）
- **風險**：中；需確保降級路徑走既有 fallback，避免新分支

#### M1-4 _schema_version 標記
- **現況**：plan 輸出 row 無版本標記，下游對 schema 變更無 forward-compat 鉤子
- **改動**：在 `core.py` 注入 `_schema_version: "plan-2026-06"`；`validate_payload.py` 對未知版本 warn-pass
- **觀測**：`schema.forward_compat_skipped` event
- **風險**：低；行為向後相容

#### P1-1 row 級 + 集合級分層 + 平行化
- **現況**：`build_payload` 為單一函式，無 row 級切面
- **改動**：
  ```python
  def build_one(seed: SeedRow, config: Config) -> Iterator[OutputRow]: ...
  def build_many(seeds: Iterable[SeedRow], config: Config) -> List[OutputRow]:
      with mp.Pool() as pool:
          return list(pool.imap_unordered(partial(build_one, config=config), seeds, chunksize=64))
  ```
  `cell_id` 為 partition key（同 cell 必走同 worker，保 dedup 局部性）
- **觀測**：`plan.parallel_partition` event（worker_id, cell_id, duration_ms）
- **風險**：中；`mp.Pool` 對 pickle 友善性敏感，需將 `build_one` 限制為 top-level 函式 + 顯式傳參

#### P1-2 Jinja 模板 LRU cache
- **現況**：`_templates.py` 每次重新編譯
- **改動**：`@functools.lru_cache(maxsize=64)` 包 `get_template(name)`；hash key = `(name, config.template_version)`
- **觀測**：`templates.cache_hit / miss` event
- **風險**：低

#### P1-3 content.fetch async wrapper
- **現況**：`content.fetch` 為同步
- **改動**：新增 `content.fetch_async(session, url) -> str`；`build_one` 內 `asyncio.gather(*[fetch_async(s, u) for u in urls])`；保留同步版本供 CLI shell
- **觀測**：`content.fetch.async_duration_ms` event
- **風險**：中；`asyncio.run` 與既有 `conftest` socket-blocking fixture 互動需測試矩陣覆蓋

#### P1-8 prompt 模板 / dataclass 瘦身
- **現況**：`_work_themed.py` 與 `_zh_short.py` 重覆載入 LLM prompt 模板；dataclass 無 `__slots__`
- **改動**：模板提為 `templates/prompts/*.md` 模組級常量；dataclass 加 `__slots__ = (...)`；hot-path attribute lookup 改 `MappingProxyType` 唯讀
- **觀測**：無
- **風險**：低；行為等價

---

### Unit 2 — validate-backlinks

#### M2-1 URL probe 預算
- **現況**：probe 預設 sequential，無上限 — 大 seed 集易 run-away
- **改動**：CLI 旗標 `--max-probes N`（預設 500）、`--probe-timeout S`（預設 5）、`--probe-concurrency M`（預設 8）；達上限 emit `validate.truncated`（reason: `probe_budget_exhausted`）
- **觀測**：`validate.truncated` event（reached, requested）
- **風險**：低；既有 `test_validate_payload_*` 不應觸及新旗標

#### M2-2 schema forward-compat warn-pass
- **現況**：未知 `_schema_version` 預設 reject
- **改動**：改 warn-pass；emit `schema.forward_compat_skipped` 攜 `(known_versions, encountered_version)`
- **觀測**：`schema.forward_compat_skipped`
- **風險**：低

#### M2-3 validate shell / engine 分層
- **現況**：`_validate_payload.py` 與 `validate/engine.py` 職責混雜
- **改動**：拆 `validate/engine.py`（已純函數）+ `validate/shell.py`（I/O、stdin/stdout、events）；CLI 入口只呼 shell
- **觀測**：無
- **風險**：中；需保持 `test_validate_engine.py` 與 `test_cli_validate_backlinks.py` 雙向覆蓋

#### M2-4 validate.outcome 事件供 quality-gate consume
- **現況**：`quality-gate` 重做部分 schema 校驗
- **改動**：`validate` 收尾 emit `validate.outcome`（per row: pass/blocked + reason）；`quality-gate` 讀 events.db 派生
- **觀測**：`validate.outcome`
- **風險**：低

#### P2-1 JSON Schema 預熱 + early-exit
- **現況**：每 row 重新建立 validator
- **改動**：module-level `_VALIDATOR = Draft202012Validator(SCHEMA)`；逐 row `for err in _VALIDATOR.iter_errors(row): yield err`；呼叫方可選 `--fail-fast` 首個 error 即停
- **觀測**：`validate.schema_first_error` event（fail-fast 模式）
- **風險**：低

#### P2-2 ThreadPoolExecutor 探測
- **現況**：sequential probe
- **改動**：`with ThreadPoolExecutor(max_workers=M) as ex: futures = [ex.submit(probe, u) for u in urls]`
- **觀測**：`validate.probe_concurrency` event（active_workers）
- **風險**：中；需 mock-friendly 介面（行為可單元測試）

#### P2-3 URL → ProbeResult LRU cache
- **現況**：同 URL 在同進程內重複探測
- **改動**：`@lru_cache(maxsize=4096)` 包 `_probe_cached(url, ts_bucket)`，`ts_bucket` = 當日日期字串，TTL = process lifetime
- **觀測**：`validate.probe_cache_hit / miss` event
- **風險**：低

#### P2-4 anchor 計算 Counter
- **現況**：`list.count` O(n²)
- **改動**：`Counter(anchors).most_common()`
- **觀測**：無
- **風險**：低

---

### Unit 3 — quality-gate

#### M3-1 LLM 評分觀測
- **現況**：`--quality-llm` 評分無事件軌跡
- **改動**：每次 emit `quality.llm_scored`（latency_ms, score, model_id, prompt_tokens, completion_tokens）
- **觀測**：`quality.llm_scored`
- **風險**：低

#### M3-2 quality-gate 純下游化
- **現況**：quality-gate 與 plan 雙向耦合（plan 收尾可能直接呼叫 quality-gate）
- **改動**：quality-gate 改純 stdin → stdout；plan 收尾僅在 `--quality-gate-after-plan` 旗標下 spawn subprocess
- **觀測**：`plan.post_quality_gate` event
- **風險**：低

#### M3-3 blocked rows 事件化
- **現況**：blocked 僅 stderr
- **改動**：emit `quality.blocked`（reason_enum: anchor_density / low_uniqueness / low_llm_score / budget_exceeded）
- **觀測**：`quality.blocked`
- **風險**：低

#### M3-4 replan-dead 互鎖
- **現況**：replan-dead 產出的 seed 可能立刻被 quality-gate 阻擋、再回送 replan
- **改動**：`quality-gate` 對 `parent_remediation_id` 標記的 seed 設 TTL（預設 24h）
- **觀測**：`quality.skip_replan_ttl` event
- **風險**：低

#### P3-5 LLM batch API
- **現況**：逐 row 同步呼叫 LLM
- **改動**：provider 加 `score_batch(rows: list[dict]) -> list[int]`；`quality-gate` 累積 5 rows 後送一次
- **觀測**：`quality.llm_batch` event（batch_size, total_tokens）
- **風險**：中；provider 介面需擴展

#### P3-6 內容相似度 MinHash
- **現況**：`difflib.SequenceMatcher` O(n²)
- **改動**：`datasketch.MinHash` 或 `shingles+jaccard` 近似；threshold 0.70 對齊
- **觀測**：`validate.similarity_minhash` event（collision_rate）
- **風險**：中；需新增 optional dep（`datasketch`）並以 lazy import 包裹

#### P3-7 events.db 索引
- **現況**：`SELECT ... WHERE event_type=? AND ts BETWEEN ? AND ?` 全表掃描
- **改動**：`CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, ts)`；啟動時 `ANALYZE`
- **觀測**：無
- **風險**：低；遷移腳本冪等

---

### Unit 4 — publish-backlinks

#### M4-1 reliability policy opt-OUT
- **現況**：`BACKLINK_PUBLISHER_RELIABILITY_POLICY_ENABLED=1` 預設 opt-in
- **改動**：翻轉預設值；env `=0` 才關；同步更新 `tests/test_publish_reliability_policy.py`
- **觀測**：`publish.policy_toggled` event（enabled, env_value）
- **風險**：中；需先在 production 跑 1 個 sprint 觀察誤觸發率（PR #279 observe→enforce pattern）

#### M4-2 平台 vs payload 問題分流
- **現況**：所有 `ExternalServiceError` 一視同仁 → circuit 條件不一
- **改動**：在 `policy.py` 引入 `classify_external_error(exc) -> {"platform": bool, "payload": bool}`；5xx/429/timeout 屬 platform（circuit half-open），422/415/4xx-other 屬 payload（不 trip，僅記錄）
- **觀測**：`publish.circuit_classified` event（class, reason）
- **風險**：中；需對每個 adapter 跑 calibration sample

#### M4-3 adaptive throttle
- **現況**：固定 band `[min, max]`
- **改動**：維護 ring buffer of last N=20 outcomes；失敗率 > 30% → band *= 1.5（上限 600s）；恢復後逐次衰減回原 band
- **觀測**：`publish.throttle_adapted` event（old_band, new_band, fail_rate）
- **風險**：中；ramp-up 期間可能誤觸發

#### M4-4 retry_after hint
- **現況**：`AuthExpiredError` 翻 status 後無冷卻
- **改動**：`channel_status` 寫 `last_attempt_at`；下次同 channel 觸發時 backoff = max(0, 60 - elapsed)；emit `publish.retry_after` event
- **觀測**：`publish.retry_after`
- **風險**：低

#### M4-5 AdapterError 統一基類
- **現況**：`DependencyError / ExternalServiceError / AuthExpiredError` 混用
- **改動**：
  ```python
  class AdapterError(Exception): ...
  class AuthError(AdapterError): ...
  class NetworkError(AdapterError): ...
  class ContentError(AdapterError): ...
  class RateLimitError(AdapterError): ...
  ```
  各 adapter 實作 `@classmethod error_map(cls) -> dict[int | str, type[AdapterError]]`
- **觀測**：所有 `publish.*` event 攜帶 `error_class`
- **風險**：高；需保持向後相容（既有 `AuthExpiredError` 改為 `AuthError` 的 subclass；既有測試的 `pytest.raises(AuthExpiredError)` 仍 work）

#### M4-6 banner.policy
- **現況**：adapter 缺 `embed_banner` 走 source_url fallback，無 per-platform 設定
- **改動**：`register()` 增 `banner_fallback: Literal["source_url", "platform", "skip"]`；`banner_dispatcher.apply` 讀此值
- **觀測**：`banner.policy_resolved` event
- **風險**：低

#### M4-7 forward-path canary contract test
- **現況**：`canary-health.json` 內 `_publish_path` 與 `canary` 鍵結構不同，但無測試鎖定 disjoint
- **改動**：新增 `tests/test_canary_health_key_disjoint.py` 驗證 `set(canary-health.json.keys())` 中 `_publish_path` 與 `canary` 永不重疊
- **觀測**：無
- **風險**：低

#### P4-8 row 級 asyncio.Semaphore
- **現況**：publish 單 row 序列化
- **改動**：
  ```python
  sem = asyncio.Semaphore(M)  # M 從 policy.throttle_band 推導（max-min 之半）
  async def publish_row(row, sem): async with sem: await dispatch_async(...)
  await asyncio.gather(*[publish_row(r, sem) for r in rows])
  ```
  預設 opt-in via `--publish-concurrency N`；env `BACKLINK_PUBLISHER_PUBLISH_CONCURRENCY` 覆寫
- **觀測**：`publish.concurrency_observed` event（active, queued, throttle_band）
- **風險**：高；可能踩到 platform anti-bot；觀察期預設 1

#### P4-9 register() lazy resolve
- **現況**：`adapters/__init__.py` import 時同步執行所有 `register()`
- **改動**：`@functools.cache` 包 `register()`；`_REGISTRY` 改用 `__getattr__` lazy；WebUI 啟動時間 -200ms
- **觀測**：`publish.registry_lazy_resolved` event（cold/warm start）
- **風險**：低；既有 `test_adapter_dofollow_gate.py` 仍覆蓋

#### P4-10 events.db batch flush
- **現況**：每次 emit 即 fsync
- **改動**：`asyncio.Queue(maxsize=1000)` + 背景 task 每 250ms flush；單筆可選 `--sync-write` 立即 fsync（error/audit event）
- **觀測**：`events.flush_window` event（window_ms, rows_per_window）
- **風險**：中；crash 時丟失 ≤250ms 事件；既有測試不依賴 event 順序即可通過

#### P4-11 stateless adapter cache
- **現況**：每次 dispatch 都 instantiate
- **改動**：`_adapter_factory(name) -> Publisher`；`@functools.cache` 包 factory；`Config` 變動經 `__hash__` 失效
- **觀測**：`publish.adapter_cached` event
- **風險**：低

#### P4-12 banner HTTP connection pool
- **現況**：每 upload 開新 connection
- **改動**：`httpx.AsyncClient(limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))` module-level
- **觀測**：`banner.upload_keepalive_reused` event
- **風險**：低

#### P4-13 throttle sleep async
- **現況**：`time.sleep` 阻塞事件循環
- **改動**：`await asyncio.sleep(seconds)` 或 `tenacity.AsyncRetrying`
- **觀測**：無
- **風險**：低

---

### Unit 5 — recheck-backlinks

#### M5-1 --probe=auto
- **現況**：零網路預設對運維不直觀
- **改動**：新增 `--probe=auto`；env `BACKLINK_PUBLISHER_RECHECK_ALLOW_NETWORK=1` 才走真實 probe
- **觀測**：`recheck.probe_mode` event（auto / on / off）
- **風險**：低

#### M5-2 probe UA ≠ publish UA
- **現況**：runbook 警告但無 code guard
- **改動**：`recheck_backlinks.py` 從 `config.recheck.user_agent` 取 UA；publish 從 `config.publish.user_agent` 取；CI 驗證兩者差異
- **觀測**：`recheck.ua_resolved` event
- **風險**：低

#### M5-3 rechecked event schema
- **現況**：`link.rechecked` 結構無顯式 schema
- **改動**：emit row 攜 `schema: "recheck-2026-06"`；`events/schema.py` 註冊 `RECHECK_RESULT_SCHEMA`
- **觀測**：`recheck.schema_forward_compat_skipped`
- **風險**：低

#### M5-4 replan-dead 純函數化
- **現況**：`replan-dead` 從 events.db 讀，可能被誤導為需要 network
- **改動**：文件化 + contract test：replan-dead 不引入 socket import；exit 0
- **觀測**：無
- **風險**：低

#### P5-5 AsyncClient + Semaphore
- **現況**：sequential probe
- **改動**：`async with httpx.AsyncClient() as client: await asyncio.gather(*[probe(c, u, sem) for u in urls])`，sem=8
- **觀測**：`recheck.probe_duration_ms` event
- **風險**：中

#### P5-6 TTL cache
- **改動**：`cachetools.TTLCache(maxsize=2048, ttl=recheck_interval)`；key = `(target_url, last_check_hash)`
- **觀測**：`recheck.cache_hit / miss` event
- **風險**：低

#### P5-7 read_events 串流
- **改動**：`events.read_events(event_type, since, until, batch=500) -> Iterator[dict]`
- **觀測**：無
- **風險**：低

---

### Unit 6 — remediation 閉環

#### M6-1 audit trail
- **現況**：ack/resolve/snooze 不入 events.db
- **改動**：三動作各 emit `remediation.ack / resolve / snooze`（攜 operator、reason、snooze_until）
- **觀測**：`remediation.*` 三種
- **風險**：低

#### M6-2 與 scorecard / cull-channels 分層
- **現況**：三者職責模糊
- **改動**：文件化三層 — `channel-scorecard`（平台級 keep/prune 信號向量）、`cull-channels`（全集合 advisory 退役建議）、`remediation-queue`（單條 backlink 級 operator action）；各自管轄不同的 events 與 exit code
- **觀測**：無
- **風險**：低

#### M6-3 /ce:health dashboard card
- **改動**：`webui_app/templates/health.html` 新增「Open remediations」card，聚合 by platform / by reason
- **觀測**：無
- **風險**：低

#### M6-4 parent_remediation_id 串接
- **改動**：`replan_dead.py` 產出 seed row 帶 `parent_remediation_id`；`quality-gate` 與後續 events 沿用
- **觀測**：`replan.parent_remediation_id_set` event
- **風險**：低

#### P6-5 remediation 事件索引
- **改動**：`CREATE INDEX IF NOT EXISTS idx_remediation_ts ON remediation_event(ts, id)`
- **觀測**：無
- **風險**：低

#### P6-6 snooze SQLite in-place
- **改動**：`UPDATE remediation_event SET snooze_until = datetime('now', '+N days') WHERE id = ?`；無 Python 端 roundtrip
- **觀測**：無
- **風險**：低

---

### Unit 7 — 橫切

#### R7-1 argparse 工廠
- **改動**：`cli/_helpers.py::make_parser(verb: str, **overrides) -> ArgumentParser`；集中 `--verbose / --dry-run / --json / --config-dir / --include-experimental`；27 個 CLI 改 import factory
- **觀測**：無
- **風險**：低；既有 CLI 旗標行為不變

#### R7-2 idempotency/store 拆分
- **改動**：拆 `idempotency/dedup.py`（dedup + dedup_gate）、`idempotency/projectors.py`（projector dispatch）、`idempotency/state_machine.py`；`store.py` 留 facade
- **觀測**：無
- **風險**：中；既有 import path 保留 facade re-export

#### R7-3 /api/health
- **改動**：`webui_app/routes/health.py` 新增 `/api/health`；回傳 `{adapters: {name: {status, last_publish, last_error}}, events_db: {write_latency_p99, last_flush_at}, circuits: {platform: open/closed}}`
- **觀測**：無
- **風險**：低

#### R7-4 Vitest
- **改動**：`vitest.config.js`（含 `static/js/`）；`tests/js/lib_api.test.js`、`tests/js/lib_dom.test.js`、`tests/js/lib_profiles.test.js`；`package.json` 加 `vitest` 到 devDeps
- **觀測**：`tests/js/` 報告
- **風險**：低

#### R7-5 type:ignore 補註解
- **改動**：全 29 處 `type: ignore` 改 `type: ignore[code]  # reason: …`；`scripts/audit_type_ignore.py` 守護未加註解者
- **觀測**：CI 步驟
- **風險**：低

#### R7-6 except 收窄
- **改動**：network / config_loader / events 三類優先收窄；目標 30%（73 處）
- **觀測**：`pytest -k except_narrowed` 報告
- **風險**：中；需逐處驗證 upstream exception chain

#### R7-7 AdapterError 基類
- 見 R4-5（同一實作從此處觸發，避免 Unit 4 與 Unit 7 雙重 PR）

---

## Files Modified / Created

### 新建

| 路徑 | 用途 | SLOC 預估 |
|---|---|---|
| `src/backlink_publisher/_util/errors_adapter.py` | `AdapterError` 基類家族 | ~80 |
| `src/backlink_publisher/validate/shell.py` | validate I/O 殼層 | ~120 |
| `src/backlink_publisher/idempotency/dedup.py` | dedup 抽出 | ~150 |
| `src/backlink_publisher/idempotency/projectors.py` | projector dispatch | ~180 |
| `src/backlink_publisher/idempotency/state_machine.py` | state machine | ~140 |
| `webui_app/routes/health_api.py` | `/api/health` | ~90 |
| `webui_app/templates/_health_open_remediations.html` | dashboard card partial | ~50 |
| `static/js/lib/banner_policy.js` | banner fallback policy | ~60 |
| `tests/test_canary_health_key_disjoint.py` | forward-path / evergreen disjoint | ~40 |
| `tests/test_publish_reliability_policy_optout.py` | opt-OUT 翻轉守護 | ~60 |
| `tests/test_adapter_error_taxonomy.py` | `error_map` classmethod 必填 | ~80 |
| `tests/test_validate_shell_engine_separation.py` | 分層守護 | ~70 |
| `tests/test_plan_engine_purity_docstring.py` | 純函數契約守護 | ~30 |
| `tests/test_quality_gate_event_emission.py` | blocked / llm_scored 事件 | ~60 |
| `tests/test_remediation_audit_trail.py` | ack/resolve/snooze 事件 | ~50 |
| `vitest.config.js` | Vitest 設定 | ~30 |
| `tests/js/lib_api.test.js` | api.js 測試 | ~80 |
| `tests/js/lib_dom.test.js` | dom.js 測試 | ~70 |
| `tests/js/lib_profiles.test.js` | profiles.js 測試 | ~80 |
| `scripts/audit_type_ignore.py` | type:ignore 註解守護 | ~40 |

### 修改

| 路徑 | 改動 |
|---|---|
| `src/backlink_publisher/cli/plan_backlinks/_engine.py` | M1-1 docstring、P1-1 row/集合分層、P1-8 slots |
| `src/backlink_publisher/cli/plan_backlinks/_templates.py` | P1-2 LRU cache |
| `src/backlink_publisher/cli/plan_backlinks/core.py` | M1-2 fan-out resolver、M1-4 schema_version、M1-3 budget |
| `src/backlink_publisher/cli/validate_backlinks.py` | M2-1 budget、M2-2 forward-compat、M2-4 outcome event |
| `src/backlink_publisher/cli/quality_gate.py` | M3-1~4 events、P3-5 batch、P3-6 minhash |
| `src/backlink_publisher/cli/_publish_throttle.py` | M4-3 adaptive |
| `src/backlink_publisher/cli/_publish_helpers.py` | M1-2 fan-out resolver、parser 工廠入口 |
| `src/backlink_publisher/publishing/registry.py` | M4-5 error_map、P4-9 lazy resolve、M4-6 banner_fallback |
| `src/backlink_publisher/publishing/reliability/policy.py` | M4-1 opt-OUT、M4-2 classify、M4-4 retry_after |
| `src/backlink_publisher/publishing/banner_dispatcher.py` | M4-6 policy、P4-12 keep-alive |
| `src/backlink_publisher/cli/recheck_backlinks.py` | M5-1 auto、M5-2 UA、M5-3 schema、P5-5/6/7 |
| `src/backlink_publisher/cli/replan_dead.py` | M5-4 contract、M6-4 parent_remediation_id |
| `src/backlink_publisher/cli/remediation_queue.py` | M6-1 events、M6-5/6 |
| `src/backlink_publisher/events/kinds.py` | 新增 11 種事件 kind（見觀測事件彙整） |
| `src/backlink_publisher/events/_project_reducers.py` | P3-7 索引遷移 |
| `tests/test_cli_timing_regression.py` | 為 Unit 1-5 各加 baseline（baseline 由實作 PR 帶入） |
| `pyproject.toml` | console_scripts（不變）；optional-dependencies 加 `datasketch` |

### SLOC 監控

新建檔案均 < 200 SLOC，無需動 `monolith_budget.toml`；既有監控檔（`core.py` 250、`quality_gate.py` 390、`_publish_throttle.py` 480 等）若超 ceiling 在同 PR 內 raise + 帶 `rationale` ≥80 字。

---

## Test Plan

### 新增測試

| 測試檔 | 覆蓋 |
|---|---|
| `tests/test_plan_engine_purity_docstring.py` | R1.1 |
| `tests/test_plan_fanout_resolver.py` | R1.2 |
| `tests/test_plan_budget_downgrade.py` | R1.3 |
| `tests/test_plan_schema_version.py` | R1.4 |
| `tests/test_plan_parallel_partition.py` | R1.5 |
| `tests/test_plan_templates_lru_cache.py` | R1.6 |
| `tests/test_content_fetch_async.py` | R1.7 |
| `tests/test_validate_probe_budget.py` | R2.1 |
| `tests/test_validate_forward_compat.py` | R2.2 |
| `tests/test_validate_shell_engine_separation.py` | R2.3 |
| `tests/test_validate_outcome_event.py` | R2.4 |
| `tests/test_validate_schema_prewarm.py` | R2.5 |
| `tests/test_validate_probe_concurrency.py` | R2.6 |
| `tests/test_validate_probe_lru.py` | R2.7 |
| `tests/test_validate_anchor_counter.py` | R2.8 |
| `tests/test_quality_gate_event_emission.py` | R3.1-3 |
| `tests/test_quality_replan_ttl.py` | R3.4 |
| `tests/test_quality_llm_batch.py` | R3.5 |
| `tests/test_quality_similarity_minhash.py` | R3.6 |
| `tests/test_events_idx_type_ts.py` | R3.7 |
| `tests/test_publish_reliability_policy_optout.py` | R4.1 |
| `tests/test_publish_circuit_classify.py` | R4.2 |
| `tests/test_publish_throttle_adaptive.py` | R4.3 |
| `tests/test_publish_retry_after.py` | R4.4 |
| `tests/test_adapter_error_taxonomy.py` | R4.5 |
| `tests/test_banner_policy_resolved.py` | R4.6 |
| `tests/test_canary_health_key_disjoint.py` | R4.7 |
| `tests/test_publish_concurrency_semaphore.py` | R4.8 |
| `tests/test_registry_lazy_resolve.py` | R4.9 |
| `tests/test_events_flush_window.py` | R4.10 |
| `tests/test_publish_adapter_cache.py` | R4.11 |
| `tests/test_banner_keepalive_pool.py` | R4.12 |
| `tests/test_publish_throttle_async.py` | R4.13 |
| `tests/test_recheck_probe_auto.py` | R5.1 |
| `tests/test_recheck_ua_disjoint.py` | R5.2 |
| `tests/test_recheck_event_schema.py` | R5.3 |
| `tests/test_replan_dead_purity.py` | R5.4 |
| `tests/test_recheck_async_probe.py` | R5.5 |
| `tests/test_recheck_ttl_cache.py` | R5.6 |
| `tests/test_read_events_streaming.py` | R5.7 |
| `tests/test_remediation_audit_trail.py` | R6.1 |
| `tests/test_remediation_dashboard_card.py` | R6.3 |
| `tests/test_replan_parent_remediation_id.py` | R6.4 |
| `tests/test_remediation_index.py` | R6.5 |
| `tests/test_remediation_snooze_sqlite.py` | R6.6 |
| `tests/test_cli_helpers_make_parser.py` | R7.1 |
| `tests/test_idempotency_split.py` | R7.2 |
| `tests/test_health_api.py` | R7.3 |
| `tests/test_type_ignore_audit.py` | R7.5 |
| `tests/test_except_narrowed.py` | R7.6 |

### Timing regression baseline

`tests/test_cli_timing_regression.py` 為每個 Unit 加 baseline（sample 500-row seed，量 end-to-end ms）：
- plan 階段：Unit 1 完成後建立 baseline（目標 -30%）
- validate 階段：Unit 2 完成後建立（目標 -40% URL probe）
- quality-gate：Unit 3 完成後（目標 LLM token -60%）
- publish：Unit 4 完成後（目標 4× 併發）
- recheck：Unit 5 完成後（目標 8× probe 併發）

baseline 由各實作 PR 帶入；本企劃 PR 僅佔位、附 `pytest.mark.timing_baseline_skip` 守護。

### 既有測試守護

- `tests/test_no_monolith_regrowth.py` — SLOC 監控
- `tests/test_adapter_dofollow_gate.py` — dofollow gate
- `tests/test_publish_reliability_policy.py` — 翻 opt-OUT 後更新
- `tests/test_cli_exit_code_contract.py` — exit code 不變
- `tests/test_security_toggle_mutation_gate.py` — CSRF/security toggle 不變
- `tests/test_agents_md_has_binding_section.py` — AGENTS.md 章節不變

---

## Rollout

> 採 PR #279 observe→enforce pattern：每 Unit 上線前先 observe 期（opt-in 旗標 + WebUI 觀測卡），再 enforce（翻預設）。

| Sprint | Unit | 動作 | 觀察期 |
|---|---|---|---|
| **Sprint 1** | Unit 1 (R1.1-8) | docstring + 純函數契約守護 + schema_version | 0d（純契約） |
| | Unit 7 (R7.1, R7.5) | argparse 工廠 + type:ignore 註解 | 0d |
| **Sprint 2** | Unit 2 (R2.1-8) | validate 預算 + 併發 + cache + Counter | 3d（觀察 probe 誤殺率） |
| | Unit 3 (R3.7) | events.db 索引 | 0d |
| **Sprint 3** | Unit 4 (R4.1, R4.4, R4.6, R4.7, R4.9, R4.11, R4.12, R4.13) | policy 翻預設前的 observe 動作 | 7d（先以 env flag 收集誤觸發） |
| | Unit 5 (R5.1-4) | recheck 觀測 + UA disjoint + 純函數契約 | 3d |
| | Unit 6 (R6.1-2, R6.4-6) | remediation 觀測 + 互鎖 | 3d |
| **Sprint 4** | Unit 4 (R4.2, R4.3, R4.5) | circuit 分流 + adaptive throttle + AdapterError 基類 | 7d |
| | Unit 1 (P1.1-3) | plan 平行化 + async content.fetch | 7d（觀察 dedup 局部性） |
| **Sprint 5** | Unit 4 (R4.8) | publish 併發（觀察期 opt-in，預設 1） | 14d（觀察 anti-bot 觸發） |
| | Unit 4 (R4.10) | events batch flush | 7d |
| | Unit 3 (R3.1-6) | quality-gate 觀測 + MinHash + LLM batch | 7d |
| | Unit 5 (P5.5-7) | recheck 併發 | 7d |
| **Sprint 6** | Unit 7 (R7.2, R7.3, R7.4, R7.6) | 橫切收尾 | 7d |
| **Sprint 7** | 翻預設 | R4.1 翻 opt-OUT、R4.8 翻預設併發 | 14d |

每個 Sprint 結束前必跑：
```bash
pytest tests/ -n auto
python -m pytest tests/test_no_monolith_regrowth.py -v
python -m radon raw -s src/backlink_publisher/cli/plan_backlinks/core.py
plan-check docs/plans/2026-06-10-001-feat-pipeline-end-to-end-optimization-plan.md
```

---

## Risks & Mitigations

| # | 風險 | 機率 | 影響 | 緩解 |
|---|---|---|---|---|
| RK-1 | P1-1 `multiprocessing.Pool` 對 `Config` 物件 pickle 失敗 | 中 | 高 | 抽 `build_one(config_snapshot)` 只傳必要欄位；預先 pickle 測試 |
| RK-2 | R4-1 reliability policy 翻預設後誤觸發 publish 拒絕 | 中 | 高 | observe 期 7d 以 WebUI 觀測卡收集 `publish.policy_skipped
