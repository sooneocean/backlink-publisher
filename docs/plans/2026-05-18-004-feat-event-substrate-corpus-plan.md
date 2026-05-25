---
title: "feat: Event substrate + published corpus (read-side projection)"
type: feat
status: completed
date: 2026-05-18
completed: 2026-05-18
origin: docs/brainstorms/2026-05-18-event-substrate-corpus-requirements.md
---

# feat: Event substrate + published corpus (read-side projection)

> ✅ **Prerequisites resolved（2026-05-18 16:00 update）**：
>
> Pass-1 document-review 揭示的 P0（webui_store/JsonStore 不存在）已在 merge commit `9408aa5` 解决——`refactor/webui-contract-tests` 已合并到 main，`webui_store/` + `webui_app/` 目录与 JsonStore 抽象都已上 main。
>
> **顺序依赖（D6 升级）满足**：Phase 2 的 U5 inline flush seam 不再被阻塞，可与 Phase 1 并行实施。
>
> 行号引用仍以实施时 `grep -n "<symbol>"` 重新定位为准（merge 后所有路径与符号已在 main 可见）。
>
> Pass-1 doc-review 应用的其他修订（threshold split、cursor sentinel、scrubber 覆盖面、p99 contention benchmark）保留有效，不受 merger 影响。

## Overview

新增 `events.db`（SQLite）作为现有 JSON 状态文件（`checkpoints/*.json` / `publish-history.json` / `draft-queue.json`）的派生**读侧投影**。写侧不变动；projector 以 inline `flush()` 函数调用挂在每个 JSON write helper 后面（RBP-4），实时将状态变化转换为 typed events + articles 行。两个新独立 console script (`bp-events-rebuild` / `bp-events-doctor`) 负责 bootstrap 与 anti-drift 对账。`report-anchors` / `footprint` 新增 `--from-events` 输入源（保留现有 stdin/--input/--from-profile 契约）。FTS5 默认关、schema slot 预留（RBP-2）。

## Problem Frame

运营者今天无法从单一查询面回答跨批 / 跨 CLI 的问题（"最近一周哪些锚点用过"、"哪个 host 发得最多"、"跨批 anchor 速度"）；`report-anchors` / `footprint` 每次从 JSONL stdin 重算，无法跨周次聚合或滑动窗口分析；下游 #2 longitudinal health daemon、#4 portfolio risk engine、#6 GSC/GA4 闭环都缺一个共享读侧底座。**写侧没问题需要修**——D1 选择 projection 而非 single-source-of-truth：JSON 仍权威，events.db 可丢可重建（详见 origin: `docs/brainstorms/2026-05-18-event-substrate-corpus-requirements.md` 的 D1 + Alternatives Considered）。

## Requirements Trace

- R1–R5（存储层）：events.db、events 表、articles 表、EventStore.append、FTS5 slot（默认关）
- R6–R10（事件类型）：`publish.*` 三态 + `draft.*` 两态投影；其余命名空间预留不冻结
- R11/R12（projector）：inline flush；与 architecture-refactor R3 JsonStore 解耦
- R13/R14/R15（读取）：`--from-events` 旗标；webui 不切读路径；`bp-events-rebuild` + `bp-events-doctor`
- R16/R17/R18（bootstrap）：per-run_id 分批事务、URL canonicalization、JSON 保持原状
- R19/R20（隐私）：白名单 schema、pre-serialization dict pruning、WARN-not-DEBUG、0600 含 WAL 副文件
- R21/R22（perf）：100K events benchmark + p99 baseline (best-effort)
- R23（doctor）：anti-drift 对账
- S1–S7（验收）：CLI 同步投影、bootstrap 幂等、51 测试不变、白名单负正向、benchmark baseline、disaster recovery、anti-drift 触发
- T1–T8（threat）：free-text scrubber、备份排除、events≡token/、persona salt、oauth 时序 oracle 接受、FTS 删除级联保留、projector 输入信任、GDPR 限制
- 8 RBP closed in brainstorm (see origin: 文档 "Resolve Before Planning" 节)

## Scope Boundaries

- **不变动 5 个 CLI 与 webui 的 JSON 写路径**——仅在每个 `JsonStore.update/insert/...` 与 `checkpoint.update_item/mark_complete` 之后多一行 `projector.flush_for(path)` 函数调用
- **不投影 `campaign-profiles.json` / `schedule-settings.json`**（用户配置，D2 边界）
- **不实现 #2 verify daemon、#4 portfolio risk、#6 GSC/GA4 与 A/B 框架**——本工作只是它们的数据底座
- **不冻结未来事件 schema**——R8/R9 命名空间预留，子值由引入它们的 PR 提交
- **v1 不创建 articles_fts** ——R5 schema slot 预留，wait for consumer
- **不引入新运行时依赖**——`sqlite3` 是 stdlib
- **不上 GDPR/takedown tombstone**（T8 已记录为已知限制）
- **不切换 WebUI 读路径**（R14）
- **不引入 `bp` umbrella CLI**——两个独立 entry point: `bp-events-rebuild` / `bp-events-doctor`（RBP-5）

## Context & Research

### Relevant Code and Patterns

| 关注点 | 文件 | 备注 |
|---|---|---|
| Console scripts | `pyproject.toml` (lines 26–31) | 新增 2 个 entry point 跟现有 `<name>:main` 约定 |
| Checkpoint contract | `src/backlink_publisher/checkpoint.py` | run_id `^\d{8}T\d{6}-[0-9a-f]{8}$`；items[].status ∈ {pending, publishing, succeeded, failed}；`list_incomplete` 限 20 文件 — bootstrap 必须直接 glob |
| Atomic write | `src/backlink_publisher/io_utils.py` (`atomic_write_json`) | JSON 路径用；SQLite 路径用 sqlite 事务，**不**复用 atomic_write_json |
| JsonStore（state files） | `webui_store/base.py` | `_lock` 单进程；wrap 它的 `update/insert/...` 是 projector seam |
| DraftsStore | `webui_store/drafts.py` | 同上 |
| Store paths 与 wiring | `webui_store/__init__.py` | 拿 `~/.config/backlink-publisher/` 默认路径 |
| WebUI save 钩子 | `webui_app/routes/pipeline.py:249`、`webui_app/routes/drafts.py:42/60/82/103/118`、`webui_app/scheduler.py:51/56` | 这些 callsite 在 JsonStore 写后 |
| plan-backlinks payload | `src/backlink_publisher/cli/plan_backlinks.py:707` (`_generate_payload`) | **没有 `anchors` 字段**；锚点在 `links[]` 内 `{url, anchor, kind, required}`；kind ∈ {main_domain, target, extra, category, detail, supporting} |
| 锚点过滤参考 | `src/backlink_publisher/cli/report_anchors.py:80` (`_build_report`) | `[L for L in links if L['kind'] in ('main_domain','target')]` |
| report-anchors 输入 | `src/backlink_publisher/cli/report_anchors.py:378–486` | `--input/-i` + `--from-profile` + stdin fallback；新增 `--from-events` 与之互斥 |
| footprint 输入 | `src/backlink_publisher/cli/footprint.py:30–75` | `--input/-i` + stdin |
| AdapterResult | `src/backlink_publisher/adapters/base.py` | `published_url=""` 当 status=drafted；events 表写入时归一化为 NULL |
| Logger + redactor | `src/backlink_publisher/logger.py:32–82` | `_SENSITIVE_KEYS` casefolded 精确匹配；新 `events_logger` 单例；free-text 用独立 regex scrubber |
| url_utils | `src/backlink_publisher/url_utils.py` | 无 canonicalize_url；`strip_fragment_query` 过激（剥 query）；新增 `canonicalize_url` |
| markdown_utils | `src/backlink_publisher/markdown_utils.py` | 无反推；本计划不需要——直接读 `links[]` |
| publish-backlinks 完成钩子 | `src/backlink_publisher/cli/publish_backlinks.py:245, 360` (`mark_complete`) | CLI 退出前 inline flush 点 |
| Test conftest | `tests/conftest.py` | autouse 双 fixture（patch publish_check_url + 重置 content_fetch 缓存） |
| Silent drop tripwire 模式 | `tests/test_silent_drop_tripwire.py` | input/output/delta/dropped 对账；events projector 必须遵循 |
| 已对齐 plan | `docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md` (R12 JsonStore SQLite slot) | EventStore 是 JsonStore **sibling**，不修改 JsonStore |

### Institutional Learnings

