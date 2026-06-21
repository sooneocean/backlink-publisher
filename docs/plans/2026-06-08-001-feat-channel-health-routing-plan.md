---
title: "feat: 渠道健康路由 — 存活率優化閉環 (方案 B)"
type: feat
status: active
date: 2026-06-08
origin: docs/brainstorms/2026-06-08-channel-health-routing-requirements.md
claims:
  paths:
    - src/backlink_publisher/events/kinds.py
    - src/backlink_publisher/events/schema.py
    - src/backlink_publisher/health/registry.py
    - src/backlink_publisher/health/router.py
    - src/backlink_publisher/cli/auto_recover.py
    - webui_app/health_metrics.py
    - webui_app/routes/health.py
    - webui_app/templates/health.html
    - pyproject.toml
  shas:
    - a04949f
---

# feat: 渠道健康路由 — 存活率優化閉環

## Overview

現有 backlink publisher 的 backlink 死亡檢測已完善（recheck-backlinks 5-verdict 分類法），但從檢測到補發的閉環需手動介入。本計畫實現完整的自動化閉環：

```
recheck → channel health registry → health router → replan → quality gate → publish → registry update
```

方案 B 的核心差異在於「健康路由」：不只自動補發，還把新內容路由到存活率更高的渠道，隨著時間累積存活數據持續優化。

**關鍵名詞定義**：
- **存活率 (survival_rate)**：渠道在所有 recheck 中回報 alive 的比例，使用滑動視窗（預設 30 天）
- **健康路由**：當原渠道存活率低於閾值（預設 0.7）時，自動將 replan seed 導向更高存活率的渠道
- **死亡歸因分佈**：每個渠道的 host_gone / link_stripped / dofollow_lost / probe_error 比例

## Requirements

- **R1**: 每個 recheck verdict 必須寫入 channel health registry，累積渠道級存活統計
- **R2**: Health router 必須根據存活率將 dead backlink 的 replan seed 路由到最佳可用渠道
- **R3**: 路由決策必須可追溯 — 記錄 routing event 到 events.db 供 dashboard 查詢
- **R4**: `auto-recover` CLI 必須單次呼叫完成 recheck → route → replan → quality-gate → publish 完整閉環
- **R5**: `--dry-run` 模式必須只報告路由決策而不實際 publish
- **R6**: flock 保護必須防止重疊執行
- **R7**: Dashboard `/ce:health` 必須顯示渠道健康總覽（存活率 / 歸因分佈 / 最後成功時間）
- **R8**: 逃逸閥：生存率低於最低閾值（0.1）或連續失敗 > 3 次的渠道自動從路由池排除
- **R9**: 路由決策歷史必須可查詢（誰 → 誰 → 何時 → 為何）

## Non-Goals

- ❌ Routing 不考慮內容品質、語言匹配或 SEO 價值 — 只以存活率為維度
- ❌ 渠道自動退役 — v1 只做 advisory routing，不自動移除渠道
- ❌ 渠道評分綜合指數（channel-scorecard GA4/GSC/AI 軸維持 inert:not-landed）
- ❌ 預防性監控（gate-probe 已有 Phase 0 falsification）
- ❌ 修改現有 recheck-backlinks / replan-dead / publish-backlinks CLI 的退出碼合約

## Implementation

### Unit 1 — 渠道存活 Registry (`ChannelHealthRegistry`)

**新檔案**: `src/backlink_publisher/health/__init__.py`
**新檔案**: `src/backlink_publisher/health/registry.py`

ChannelHealthRegistry 是純讀取元件（read-side aggregation），資料來源是 events.db 中現有及新增的事件。不建立獨立 table，而是用 SQL query 聚合計算。

#### 新增事件種類

在 `src/backlink_publisher/events/kinds.py` 新增三個事件 kind：

```python
# Channel health events — written by recheck pipeline and auto-recover CLI
CHANNEL_RECHECK_OBSERVED: Final = "channel.recheck_observed"
CHANNEL_ROUTED: Final = "channel.routed"
CHANNEL_PUBLISHED_TO: Final = "channel.published_to"
```

