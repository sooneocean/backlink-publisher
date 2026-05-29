---
date: 2026-05-28
topic: v_next-consistency-observability-release
---

# v_next 版本: 发布链路一致性 + 可观测性

## Problem Frame

过去三周（PR #200 – #290）系统性地把发布管线从「能跑」推到了「能复盘」：
publish reliability policy、registry-driven binding、recurring-trap eradication、
dedup failed→done ratchet 都已 ship。

**当前已完成（本版不计入工作量）：**
- `deterministic-planning-purity` — `628bed2d`（principle doc + AGENTS.md）
- `readtime-reconciliation-hub U1–U4` — `482de679`（PR #290 squash）：`events/reconciler.py` 355 行、`--reconcile`/`--reconcile-all` CLI args、RECON.log 写入、reconciler summary stderr 输出

**operator 视角仍有两块未关闭：**

1. **publish 管线 saga 边界隐式** — inner loop 的成功/失败/重试/补偿散落在 exception handler、exit code 和行内注释里，**没有审计/测试/runbook 可引用的契约文档**。saga worktree `bp-saga-hardening`（HEAD `28e9434c`，已 rebase 到 `482de679`）已有 2 commits（G3/G4 RECON 字段 + 368 行 contract tests + 220 行 runbook），等待 PR。

2. **RECON.log 行已写入，但 dashboard 看不见** — `reconcile_all()` 产出的 `quarantine`/`history_gap` 事件已落在 RECON.log，但 `webui_app/routes/health.py` 和 `webui_app/templates/health.html` 完全没有读取或展示这些计数（readtime U5 未实施）。operator 仍须手翻 RECON.log 才能看到 gap 数。

本版把 **saga 契约合并进 main** 与 **readtime dashboard U5** 合并为单一"发布链路一致性 + 可观测性"主题版本。

## Requirements

**[版本组成]**

- R1. v_next 由 **两条独立 ship 通道** 组成：
  - **Saga 通道**：`bp-saga-hardening` worktree（HEAD `28e9434c`）已含全部内容，可直接走 PR → merge。
  - **Readtime U5 通道**：dashboard gap display，按 plan `2026-05-28-004-feat-readtime-reconciliation-hub-plan.md` §U5 实施（改 `webui_app/routes/health.py` + `webui_app/templates/health.html`）。
  - readtime U1–U4 已在 `482de679` 完工，不计入本版工作量。

- R2. 两条通道**相互独立，无强制顺序**。U5 读取 `quarantine_log`（已由 `reconciler.py` 写入，在 main）；saga G3/G4 写入的是 RECON *stderr*，与 RECON.log / dashboard 路径不重叠，无命名协调风险。可并行进行，也可任意顺序 ship。

**[ship 通道]**

- R3. **Saga 通道**：`bp-saga-hardening` worktree 已满足 (a) `adapters/base.py` = 165 行干净基线、(b) rebase 到 `482de679`。本通道仅需 (c) 走 `/ship` → PR → merge。stray base.py refactor 已隔离在 `stash@{1}`（handshake message 见 R7）。

- R4. **Readtime U5 通道**：按 plan §U5（health route `_get_reconciliation_gaps()` + template banner）实施：
  - 新增：`webui_app/routes/health.py` 中读取 `quarantine_log` 的 gap count（只读查询，never-raises）
  - 新增：`webui_app/templates/health.html` 展示 quarantine 计数（gap > 0 时显示 warning banner）
  - 测试：扩展 health route 覆盖 gap 计数渲染和空态

**[版本级验收]**

- R5. v_next 完成的判定为复合条件：
  - C1. saga PR merge 进 main
  - C2. readtime U5 PR merge 进 main（dashboard gap display 可见）
  - C3. **operator-facing 验证**：在 main 上访问 `/health` dashboard，dashboard 正常渲染；若环境有真实 RECON.log 条目，quarantine 计数展示正确；若无，显示"0 gaps"而非 500 或空白。
  - C4. footprint test 全绿：`pytest tests/test_footprint*.py` 无 dropped events 回归

**[stray 改动隔离]**

- R6. `stash@{1}`（`STRAY: -72 line refactor in adapters/base.py 2026-05-28`，在 `feat/saga-hardening` 上）在 v_next 周期内**禁止 pop**。删除了 `AdapterResult` dataclass + `carry_link_attr_verification` + `_resolve_article_urls`，来源未识别——必须等原作者认领后再决定 cherry-pick 还是 drop。
- R7. `stash@{0}`（`WIP: plan_rows DI refactor + reconcile.py dedup_key extensions`，在 main 上）同样禁止 pop；来自并发 agent，尚未识别归属——v_next 完工后下次 brainstorm 评估是否合入。

