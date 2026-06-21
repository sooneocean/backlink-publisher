---
date: 2026-06-08
topic: channel-health-routing
builds_on:
  - docs/plans/2026-06-07-001-feat-backlink-remediation-queue-plan.md
  - docs/plans/2026-06-07-002-feat-replan-dead-pipeline-plan.md
  - docs/plans/2026-06-07-003-feat-quality-gate-plan.md
  - docs/plans/2026-06-01-005-gate-first-governance-plan.md
reviewed: 2026-06-08
---

# 渠道健康路由 — 存活率優化閉環

## 問題陳述

現有 backlink publisher 已經具備完整的檢測基礎設施：
- `recheck-backlinks` — 5 種 verdict（alive / host_gone / link_stripped / dofollow_lost / probe_error）
- `canary-targets` — 永續存活監控
- `replan-dead` — 死鏈重新規劃
- `remediation-queue` — 操作員確認/解決/延遲
- `quality-gate` — 發布前品質閘門

但關鍵缺口在於：**recheck → replan → publish 之間沒有自動串接**。操作員必須手動：
1. 執行 recheck
2. 審視死鏈結果
3. 手動呼叫 replan-dead
4. 手動發布

當渠道數 > 30 時，這個手動 loop 的 latency 以天甚至週為單位，造成「死亡後自動補救不夠快」。

## 目標

1. **全自動 cron 循環**：backlink 死亡後自動 recheck → health routing → replan → publish，無需人工介入
2. **渠道健康路由**：自動將替換內容路由到存活率最高的渠道，避免把新內容餵給高死亡率渠道
3. **累積優化增益**：隨著時間收集渠道存活數據，路由決策持續改善
4. **運營可視性**：health dashboard 展示渠道級存活率、路由決策歷史、失敗歸因分佈

## 非目標

- ❌ 內容品質評分（跨語言 routing、SEO 價值 — 不在本回合範圍）
- ❌ 預防性監控（Phase 0 疑點測試 — 已由 gate-probe 涵蓋）
- ❌ 渠道評分綜合指數（channel-scorecard 的 GA4/GSC/AI 軸 — 維持 inert:not-landed）
- ❌ 渠道自動退役（本回合只做 advisory routing，不自動移除渠道）

## 方案描述：渠道健康路由

### 核心閉環

```
                    ┌──────────────────┐
                    │   recheck loop    │ (cron 驅動, flock 保護)
                    │  (現有 CLI 擴充)  │
                    └────────┬─────────┘
                             │ verdicts
                             ▼
                    ┌──────────────────┐
                    │  health registry  │ ← 每個渠道的存活率/歸因統計/最後成功時間
                    │  (新元件)        │
                    └────────┬─────────┘
                             │ dead links + channel health
                             ▼
                    ┌──────────────────┐
                    │  health router   │ ← 決定 dead backlink 重發到哪個渠道
                    │  (新元件)        │
                    └────────┬─────────┘
                             │ seed JSONL (含 target_channel)
                             ▼
                    ┌──────────────────┐
                    │  replan + publish │ (現有 CLI 串接)
                    │  (自動鏈式呼叫)   │
                    └────────┬─────────┘
                             │ publish result
                             ▼
                    ┌──────────────────┐
                    │   health update   │ ← 寫回 publish 結果到 registry
                    │  (新元件)        │
                    └──────────────────┘
```

### 元件說明

#### 1. 存活 Registry (`ChannelHealthRegistry`)

**資料儲存**：events.db（利用現有 events 基礎設施）

每個渠道維護一組指標：

| 欄位 | 類型 | 說明 |
|---|---|---|
| channel | str | 渠道名稱（velog / medium / blogger / telegraph / ...） |
| total_rechecks | int | 該渠道的 recheck 總次數 |
| alive_count | int | alive verdict 次數 |
| dead_count | int | dead verdict 總次數 |
| host_gone_count | int | host_gone 歸因次數 |
| link_stripped_count | int | link_stripped 歸因次數 |
| dofollow_lost_count | int | dofollow_lost 歸因次數 |
| probe_error_count | int | probe_error 歸因次數 |
| last_alive_at | timestamp | 最後一次 alive 時間 |
| last_dead_at | timestamp | 最後一次 dead 時間 |
| last_routed_to | timestamp | 最後一次被 router 選中 |
| consecutive_failures | int | 連續失敗次數（recheck dead） |
| survival_rate | float | 存活率（alive / total，滑動視窗） |