更新 `KINDS` frozenset 和 `REQUIRED_FIELDS` floor（如有必要）。

**`channel.recheck_observed` payload**:
```python
{
    "verdict": "alive|host_gone|link_stripped|dofollow_lost|probe_error",
    "platform": "blogger|medium|...",
    "live_url": "https://...",
    "target_url": "https://...",
}
```

**`channel.routed` payload**:
```python
{
    "source_channel": "medium",
    "target_channel": "blogger",
    "reason": "survival_rate_below_threshold|channel_unavailable|operator_override",
    "source_survival_rate": 0.45,
    "target_survival_rate": 0.92,
    "dead_live_url": "https://...",
    "target_url": "https://...",
}
```

**`channel.published_to` payload**:
```python
{
    "platform": "blogger",
    "live_url": "https://...",
    "target_url": "https://...",
    "status": "drafted|published",
}
```

#### `ChannelHealthRegistry` class

```python
# src/backlink_publisher/health/registry.py

@dataclass(frozen=True)
class ChannelHealth:
    channel: str
    total_rechecks: int
    alive_count: int
    dead_count: int
    host_gone_count: int
    link_stripped_count: int
    dofollow_lost_count: int
    probe_error_count: int
    last_alive_at: str | None  # ISO-8601
    last_dead_at: str | None
    survival_rate: float | None  # None = no data (not 0)

    @property
    def has_data(self) -> bool:
        return self.total_rechecks > 0

    @property
    def primary_death_cause(self) -> str | None:
        """Return the most common deterministic dead verdict."""
        ...


class ChannelHealthRegistry:
    """Read-side aggregation: queries events.db for per-channel survival metrics.

    Construction does not open the DB; pass an EventStore instance.
    All methods are pure queries — never write.
    """

    def __init__(self, store: EventStore) -> None: ...

    def get_health(self, channel: str, *, window_days: int = 30) -> ChannelHealth:
        """Return health for a single channel within the sliding window."""
        ...

    def get_all_health(self, *, window_days: int = 30) -> dict[str, ChannelHealth]:
        """Return health for ALL channels with data."""
        ...

    def get_routing_history(
        self, *, limit: int = 50, since_dt: datetime | None = None,
    ) -> list[dict]:
        """Return channel.routed events for dashboard display."""
        ...

    def get_available_channels(
        self, *, min_survival_rate: float = 0.1,
        exclude_channels: set[str] | None = None,
    ) -> list[str]:
        """Return channels eligible for routing (above floor rate, not excluded).

        Excludes channels with no data (treats as known-unknown).
        """
        ...

    def _window_start(self, window_days: int) -> datetime:
        """Compute the sliding window start timestamp."""
        return datetime.now(timezone.utc) - timedelta(days=window_days)
```

**SQL pattern for survival_rate calculation**:
```sql
SELECT
    json_extract(payload_json, '$.platform') AS channel,
    COUNT(*) AS total,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') = 'alive' THEN 1 ELSE 0 END) AS alive_count,
    SUM(CASE WHEN json_extract(payload_json, '$.verdict') IN ('host_gone', 'link_stripped') THEN 1 ELSE 0 END) AS dead_count,
    ...
FROM events
WHERE kind = 'channel.recheck_observed'
  AND ts_utc >= ?
GROUP BY channel
```

**Note**: 為了確保 channel health registry 有數據，recheck-backlinks 在寫入 `link.rechecked` 事件時，必須同時（或在 auto-recover 中轉寫）寫入 `channel.recheck_observed` 事件。建議在 auto-recover 的 recheck phase 中，從 `link.rechecked` payload 提取 platform+verdict 後轉寫 `channel.recheck_observed` — 這樣不修改現有 recheck-backlinks 的寫入模式。

#### 寫入 helper

在 registry.py 中提供寫入函數供 auto-recover 呼叫：