- **`save_config` 静默丢字段**（`docs/solutions/test-failures/inverted-negative-assertion-...`）→ EventStore 用**正向 round-trip burn 测试**（`load(save(fixture)) == fixture`）；不允许 negative-shape "不含 X" 类断言。
- **`language_matches()` always-True 静默 gate**（`docs/solutions/logic-errors/language-matches-always-true-...`）→ `bp-events-doctor` 必须断言**具体正向不变量**（行数等、max(ts) ≥ 源 mtime 等），不能退化为 `isinstance(warnings, list)` 类 shape-only 断言。
- **CI 隔离 + 注入 sleep + 在 dispatch 层 mock**（`docs/solutions/test-failures/ci-test-isolation-...`）→ 任何 retry/backoff 路径走 `sleep_fn=time.sleep` 注入；mock 在 dispatch 边界。
- **Plan-time multi-persona document-review**（`docs/solutions/best-practices/document-review-catches-...`）→ 本 plan 落地后调用 `compound-engineering:document-review` 一次（Phase 5.3.8）。
- **WebUI blocking subprocess**（`docs/solutions/ui-bugs/webui-blocking-subprocess-...`）→ projector inline `flush()` 在 webui request handler 内必须 **<10ms**；超出移到 APScheduler 单 worker 线程。

### External References

跳过外部研究——SQLite 是 stdlib；FTS5 v1 默认关；patterns 完整。

## Key Technical Decisions

- **EventStore 与 JsonStore 平级**：不复用 `JsonStore` 子类化；新建 `src/backlink_publisher/events/store.py:EventStore`。理由：JsonStore 契约是 dict / list 双面，SQLite 是 schema-bound；强行复用会让两侧抽象都模糊。但 projector 通过 wrap `JsonStore` 的 write helpers 来获取触发点，所以 JsonStore 仍是变化检测的入口。
- **Projector 触发点用装饰器/middleware，不在每个 callsite 手贴**：在 `JsonStore.__init__` 注册"write-then-flush"包装器，而非要求 webui routes / scheduler 各处 import + call。理由：避免 grep-and-replace 散落 → drift；统一在 store 抽象层调用一次。
- **Datetime 双列**：每个事件 + 每条 articles 行存 `ts_raw` (原文，可能是 ISO UTC 或 `YYYY-MM-DD HH:MM` 本地) + `ts_utc` (规范化 UTC ISO)。理由：保留 round-trip 还原；查询走 `ts_utc`。
- **published_url="" 归一化为 NULL**：AdapterResult 在 drafted 态写空字符串；事件层把 falsy 值在 normalize step 转 NULL。理由：避免 dup-detect/查询写 `WHERE published_url != ''` 这种脆弱条件。
- **Anchors 来源 = `payload.links[]` 过滤 kind ∈ {main_domain, target}**：与 `report_anchors._build_report` 一致。`anchors_json` 列存为 `[{url, anchor, kind, required}]` JSON。Markdown 反推**仅 fallback**，仅当 history-only 行无 payload 时（极少数）。
- **URL 规范化**：新增 `url_utils.canonicalize_url(url)`：lowercase scheme + host、剥末尾斜杠、统一默认端口、剥 `utm_*` query 参数（其他 query 保留）。**不**复用 `strip_fragment_query`（它把整个 query 一起剥）。
- **R10 enum 实现**：`events/kinds.py` 用 `typing.Literal[...]`；CI 单测扫 events 表 `SELECT DISTINCT kind` 与枚举集合做差集——零容忍。
- **白名单序列化**：`events/schemas.py` per-kind dict（`{"publish.confirmed": {"target_url", "host", "live_url", "article_id", "run_id", ...}, ...}`）；`EventStore.append(kind, payload)` 在 JSON encode 前做 dict pruning，**未在白名单的字段静默丢弃 + WARN 日志 + counter**。
- **Free-text scrubber 独立于 `_SENSITIVE_KEYS` + 覆盖面扩到所有 string-typed columns**（security-lens SEC-1/SEC-2）：现有 logger redactor 走精确 key 匹配（注释明令禁 substring）；body / error / message / **以及 events 表的所有 string-typed 结构化列（target_url, host, live_url）+ articles.body** 内嵌 secret 都需 regex 集（OAuth Bearer、`eyJ` JWT、`AIza` Google API key、basic-auth URL、高熵 ≥32 chars）扫一遍。放 `events/scrubber.py`，给 `EventStore.append` + `EventStore.add_article` 在入库前调用。U4 reducer 必须把 scrubber 作为入库前最后一道闸门——payload pruning 之后、INSERT 之前。
- **Persona salt**：`~/.config/backlink-publisher/persona.salt`（32 字节随机）首次创建；helper `events/persona.py:persona_id(provider, account_label)`；events.db 损坏重建 **不**触发 salt 重生（保持 ID 稳定）。
- **Bootstrap 分批策略**：`bp-events-rebuild` 按 `run_id` 分批，每批一事务；失败仅回滚当前批；quarantine 跨批累加到 `events.db.quarantine_log` 表。
- **Doctor invariants**（具体正向断言，对抗"degenerate gate"陷阱；threshold 按指标语义分开）：
  - I1 完整性: `SELECT COUNT(*) FROM events WHERE kind='publish.confirmed'` ≥ 总 checkpoint done items（去重后）
  - I2 新鲜度: `projection_cursor.updated_at ≥ source mtime - 5s`（每个 source 单独判定；用 cursor 时间戳而非 ts_utc）
  - I3 articles 一致性: 每个 articles.live_url（NOT NULL）都能在 checkpoint.items[].published_url 或 publish-history[].article_urls 中找到 source
  - I4 漂移率 / 量化 drift: **drift ratio = missing_events / total_expected ≤ 0.5%**（对齐 origin R23 "drift > 0.5% exit non-zero"，**严格**——missing 是真问题）
  - I5 quarantine 量化阈值: **quarantine_log / events 总数 ≤ 5%**（对齐 RBP-7 quarantine 语义，**宽松**——quarantine 是粒度 noise）
- **WAL 副文件 0600**：sqlite3 默认 WAL 副文件继承主文件权限——验证；如未继承则手动 chmod。
- **Backup-exclude xattr 覆盖面**（security-lens 收紧）：macOS 首次创建时不仅给 `events.db` 设 `com.apple.metadata:com_apple_backup_excludeItem`，同时给 `events.db-wal`、`events.db-shm`（创建后）、`persona.salt`、整个 `token/` 目录都设。Linux 无对应 op。WAL/SHM 文件 lazy create，xattr 在首次 commit/checkpoint 触发后立即 set。
- **Doctor invariant 2 时钟比较**（adversarial 修正）：不比较 `events.ts_utc`（来自 checkpoint.started_at，发布开始时间）vs `source mtime`（最后写时间）——这两个钟差可达小时。改为：比较 **per-source `projection_cursor.updated_at`** vs `source mtime` — 容忍 5s（projector 投影后 cursor 立即更新）。
- **U4 dedup 必须覆盖 url 摆动**（adversarial 修正）：当一个 item 经历 succeeded(url=A) → 重置 → succeeded(url=B) 时（合法跨平台重发），二者都应被记录为不同的 `publish.confirmed` 事件 + 不同 articles 行。Dedup 仅以 `(canonicalize_url(host), canonicalize_url(live_url))` 为键——不同 live_url 不应被吃掉。U4 测试必须覆盖此场景。

## Open Questions

### Resolved During Planning

- **Projector 触发位置**：wrap `JsonStore` write helpers + `checkpoint.update_item` / `mark_complete` 调用点（不在 webui route handlers 散落 import + call）。
- **`run_id` 类型**：保留 checkpoint.py 现有字符串格式 `^\d{8}T\d{6}-[0-9a-f]{8}$`，events.run_id 直接引用，不重新编码。
- **history.created_at 解析**：`%Y-%m-%d %H:%M` 本地，假设运营者机器 TZ；UTC 转换用 `datetime.now().astimezone()`。
- **draft-queue 状态字面值**：`drafted` / `scheduled` / `pending` / `published` / `failed`（来自 `webui_app/routes/drafts.py`）；不是 `"draft"`。
- **`published_url=""` 处理**：events 表 INSERT 前 normalize 为 NULL。
- **Anchors 来源**：`payload.links[]` 过滤 kind ∈ {main_domain, target}；不需要 markdown 反推。
- **R23 `bp-events-doctor` 算什么算"漂移"**：见 "Doctor invariants" 决定（4 条具体正向断言）。

### Deferred to Implementation