**事件類型**：
- `channel.recheck_observed` — recheck 回報 verdict 時寫入
- `channel.routed` — router 選擇該渠道時寫入
- `channel.published_to` — publish 成功時寫入

**滑動視窗**：支援可配置的時間視窗（預設 30 天）計算存活率，避免遠古數據稀釋近期表現。

#### 2. 健康路由器 (`HealthRouter`)

**輸入**：dead backlink 列表 + ChannelHealthRegistry 查詢

**輸出**：seed JSONL（與 plan-backlinks 相容，額外包含 `target_channel` 欄位）

**路由策略（v1）**：

```
for each dead backlink:
  1. 排除不可用的渠道（未綁定 / auth expired / 剛失敗）
  2. 從可用渠道中，按 survival_rate 降冪排序
  3. 選 survival_rate 最高者作為 target_channel
  4. 如果原渠道 survival_rate >= threshold（預設 0.7），保留原渠道
  5. 如果原渠道 survival_rate < threshold，自動路由到更健康的渠道
```

**路由觸發條件**：
- dead backlink 的 `channel` 的 `survival_rate < threshold`（預設 0.7）
- OR 原 channel 不再可用（auth expired / 已退役）
- OR operator 手動指定（`--force-reroute`）

**逃逸閥**：
- 所有可用渠道都低於最低存活率（< 0.1）→ 寫入 warning event，暫停該批路由
- 單一渠道連續失敗 > 3 次 → 暫時從路由池排除 24 小時
- 無可用渠道 → 寫入 `routing.blocked` event，通知 operator

#### 3. 自動化 CLI (`auto-recover`)

**新 CLI entrypoint**：`auto-recover`

```
auto-recover [--dry-run] [--max-dead N] [--routing-threshold 0.7]
```

**執行流程**（單次呼叫）：
1. **Recheck phase**：呼叫 recheck-backlinks 核心（現有邏輯，非獨立子行程）
2. **Health update phase**：將 verdicts 寫入 ChannelHealthRegistry
3. **Routing phase**：HealthRouter 決定 dead links 的 target_channel
4. **Replan phase**：呼叫 replan-dead 核心（通道內，生成 seed JSONL）
5. **Quality gate phase**：通過 quality-gate 過濾
6. **Publish phase**：呼叫 publish-backlinks（draft mode）
7. **Report phase**：輸出 JSONL report（routing 決策、publish 結果、registry 更新）

**安全機制**：
- `--dry-run`：只報告 routing 決策，不 publish
- `--max-dead N`：每輪最多處理 N 個死鏈（預設 50）
- `flock` 保護：避免重疊執行（與 recheck-backlinks 共用 flock 慣例）
- `--mode draft|publish`：預設 draft，operator 可選擇直接 publish

#### 4. cron 整合

**建議 crontab**（外部設定，非本專案管理）：

```cron
# 每 6 小時執行一次 auto-recover（只處理已確定的死鏈）
0 */6 * * * cd /path/to/backlink-publisher && python -m backlink_publisher.cli.auto_recover --max-dead 50 --mode draft
```

**單次執行 vs 常駐程序**：採用單次 CLI + cron 模式（vs 常駐 daemon），與專案現有架構一致。

### Dashboard 可見性

**`/ce:health` 新增卡片：**

1. **渠道健康總覽**（表格）
   - 渠道名稱 / 總 recheck 次數 / 存活率 / 主要死因 / 最後 alive / 路由建議
   - 顏色編碼：> 0.8 綠 / 0.5-0.8 黃 / < 0.5 紅

2. **路由決策歷史**（列表）
   - 時間 / 來源渠道 / 目標渠道 / verdict / 路由理由
   