```python
def write_recheck_observed(
    store: EventStore,
    *,
    verdict: str,
    platform: str,
    live_url: str,
    target_url: str,
    run_id: str | None = None,
) -> int:
    """Append a channel.recheck_observed event. Returns event id."""
    return store.append(
        kind=CHANNEL_RECHECK_OBSERVED,
        payload={
            "verdict": verdict,
            "platform": platform,
            "live_url": live_url,
            "target_url": target_url,
        },
        target_url=target_url,
        host=platform,
        run_id=run_id,
    )


def write_routed_event(
    store: EventStore, *, ...  # all routing decision fields
) -> int: ...


def write_published_to_event(
    store: EventStore, *, ...
) -> int: ...
```

### Unit 2 — 健康路由器 (`HealthRouter`)

**新檔案**: `src/backlink_publisher/health/router.py`

```python
@dataclass(frozen=True)
class RoutingDecision:
    dead_live_url: str
    target_url: str
    original_platform: str | None
    assigned_channel: str
    source_survival_rate: float | None
    target_survival_rate: float | None
    reason: str  # "survival_rate_below_threshold" | "channel_unavailable" | "no_change_needed"


class HealthRouter:
    """Decides which channel a dead backlink should be re-published through.

    V1 strategy: compare original channel's survival_rate against threshold.
    If below threshold, pick the healthiest available channel.
    """

    DEFAULT_THRESHOLD = 0.7
    MIN_SURVIVAL_RATE = 0.1  # absolute floor: below this, channel is excluded
    CONSECUTIVE_FAILURE_BACKOFF_HOURS = 24
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        registry: ChannelHealthRegistry,
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> None: ...

    def route(
        self,
        dead_events: list[dict],
        *,
        exclude_channels: set[str] | None = None,
    ) -> list[RoutingDecision]:
        """Route a batch of dead backlinks to optimal channels.

        Input: list of {live_url, target_url, host, platform, verdict}
        Output: list of RoutingDecision, one per dead link

        Algorithm per dead event:
        1. Look up original channel's health.
        2. If original channel has no data OR survival_rate >= threshold → keep original.
        3. If original channel survival_rate < threshold:
           a. Query available channels (exclude those below MIN_SURVIVAL_RATE).
           b. Pick the channel with highest survival_rate.
           c. If none available → reason="no_available_channel"; keep original + warning.
        4. Track consecutive failures per channel; exclude temporarily if exceeded.
        """
        ...

    def _available_channels_sorted(self) -> list[tuple[str, float]]:
        """Return (channel, survival_rate) sorted descending, excluding floor."""
        ...

    def _check_failure_backoff(self, channel: str) -> bool:
        """True if channel is in temporary backoff due to consecutive failures."""
        ...
```

### Unit 3 — `auto-recover` CLI

**新檔案**: `src/backlink_publisher/cli/auto_recover.py`

```
auto-recover [--dry-run] [--max-dead N] [--routing-threshold 0.7]
             [--days N] [--mode draft|publish] [--emit-stderr]
```

**執行流程**（單次呼叫六個 phase，連續且在單一行程內）：

```
Phase 1: Recheck
    └ Execute recheck-backlinks core logic (programmatic call to probe function)
    └ Write verdicts as link.rechecked (existing) AND channel.recheck_observed (new)

Phase 2: Health Update
    └ ChannelHealthRegistry queries events.db for latest verdicts
    └ (Registry is query-only; data was already written in Phase 1)

Phase 3: Routing
    └ Call replan-dead's _deterministic_dead_events() to get dead links
    └ Pass through HealthRouter.route() → RoutingDecision list
    └ Write channel.routed events for each routing decision
    └ Build enhanced seed JSONL (replan-dead seeds + target_channel override)

Phase 4: Replan + Quality Gate
    └ Pass seeds through plan-backlinks core (if target_channel differs, override platform)
    └ Pass through quality-gate CLI core (stdin/stdout filter)

Phase 5: Publish
    └ Pass through publish-backlinks core
    └ Write channel.published_to events for each successful publish

Phase 6: Report
    └ Emit JSONL report to stdout:
      {phase, routing_decisions, publish_results, registry_snapshot}
```

**程式化呼叫（非 subprocess）**: 所有現有 CLI 的 core logic 必須能被 import 呼叫。這需要：