## Success Criteria

- operator 端：dashboard `/health` 展示 quarantine gap 计数；gap > 0 时有 warning banner；无 RECON.log 数据时显示 0 而不是报错。
- 文档端：saga runbook（220 行）+ saga contract tests（368 行）in tree；readtime U5 template 可见。
- 回归端：footprint test 全绿；`make check-all` 在 main 不红。
- 不引入：新平台、新存储后端、新 LLM 依赖、对 Telegraph Phase 0 / Thin WebUI U8 / Reg-Drift U4-U5 的任何改动。

## Scope Boundaries (Non-Goals)

明确**不做**的项：

- **Thin WebUI U8 mock seams** — Plan `2026-05-27-004` 显式 deferred 为 low-pri，不在本版恢复
- **Registration-Drift U4/U5** — 从 PR #283 deferred，本版不补
- **Telegraph Phase 0 T+7/14/21 remote-trigger routines** — 三个独立 remote routine，与本版正交
- **`stash@{0}` / `stash@{1}` 的 stray 改动** — 见 R6/R7，本版不处理
- **任何 `adapters/base.py` 重写 / `AdapterResult` 搬迁**
- **新 publisher adapter**
- **RECON.log 格式变更** — 已在 `482de679` 落定为 JSONL；本版不改格式
- **readtime U1–U4 的任何修改** — 已 ship，本版只做 U5

## Key Decisions

- **D1 — 并行而非串行**：saga 与 readtime U5 无硬序依赖，可并行或任意顺序 ship。U5 读取 `quarantine_log`（由 `reconciler.py` 写入，`482de679` 已在 main）；saga G3/G4 字段只写 RECON *stderr*，不涉及 RECON.log 或 `quarantine_log`——两者命名空间完全独立。
- **D2 — 单一版本而非两连发**：saga（契约）与 U5（可见性）合并为同一叙事，operator 一次接收"步骤有约束 + 偏离可见"完整能力。readtime U1–U4 已在 main 强化了此论据：功能已在但看不见，U5 是将其闭环的最后一步。
- **D3 — operator-facing 验收为必要条件**：C3 确保 dashboard 真实渲染，而不只是 PR merge。理由：[[probe-then-pivot-when-api-unverifiable]] 教训——契约写在测试里和实际能跑是两回事。
- **D4 — stash 而非 revert**：stray 改动用 stash + handshake message 隔离，不直接 checkout。理由：[[stash-message-as-concurrent-agent-handshake]]——删除了 `AdapterResult` 这类核心 dataclass，不可假设无主。
- **D5 — determ-planning 已 ship 不再计入版本工作量**：commit `628bed2d` 已落 principle doc + AGENTS.md ref；无更多 unit。

## Dependencies / Assumptions

- **已满足**：saga worktree `bp-saga-hardening` HEAD `28e9434c` 已 rebase 到 `482de679`，`adapters/base.py` = 165 行基线，PR ready。
- **已满足**：readtime U1–U4 在 main `482de679`；`reconciler.py` 写 `quarantine_log`，U5 可安全读取。
- **依赖**：footprint test 已稳定（`PYTHONHASHSEED=0` 由 `pytest-env` 注入）。
- **假设**：本版周期内 `dedup/checkpoint/history` 三方 store 结构不变；如有外部 PR 拟引入第四 store 需 freeze。
- **假设**：`adapters/base.py` 165 行版本是 saga PR 的稳定基线；stash 不 pop 期间无其他 worktree 改此文件。

## Outstanding Questions

### Resolve Before Planning

（空 — 所有阻塞性决策已在本 brainstorm 内解决）

### Deferred to Planning

- [Affects R4][Technical] U5 health route 的 gap count 查询是直接 `SELECT COUNT(*)` from quarantine_log，还是调用 `_open_quarantine_count()` wrapper？由实施时决定（wrapper 已存在于 `events/reconcile.py`，优先复用）。
- [Affects R5][Needs research] C3 (operator-facing 验证) 在 CI 内是否可 stub quarantine_log 数据触发 dashboard 展示？由 plan 阶段评估 health route fixture 能否注入 seeded EventStore。
- [Affects R6/R7][Process] 两个 stash 在 v_next 完工后若仍未被原作者认领，下个版本周期是否主动 drop？保留到下次 brainstorm。

## Next Steps

→ `/ce:plan` for structured implementation planning（saga 通道可直接 plan 复审；readtime U5 按 plan §U5 执行）