- [Affects U1] `events` 表是否需要 `causation_id` / `correlation_id` 串联多步事件？倾向"用 `run_id` + `article_id` 已够"；如发现不够再加。
- [Affects U4] projector 处理 `checkpoint.update_item` 中"reset all optional fields to None" 行为时如何避免把"已知 succeeded → 未知（None）→ 重新 succeeded" 投影成两条 publish.confirmed？实现时验证 dedup 键。
- [Affects U6] regex scrubber 的 high-entropy 阈值（32 chars + alphanumeric ratio？）需实测调参；起始 entropy threshold 设 4.5（per-char Shannon），false-positive 率超 5% 则放宽。
- [Affects U8] R16 quarantine 阈值（5%）的合理性需 100K benchmark 后 calibrate。
- [Affects U10] `--from-events` 输出格式必须与 stdin/--input 输出 **字节相等**（S3 要求 51 测试不修改通过）—— 实现时考虑 reuse 同一 formatter 函数。

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```
src/backlink_publisher/events/
├── __init__.py          # public API: get_store(), flush_for(path)
├── store.py             # EventStore: connect(WAL, busy_timeout=5000), append(kind, payload), query helpers
├── schema.py            # CREATE TABLE events / articles / projection_cursor / quarantine_log / schema_version
├── kinds.py             # EventKind = Literal["publish.intent", "publish.confirmed", ...]; KIND_TO_FIELDS whitelist
├── schemas.py           # WHITELIST_PER_KIND: dict[EventKind, frozenset[str]]
├── projector.py         # Projector: reads JSON state, computes diff against projection_cursor, emits events
├── scrubber.py          # SECRET_PATTERNS regex list; scrub_text(s) -> (cleaned, hit_counts)
└── persona.py           # persona_id(provider, account_label) -> str; bootstrap_salt() at import

src/backlink_publisher/cli/
├── events_rebuild.py    # bp-events-rebuild: per-run_id batch, quarantine cross-batch
└── events_doctor.py     # bp-events-doctor: 4 invariants, exit non-zero on drift

webui_store/base.py      # MODIFY: wrap update/insert/delete to call events.flush_for(self.path) after success
src/backlink_publisher/checkpoint.py  # MODIFY: same — wrap update_item / mark_complete
src/backlink_publisher/cli/report_anchors.py  # MODIFY: add --from-events flag, mutex with --input / --from-profile
src/backlink_publisher/cli/footprint.py        # MODIFY: add --from-events flag
src/backlink_publisher/url_utils.py            # MODIFY: add canonicalize_url(url) -> str
pyproject.toml           # MODIFY: add [project.scripts] bp-events-rebuild / bp-events-doctor
```

```
事件投影管线（inline flush 模型）
─────────────────────────────────
CLI 路径:
  publish-backlinks → checkpoint.update_item(...)  ── wrap ──▶ projector.flush_for(checkpoint_path)
                   → checkpoint.mark_complete()    ── wrap ──▶ projector.flush_for(checkpoint_path)
                                                                            │
                                                                            ▼
                                                     SELECT projection_cursor WHERE source = checkpoint_path
                                                     LOAD checkpoint JSON
                                                     DIFF against cursor.last_seen_state_json
                                                     EMIT events + articles (in single tx)
                                                     UPDATE projection_cursor

WebUI 路径:
  POST /publish → history_store.update(...)        ── wrap ──▶ projector.flush_for(history_path)
  POST /drafts  → drafts_store.update_item(...)    ── wrap ──▶ projector.flush_for(drafts_path)
  APScheduler   → 同上（共享 store 实例）

Bootstrap 路径:
  bp-events-rebuild → glob(~/.cache/.../checkpoints/*.json) PER-RUN BATCH
                   → 每批 BEGIN; emit events; UPDATE cursor; COMMIT
                   → 失败 ROLLBACK + 写 quarantine_log
                   → 继续下一 run_id

Doctor 路径:
  bp-events-doctor → 4 invariants → exit 0/1 + 具体 (run_id, target_url, kind) 报告
```

## Implementation Units

```mermaid
flowchart TB
  U1["U1: Schema + EventStore + WAL"]
  U2["U2: Kind enum + whitelist serializer"]
  U3["U3: url_utils.canonicalize_url"]
  U4["U4: Projector + Projection Rules"]
  U5["U5: Inline flush seam in JsonStore + checkpoint"]
  U6["U6: Free-text scrubber + persona salt"]
  U7["U7: bp-events-rebuild CLI"]
  U8["U8: bp-events-doctor CLI"]
  U9["U9: --from-events on report-anchors + footprint"]
  U10["U10: Threat-model ops + macOS backup-exclude + docs"]

  U1 --> U2
  U2 --> U4
  U3 --> U4
  U4 --> U5
  U4 --> U7
  U6 --> U4
  U5 --> U8
  U7 --> U8
  U4 --> U9
  U1 --> U10
```

### Phase 1 — Foundation（U1 → U2 → U3 → U6 → U4）

- [ ] **U1: SQLite schema + EventStore + WAL**

**Goal:** 建立 events.db 持久化骨架与连接管理。

**Requirements:** R1, R2, R3, R4, R5, R20

**Dependencies:** None

**Files:**
- Create: `src/backlink_publisher/events/__init__.py`
- Create: `src/backlink_publisher/events/store.py`
- Create: `src/backlink_publisher/events/schema.py`
- Test: `tests/test_events_store.py`
- Test: `tests/test_events_schema.py`

**Approach:**
- `events.db` 默认路径 `~/.config/backlink-publisher/events.db`；从 `config._config_dir()` 拿（与 token/ 同目录）
- 连接打开时设 `PRAGMA journal_mode=WAL` + `PRAGMA synchronous=NORMAL` + `PRAGMA busy_timeout=5000` + `PRAGMA foreign_keys=ON`
- Tables（schema_version=1）：
  - `events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_raw TEXT NOT NULL, ts_utc TEXT NOT NULL, run_id TEXT, kind TEXT NOT NULL, target_url TEXT, host TEXT, article_id INTEGER, payload_json TEXT NOT NULL)`，索引 `(kind, ts_utc)`、`(host, kind)`、`(article_id, kind)`
  - `articles(article_id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT, anchors_json TEXT NOT NULL DEFAULT '[]', target_urls_json TEXT NOT NULL DEFAULT '[]', lang TEXT, host TEXT, live_url TEXT UNIQUE, published_at_raw TEXT, published_at_utc TEXT, run_id TEXT)`，索引 `(host, published_at_utc)`、`(run_id)`
  - `projection_cursor(source TEXT PRIMARY KEY, last_flush_status TEXT NOT NULL DEFAULT 'unknown', last_attempt_at TEXT, last_success_at TEXT, last_error TEXT)` —— **不存 `last_seen_state_json` 或 mtime/checksum**（RBP-4 关闭 diff-against-cursor 方案）；仅作 flush 失败 sentinel 用于 CLI 启动时扫描重试。dedup 依赖 R17 自然键 + `articles.live_url UNIQUE` + `INSERT OR IGNORE`。
  - `quarantine_log(id INTEGER PRIMARY KEY, ts_utc TEXT, source TEXT, run_id TEXT, reason TEXT, raw_payload_json TEXT)`
  - `schema_version(version INTEGER PRIMARY KEY)` 初始插入 `(1)`
- FTS5 表 **不创建**（RBP-2）；`schema.py` 留 `def maybe_create_fts5(conn)` 函数但 v1 不调用
- `EventStore` 暴露：`connect()`（返回上下文管理器）/ `append(kind, payload, *, run_id, target_url, host, article_id) -> int`（事件 id）/ `add_article(article_dict) -> int` / `query(sql, params)`（薄包装，仅供 CLI 用）
- WAL 副文件权限：sqlite3 默认继承；测试断言 `.db-wal` / `.db-shm` 创建后 mode == 0o600
- macOS backup-exclude xattr 在首次创建时尝试 set（subprocess.run(["xattr", ...])）；Linux skip；failure WARN 不抛

**Execution note:** Start with a failing test that round-trips a single event INSERT/SELECT to lock in column types.

**Patterns to follow:**
- `src/backlink_publisher/checkpoint.py` 的 `_atomic_write` / 文件 mode 0600 / 目录 0700 设定（事件 DB 文件也用 0600）
- `tests/test_silent_drop_tripwire.py` 的 input/output 对账风格——append → SELECT round-trip 测试要列出每个字段

**Test scenarios:**
- Happy path: `append(kind='publish.intent', payload={'target_url': 'https://x.com/a'}, run_id='20260518T120000-abcd1234', target_url='https://x.com/a', host='x.com')` → SELECT 该事件，字段全部回来
- Happy path: `add_article({...})` → SELECT articles 行，body/anchors_json/live_url 全部回来
- Edge case: append 一个 articles，再 append 一个相同 live_url 的 article → UNIQUE 约束触发 IntegrityError（caller 应捕获并走 dedup 路径）
- Edge case: 并发两个进程同时 append——busy_timeout 起作用，二者最终都成功（pytest-timeout 限 10s，注入 `connect_fn` 兜底）
- Edge case: payload_json = 1.5 MB（超 R16 1MB 阈值上限但 store 层本身不限）→ 仍能 INSERT 并 SELECT 回来（限大小是 projector 层职责，不是 store）
- Edge case: 删 events.db 后调 `connect()` → 自动重建 schema，schema_version 仍为 1
- Edge case: schema_version=0（人工 DB）→ `connect()` 升级到 1 或抛 `IncompatibleSchemaError`（明确选一种；本 plan 选**自动升级**因为 v1 是初版）
- Error path: `connect_fn` 抛 `sqlite3.OperationalError("disk I/O error")` → `EventStore.append` 重试至多 3 次后抛同名异常（注入 retry_count, sleep_fn）
- Integration: `append` + `add_article` 在同一事务内（用 `with store.connect() as conn:`），一方失败两方回滚