1. **recheck-backlinks**: 提取 probe + verdict 核心為可 import 的函數
2. **replan-dead**: 已提供 `_deterministic_dead_events()` 可 import（現有）
3. **publish-backlinks**: 需要一個程式化入口（類似 `run_publish_loop` 但包裝完整 setup → loop → epilogue）
4. **quality-gate**: 需要可 import 的 filter 函數

**If programmatic import is not cleanly separable for any CLI, auto-recover falls back to subprocess piping** (stdin→stdout JSONL) — 但這是例外路徑，預設走 programmatic。

**程式化入口封裝**：

```python
# auto_recover.py

def _run_recheck_phase(config, args) -> list[dict]:
    """Run recheck-backlinks core, return verdict rows.
    
    Calls the probe function from recheck_backlinks module directly,
    not via subprocess.
    """
    ...

def _run_routing_phase(dead_events, registry, router, store) -> list[RoutingDecision]:
    """Route dead events, write channel.routed events, return decisions."""
    decisions = router.route(dead_events)
    for d in decisions:
        write_routed_event(store, ...)
    return decisions

def _build_seeds_with_routing(dead_events, decisions) -> list[dict]:
    """Merge replan-dead seeds with routing decisions (override platform)."""
    ...

def _run_publish_phase(seeds, config, args, state) -> PublishRunState:
    """Call publish-backlinks core loop directly."""
    ...

def main(argv: list[str] | None = None) -> None:
    """Entry point — runs the full survival loop."""
    ...
```

**pyproject.toml entry**:
```toml
auto-recover = "backlink_publisher.cli.auto_recover:main"
```

**flock protection**: 從 `recheck_backlinks.py` 複用相同的 flock 模式（`portalocker` 或 `fcntl.flock` 在 PID file 上）。使用共用的 lock file 路徑：`<cache_dir>/auto-recover.lock`。

### Unit 4 — Dashboard 渠道健康卡片

**修改**: `webui_app/health_metrics.py`, `webui_app/routes/health.py`, `webui_app/templates/health.html`

在 `/ce:health` 新增兩個卡片區塊：

#### 卡片 1: 渠道健康總覽 (Channel Health Overview)

**資料來源**: `ChannelHealthRegistry.get_all_health()` + `get_routing_history()`

在 `health_metrics.py` 新增聚合函數：

```python
@dataclass(frozen=True)
class ChannelHealthCard:
    rows: list[dict]  # channel, survival_rate, total, primary_death, last_alive, routing_advice
    routing_history: list[dict]


async def build_channel_health_card(store: EventStore) -> ChannelHealthCard:
    """Build the channel health overview card data."""
    registry = ChannelHealthRegistry(store)
    health_rows = []
    for channel, health in registry.get_all_health().items():
        advice = "healthy"  # green
        if health.has_data and health.survival_rate is not None:
            if health.survival_rate < 0.5:
                advice = "avoid"  # red
            elif health.survival_rate < 0.7:
                advice = "caution"  # yellow
        health_rows.append({
            "channel": channel,
            "survival_rate": round(health.survival_rate, 2) if health.survival_rate is not None else None,
            "has_data": health.has_data,
            "total_rechecks": health.total_rechecks,
            "dead_count": health.dead_count,
            "primary_death_cause": health.primary_death_cause,
            "last_alive_at": health.last_alive_at,
            "last_dead_at": health.last_dead_at,
            "routing_advice": advice,
        })
    return ChannelHealthCard(
        rows=sorted(health_rows, key=lambda r: r["survival_rate"] or 0, reverse=True),
        routing_history=registry.get_routing_history(limit=20),
    )
```

在 `webui_app/routes/health.py` 中新增 `_channel_health_card()` helper（fail-open pattern），並在 `ce_health()` 中收集資料傳入 template。

#### 卡片 2: 路由決策歷史

在 health.html 新增 `<div class="card shadow-sm mb-4">`:

```html
{# ── Channel Health card ─────────────────────────────── #}
<div class="card shadow-sm mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <span><i class="bi bi-activity me-2"></i>Channel Health</span>
  </div>
  <div class="card-body">
    {% if channel_health.rows %}
    <div class="table-responsive">
      <table class="table table-sm table-hover mb-0">
        <thead>
          <tr>
            <th>Channel</th>
            <th>Survival Rate</th>
            <th>Total</th>
            <th>Dead</th>
            <th>Primary Cause</th>
            <th>Last Alive</th>
            <th>Advice</th>
          </tr>
        </thead>
        <tbody>
          {% for row in channel_health.rows %}
          <tr class="{{ 'table-danger' if row.routing_advice == 'avoid'
                        else 'table-warning' if row.routing_advice == 'caution' }}">
            <td>{{ row.channel }}</td>
            <td>
              {% if row.survival_rate is not none %}
                <strong>{{ "%.0f"|format(row.survival_rate * 100) }}%</strong>
              {% else %}
                <span class="text-muted">—</span>
              {% endif %}
            </td>
            <td>{{ row.total_rechecks }}</td>
            <td>{{ row.dead_count }}</td>
            <td>{{ row.primary_death_cause or '—' }}</td>
            <td class="as-of">{{ row.last_alive_at[:10] if row.last_alive_at else '—' }}</td>
            <td>
              {% if row.routing_advice == 'healthy' %}
                <span class="badge bg-success">Healthy</span>
              {% elif row.routing_advice == 'caution' %}
                <span class="badge bg-warning text-dark">Caution</span>
              {% else %}
                <span class="badge bg-danger">Avoid</span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    {% if channel_health.routing_history %}
    <hr>
    <h6 class="mt-3"><i class="bi bi-arrow-left-right me-1"></i>Recent Routing Decisions</h6>
    <div class="table-responsive">
      <table class="table table-sm mb-0 small">
        <thead>
          <tr>
            <th>Time</th>
            <th>From</th>
            <th>To</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {% for ev in channel_health.routing_history %}
          <tr>
            <td class="as-of">{{ ev.ts_utc[:16] }}</td>
            <td>{{ ev.source_channel }}</td>
            <td><i class="bi bi-arrow-right"></i> {{ ev.target_channel }}</td>
            <td>{{ ev.reason }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    {% endif %}

    {% else %}
    <p class="text-muted mb-0"><i class="bi bi-info-circle me-1"></i>No channel health data yet — run auto-recover to start collecting.</p>
    {% endif %}
  </div>
</div>
```

### Unit 5 — 替換 seed 的 channel routing 邏輯

當 health router 決定把 backlink 路由到不同渠道時，seed JSONL 需要反映這個決定。這需要在 replan-dead seed 產生的基礎上，允許 override platform 欄位。

**方案**: 在 auto-recover 的 routing phase 中，對每個 routing decision：
1. 呼叫 `_build_seed()`（現有 replan-dead 函數，可 import）
2. 如果 `assigned_channel != original_platform`，覆蓋 seed 的 `platform` 欄位
3. 在 seed 中加入 `_routing_provenance` 記錄路由決策

```python
def _build_routed_seed(
    dead_event: dict,
    decision: RoutingDecision,
    language: str,
    url_mode: str,
    publish_mode: str,
) -> dict:
    """Build seed JSONL with channel routing override."""
    from backlink_publisher.cli.replan_dead import _build_seed
    
    seed = _build_seed(
        live_url=dead_event["live_url"],
        target_url=dead_event["target_url"],
        host=dead_event.get("host"),
        platform=decision.assigned_channel,  # MAY BE DIFFERENT from original
        language=language,
        url_mode=url_mode,
        publish_mode=publish_mode,
    )
    # Keep routing provenance
    seed["_routing_provenance"] = {
        "original_platform": dead_event.get("platform"),
        "routed_to": decision.assigned_channel,
        "reason": decision.reason,
    }
    return seed
```

### 邊界情況處理 (R8)

