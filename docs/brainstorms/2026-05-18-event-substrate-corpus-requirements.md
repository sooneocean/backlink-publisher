---
date: 2026-05-18
topic: event-substrate-corpus
---

# Event Substrate + Published Corpus

> 把"分散的 4 个 JSON 状态文件 + per-run checkpoint"投影成一个 append-only 类型化事件流 + 已发布语料库，作为后续 longitudinal health daemon（#2）、portfolio risk engine（#4）、GSC/GA4 闭环与 A/B 框架（#6）的共享**读侧**数据底座。
>
> **关键定位（D1 修订后）：现有 JSON 文件 + checkpoint 仍是写侧真相源（authoritative）；events.db 是它们的派生投影（projection），由 projector 进程从 JSON 写入事件流单向消费。读侧统一到 events.db；写侧不变动。**
>
> 来源：`docs/ideation/2026-05-18-open-ideation.md` #1。

## Problem Frame

今天 `backlink-publisher` 的运行状态散落在 5 处：

- `~/.cache/backlink-publisher/checkpoints/<run_id>.json` —— per-run 发布检查点（输入 + per-item status + live_url + completed_at），CLI 与 WebUI 都写
- `~/.config/backlink-publisher/publish-history.json` —— **仅由 WebUI 路径写入**（`_append_history` 只在 `webui.py` 内调用，且 `history[:100]` 强制截断到最近 100 条）；CLI `publish-backlinks` 直接走 stdout JSONL + checkpoint，不进 history
- `draft-queue.json` / `campaign-profiles.json` / `schedule-settings.json` —— WebUI 状态，位于 `~/.config/backlink-publisher/`
- 各 CLI 的 stdout JSONL + 散落 `logs/` —— 一次性消费即丢
- `link_attr_verifier` 一次性产物，未持久化

这造成五个具体后果：

1. **运营者无法回答简单问题：** "上月 400 条反链还活几条？哪个 host 索引率最低？最近一周哪些锚点用过？" 因为答案要从 5 个数据源拼接，每次现做。
2. **`report-anchors` / `footprint` 当前从 JSONL stdin（或 `--input`）/ anchor profile sidecar 重算**——不是从 plan 文件，但同样得到的只是当批快照，无法跨批/跨周次聚合，也算不了滑动窗口速度或熵下降。下面 R13 必须保留这个 stdin 输入契约。
3. **下游想做的 #2/#4/#6 全部依赖一个共享数据层**——如果各自造一份，会出现"4 份重复的 SQLite/JSONL/files"。
4. **2026-05-18 architecture-health-refactor 的 R3 / R12** 已经明确"`JsonStore` 抽象 + SQLite 实现 slot 预留"，但留白了"slot 里到底装什么"。本文档回答这个问题。
5. **dedup / 跨文章 anchor footprint 检查 / 重发已 decay 链接** 都需要"凡是已发布的，都能被查到正文 + 锚点 + 状态"——目前 `publish-history.json` 不含正文，无法支撑。

目标（修订）：**写侧仍是 JSON 文件（不变动现存 5 个 CLI 与 webui 的 `_load_*`/`_save_*` 调用），读侧统一到 events.db**——所有 query/aggregation/longitudinal/A/B 都走 SQL。一个 projector 进程把 JSON 写事件单向同步到 events.db。运营者从此有"一处可查"的查询面，但不付"重写写路径"的代价。

## Architecture（概念，非实现）

```mermaid
flowchart TB
  subgraph Authoritative["JSON 写侧 &#40;authoritative，不变动&#41;"]
    cp[checkpoints/*.json]
    hist[publish-history.json]
    dq[draft-queue.json]
    cfg[campaign-profiles / schedule-settings]
  end

  subgraph Projector["projector &#40;新组件&#41;"]
    tail[JSON tailer + transformer]
  end

  subgraph Store["events.db &#40;SQLite WAL, 派生&#41;"]
    events[(events table)]
    articles[(articles table)]
    fts[(articles_fts FTS5)]
  end

  subgraph Consumers["读侧消费者"]
    report[report-anchors --from-events]
    foot[footprint --from-events]
    risk[portfolio risk engine &#40;#4&#41;]
    ab[A/B + GSC closed-loop &#40;#6&#41;]
    verify[verify daemon &#40;#2&#41;]
  end

  Authoritative --> Projector
  Projector --> Store
  Store --> Consumers
```