**Verification:**
- `events.db` 在 `~/.config/backlink-publisher/` 下创建，文件 mode 0o600，目录 0o700
- `sqlite3 events.db '.schema'` 输出 4 张业务表 + schema_version 表
- WAL 模式启用：`PRAGMA journal_mode` 返回 `wal`
- 不依赖外部包；`grep "import sqlite3" pyproject.toml` 无 dependency 改动

---

- [ ] **U2: Kind enum + whitelist serializer + CI gate**

**Goal:** R10 + R19 + D7 + D8 落地：枚举集中声明、白名单 schema 同文件、pre-serialization dict pruning、CI gate 检查无未声明 kind。

**Requirements:** R6, R7, R8, R9, R10, R19

**Dependencies:** U1

**Files:**
- Create: `src/backlink_publisher/events/kinds.py`
- Create: `src/backlink_publisher/events/schemas.py`
- Modify: `src/backlink_publisher/events/store.py`（在 `append` 调白名单序列化）
- Test: `tests/test_events_kinds.py`
- Test: `tests/test_events_whitelist.py`

**Approach:**
- `kinds.py`:
  ```
  EventKind = Literal["publish.intent", "publish.confirmed", "publish.failed", "draft.created", "draft.scheduled"]
  ALL_KINDS: frozenset[str] = frozenset(get_args(EventKind))
  ```
  - 未来 PR 新增 kind = 同 PR 改这个枚举 + 改 schemas.py 白名单 + 加单测
- `schemas.py`:
  ```
  WHITELIST_PER_KIND: dict[str, frozenset[str]] = {
      "publish.intent": frozenset({"target_url", "host", "run_id", "article_id"}),
      "publish.confirmed": frozenset({"target_url", "host", "live_url", "run_id", "article_id", "adapter", "platform"}),
      "publish.failed": frozenset({"target_url", "host", "run_id", "article_id", "error_class", "error_message_clean"}),
      "draft.created": frozenset({"target_url", "platform", "draft_id", "language"}),
      "draft.scheduled": frozenset({"target_url", "platform", "draft_id", "scheduled_at_utc"}),
  }
  ```
  - `error_message_clean` = scrubber 输出（U6）
- `EventStore.append(kind, payload, **structured)`:
  - 断言 `kind in ALL_KINDS`，否则 raise `UnknownKindError`
  - `allowed = WHITELIST_PER_KIND[kind]`
  - `pruned = {k: v for k, v in payload.items() if k in allowed}`
  - `dropped = set(payload) - allowed`
  - 若 dropped 非空：`events_logger.warn("dropped fields", kind=kind, dropped=sorted(dropped))` + counter +1
  - `payload_json = json.dumps(pruned, sort_keys=True, ensure_ascii=False)`
- CI 单测扫 `SELECT DISTINCT kind FROM events` 与 `ALL_KINDS` 取差集，非空 fail

**Patterns to follow:**
- `src/backlink_publisher/logger.py:32-44` 的 `_SENSITIVE_KEYS = frozenset({...})` 风格

**Test scenarios:**
- Happy path: `append("publish.confirmed", {"target_url": "x", "host": "x", "live_url": "x", "run_id": "x", "article_id": 1, "adapter": "blogger-api", "platform": "blogger"})` → 全部字段保留
- Negative (token leak): `append("publish.confirmed", {..., "access_token": "ya29.xxxx"})` → SELECT payload_json 不含 `access_token` 字符串；WARN 日志 contain "dropped fields"；counter +1
- Negative (allowed-shape secret): `append("publish.failed", {..., "error_message_clean": "ya29.SECRET token leaked here"})` → 当前白名单允许该字段；U6 scrubber 必须挡住值。**本单测仅验证白名单不拦字段名**，scrubber 测试在 U6 写。
- Positive (validate allowed field NOT silently dropped): `append("publish.confirmed", {"target_url": "x", "host": "x", "live_url": "x"})` → 部分允许字段缺失时不报错；SELECT 回来 `target_url == "x"`（**对抗 R19 对称失败模式：白名单遗漏 vs 字段缺失要可区分**）
- Edge case: `append("publish.unknown_subkind", {...})` → `UnknownKindError`，无任何 DB 写入
- Integration: `tests/test_events_kinds_ci_gate.py` 跑一次 `db.execute("SELECT DISTINCT kind FROM events")`，与 `ALL_KINDS` 比对——专为 CI 落地

**Verification:**
- `pytest tests/test_events_whitelist.py -v` 全过，覆盖三个 kind 的字段 prune
- CI 单测 fail 当且仅当有人手动 INSERT 一个非枚举 kind 进去（人工测试：临时 `db.execute("INSERT INTO events ... kind='publish.evil' ...")` 然后跑 CI gate 测试）

---

- [ ] **U3: url_utils.canonicalize_url**

**Goal:** R17 URL 规范化为去重键提供稳定基础。

**Requirements:** R17

**Dependencies:** None（独立）

**Files:**
- Modify: `src/backlink_publisher/url_utils.py`（新增 `canonicalize_url`）
- Test: `tests/test_url_utils_canonicalize.py`

**Approach:**
- `canonicalize_url(url: str) -> str`:
  1. parse via `urllib.parse.urlsplit`
  2. lowercase `scheme` + `netloc`
  3. 默认端口剥除（`:80` for http，`:443` for https）
  4. 路径剥末尾斜杠（保留 `/` 根路径）
  5. query 解析：剥所有 `utm_*` 参数；其他 query 按 key 排序后 reassemble
  6. fragment 完全剥除
  7. `urlunsplit` 返回
- `strip_fragment_query` 不动；`canonicalize_url` 是平行 utility

**Patterns to follow:**
- `src/backlink_publisher/url_utils.py:_normalize_host_for_compare` 的 lowercase 风格

**Test scenarios:**
- Happy: `https://X.COM/Path/` → `https://x.com/Path`
- Happy: `http://x.com:80/a` → `http://x.com/a`
- Happy: `https://x.com:443/a?utm_source=x&id=5` → `https://x.com/a?id=5`
- Happy: `https://x.com/a#frag` → `https://x.com/a`
- Edge: `https://x.com/` → `https://x.com/`（根 `/` 保留）
- Edge: 空 query 全 utm_*: `https://x.com/a?utm_source=x` → `https://x.com/a`（query 空字符串而非 `?`）
- Edge: query 重复键: `https://x.com/a?b=1&b=2` → 保留两个（key 排序但保留重复值的顺序）
- Edge: 非 http(s) scheme `mailto:x@y.com` → 透传（仅 http(s) 走规范化）
- Edge: 国际化域名 `https://中文.com/a` → 不做 IDN/punycode 转换（避免引入额外依赖）；底层 urllib 已处理。验证 round-trip 一致

**Verification:**
- 100% 分支覆盖；hypothesis 测试 100 随机 URL 走两遍 `canonicalize_url` 等幂

---

- [ ] **U6: Free-text scrubber + persona salt**

**Goal:** T1 free-text 字段 secret 擦除；T4 persona ID 加盐不可逆。

**Requirements:** T1, T4

**Dependencies:** U1

**Files:**
- Create: `src/backlink_publisher/events/scrubber.py`
- Create: `src/backlink_publisher/events/persona.py`
- Test: `tests/test_events_scrubber.py`
- Test: `tests/test_events_persona.py`

**Approach:**
- `scrubber.py`:
  - `SECRET_PATTERNS = [...]` 列出 named regex（patterns 起步集合）：
    - OAuth Bearer: `r"\bBearer\s+[A-Za-z0-9._\-]+"`
    - JWT: `r"\beyJ[A-Za-z0-9._\-]+"`
    - Google API key: `r"\bAIza[0-9A-Za-z\-_]{35}\b"`
    - Medium integration token: `r"\b[0-9a-f]{64}\b"`（medium internal token shape，候选）
    - Basic-auth URL: `r"https?://[^:/\s]+:[^@/\s]+@[^\s]+"`
    - High-entropy ≥32 chars: 后置 entropy 检测（不是单纯 regex）
  - `scrub_text(s: str) -> tuple[str, dict[str, int]]`：返回（清理后字符串、{pattern_name: hit_count}）
  - High-entropy check 用 Shannon entropy ≥ 4.5/char 阈值（U6 deferred 项允许调参）
- `persona.py`:
  - `_SALT_PATH = config._config_dir() / "persona.salt"`
  - `_bootstrap_salt() -> bytes`: 文件不存在则 `os.urandom(32)` 写入（mode 0600）；存在则读取
  - `persona_id(provider: str, account_label: str) -> str`: `hashlib.sha256(salt + provider.encode() + account_label.encode()).hexdigest()[:16]`
  - salt 不进 events.db；events.db 损坏重建 salt **不**重生（D5 配套）