| 情況 | 行為 | 事件 |
|---|---|---|
| 所有渠道 survival_rate < 0.1 | 暫停該批路由，dead backlink 保留原渠道 | `channel.routing_blocked` (stderr warning + JSONL record) |
| 單一渠道連續 > 3 次失敗 | 暫時從路由池排除 24h | `channel.routing_excluded_temp` + logging |
| 無渠道有數據（剛上線） | HealthRouter 回退：保留原渠道 | 預設 routing 行為（不路由） |
| 原渠道在 routing 途中 expire | 路由到下一個最佳渠道，跳過原渠道 | `channel.routed` reason = "original_expired" |
| `--dry-run` | 執行 Phase 1-4，跳過 Phase 5（publish） | 報告 routing 決策但不 publish |
| 重疊執行 | flock 鎖阻擋第二個行程 | stderr "auto-recover: already running" + exit 1 |

## Files

### 新檔案

| File | Purpose |
|---|---|
| `src/backlink_publisher/health/__init__.py` | Package init, exports |
| `src/backlink_publisher/health/registry.py` | ChannelHealthRegistry + write helpers |
| `src/backlink_publisher/health/router.py` | HealthRouter + RoutingDecision |
| `src/backlink_publisher/cli/auto_recover.py` | auto-recover CLI entrypoint |

### 修改檔案

| File | Change |
|---|---|
| `src/backlink_publisher/events/kinds.py` | 新增 CHANNEL_RECHECK_OBSERVED, CHANNEL_ROUTED, CHANNEL_PUBLISHED_TO kinds |
| `webui_app/health_metrics.py` | 新增 build_channel_health_card() 和 ChannelHealthCard dataclass |
| `webui_app/routes/health.py` | 新增 _channel_health_card() helper，在 ce_health() 中收集資料 |
| `webui_app/templates/health.html` | 新增 Channel Health 卡片區塊（表格 + routing 歷史） |
| `pyproject.toml` | 新增 auto-recover 和 health 相關 console_scripts entry、套件路徑 |

## Risks

1. **recheck-backlinks 核心提取困難**：如果 recheck-backlinks 的核心 probe 邏輯與 CLI 層（argparse、exit code、stdout）耦合太深，extraction 成本可能很高。
   - *緩解*：fallback 到 subprocess piping（JSONL stdin→stdout），在 auto-recover 中包裝 subprocess 呼叫。
   
2. **publish-backlinks 程式化呼叫**：現有 `run_publish_loop` 需要 caller 提供 `args`、`config`、`state` 等物件。auto-recover 需要複製 publish-backlinks 的 setup 邏輯。
   - *緩解*：提取一個 `run_publish_pipeline(seeds, config) -> PublishRunState` helper 到 `_publish_helpers.py`。

3. **初始數據空白**：剛上線時 registry 無數據，router 無法做有意義的路由。
   - *緩解*：初始假設所有渠道 survival_rate = 0.8（樂觀），不觸發路由；隨第一輪 recheck 後自然累積數據。router 的 `get_available_channels()` 在無數據時回傳空列表。

4. **Routing 振盪**：可能發生 A→B→A→B 的反覆路由同一篇文章。
   - *緩解*：RoutingDecision 記錄每次路由；router 檢查 routing history 中同一 target_url 在 N 天內是否已路由過。v1 先不做 strict dedup，但 dashboard 提供可見性讓 operator 發現。

5. **事件量增長**：每個 recheck verdict 現在產生 2 個事件（`link.rechecked` + `channel.recheck_observed`），事件量加倍。
   - *緩解*：events.db 使用 WAL + 索引，30 個渠道 × 每輪 recheck 的 verdicts 量級很小（< 1000 事件/輪），不構成效能問題。如果未來需要，可加 TTL cleanup。

## Verification

1. **Unit test**: `tests/health/test_registry.py` — 用 mock EventStore 驗證聚合邏輯
2. **Unit test**: `tests/health/test_router.py` — 路由決策邏輯（threshold、backoff、exclusion）
3. **Unit test**: `tests/cli/test_auto_recover.py` — 閉環流程（mock 各 phase）
4. **Integration test**: `tests/test_channel_health_routing.py` — 寫入 events.db → query → route 的 E2E
5. **Dashboard test**: 手動瀏覽 `/ce:health` 確認卡片渲染無 error
6. **Monolith budget**: 新檔案不觸發現有 ceiling；如果修改既有 CLI 檔案接近 ceiling，需同步更新 `monolith_budget.toml`