写侧 JSON 文件保持现状；projector 单向投影到 events.db；所有读侧通过 SQL 查询。当 events.db 损坏/丢失，重跑 projector 即可从 JSON 完整重建。

## Requirements

> 使用稳定 ID（R1…），后续 `/ce:plan` 与代码评审都可引用。

**存储层（Storage）**

- R1. 引入单一 `events.db`（SQLite 3.7+ with WAL）作为唯一持久化后端。默认路径 `~/.config/backlink-publisher/events.db`（与 token / oauth.json / 旧 JSON state 同目录，匹配"用户级、需备份"的 XDG 语义；不放 `~/.cache/` 是因为该目录被各类清理工具视为可丢），可通过 `events_db_path` 配置覆盖。
- R2. `events` 表 append-only，字段含义至少包含：单调自增 `id`、UTC 时间戳 `ts`、`run_id`、事件 `kind`（类型字符串）、`target_url`、`host`、`payload_json` （事件特定载荷）；不允许 UPDATE / DELETE 业务事件。
- R3. `articles` 表存"已成功发布的文章正文 + 锚点 + 元数据"：`article_id`（主键）、`body`、`anchors_json`、`target_urls_json`、`lang`、`host`、`live_url`、`published_at`、`run_id`。一个 `publish.confirmed` 事件对应一条 `articles` 记录，二者在同一事务内写入。**`article_id` 在 `publish.intent` 时分配并写入事件；`plan.created` / `validate.*` 阶段不分配 `article_id`，避免计划/校验产物污染**。
- R4. 全部写入通过单一 `EventStore.append(event)` API；事务、WAL、`PRAGMA synchronous=NORMAL`、连接管理收敛在该模块，业务代码不直接接触 sqlite3。**写者仅有两个：(a) projector 进程从 JSON 投影；(b) 未来 #2 verify daemon 直接产生 `verify.*` 事件。** 5 个 CLI 与 WebUI **不直接** 写 events.db。`PRAGMA busy_timeout = 5000`（ms）；遇 `SQLITE_BUSY` 用指数退避重试最多 3 次后抛出。
- R5. 提供 FTS5 索引为可选能力（`articles_fts`），默认启用；若运营者环境 SQLite 编译无 FTS5，模块自动降级为不建 FTS 表并发出一次性 warning。**多语种 body（zh-CN + en + ru）需用 `unicode61` 分词器而非默认 porter；CJK 完美分词不在本 brainstorm 范围，可接受 unicode61 的 N-gram 近似**。

**事件类型最小集合（Event Taxonomy）**

> **v1 实际投影的只有 `publish.*` 与 `draft.*`**；其余命名空间（`verify.*` / `plan.*` / `validate.*` / `throttle.*` / `retry.*` / `oauth.*`）**仅保留为命名空间约定，不冻结具体子事件值**——当 #2 daemon / future PRs 引入它们时，由同一 PR 同时提交枚举值 + 白名单 schema + 测试。