**Patterns to follow:**
- `src/backlink_publisher/logger.py:54-82` 的 redactor 结构（独立函数 + 单测列表）

**Test scenarios:**
- Happy: `scrub_text("error: invalid_grant; token=ya29.SECRET123")` → 含 `<REDACTED>`、hit_counts['google_api_key'] >= 0（pattern 取决于具体识别）
- Happy: `scrub_text("Bearer abc123def456")` → 替换；hit_counts['oauth_bearer'] == 1
- Happy: `scrub_text("https://user:pass@example.com/")` → basic-auth pattern 触发
- Happy (JWT): `scrub_text("token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc")` → 替换
- Negative (false positive guard): `scrub_text("the cat sat on the mat")` → 无替换；hit_counts 全 0
- Negative (allowed prefix): `scrub_text("This is example AIza but not a real key")` → AIza prefix 但长度不够 35 chars，不替换
- Edge (high-entropy 32 chars): `scrub_text("data: " + "a1b2c3d4" * 4)` → entropy 较低（重复模式），**不**触发；但 `secrets.token_urlsafe(32)` 输出 → 触发
- Persona happy: `persona_id("blogger", "alice@x.com")` 第二次调返回同一 16 hex；不同 (provider, label) → 不同 ID
- Persona security: 无 salt 文件时 → `_bootstrap_salt()` 创建 salt 文件 mode 0600；之后 `persona_id` 与之前 hash 同（验证 salt 持久）
- Persona security: 删 events.db 重启 → salt 文件仍在，persona_id 不变
- Integration: scrubber 与 U2 集成——`append("publish.failed", {..., "error_message_clean": scrub_text(raw_error)[0]})` 完整跑

**Verification:**
- 已知 secret patterns 6 类各 1 个测试；负向 5 个测试覆盖常见误报
- `~/.config/backlink-publisher/persona.salt` 文件存在，32 字节，mode 0600

---

- [ ] **U4: Projector + Projection Rules**

**Goal:** 投影规则机制：读 JSON 源、diff against cursor、emit events + articles、更新 cursor，原子事务。

**Requirements:** R6, R7, R11, R12, R13（数据准备）, R16, R17, R23

**Dependencies:** U1, U2, U3, U6

**Files:**
- Create: `src/backlink_publisher/events/projector.py`
- Modify: `src/backlink_publisher/events/__init__.py`（暴露 `flush_for(path)` 公开 API）
- Test: `tests/test_events_projector_checkpoints.py`
- Test: `tests/test_events_projector_history.py`
- Test: `tests/test_events_projector_drafts.py`
- Test: `tests/test_events_projector_idempotency.py`

**Approach:**
- `Projector` 接收 `EventStore` 实例（依赖注入便于测试）
- `flush_for(path: Path)` 入口：检测 path 是 checkpoint / history / drafts 中哪种，dispatch 到对应 reducer
  - 三个 reducer：`_project_checkpoint(path)`、`_project_history(path)`、`_project_drafts(path)`
- 每个 reducer 流程：
  1. `BEGIN`
  2. `SELECT * FROM projection_cursor WHERE source = ?`（拿 `last_seen_state_json`）
  3. `read JSON file`（公平处理 publish-history 非原子写：`json.loads` 失败 → 重试至多 5 次间隔 100ms；仍失败跳过本周期且 WARN）
  4. compute diff: 新增 items / status 改变 items / 删除 items（删除一般不投影，但要记录在 cursor 中以免重复）
  5. for each diff item: emit event(s) + article row（如 publish.confirmed）
  6. `UPDATE projection_cursor SET last_seen_state_json = ?, last_mtime = ?, last_checksum = ? WHERE source = ?`
  7. `COMMIT`
- **Per-kind dedup keys**（R17，写入时硬编码到 reducer）:
  - `publish.confirmed` + `articles`: `(canonicalize_url(host), canonicalize_url(live_url))` → 命中 `articles.live_url UNIQUE` 时 INSERT OR IGNORE
  - `publish.intent` / `publish.failed`: `(run_id, target_url, kind)` → reducer 维护一个 set in-transaction 防止重复 emit
  - `draft.created` / `draft.scheduled`: `(draft_id, kind)` → 同上
- **Anchors 提取**：从 checkpoint 的 `items[i].payload.links[]` 过滤 `kind in ('main_domain', 'target')`，写入 `articles.anchors_json`
- **Body**：来自 `items[i].payload.content_markdown`；缺则 NULL（RBP-3 妥协策略）
- **Datetime 双列**：
  - checkpoint `started_at` → `ts_raw` 原文，`ts_utc` parse
  - history `created_at = 'YYYY-MM-DD HH:MM'` → 本地 TZ 加 UTC offset 转换
  - drafts 同 history
- **published_url 归一化**：`if not published_url: published_url = None` 入 articles 与 publish.confirmed event
- **状态机映射**：见原始 brainstorm 的 Projection Rules 表（drafted/scheduled/pending/published/failed）

**Execution note:** Test-first — write the per-source reducer's "happy path round-trip" test before implementation; then add idempotency + diff tests.

**Patterns to follow:**
- `tests/test_silent_drop_tripwire.py` 的 input/output/delta/dropped 对账风格——每个 reducer 测试断言"输入有 N 个变更 → events 表新增 N 个 row + cursor 更新"
- Anchor 过滤参考 `src/backlink_publisher/cli/report_anchors.py:80` 同款表达式

**Test scenarios:**
- Checkpoint happy (新 run): 写 checkpoint 含 3 个 pending items → `flush_for(checkpoint_path)` → SELECT events: 3 个 `publish.intent`，cursor 更新
- Checkpoint update (pending → succeeded): 同上后 update_item 第一个 → 第二次 flush → events 表增加 1 个 `publish.confirmed`，articles 表 +1 行 body/anchors 齐全
- Checkpoint update (pending → failed): item 1 → failed → flush → events 表 +1 `publish.failed`；payload_json 含 `error_class` + `error_message_clean`（scrubbed）
- History 非原子读 retry: mock `Path.read_text` 抛 `JSONDecodeError` 前 4 次、第 5 次返回有效 JSON → flush 最终成功；retry 计数 = 4
- History dedup vs checkpoint: history 中含 1 个 `live_url='https://x.com/a'` published，checkpoint 中 done item 也指向同一 URL → flush 后只 1 个 publish.confirmed + 1 article row（checkpoints 优先；命中 dedup 跳过 history）
- Drafts state transition: drafts.json 含 `status='drafted'` 1 项 → flush → `draft.created`；状态变 `scheduled` → flush → `draft.scheduled`（旧 `draft.created` 不被 overwrite，append-only）
- Drafts → published transition: drafts item `status='published'` 时 → flush → emit `publish.confirmed` + articles row；NOT emit 新的 draft.* 事件（同 article_id 内 dedup）
- Idempotency: flush 两次同样状态 → events 表行数不增、articles 不增、cursor 不变
- Concurrent flush: 两个 flush_for 调用并发同一 source path → busy_timeout 起作用，串行化，最终一致（pytest-timeout 10s，注入 sleep 兜底）
- Edge (publish_url=''): drafted item payload 含 `published_url=''` → articles.live_url 写入 NULL（不是空字符串）
- Edge (高频 update_item reset to None): `checkpoint.update_item` 把 succeeded 字段重置为 None 再重置回 succeeded → flush 两次后只看到 1 个 publish.confirmed（dedup 起作用）
- Edge (anchors 缺失): payload.links 为空 → anchors_json='[]'；不抛错
- Edge (anchors fallback from markdown): history-only 行无 payload → markdown 反推 `[\[anchor\](url)` 模式；测试 1 段 markdown 抽 2 个 anchor 出来
- Error path: cursor 表损坏 → reducer 抛 `ProjectionError`，事务回滚，不污染 events 表
- Integration: 完整端到端 — 模拟 publish-backlinks 写 checkpoint → projector flush → report-anchors --from-events 跑通（U9 提前依赖测试）

**Verification:**
- `flush_for(checkpoint_path)` 在测试机上单次 < 50ms（实测后写入 baseline，S5 联动）
- 100K 合成事件 bootstrap 跑通无 OOM（U7 配合测试）
- Idempotency 测试不闪退

**Design notes** (added 2026-05-18 post-U1/U2/U3/U6 survey, before U4 implementation):

*Public API surface (added by U4):*

```python
# events/__init__.py — new exports
from .projector import flush_for, ProjectionError, ProjectionResult

# events/projector.py — new module
@dataclass(frozen=True)
class ProjectionResult:
    events_inserted: int
    articles_inserted: int
    skipped_due_to_dedup: int
    cursor_updated: bool

def flush_for(path: Path, *, store: EventStore | None = None) -> ProjectionResult: ...
class ProjectionError(RuntimeError): ...
```

