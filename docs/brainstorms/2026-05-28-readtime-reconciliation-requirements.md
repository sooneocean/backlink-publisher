---
date: 2026-05-28
topic: readtime-reconciliation-hub
---

# 读时 Reconciliation Hub

## Summary

在现有 `events/reconcile.py::project_on_read` 基础上，扩展为系统化的读时 reconciliation hub——dashboard 加载或 CLI 显式触发时，跨 checkpoint、dedup store、publish history 三个 store 交叉核对，自动修复发散，无法修复的入 quarantine。

---

## Problem Frame

当前 publish-backlinks 写入三个 store：checkpoint（进度跟踪）、dedup store（去重记录）、publish history（历史存档）。进程在 API 调用成功与 store 写入之间崩溃时，三者发散。已有两种兜底：

- **`_publish_epilogue`** 每次运行结束调 `project_run_safe`，但进程还没到终态就崩溃时跑不到
- **`project_on_read`** 读时投影，但目前只处理 checkpoint→events 一个方向，不跨 store 核对

结果是：operator 遇到"dedup 说已发布但 checkpoint 说 pending"、"history 和 events 条数对不上"等不一致时，没有系统化的方法来诊断和修复。能修但需手动翻 JSON 文件，排查成本高。

---

## Actors

- A1. **Operator**: 使用 `publish-backlinks` CLI 或 WebUI dashboard 的人。需要知道发布状态是否一致，不一致时能快速修复或知晓差异。
- A2. **publish-backlinks (publish loop)**: 写入 checkpoint + dedup store + publish history 的主写入者。每次 publish 事件产生三路写。
- A3. **Dashboard (/health, /history)**: 读 events DB + publish history 展示状态。读时 reconciliation 的触发器。
- A4. **Reconciler**: reconciliation hub 本身。运行 cross-store 核对，输出报告，操作 quarantine。

---

## Key Flows

- F1. **Dashboard 加载时 reconciliation**
  - **Trigger:** operator 访问 `/health` 或 `/history`
  - **Actors:** A3 (Dashboard), A4 (Reconciler)
  - **Steps:**
    1. Dashboard 加载时调 `project_on_read`（现有流程）
    2. `project_on_read` 完成后，reconciler 运行 cross-store 核对 pass
    3. 对比 checkpoint ↔ dedup store ↔ publish history 三方的行状态
    4. 符合自动修复规则的，原地修复并记录到 RECON log
    5. 无法自动修复的，入 `quarantine_log`（已有表）
    6. Dashboard 渲染时展示 `quarantine_log` 条目数作为警告标识
  - **Outcome:** Dashboard 至少展示 reconciliation 状态（gap count / latest divergence），operator 能知道数据是否一致
  - **Covered by:** R1, R2, R4, R7

- F2. **CLI 显式 reconciliation**
  - **Trigger:** operator 运行 `publish-backlinks --reconcile <run_id>` 或 `--reconcile-all`
  - **Actors:** A1 (Operator), A4 (Reconciler)
  - **Steps:**
    1. Reconciler 加载指定 run_id 的 checkpoint 文件
    2. 调 dedup store 读取该 run 涉及的所有行
    3. 调 events DB 读取相关 publish 事件
    4. 调 publish history 文件加载
    5. 运行 cross-store 核对，输出结果 JSONL 到 stdout
    6. 自动修复的发散输出 `RECON auto_fixed` 行
    7. 无法修复的输出 `RECON gap` 行供 operator 审阅
  - **Outcome:** operator 获得一份该次 publish run 的 state consistency 报告
  - **Covered by:** R3, R5, R6

- F3. **自动修复链路：dedup done 但 checkpoint pending**
  - **Trigger:** reconciler 发现 checkpoint 某行 pending 但 dedup store 对应行状态为 done
  - **Actors:** A4 (Reconciler)
  - **Steps:**
    1. Reconciler 读取 dedup store 中该行的 `live_url`、`verify_ok`、`ts_utc`
    2. 将 checkpoint 对应行修正为 done，填入 `published_url`、`verified`
    3. 事件日志写一条 `recon.patch.checkpoint` 事件
    4. 更新 `quarantine_log`（如果之前有相关记录则清除）
  - **Outcome:** checkpoint 与 dedup store 一致，`quarantine_log` 无残留
  - **Covered by:** R2, R5

---

## Requirements

**[Core: reconciliation pass]**

- R1. Reconciler 在 `project_on_read` 完成后运行，作为同一读时 pass 的一部分。两者共享 `_PROJECTION_LOCK` 序列化。
- R2. Reconciler 对比 checkpoint ↔ dedup store 的状态。对 dedup store 标记为 done 但 checkpoint 标记为 pending/failed 的行，自动修复 checkpoint（填充 `published_url`、`verified`、`completed_at`）。
- R3. reconciler CLI 子命令：`publish-backlinks --reconcile <run_id>` 对指定 run；`publish-backlinks --reconcile-all` 扫描所有残留 checkpoint。
- R4. reconciler 对比 publish history（`publish-history.json`）与 dedup store，检测 history 中有但 dedup 无的记录（反向发散），发出 `RECON gap` 但不自动写入（信息类，operator 判断）。