- R6. `publish.*` 三态（v1 投影）：`publish.intent` / `publish.confirmed` / `publish.failed`。`publish.confirmed` 与 `articles` 行写入同一事务。**幂等键按 kind 分**：confirmed 用 `(canonicalized_host, canonicalized_live_url)`；intent / failed 用 `(run_id, target_url, kind)` 自然键（pending / failed item 可能没有 live_url）。
- R7. `draft.*` 两态（v1 投影，从 `draft-queue.json` 派生）：`draft.created`（运营者保存草稿但未排程，对应 webui 状态字面值 `"drafted"`）/ `draft.scheduled`（已排程未发布，对应 `"scheduled"`）。**注意**：webui 实际写的状态字面是 `"drafted"` / `"scheduled"` / `"pending"` / `"published"` / `"failed"`——projector 必须按这些字面值匹配，不是 `"draft"`。`status="published"` / `"failed"` 透出为 `publish.confirmed` / `publish.failed`（已被 R6 覆盖）。draft.* 幂等键：`(draft_id, kind)`。
- R8. **保留命名空间不冻结子值**：`verify.*` / `plan.*` / `validate.*` 在 v1 不投影；R10 枚举不预先列出它们的子值。当 #2 daemon 等落地时，它的 PR 负责把 `verify.live` / `verify.404` / ... 加进枚举与白名单。
- R9. **保留命名空间不冻结子值**：`throttle.*` / `retry.*` / `oauth.*` 同 R8。**唯一硬约束（提前写入 R19 白名单）**：未来引入 `oauth.refreshed` 时，其 `payload_json` 严禁写入 token 内容、refresh_token、access_token——只允许 provider、是否成功、新 token 过期时间戳。
- R10. 事件 `kind` 集合在代码中以枚举/`Literal` 类型集中声明（v1 仅含 `publish.*` 三态 + `draft.*` 两态），**禁止字面字符串散落**。新增 kind 必须同步更新枚举 + R19 白名单 + 至少一条单测。CI 单测检查 `events` 表所有 distinct kind ⊆ 枚举集合。

**Projector（新组件）**

- R11. 引入 `projector` 进程，监听 `checkpoints/*.json` 写入、`publish-history.json` append、`draft-queue.json` 状态变化，转换为 R6–R7 事件 + articles 行写入 `events.db`。**v1 监听机制使用轮询 + mtime/checksum 比对（默认 1s 间隔）**，跨平台一致、易测；inotify/FSEvents 作为 P2 性能优化。每个 JSON 源在 events.db 内有 cursor 表（`projection_cursor(source, last_mtime, last_checksum, last_seen_state_json)`）支持重启幂等。**publish-history.json 非原子写**（webui 直接 `write_text`），projector 读到 `JSONDecodeError` 时重试至多 5 次（间隔 100ms），仍失败则跳过本周期。**承载方式延后到 RBP-4 决定**（候选：webui 进程内 thread / 独立 launchd-systemd daemon / CLI 结束时 inline `projector.flush()`）。**5 个 CLI 与 WebUI 不变动**。
- R23. **Anti-drift 对账**：提供 `bp events doctor` 子命令，扫 JSON 真相源 vs events.db 投影，diff 报警；运行频率由运营者按需触发，CI 集成测试每次跑。漂移 > 阈值（默认 0.5%）时 exit non-zero。S7 验收覆盖。
- R12. projector 与 `2026-05-18-architecture-health-refactor` 的 R3 `JsonStore` 抽象**解耦**：projector 直接读 JSON 文件，不依赖 JsonStore 抽象落地——意味着本 brainstorm 可独立于 architecture-refactor 推进。当 JsonStore 抽象完成后，projector 可改为订阅 JsonStore 的写事件而非文件 watch，但这是后续优化，不是 v1 前置依赖。

**读取层（Consumers + Snapshot Views）**

- R13. `report-anchors` 与 `footprint` 增加 `events.db` 数据源；**保留现有的 JSONL stdin / `--input` / `--from-profile` 输入契约**（51 个测试与下游 pipe 链依赖这些路径，不破坏）。新增的事件源以 `--from-events` flag 显式触发。聚合维度至少支持：按 host、按 run_id、按 target_url、按时间窗、按锚点类型。"按时间窗"指 ad-hoc `WHERE ts BETWEEN` 查询，**不**预计算时序物化视图（那是 #2 daemon 范围）。
- R14. **WebUI 不切换读路径**：`publish-history.json` / `draft-queue.json` 仍由 webui 直接读，与今天一致。`events.db` 仅服务 R13 + R15 的新查询场景与下游 #2/#4/#6 消费者。短期没有"WebUI 走派生视图"的需求；如果未来出现（例如 webui 要展示跨周次历史聚合），再引入 SELECT 路径。`campaign-profiles.json` / `schedule-settings.json` 是用户编辑配置，不进 events.db（D2 边界）。
- R15. 提供 `bp events` 子命令族（最小：`bp events tail` / `bp events query --kind verify.404 --since 7d` / `bp events stats`），让运营者命令行直查。

**初始投影（Bootstrap Projection）**