*Internal dispatch:* `flush_for` calls `_detect_source(path) → "checkpoint" | "history" | "drafts"`, then routes to `_project_checkpoint` / `_project_history` / `_project_drafts`. Each reducer runs inside one `with store.connect() as conn:` block — the U1 context manager auto-commits on normal exit and rolls back on exception. `EventStore.append(..., conn=conn)` and `EventStore.add_article(..., conn=conn)` are explicitly designed (per U1 store.py) to share that transaction, so the reducer never half-commits.

*Idempotency, three layers:*

| Layer | Mechanism | Defeats |
|---|---|---|
| Within reducer | `seen_in_tx: set[tuple]` keyed per kind | Same logical change emitted twice in one flush |
| Across flushes | `projection_cursor.last_seen_state_json` diff | Double-flush of identical state inserts 0 events |
| DB physical | `articles.live_url UNIQUE` (U1 schema) | Cross-source dup (history row whose live_url checkpoint already projected) |

The Open Question §Deferred "Affects U4 — reset all optional fields to None" corner case (succeeded → None → succeeded re-emitting `publish.confirmed`) is resolved by the DB-layer UNIQUE — the second `add_article` raises `sqlite3.IntegrityError`, reducer catches it as expected dedup, no second event is appended. Needs an explicit regression test in `test_events_projector_idempotency.py`.

*Per-kind dedup keys (R17, hardcoded in reducers):*

- `publish.confirmed` + `articles` insert: `(canonicalize_url(host), canonicalize_url(live_url))` → catch `IntegrityError` on the article INSERT and skip the event
- `publish.intent` / `publish.failed`: `(run_id, target_url, kind)` held in `seen_in_tx`
- `draft.created` / `draft.scheduled`: `(draft_id, kind)` held in `seen_in_tx`

*Per-source reducer specifics:*

- **Checkpoint** — diff by `item_id`. NEW pending → `publish.intent`. `* → succeeded` → `add_article(live_url=...)` + `publish.confirmed`; IntegrityError = dedup. `* → failed` → `scrub_text(error_message)` before payload (U6 `scrub_text`, not raw). Anchors: filter `payload.links[]` where `kind in ('main_domain', 'target')` (mirror `cli/report_anchors.py:80`). Body: `payload.content_markdown` or NULL (RBP-3 compromise). live_url: `payload.published_url or None` (empty string → NULL).
- **History** — non-atomic writer: wrap `json.loads(path.read_text())` in 5×100ms retry; final failure logs WARN, skips this cycle, leaves cursor unchanged. Diff by append-only index. Cross-source dedup is automatic via `articles.live_url UNIQUE` (history loses to checkpoint).
- **Drafts** — state machine: NEW + status=drafted → `draft.created`; drafted → scheduled → `draft.scheduled` (does NOT re-emit `draft.created`); → published → `publish.confirmed` + article (does NOT emit a fresh `draft.*`).

*Datetime double-column (ts_raw + ts_utc, R3 schema):*

- checkpoint `started_at` is already ISO-with-offset → `ts_raw = original literal`, `ts_utc = parsed.astimezone(UTC).isoformat()`
- history `created_at = 'YYYY-MM-DD HH:MM'` is operator-local → apply local-TZ-to-UTC; tz drift between writes does NOT invalidate historical events (R23 doctor handles drift detection)
- drafts: same as history

*Error taxonomy (in projector.py):*

- `ProjectionError(RuntimeError)`: cursor table corruption, schema mismatch, source dispatch failure. Surfaces to caller, transaction rolls back via U1 ctx manager.
- `json.JSONDecodeError` during JSON read: retried 5×100ms; final failure → WARN + skip cycle + cursor untouched (NOT raised).
- `sqlite3.IntegrityError` on `articles.live_url`: expected dedup signal, swallowed, increments `skipped_due_to_dedup` counter.
- `kinds.UnknownKindError` (from U2): should be unreachable in correct projector code — surface as bug.

*EventStore call sites (sourced from U1 `e23ea1f` store.py):*

- `store.append(kind, payload, *, run_id, target_url, host, article_id, ts_raw, ts_utc, conn) -> int`
- `store.add_article(article: dict, *, conn) -> int` — `article` keys ∈ `{body, anchors_json, target_urls_json, lang, host, live_url, published_at_raw, published_at_utc, run_id}`; unknown keys raise `KeyError` (fail-fast on typo).
- `payload` dict pruning by U2 whitelist happens inside `append` (per U2 commit `6349f58`) — projector must NOT pre-prune.

*Pre-implementation handoff (when U1 ships):*

1. `git worktree add ../bp-events-u4 -b feat/event-substrate-u4 origin/main`
2. `cd ../bp-events-u4 && pip install -e . --quiet` (re-point editable away from whichever worktree owns it; the global `pip install -e` keeps getting hijacked across worktrees — note in §Risks)
3. Re-read U1 `test_events_store.py` for the canonical `EventStore(path=tmp_path / "events.db")` fixture pattern
4. Walk the 4 test files top-down: happy round-trip first per reducer (test-first guard), then idempotency, then edge cases

---

### Phase 2 — Integration（U5 → U7 → U8）

- [ ] **U5: Inline flush seam in JsonStore + checkpoint**

**Goal:** RBP-4 落地——把 `projector.flush_for(path)` 调用挂在 JsonStore 与 checkpoint 的 write helpers 之后，所有 callsite 自动获得投影。

**Requirements:** R11, R12, RBP-4

**Dependencies:** U4

**Files:**
- Modify: `webui_store/base.py`（wrap `_save_*` / `update` / `insert_first` / `delete_item` / `set`）
- Modify: `src/backlink_publisher/checkpoint.py`（wrap `update_item` + `mark_complete` + `create_checkpoint`）
- Modify: `webui_store/drafts.py`（继承 base.py 自动获得；验证测试覆盖）
- Test: `tests/test_events_seam_jsonstore.py`
- Test: `tests/test_events_seam_checkpoint.py`

**Approach:**
- 在 `JsonStore.__init__` 末尾注册 a write hook：每次成功 atomic write 后调 `events.flush_for(self.path)`（lazy import 避免循环）
- 失败处理（投影失败不影响 JSON 写）：`try: events.flush_for(self.path)` `except Exception as e: events_logger.warn("flush failed, will retry on next write", source=str(self.path), error=type(e).__name__)`——投影是 best-effort，下次 write 会重试整段 diff
- checkpoint.update_item / mark_complete：直接在函数末尾加 try/except + flush_for
- **关键性能约束 + contention benchmark**：webui request handler 内的 flush 延迟 p99 < 10ms。**测试必须模拟实际并发**——pytest 起 CLI subprocess（连续 100 次 publish flush）+ webui request 并发触发 + APScheduler 单 worker 同时跑——三方共写 events.db 时仍维持 p99 < 10ms。超标走 APScheduler 单 worker 异步触发
- **flush 失败 sentinel + CLI 启动扫重试**（adversarial adv-002 修复）：projector.flush_for(path) 内部 try/except 包 SQLite ops；成功则 `UPDATE projection_cursor SET last_flush_status='success', last_success_at=now()`；失败则 `UPDATE projection_cursor SET last_flush_status='fail', last_attempt_at=now(), last_error=...`。**CLI 启动钩子**：`bp-events-rebuild` 与 publish-backlinks / validate-backlinks / report-anchors / footprint 入口处加一个轻量"扫描 sentinel 表"步骤：`SELECT source FROM projection_cursor WHERE last_flush_status='fail'` → 对每条调 `flush_for(source)` 重试一次。这把 "等下次写"补成 "等下次任何 CLI 启动"——completed run 也能被重试到。
- 暴露开关：**移除 `BACKLINK_EVENTS_DISABLE`**（scope-guardian + try/except 已经覆盖优雅降级；env 开关是冗余的 audit-bypass 面）

**Patterns to follow:**
- `webui_store/base.py:59-66` 的 atomic_write_json pattern 之后插入 hook
- `src/backlink_publisher/checkpoint.py:119` `update_item` 函数尾部

**Test scenarios:**
- Integration (webui save 触发 flush): `_drafts_store.update_item(id, status='scheduled')` → events 表新增 `draft.scheduled` 事件
- Integration (checkpoint update 触发 flush): `checkpoint.update_item(run_id, item_id, status='succeeded', published_url='x')` → events 表新增 `publish.confirmed` + articles 行
- Integration (flush 失败不影响 JSON write): mock `events.flush_for` 抛 `sqlite3.OperationalError` → JSON 写入仍成功；WARN 日志记录
- Edge (BACKLINK_EVENTS_DISABLE=1): 设环境变量 → JSON 写正常进行，events 表无新增行
- Performance (latency budget): mock 一个 5-item checkpoint update，连续 100 次 → p99 flush 延迟 < 10ms（measure with `time.perf_counter()`）
- Concurrent (webui 同 request 内多次 save): 一次请求触发 history.update + drafts.update + checkpoint.update → 三次 flush 串行执行不冲突，总耗时 < 30ms