**[Reporting & observability]**

- R5. 每个自动修复发出 RECON log 行（`publish_logger.recon`），格式：`RECON auto_fixed run_id=<r> row_id=<id> from=<old_status> to=<new_status> reason=<reason>`。
- R6. 每个不可自动修复的 gap 发出：`RECON gap run_id=<r> row_id=<id> checkpoints=<s> dedup=<s> history=<s>`。
- R7. Dashboard `/health` 展示 `reconciliation_gaps` 计数（`_open_quarantine_count` 的扩展或并行指标），gap > 0 时显示警告 banner。

**[Quarantine lifecycle]**

- R8. 自动修复成功时，清除该源在 `quarantine_log` 中的对应条目。
- R9. `quarantine_log` 条目增加 `run_id` 和 `row_id` 字段，方便 dashboard 按 run 聚合展示 gap。
- R10. reconciler 对有 quarantine 条目的 run 不做自动修复（避免修复基被 operator 正在排查的记录污染）。

**[Compatibility]**

- R11. reconciler 必须兼容旧 checkpoint 格式（字段缺失不计为发散，仅 emit INFO 跳过）。
- R12. reconciler 必须在 checkpoint 文件的 `claims:` 块到期后扔能正常运行（不依赖 `plan-check` 基础设施）。

---

## Success Criteria

- operator 在 dashboard 能一眼看出当前 publish state 是否一致，无需手动翻 JSON 文件
- CLI `--reconcile <run_id>` 在 5 秒内（1000 行 checkpoint 规模）输出完整报告
- 自动修复覆盖 80%+ 的常见发散场景（dedup_done_but_pending、orphaned_attempting）
- `quarantine_log` 在 CI 快照测试中不意外堆积（各场景的修复路径都清理残留）

---

## Scope Boundaries

- ❌ 不改造 publish 主循环的写入路径（保持三路写入不变）
- ❌ 不引入事件溯源或 CQRS
- ❌ 不加常驻后台进程或 cron job
- ❌ 不修改 adapter 层
- ❌ 不对 publish history 做反向修复（history → dedup/checkpoint 的发散只报告不修复）
- ❌ 不处理 canary health store（canary-health.json）的 reconciliation——那是另一个问题域

---

## Key Decisions

- **读时触发而非写时改造**：用 reconciliation 取代"让写原子化"的思路，接受读时修复的延迟换取低侵入式改动。这直接对应 feature description 的"use reconciliation instead of pretending the whole publish flow is atomic"。
- **自动修复只走 checkpoint←dedup 方向**：dedup store 是外部行为（API call）的最贴近记录，它标了 done 说明 publish 实际发生了。checkpoint 才是容易失真的中间状态。反向（dedup ← checkpoint）不可靠。
- **reconciler 与 project_on_read 共享锁**：两者都运行在同一个读时 pass 中，避免两个 pass 各自查询 SQLite 引入的时序偏差。

---

## Dependencies / Assumptions

- **假设**：dedup store 比 checkpoint 更可信（dedup 在 publish 成功后写入，而 checkpoint 在之前和之后都写——前者可能没执行到后一步）。
- **依赖**：`events/reconcile.py` 的 `_PROJECTION_LOCK` 和 `_PROJECTION_LOCK` 的 infrastructure（`EventStore`、`_collect_sources`、`_quarantine`）可以直接复用或扩展。
- **假设**：`publish-history.json` 的格式与 `HistoryStore`（`webui_store/history.py`）的 schema 一致，无意外 drift。
- **依赖**：`dedup` store 的每个记录包含 `run_id`、`row_id`、`live_url`、`verify_ok`、`status` 字段（observe 模式下的现有字段）。

---

## Outstanding Questions

### Deferred to Planning

- `[Affects R2][Needs research]` dedup store 的 key schema 具体是什么？需要读 `_dedup_gate.py` 确认状态枚举（done/failed/uncertain/attempting）以及是否每个记录都带 run_id。
- `[Affects R9][Needs research]` `quarantine_log` 当前 schema 是否已有 `run_id` 列？如果没有，是否需要 migration？
- `[Affects R4][Needs research]` publish history（`publish-history.json`）如何关联到 checkpoint/dedup 的行？通过 `row_id` / `title` 还是其他 key？如果缺失关联字段，history reconciliation 可能倒退为 heuristic。
- `[Affects R7][Technical]` Dashboard `/health` 需要经过多少修改才能展示 `reconciliation_gaps`？需要先读 `webui_app/` 的 health route。
- `[Affects R1][Technical]` `project_on_read` 目前返回 `ReadProjectionResult`，reconciler 的返回需要合并进去还是单独返回？需要确定 API 契约。