- R16. projector 启动时（或通过 `bp events rebuild` 显式触发）做一次全量初始投影：扫 `checkpoints/*.json`（CLI 历史主源，因为 publish-history.json 只在 webui 写且 cap 100）+ `publish-history.json`（最近 100 条 webui 历史）+ `draft-queue.json`，生成等效的 `publish.intent` / `publish.confirmed` / `publish.failed` 事件 + `articles` 行写入 `events.db`。读取时做 schema 验证 + 单条记录大小上限（默认 1 MB），不合格记录走 quarantine 日志。整体在单事务内执行；部分失败回滚。
- R17. 投影必须幂等。**幂等键按 kind 区分**（与 R6/R7 一致）：`publish.confirmed` + articles 用 `(canonicalized_host, canonicalized_live_url)`；`publish.intent` / `publish.failed` 用 `(run_id, target_url, kind)`；`draft.*` 用 `(draft_id, kind)`。URL 规范化：小写 host、剥末尾斜杠、统一 scheme、剥 utm_* 参数。重跑（增量或全量）不产生重复事件。当 checkpoints 与 publish-history 对同一 `live_url` 都有 confirmed 记录时，**优先采用 checkpoints**（结构更完整：含 anchors、content_markdown 可推 body）。**anchors_json 源**：从 checkpoint payload 中的结构化 `anchors` 字段直接读取（plan-backlinks 已经写入），不要从 markdown 反推。
- R18. **JSON 文件保持原状不重命名**——它们仍是写侧权威。events.db 损坏或 schema 升级时，删 events.db、重跑 `bp events rebuild` 即可从 JSON 完整重建。这是 D1 反转的核心红利。

**隐私与安全（Privacy）**

- R19. 任何事件载荷不得包含：Blogger / Medium token、cookie、Playwright storage_state、OAuth refresh_token、用户邮箱密码。`EventStore.append` 必须经过 **per-kind allowed-field schema 白名单**，**白名单 schema 与 R10 的 kind 枚举同文件声明**（强制 code review 联动）。序列化策略：**pre-serialization dict pruning**（在 JSON encode 前删除未在白名单内的字段），不允许"post-serialization 正则擦除"等弱方案。被丢弃字段 **以 WARN 级日志 + 计数器埋点上报**（不是 DEBUG），CI 通过扫描生产者调用点强制不引入未声明字段。
- R20. `events.db` 默认权限 `0600`，与现有 `token/`、`oauth.json` 一致；**WAL 副文件 `events.db-wal` / `events.db-shm` 同样 0600**。WebUI 端 `events.db` 不暴露任何只读 HTTP 端点；`bp events` 子命令（R15）**继承文件系统 ACL（0600 即 owner-only），不开网络 / IPC 监听**——这是 events.db 的访问控制边界。**多用户假设**：本工具 local-first single-user；任何作为运营者用户运行的进程被视为可信。

**Threat Model（events.db 新增的攻击面）**