**Verification:**
- 跑 `pytest tests/` 全套（57+ 新增 tests）— 51 个老测试不修改通过（S3 硬约束）
- webui 路由 39 个返回相同 status code（S6 风格回归）

---

- [ ] **U7: bp-events-rebuild CLI（bootstrap）**

**Goal:** RBP-7 落地——按 run_id 分批全量回填；从 checkpoint glob、publish-history 100 条、drafts 当前；幂等。

**Requirements:** R16, R17, R18, S2, S6

**Dependencies:** U4

**Files:**
- Create: `src/backlink_publisher/cli/events_rebuild.py`
- Modify: `pyproject.toml`（注册 `bp-events-rebuild = backlink_publisher.cli.events_rebuild:main`）
- Test: `tests/test_cli_events_rebuild.py`
- Test: `tests/test_cli_events_rebuild_idempotency.py`

**Approach:**
- argparse：`--dry-run`（只统计不写）/ `--force`（删 events.db 后重建）/ `--batch-size N`（默认 unlimited but per-run_id）
- 流程：
  1. `events.db` 不存在则 `EventStore.connect()` 建表
  2. `glob(_cache_dir() / "checkpoints" / "*.json")` —— **不用 `checkpoint.list_incomplete`**（限 20）
  3. 排序按 mtime（旧到新）
  4. for each checkpoint file: BEGIN transaction → reducer `_project_checkpoint(path)`（与 U4 同入口）→ COMMIT；失败 → ROLLBACK + INSERT quarantine_log → continue
  5. process `publish-history.json`（100 条）
  6. process `draft-queue.json`
  7. 输出 stats: events/articles 总数 + quarantine 数 + skipped 数
  8. quarantine 比例 > 5% → exit 2（warn but non-fatal；user 决定是否 --force）
- Schema 验证：每条 JSON 用 `_validate_checkpoint_shape(obj)` / `_validate_history_row(obj)` / `_validate_drafts_row(obj)`；失败入 quarantine

**Patterns to follow:**
- `src/backlink_publisher/cli/publish_backlinks.py` 的 argparse + `def main(argv=None)` 风格
- `tests/test_checkpoint.py` 的 tmp_path fixture

**Test scenarios:**
- Happy path: 3 个 checkpoint + 5 条 history + 2 个 drafts → rebuild → events 表 publish.* + draft.* 数量符合预期；exit 0；stats 正确
- Idempotency: 跑 rebuild 两次 → 第二次 events / articles 数量不增；exit 0
- Per-run_id batching: 第二个 checkpoint 故意写一个不合 schema 的 item → 第一个完成正常入库；第二个 ROLLBACK；第三个继续；quarantine_log +1
- Failure mid-batch: mock `EventStore.append` 在第二个 checkpoint 第三个 item 抛 OperationalError → 该批次 ROLLBACK，前面的 checkpoint 保留；exit 1
- `--force`: 跑 `bp-events-rebuild --force` → 删 events.db 重建；events 数量 = 源数据（按 R17 dedup）
- `--dry-run`: 跑 → 不创建 events.db；输出 stats 准确
- Quarantine threshold: 故意制造 6% 不合 schema 数据 → exit 2 + WARN
- Edge (无 checkpoints 但有 history): publish-history.json 5 条 published → 5 个 publish.confirmed + 5 articles 行（body/anchors 可能 NULL/markdown-derived per RBP-3）
- Edge (checkpoint 但 file 损坏): 一个 checkpoint 是非合 JSON → quarantine_log +1，其他正常处理
- Integration: rebuild 后 `bp-events-doctor` 跑通（U8 配合）

**Verification:**
- `python -m backlink_publisher.cli.events_rebuild --dry-run` 在 fresh repo 上 exit 0
- 重跑两次后 `sqlite3 events.db 'SELECT COUNT(*) FROM events'` 数值不增

---

- [ ] **U8: bp-events-doctor CLI（anti-drift）**

**Goal:** R23 + S7 落地——4 条具体正向不变量；drift 触发 exit 1 + 具体 (run_id, target_url, kind) 报告。

**Requirements:** R23, S7

**Dependencies:** U7

**Files:**
- Create: `src/backlink_publisher/cli/events_doctor.py`
- Modify: `pyproject.toml`（注册 `bp-events-doctor = backlink_publisher.cli.events_doctor:main`）
- Test: `tests/test_cli_events_doctor.py`

**Approach:**
- 4 invariants（**对抗 "always-true gate" 教训**）：
  1. **完整性**：`SELECT COUNT(*) FROM events WHERE kind='publish.confirmed'` ≥ checkpoints 中 done items 数量（按 canonicalize_url 去重后）
  2. **新鲜度**：`max(events.ts_utc) ≥ max(JSON source mtime) - 5s`（容忍 inline flush 调度抖动）
  3. **articles 一致性**：每个 `articles.live_url`（NOT NULL）在 checkpoint.items[].published_url 或 publish-history[].article_urls 中可找到来源
  4. **quarantine 比例**：quarantine_log 行数 / events 总数 ≤ 5%
- 报告格式：
  - Exit 0 + "All invariants pass" 当 4 条全过
  - Exit 1 + 具体 `(run_id, target_url, kind)` 列表的 missing/extra 不变量 1 / 不变量 3 时
  - Exit 2 当不变量 2 / 4 触发（degenerated but non-fatal）

**Patterns to follow:**
- `tests/test_silent_drop_tripwire.py` 的逐项断言风格
- `src/backlink_publisher/cli/validate_backlinks.py` 的 main 函数返回 exit code

**Test scenarios:**
- Happy: 健康的 events.db → exit 0 "All invariants pass"
- Drift (missing): 跑 rebuild 后手动 `DELETE FROM events WHERE id=1` → doctor exit 1，输出含丢失的 (run_id, target_url, kind)
- Drift (extra): 手动 INSERT 1 个不在 source 的事件 → doctor invariant 3 fail → exit 1
- Stale: mock `events.ts_utc` 都比 source mtime 旧 6s+ → invariant 2 fail → exit 2
- Quarantine high: quarantine_log = events 总数的 10% → invariant 4 fail → exit 2
- Degenerate (no events but no source either): events.db 空 + 无 checkpoint → exit 0（边界条件 OK）
- **Anti-degenerate gate test**: 写一个 "no events table, no source" 的状况 → doctor 必须明确说 "no data to compare" 而非 silently exit 0（验证不掉入 always-true 陷阱）
- Integration: U5 模拟丢失（mock `events.flush_for` 抛错某次）→ rebuild 后 doctor 报漂移；rebuild --force 后 doctor 全过

**Verification:**
- `bp-events-doctor` 在 fresh + rebuilt repo 上 exit 0 + 输出 "All invariants pass"
- 任何 invariant 失败时输出包含具体 `(run_id, target_url, kind)` 三元组的 actionable hint

---

### Phase 3 — Read consumers + Ops（U9 → U10）

- [ ] **U9: --from-events flag on report-anchors + footprint**

**Goal:** R13 读侧接入；保留现有 stdin / --input / --from-profile 契约 51 测试不变（S3）。

**Requirements:** R13, S3

**Dependencies:** U4

**Files:**
- Modify: `src/backlink_publisher/cli/report_anchors.py`（加 `--from-events`）
- Modify: `src/backlink_publisher/cli/footprint.py`（同）
- Create: `src/backlink_publisher/events/queries.py`（聚合查询薄包装）
- Test: `tests/test_report_anchors_from_events.py`
- Test: `tests/test_footprint_from_events.py`

**Approach:**
- argparse mutex group: `--input` / `--from-profile` / `--from-events`（互斥）；无任何标志走 stdin（保留默认）
- `--from-events` 实现路径：
  - report-anchors：`events.queries.list_anchors_by_host(host=None, since=None, kind=None)` → 与现有 `_build_report` 输入兼容的 list[dict]
  - footprint：`events.queries.iter_publish_confirmed(since=None)` → 与 footprint 现有 JSONL 流兼容
- 输出 formatter **复用现有代码**（同 stdin/--input 路径用同一 `_render_*` 函数）→ 保证字节相等
- 新增 3 个聚合查询测试：
  - by host
  - by time window
  - cross-run_id anchor velocity

**Patterns to follow:**
- `src/backlink_publisher/cli/report_anchors.py:378-486` 现有 argparse mutex 风格（已用 `--from-profile`）

**Test scenarios:**
- Happy (--from-events, no extra filter): events.db 含 5 个 publish.confirmed → 输出同 stdin 路径喂 5 个等价 JSONL 时
- Happy (filter by host): `--from-events --host x.com` → 仅 x.com 记录
- Happy (filter by time): `--from-events --since 7d` → 仅最近 7 天
- Aggregation 1 (by host): events 含 3 个 host × 10 records → `bp-events-rebuild && report-anchors --from-events --group-by host` 输出 3 行汇总（如果该 grouping 存在）
- Aggregation 2 (time window): events 跨 14 天 → `--from-events --since 7d` 仅返回后半周；anchor 总数 = 输入后半周条目数
- Aggregation 3 (anchor velocity): events 含 30 天数据 → 计算"每个 target_url 最近 7 天 vs 之前 7 天的 anchor 数量比"
- Mutex (--input + --from-events): argparse 报错 exit 2
- Regression (existing 51 tests): `pytest tests/test_report_anchors.py tests/test_footprint.py` 通过 0 修改