3. **健康趨勢圖**（純文字或簡易 sparkline）
   - 存活率隨時間變化（30 天滑動視窗）

## 渠道對 routing 的適用性（初步分析）

基於探索結果，各渠道對健康路由的適用性：

| 渠道 | 適用 routing？ | 原因 |
|---|---|---|
| velog | ✅ | 存活率穩定，GraphQL publish 穩定 |
| blogger | ✅ | Google 基礎設施，存活率極高 |
| medium | ✅ | 成熟平台，存活率高 |
| telegraph | ✅ | 靜態頁面，存活率高 |
| writeas | ✅ | 輕量，存活率穩定 |
| ghpages | ✅ | GitHub 基礎設施，存活率極高 |
| devto | ⚠️ | 存活率 OK，但 publish 流程有特殊限制 |
| notable | ⚠️ | 需要更多數據 |
| wordpresscom | ⚠️ | 存活率 OK，但平台限制較多 |
| wiki channels | ❌ (v1) | 語法複雜，存活率不穩定 |
| self-hosted | ❌ (v1) | 自託管，不適用跨渠道 routing |

> 注意：v1 的 routing granularity 是「渠道級」，非「帳號級」。如果同一渠道有多個綁定帳號，v1 不區分。

## 關鍵設計決策

### D1: 存活 registry 的位置

**決策**：存在 events.db，使用現有 events 框架

**理由**：
- 不需新儲存層
- 事件查詢已有 `events._project_reducers` 模式可參考
- 與 `link.rechecked`、`citation.observed` 等事件一致
- 支援跨 session persistence

### D2: 路由策略的複雜度

**決策**：v1 只使用 survival_rate 作為 routing 維度

**理由**：
- 保持 v1 簡單，快速交付閉環價值
- 後續可疊加更多維度（渠道容量、內容類型適配、語言支援性）
- 避免 premature optimization

### D3: publish 模式

**決策**：預設 draft mode，operator 可選直接 publish

**理由**：
- draft mode 給 operator 最後檢查機會
- 全自動 publish 選項給進階使用者
- 與現有 publish-backlinks 的 `--mode` 參數一致

### D4: backlink 類型範圍

**決策**：所有可重新計畫的 dead backlinks（不限於 dofollow）

**理由**：擴大自動化覆蓋面。如果某個 backlink 死了，無論它原本是 dofollow 還是 nofollow，自動重新規劃都創造新機會。

## 成功標準

1. [ ] **閉環自動化**：auto-recover 單次呼叫完成 recheck → route → replan → publish，exit 0
2. [ ] **健康路由生效**：dead backlink 自動路由到更高存活率的渠道
3. [ ] **渠道 registry 累積數據**：每輪執行後 registry 正確更新，存活率反映真實表現
4. [ ] **dashboard 顯示**：`/ce:health` 正確顯示渠道健康卡
5. [ ] **錯誤處理**：所有邊界情況（無可用渠道、registry 空、批次全失敗）正確處理
6. [ ] **dry-run 安全**：`--dry-run` 確實不 publish，只報告 routing 決策
7. [ ] **flock 保護**：重疊執行被正確阻擋

## 待規劃事項（下一階段）

以下問題在 requirements doc 層級已釐清，留待 planning phase 深化：

1. **Routing 策略的權重參數**：是否加入 channel capacity（每個渠道同時發布多少文章）？
2. **registry 的 retention**：events.db 的 channel 事件 retention 策略為何？
3. **初始存活數據**：剛上線時 registry 為空，router 如何初始化？
   - 建議：初始假設所有渠道 survival_rate = 0.8（樂觀），隨真實數據更新
4. **多輪 routing 的防止振盪**：避免 A→B→A→B 反覆路由同一篇文章

## 依賴關係

- `recheck-backlinks` CLI（現有）
- `replan-dead` CLI（現有）
- `publish-backlinks` CLI（現有）
- `quality-gate` CLI（現有）
- `events.db` 基礎設施（現有）
- `flock` 保護機制（現有）