- T1. **Free-text 字段内嵌 secret**：`publish.failed.payload_json.error` 可能含 provider 错误字符串、stack trace、`invalid_grant` 回声等，间接泄露 token 状态。R19 白名单挡字段名不挡值——补充：所有 free-text 字段（`error` / `message` / `url`）在 projector 入库前过一遍正则 scrubber，**覆盖至少**：OAuth Bearer、JWT（`eyJ...`）、Google API key（`AIza...`）、Medium / Blogger token prefix、basic-auth URL（`https://user:pass@host`）、长度 ≥ 32 的高熵字符串。命中替换为 `<REDACTED>` 并触发 WARN + 计数器。S4 必须为每条模式补一个负向单测。
- T2. **events.db 备份/导出泄露**：events.db 含全部 publishing footprint（live URLs + anchors + host map + run timeline）。`bp events dump` 输出**默认 0600**；macOS 首次创建时给 events.db **设置 `com.apple.metadata:com_apple_backup_excludeItem` xattr**；目录中放 `README.security.md` 说明"该文件应排除于 Time Machine / iCloud Drive / Dropbox 自动同步"。不提供加密功能。
- T3. **events.db + token/ 联合泄露 = 账号画像 + 完整接管**：威胁等级**等同**——任何提到 token/ 的备份/安全文档必须同等覆盖 events.db + WAL 副文件 + `bp events dump` 输出物。
- T4. **Persona / Session 引用必须不可逆 + 加盐**：未来 #2/#4/#6 想标识"哪个 persona 发的"，**禁止**把 Playwright storage_state 路径、cookie jar 文件名、账号邮箱写进 payload；必须用 `sha256(salt || provider || account_label)[:16]`。**salt 是 32 字节随机值，存放于 `~/.config/backlink-publisher/persona.salt`（0600）**——攻击者拿到 events.db 但未拿到 salt 文件时无法 preimage 攻击。
- T5. **`oauth.refreshed` 时序 oracle 已知接受**：作为接受残留风险记录；缓解方案"刷新时间戳粒度降到小时桶"在 #2 daemon 阶段评估。
- T6. **FTS5 删除级联**：DELETE FROM articles 必须通过 trigger 同事务 DELETE FROM articles_fts，**不留索引残影**。R5 实现层保证。
- T7. **Projector 输入信任边界（已知残留）**：任何作为运营者运行的进程可写 checkpoint/draft-queue JSON，projector 会照单全收。不增加输入鉴权——与现有 token/ 同等假设（"作为本用户运行的进程已可读 token，不可信代码不应在此环境运行"）。R16 的 schema 验证 + 1MB 单条上限是 payload-bombing 防御，不是鉴权。
- T8. **GDPR / takedown 完整性（已知 gap）**：v1 events 表 append-only，takedown 时 DELETE articles + articles_fts 之后，`publish.intent` / `publish.confirmed` / `publish.failed` 事件仍残留 target_url、host、anchors_json——元数据不可被擦除。该选择是为 D5 重建不变量服务；若未来必须支持 GDPR-grade 删除，需引入 tombstone 操作（碰 append-only 约束）。本 brainstorm 不实现，记录为已知限制。

**性能与容量预期（Performance Envelope）**

- R21. 设计容量目标：100K events + 50K articles（articles.body 平均 1.5KB）= 单库 ≈ 100 MB；预期能在 MacBook 上保持 `EventStore.append` p99 < 5ms、`bp events query` < 200ms。超出此规模的归档策略放 P2。
- R22. WAL checkpoint 自动触发即可；不需要外部 cron。

## Projection Rules（JSON → events.db）

projector 的转换规则约定（实现细节进 plan）：

| JSON 源（写侧权威） | 投影为 |
|---|---|
| `checkpoints/<run_id>.json` 中 `items[].status == "done"` | 一条 `publish.confirmed` 事件 + 一条 `articles` 行（body 由 `payload.content_markdown` 推得） |
| `checkpoints/<run_id>.json` 中 `items[].status == "pending"`（初始全部）| 一条 `publish.intent` 事件 |
| `checkpoints/<run_id>.json` 中 `items[].status == "failed"` | 一条 `publish.failed` 事件（含 error 分类） |
| `publish-history.json` 中 status=`published` 行 | 一条 `publish.confirmed` 事件（去重键命中时跳过；checkpoints 优先） |
| `draft-queue.json` 中 `status="drafted"` | 一条 `draft.created` 事件（**字面值 `"drafted"` 而非 `"draft"`——webui 实际写入的是 `"drafted"`**） |
| `draft-queue.json` 中 `status="scheduled"` | 一条 `draft.scheduled` 事件 |
| `draft-queue.json` 中 `status="published"` / `"failed"` | 透出为 `publish.confirmed` / `publish.failed`（与 checkpoints 路径同 R6 幂等键合并，命中跳过） |
| `draft-queue.json` 中 `status="pending"` | 不投影（已取消调度，回归 checkpoint 路径） |
| `campaign-profiles.json` / `schedule-settings.json` | **不投影**——用户编辑配置（D2 边界） |

> 注：因为 JSON 是权威源，任何"派生不正确"都可以通过 `bp events rebuild` 修复，不影响业务连续性。

## Success Criteria