**Verification:**
- `report-anchors --from-events < /dev/null` 与原有 `report-anchors < payloads.jsonl` 在等效数据下输出字节相等
- 51 个老测试不修改通过

---

- [ ] **U10: Threat-model ops + macOS backup-exclude + docs**

**Goal:** T2 / T3 落地——macOS xattr 标记不备份；README 安全说明；events.db ≡ token/ 文档对等。

**Requirements:** T2, T3, R20

**Dependencies:** U1

**Files:**
- Modify: `src/backlink_publisher/events/store.py`（首次创建 events.db 时 try set xattr）
- Create: `~/.config/backlink-publisher/README.security.md`（首次 events.db 创建时由 store 自动生成）
- Modify: 根 `README.md`（加 "Backup & Security" 小节，提 events.db、token/、persona.salt、WAL 副文件）
- Test: `tests/test_events_backup_hygiene.py`

**Approach:**
- store.py 在 `_first_create_db()` 之后：
  - 写 `~/.config/backlink-publisher/README.security.md` 模板（"以下文件不应进 iCloud/TM/Dropbox 备份：events.db, events.db-wal, events.db-shm, persona.salt, token/, oauth.json"）
  - macOS：`subprocess.run(["xattr", "-w", "com.apple.metadata:com_apple_backup_excludeItem", "bplist00...", str(db_path)], check=False)`（失败 WARN 不抛）
  - Linux：skip
- 根 README 加章节，列出敏感文件清单 + 备份排除建议

**Patterns to follow:**
- `src/backlink_publisher/io_utils.py` 的 chmod 0o600 风格

**Test scenarios:**
- macOS happy: 创建 events.db → xattr 命令被调用（mock subprocess）；README.security.md 文件创建
- macOS failure tolerance: mock subprocess 抛 FileNotFoundError（无 xattr cmd）→ events.db 仍创建成功；WARN 日志
- Linux: 创建 events.db → xattr 调用被跳过；README.security.md 仍创建
- Idempotency: 第二次创建 events.db（已存在）→ xattr 不重设；README 不重写

**Verification:**
- macOS 上：`xattr -l ~/.config/backlink-publisher/events.db` 列出 `com.apple.metadata:com_apple_backup_excludeItem`
- 根 README 含 "Backup & Security" 小节

---

## System-Wide Impact

- **Interaction graph:**
  - `JsonStore.update` → 新增 `events.flush_for(path)` hook（所有继承类自动获得：DraftsStore、HistoryStore）
  - `checkpoint.update_item` / `mark_complete` → 新增 hook（CLI publish 路径）
  - `webui_app/routes/pipeline.py:249` / `drafts.py:42/60/82/103/118` / `scheduler.py:51/56` → 不需要改（hook 在 store 层）
  - `report-anchors` / `footprint` → 新增 `--from-events` 输入路径；现有 stdin / --input / --from-profile 不动
- **Error propagation:**
  - events.flush_for 异常 → WARN + 不影响 JSON write（best-effort）
  - bp-events-rebuild 单批失败 → ROLLBACK 该批 + INSERT quarantine_log + 继续下批
  - bp-events-doctor 漂移 → exit 1/2 + 具体三元组报告
- **State lifecycle risks:**
  - Cursor 漂移：每个 source 的 `last_seen_state_json` 必须包含足以 diff 的 minimal state（item ids + statuses）；过大会膨胀 cursor 表（mitigation: cursor 行限 100KB，超出后退化为 mtime+checksum-only sync 重投）
  - Per-kind dedup 键不能覆盖所有 path → 在 reducer 测试中显式枚举所有状态转换
- **API surface parity:**
  - 51 个老测试不修改通过（S3 硬约束）
  - 5 个现有 CLI entry point 与现有 stdin/--input/--from-profile 输入契约不变
- **Integration coverage:**
  - U5 集成测试覆盖 webui save + checkpoint update 双触发
  - U9 等价输出测试（--from-events vs stdin）确保两路径结果一致
- **Unchanged invariants:**
  - `JsonStore` / `DraftsStore` 公开 API 不变（仅内部 hook 注册）
  - `checkpoint` 模块的所有公开函数签名不变
  - `pyproject.toml` 现有 5 个 entry point 不变；新增 2 个独立 entry，无 umbrella `bp`
  - `~/.config/backlink-publisher/` 现有文件位置与权限不变

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `JsonStore.update` 引入 hook 后 webui 请求延迟超 10ms | Medium | High | U5 性能测试；超标走 APScheduler 单 worker；环境变量 disable |
| events.db schema v1 需要演进（v2、v3） | High | Medium | U1 schema_version 表；rebuild --force 重建路径；plan 显式接受 v1 短命周期 |
| `_SENSITIVE_KEYS` 与 scrubber regex 漏过新 provider token shape | Medium | High | T1 6+ patterns 起步；新 provider 加入时同 PR 加测试；事件 logger WARN 计数器监控 |
| Datetime 解析失败（history 本地格式无 TZ） | High | Low | reducer 抛错 → quarantine_log；fallback 用机器当前 TZ；下游 query 用 ts_utc |
| projector 在 webui APScheduler thread 与 CLI 进程并发写 events.db | Medium | Medium | busy_timeout=5000；3 次指数退避重试；BACKLINK_EVENTS_DISABLE 兜底 |
| 100K events 后 query 慢 | Low | Medium | R21 baseline + 2x regression 守门；索引覆盖 (kind, ts_utc), (host, kind), (article_id, kind) |
| 与 parallel session（refactor/webui-contract-tests）改 `webui_store/base.py` 冲突 | High | Medium | 该 plan 仅在 base.py 末尾注册 hook，最少改动；执行前先 git pull / coordinate |
| `xattr` 命令在某些 macOS 容器（Docker）不存在 | Low | Low | U10 mock 测试；失败 WARN 不抛 |

## Documentation / Operational Notes

- 根 README 加 "Backup & Security" 小节（U10）
- `~/.config/backlink-publisher/README.security.md` 由 U10 自动生成
- CHANGELOG 加 "feat: event substrate v1" 项；列 2 个新 entry point、`--from-events` 新增 flag
- Rollout：events.db 是新文件，无 migration；旧用户首次跑 bp-events-rebuild 后即可
- Monitoring：events_logger WARN 计数（dropped fields / quarantine / scrubber hits）应进 webui 健康面板（后续 PR）

## Phased Delivery

### Phase 1 — Foundation
- U1 (Schema + EventStore)
- U2 (Kind enum + whitelist)
- U3 (canonicalize_url)
- U6 (Scrubber + Persona)
- U4 (Projector + Projection Rules)

里程碑：`tests/test_events_*.py` 全过；events.db 能从测试 fixture 生成 + round-trip。

### Phase 2 — Integration
- U5 (Inline flush seam)
- U7 (bp-events-rebuild)
- U8 (bp-events-doctor)

里程碑：`bp-events-rebuild` 在 staging fixture 上 idempotent；`bp-events-doctor` exit 0。51 个老测试 0 修改通过。

### Phase 3 — Read consumers + Ops
- U9 (--from-events on report-anchors + footprint)
- U10 (Threat-model ops + macOS backup-exclude + docs)

里程碑：3 个新聚合查询测试通过；S3 + S6 + S7 全验收；CHANGELOG / README 更新。

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-18-event-substrate-corpus-requirements.md](docs/brainstorms/2026-05-18-event-substrate-corpus-requirements.md)（main:cbd4c9c，含 D1 reversal + 8 RBP closed + Threat Model T1–T8）
- **Aligned plan:** [docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md](docs/plans/2026-05-18-001-refactor-architecture-health-roadmap-plan.md)（R12 JsonStore SQLite slot — 本 plan 是 sibling 而非修改）
- **Anti-patterns**：
  - `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md`
  - `docs/solutions/logic-errors/language-matches-always-true-no-op-gate-2026-05-14.md`
  - `docs/solutions/test-failures/ci-test-isolation-failures-medium-brave-sleep-timeout-2026-05-13.md`
  - `docs/solutions/best-practices/document-review-catches-runtime-errors-at-plan-time-2026-05-14.md`
  - `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md`
- **Relevant code surfaces**: `src/backlink_publisher/checkpoint.py`, `webui_store/base.py`, `src/backlink_publisher/cli/plan_backlinks.py:707`, `src/backlink_publisher/adapters/base.py`, `src/backlink_publisher/logger.py:32-82`
- **Test pattern**: `tests/test_silent_drop_tripwire.py`