- S1. CLI 跑完一次 publish 后，projector 在 ≤ 5s 内向 `events.db` 投影出 R6 三态事件 + `articles` 表对应行；CI 集成测试断言。
- S2. 跑完 `bp events rebuild` 后，`events.db` 中 `publish.confirmed` 数量 = (checkpoints 中 `done` items 数量 ∪ publish-history 中 `published` rows，按 R17 去重键合并)；二次执行 events / articles 数量不增（幂等）。
- S3. `report-anchors --from-events` 与 `footprint --from-events` 通过至少 3 个新增聚合查询测试（按 host、按时间窗、跨 run_id 锚点速度）；**原有 51 个测试不修改通过**（stdin 输入契约不破）。
- S4. 触发"故意把 access_token 塞进 payload"的负向单测，写入后 SELECT 出来字段不含 token；测试同时覆盖 oauth.refreshed / publish.confirmed / retry.attempted 三个事件类型。**追加正向单测**：白名单内的合法字段不被静默丢弃。
- S5. 100K events 合成负载下，projector 单事件投影延迟 p99 < 50ms（不是 5ms——因为是异步路径，不在 publish 关键路径上）；`bp events query --since 7d --kind publish.confirmed` < 200ms 在普通 MacBook 上达成。
- S6. **events.db 删除后**，`bp events rebuild` 能从**当前在盘的** JSON 文件重建——重建后的 publish.confirmed 数量 ≥ checkpoints/*.json 的 done items 数量 ∪ publish-history.json 当前 100 条 cap 内的 published rows，按 R17 合并。**承认限制**：超出 publish-history 100 条 cap、或 checkpoint 已被 `delete_complete` 删除的远期历史无法重建——下一版本周期评估是否把 events.db 升为远期数据的备援角色。disaster-recovery 测试覆盖"近期数据无损"，并显式验证 verify.* 事件**不**进入重建覆盖范围（它们是派生型，由 #2 daemon 重新探测）。
- S7. **Anti-drift**：CI 集成测试有意让 projector 漏 1 个事件（模拟丢失 IN_MOVED_TO），运行 `bp events doctor` 必须 exit non-zero 且指出缺失的 `(run_id, target_url, kind)`。

## Scope Boundaries

- **不替换 `campaign-profiles.json` / `schedule-settings.json`**：它们是用户配置，不属于事件流（D2）。
- **不引入第二存储引擎**：不上 PostgreSQL / DuckDB / Redis；events.db 单库覆盖所有用途。
- **不做 events.db 的远程同步 / 多机合并**：local-first 不变。
- **不在本 brainstorm 里建 longitudinal health daemon**：只定 `verify.*` 事件的 schema，daemon 本身是 #2 的范围。
- **不实现 #4 portfolio risk engine 与 #6 A/B framework**：本底座仅保证它们到来时数据已就位。
- **不引入新运行时依赖**：sqlite3 是 Python stdlib；FTS5 是 SQLite 内置；不上 SQLAlchemy / Peewee。
- **不删旧 JSON 文件**：R18 仅重命名为 `*.legacy.json`；删除推迟到下个版本周期。
- **回填脚本只读不写旧文件**：`bp migrate-events` 不修改任何遗留 JSON。

## Key Decisions

- **D1. Projection 而非 source-of-truth：** JSON 写侧权威不变，events.db 是 projector 派生的查询面。理由：(a) 痛点几乎全在读侧（cross-batch 聚合、cross-CLI 查询、活力检查），写侧没有问题需要修；(b) 不付"重写 5 个 CLI + WebUI 写路径"的成本；(c) events.db 可丢可重建——schema 演进风险大幅降低；(d) 解耦 architecture-health-refactor 的 JsonStore 依赖，本工作可独立交付。**舍弃**："events.db 单一真相源"路径——它的"避免双写漂移"红利不抵"全面改造写路径 + 不可恢复 schema 锁定"的成本。
- **D2. 事件 vs 配置的边界：** 凡是"系统产生的、不可变事实"会被 projector 投影；凡是"用户编辑意图、可变配置"留在原 JSON / TOML 不投影。`campaign-profiles.json` / `schedule-settings.json` 是后者。
- **D3. SQLite 单库 + WAL：** 选 SQLite 而非 JSONL 因为需要并发安全 + SQL 聚合 + 跨表事务（`publish.confirmed` 必须与 `articles` 行同事务）；选 SQLite 而非 DuckDB 因为 stdlib、无外部依赖、足以覆盖 R21 容量目标。
- **D4. articles 与 events 同库同事务：** 不拆成 `published_corpus/` 文件目录，避免事务边界跨进程文件系统；FTS5 直接挂在 articles 上，下游 dup-detect / 重用历史文章是 SQL JOIN 而非文件 walk。
- **D5. 一次性 backfill + 旧文件保留：** 避免运营者升级首日丢历史；`*.legacy.json` 在下个版本周期再删，给一个完整 PR 周期发现漏迁的字段。
- **D6. 与 architecture-health-refactor 解耦：** projector 直接读 JSON 文件，不依赖 R3 JsonStore 抽象——两份 PR 可独立推进。理由：D1 反转后 webui.py 写路径**不需要改动**，merge 冲突面消失。若 architecture-refactor 后续落地，projector 可优化为订阅 JsonStore 写事件而非文件 watch，但不是 v1 前置条件。
- **D7. 敏感字段白名单序列化：** 安全收口策略选"白名单"而非"黑名单"；任何未在 schema 中声明的字段静默丢弃。理由：黑名单挡不住"忘了把新 secret 字段加进禁列"。
- **D8. 事件 kind 用枚举集中声明：** 防止字面字符串散落导致 typo（`publish.confimed` 永远不会被查到）；CI 单测以 `events` 表所有 distinct kind ⊆ 枚举集合作为门槛。

## Alternatives Considered

| 路径 | 是否采纳 | 拒绝原因 |
|---|---|---|
| **events.db 单一真相源**（initial draft 的 D1） | ✗ | 写路径改造覆盖 5 个 CLI + webui.py 4904 行，blast radius 大；schema 一旦演进难以回退（append-only + R10 enum）；与同期 architecture-health-refactor 产生 PR 冲突；最关键的是——所有 Problem Frame 痛点都是读侧问题，写侧没问题需要修。 |
| **events.db projection**（已采纳，D1 修订后） | ✓ | 单写者 = projector，无双写漂移；JSON 仍权威，events.db 可丢可重建；与 architecture-refactor 解耦；不破坏现有 51 个测试。代价：projector 是新组件，需要 anti-drift 检查。 |
| **混合双写过渡**（CLI 新增逻辑直接写 EventStore，旧 JSON 路径继续保留双写） | ✗ | 同时背"双写漂移"和"写路径改造"两份成本，最差选项。 |
| **DuckDB 单库** | ✗ | 多进程并发写支持仅近期才稳定；增加几十 MB 依赖；SQLite + SQL 已足够本 brainstorm 的查询模式。 |
| **JSONL append-only 而非 SQLite** | ✗ | 多进程写需手工 flock；agg 查询要全文件 scan；无事务保证 events 与 articles 一致。 |

## Dependencies / Assumptions

- **D1 反转后，本 brainstorm 不再依赖 `2026-05-18-architecture-health-refactor` 的 R1–R4 落地**。projector 直接读 JSON 文件；两条工作线可并行推进。
- 假设 `2026-05-13-checkpoint-resume` 的 checkpoint.py 行为稳定——回填脚本（R16）依赖其文件格式。**实测的 checkpoint.py 字段（v0.2）包含**：`run_id`、`started_at`、`platform`、`mode`、`items[]`（每条含 `target_url`、`status`∈{pending,done,failed}、`payload`（其中含 `content_markdown` 可推 body）、`published_url`、`adapter`、`completed_at`、`error`）。回填 anchors_json 须从 `payload.content_markdown` 解析 link 提取；lang 从 payload 或缺省 `en`。如 checkpoint 字段变更需同步更新迁移器并 bump 兼容版本号。
- 假设 Python stdlib sqlite3 编译时启用了 FTS5；在主流 macOS / Ubuntu 系统 Python 安装包中默认启用，但为保险 R5 已要求降级路径。
- 假设运营者 local-first 单用户单进程不变（架构重构文档已假设此点），多进程并发只通过 WAL + 写锁解决，不上 server 模式。

## Outstanding Questions

### Resolve Before Planning

**全部 8 条已在 brainstorm 阶段闭合（2026-05-18）：**

- **RBP-1 (closed)** `article_id` = **自增 INTEGER**——与 SQLite ROWID 对齐；Scope Boundaries 已明确不做 remote sync，跨设备同步不在 6–12 个月范围。R3 / R6 / R17 中"`article_id`"实现按 INTEGER 处理。
- **RBP-2 (closed)** FTS5 **默认关、划 schema slot**——v1 不创建 `articles_fts` 表，`articles.body` 保留为 TEXT；future dup-detect / 重用历史 consumer 出现时再 ALTER 加 FTS5 + trigger。R5 改为"FTS5 schema slot 预留但 v1 不启用"。T6 删除级联约束保留为"FTS5 启用后的约束"。
- **RBP-3 (closed)** 历史 articles **妥协策略**：anchors 从 `payload.anchors` 优先读 → 退化到 markdown 反推 → 再退化到 `anchors_json=[]`；`body` 缺则 `NULL`；下游 dup-detect 与文本查询带 `WHERE body IS NOT NULL` filter。
- **RBP-4 (closed)** Projector 承载 = **inline `projector.flush(source_path)`**——CLI 结束时调用，webui `_save_*` 后同步调用。**取消 R11 的独立进程 / 文件 watch / cursor 表方案**——退化为不必要：写路径每个 save point 多一行 `projector.flush_for(path)` 函数调用。R11 / D6 / Scope Boundaries "5 个 CLI 与 WebUI 不变动" 微调为 "仅 save point 多一行 import + flush 调用"。S1 SLO 改为"调用完成时已投影"（同步），不再 5s。R23 / S7 anti-drift doctor 保留但运行频率从"持续监控"降到"按需 + CI"。
- **RBP-5 (closed)** 仅保留 **`bp-events-rebuild` + `bp-events-doctor` 两个独立 entry point**——不引入 `bp` umbrella CLI；其他查询走 `sqlite3 events.db` shell 或 `report-anchors --from-events`。R15 改写为"仅 rebuild + doctor 两个独立 console script"。
- **RBP-6 (closed)** S5 reword 为：**"100K 合成负载 benchmark 在 CI 中记录 p99 baseline；regression > 2x baseline fail CI"**——不 hard-code 50ms。R21 数字改为"实测后填入 baseline，初版不约定具体值"。
- **RBP-7 (closed)** R16 全量回填：**按 run_id 分批、每批一事务、quarantine 跨批累加**——失败仅回滚当前 run_id 批次，其他已完成批次保留。S2 验收对应调整。
- **RBP-8 (closed)** GDPR / takedown 不进 v1。T8 已记录为已知限制；R2 append-only 保持纯净；DELETE 仅清 `articles`（FTS5 关时无 `articles_fts` 需级联）。未来若法务要求，单独立项引入 tombstone。

### Deferred to Planning

- [Affects R2][Technical] `events` 表是否需要 `causation_id` / `correlation_id` 以串联同一个发布的多步事件？倾向于"用 `run_id` + `article_id` 已够"，但 plan 阶段如发现需要再加。
- [Affects R14][Technical] 旧 JSON 派生视图的实现位置：放 `webui/services/` 还是 `EventStore` 模块自身？属于代码组织决策，plan 决定。
- [Affects R20][Technical] `events.db` 的备份策略：是否在 `bp events` 子命令里提供 `dump` / `restore`？也可以靠 `sqlite3 .dump` 解决，plan 阶段定。
- [Affects R21][Needs research] 100K events 合成负载 benchmark 怎么写在 CI 里——hypothesis 还是 fixture？plan 阶段定基线。
- [Affects R15][Technical] `bp` umbrella CLI 用什么 dispatch 库（argparse / click / typer）？现有 5 个 CLI 都用 bare argparse，是否一并迁移；plan 阶段定。

## Next Steps

→ `/ce:plan docs/brainstorms/2026-05-18-event-substrate-corpus-requirements.md` 进入实施规划。规划阶段需先确认 architecture-health-refactor 的 R1–R4 落地节点，再切片本 brainstorm 的 R1–R22。
