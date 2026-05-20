---
date: 2026-05-20
topic: worktree-branch-pr-cleanup
---

# Worktree / Branch / PR Cleanup — Post-2026-05-20 Ship Burst

## Problem Frame

2026-05-20 一日内 squash-merge 了 13 个 PR（#88/#99/#101–#107/#110–#115），但本地与 origin 留下大量未清理债务：

- **10 个本地 branch** ahead of main，其中 6 个各带 1 个独立 commit、**从未开过 PR**
- **14 个远端 branch** ahead of main，多数是 squash-merge 后未删的 PR head + 4 个 telegraph rehearsal
- **3 个 worktree** 中 2 个有问题：`bp-banner-image-gen` 已 orphan（origin 删除），`bp-roundtrip-a2` 持有 Plan 003 Phase A.2 dirty WIP
- 主 worktree 携带 2 个 untracked docs（Plan 003 + RUNBOOK）

不清理的代价：再次 ce:work 时违反 [[feedback-ce-work-must-audit-worktrees-first]] / [[feedback-ce-work-must-reverify-state]] 的成本攀升；`bp-*/` 多了变成 [[feedback-foreign-agent-wip-spreads-as-broken-replace]] 的滋生土壤；6 个未 PR 的独立 commit 时间越久越难还原成可 review 的形态。

**额外风险信号**：A 类 6 个 commit 全部在 2026-05-20 12:15–12:27 这 **12 分钟窗口**、commit body 为空、无对应 PR。匹配 [[feedback-foreign-agent-wip-spreads-as-broken-replace]] 的 fingerprint，可能是并发 agent 的 WIP 而非这条 session 应当 ship 的代码。**必须先验证再决定 land**。

## Inventory

| Class | Source | Identifier | 处置方向 |
|---|---|---|---|
| A | local branch | `feat/browser-login-service` `726330e` | 验证后 land |
| A | local branch | `feat/concurrent-publish-leases` `d186f3b` | 验证后 land |
| A | local branch | `feat/exception-classification` `bb6b48b` | 验证后 land |
| A | local branch | `feat/persistence-safe-write` `1bc3054` | 验证后 land |
| A | local branch | `feat/webui-cover-image-wiring` `60437c3` | 验证后 land（高冲突风险）|
| A | local branch | `chore/debris-catchall-2026-05-20` `3c4f360` + `8a831f0` | 拆分：docs+Makefile+scripts 单独 PR；adapter test fixes 单独 PR |
| B | dirty worktree | `bp-roundtrip-a2` (Plan 003 Phase A.2 WIP) | 完成 → ship |
| C | local branch | `feat/homepage-url-autoderive-v1` | 已 PR #113 — prune |
| C | local branch | `fix/plan-claims-opt-out-url-derive` | 已 PR #115 — prune |
| C | orphan worktree | `bp-banner-image-gen` (PR #110 已 squash) | 验证无独立 WIP 后退 |
| C | remote branch | `origin/feat/config-subsection-fix` | 已 PR #99 — prune |
| C | remote branch | `origin/feat/save-config-taxonomy-canary` | 已 PR #114（origin 已自动删除）— 本地同步即可 |
| C | remote branch | `origin/fix/plan-check-claims-coerce-and-recon-doc` | 已 PR #104 — prune |
| D | remote branch | `origin/local/telegraph-unit{2,4,5,6}-staged` | 保留（6/01 Pass ship rehearsal，per [[reference-phase0-local-rehearsal-branches]]）|

## Requirements

**A 类独立 commit 处置**

- R1. 对 A 类每个 commit 独立验证：(a) `git diff origin/main..<branch>` 检查内容是否合理、(b) checkout 后跑该 commit 涉及文件的 pytest 子集、(c) 跨 `bp-*/` worktree grep 同一内容是否散布作 [[feedback-foreign-agent-wip-spreads-as-broken-replace]] 判定。任一可疑 → 不 land。
- R2. 通过 R1 验证的 commit，**逐个 rebase 到当前 main 并开独立 PR**（catch-all 路线被显式拒绝）。每个 PR 必带 squash-friendly commit body + ce:review 通过 + plan-claims-gate 通过。
- R3. R2 PR 顺序按冲突依赖排：(i) `1bc3054 safe_write 抽取` 最先（被其他依赖的可能性最高 + 与 PR #99 writer.py 冲突最重），(ii) `d186f3b concurrent leases` 第二（publish_backlinks.py monolith ceiling 必须同 PR 调），(iii) `bb6b48b retry classification` 独立无冲突，(iv) `726330e browser-login service` + `60437c3 cover-image wiring` 同 webui 域、需检查与 PR #110/#112/#113 冲突。
- R4. catch-all 拆分原则：plan/docs 类（已 land feature 的 post-hoc plan）→ 直接 commit 到 main 不开 PR（per [[feedback-solutions-category-frontmatter]] 不归一）；Makefile + scripts/check-all.sh + 3 个 adapter test fixes → 一个 chore PR。

**B 类活跃 WIP 收尾**

- R5. `bp-roundtrip-a2` 完成 Plan 2026-05-20-003 Phase A.2：commit 当前 dirty diff + writer.py emission 代码 + new test，确保 `pytest tests/test_save_config_new_channel_roots.py` 全过，开 PR。
- R6. 主 worktree 的 untracked Plan 003 doc + RUNBOOK 与 R5 同 PR commit（避免 [[feedback-plan-doc-on-cutoff-needs-claims-block]] 的 plan-claims-gate exit 8）。

**C/D 类清理**

- R7. C 类 PR-merged 本地 branch（`feat/homepage-url-autoderive-v1` / `fix/plan-claims-opt-out-url-derive`）→ `git branch -D` + `git push origin --delete` 同步删除。
- R8. C 类 origin-only remnant（`config-subsection-fix` / `plan-check-claims-coerce-and-recon-doc` / `feat/save-config-taxonomy-canary` 若仍存在）→ `git push origin --delete`；本地 `git fetch --prune`。
- R9. `bp-banner-image-gen` worktree：先 `git diff origin/main..feat/banner-image-gen -- :!docs :!*.md` 比对实际代码 delta 是否被 PR #110 squash 覆盖完整；确认无遗漏后 `git worktree remove` + `git branch -D`。
- R10. D 类 telegraph rehearsal 4 个远端 branch **保留不动**（[[reference-phase0-local-rehearsal-branches]] 标为 6/01 Pass ship load-bearing）。仅在 2026-06-01 ship 完成后由该 plan owner 决定 retire。

**预防 regression**

- R11. 写一条 worktree-prune 提示加入 `backlink-publisher/AGENTS.md` 的 "Worktree cleanup" 段（如未存在）：每个 squash-merge PR 着陆后，作者需 (a) `git branch -D <feat-branch>` (b) `git worktree remove bp-<topic>` (c) `git push origin --delete <feat-branch>`（如未启用 GitHub auto-delete）。

## Success Criteria

- R1 验证后**确切知道** A 类 6 commit 哪些是真工作、哪些是 foreign-agent 残留；可疑项被显式 stash 到 `refs/stash/foreign-agent-wip-2026-05-20-<sha>` 不丢，但不进 main。
- 完成 R2–R8 后：`git branch` 仅含 `main` + 当前活跃 feature 分支（数量 ≤2）；`git branch -r` 仅含 `origin/main` + `origin/local/telegraph-unit*-staged` 4 条 + 当前活跃 PR 分支。
- `git worktree list` ≤2 项（主 + 至多 1 个活跃 feature）。
- 主 worktree `git status` clean（或仅含当前 ce:work session 进行中的明确 untracked）。
- 全套 `pytest tests/` 仍 ≥2604 passed（PR #94 后的基线，不能因 cleanup 而 regression）。

## Scope Boundaries

- **不**碰 D 类 `origin/local/telegraph-unit*-staged` — 推迟到 2026-06-01 Phase 0 Pass ship 完成后处理。
- **不**做任何 A 类 commit 的内容 refactor / 风格改动 / 测试补充——要么 as-is land 要么 stash 弃用；想增量改进的话开后续 PR。
- **不**做 archeology 去恢复 PR #108 revert（Phase 4 dofollow 灾难）相关内容——已是显式负债（[[feedback-grep-dofollow-map-before-shipping-adapter]]）。
- **不**触碰 `chore/debris-catchall-2026-05-20` 里的 4 个 plan doc 的内容（comprehensive-optimization-proposal / autoderive-and-ui-polish-requirements / banner-image-gen-plan / autoderive-v1-plan）——它们对应的 feature 都已 ship，docs 只做归档 commit 不修订。
- **不**追求 `git branch -r` 完全空——保留 telegraph rehearsal + 当前 active PR head 是预期状态。

## Key Decisions

- **A 类逐 PR ship 而非 catch-all 单 PR**：用户显式选了"保留独立工作 + 清理 C/D 残留"路线，明确 review 颗粒度优先于速度（per [[feedback-cherry-pick-to-main-when-parent-pr-blocks-ci]] 学到的"单点出问题难独立 revert"教训）。
- **R1 验证 gate 必跑**：A 类 commit 的 12-分钟窗口 + 空 body + 无 PR 三联签名是 [[feedback-foreign-agent-wip-spreads-as-broken-replace]] 的典型特征。不验证就 land 会重蹈 PR #108 的覆辙（[[project-phase4-scaffold]]）。
- **B 类先于 A 类**：`bp-roundtrip-a2` 是当前 session 真正在做的 Plan 003 工作，与已 ship 的 PR #114 配套；先收尾避免 worktree 长期 dirty 干扰 R1 的"foreign content 跨 worktree 散布"判定。
- **R3 顺序按冲突依赖**：safe_write 抽取最可能与 PR #99 (config writer subsection preservation) 冲突 + 被其他 commit 依赖，先 land 最易 isolate 冲突源。
- **D 类不动**：[[reference-phase0-local-rehearsal-branches]] 明确 telegraph rehearsal 是 6/01 ship 的 load-bearing context，prune 它们会破坏 plan-claims-gate 的 referenced SHAs。

## Dependencies / Assumptions

- 假设 origin 仓库的 GitHub auto-delete-branch-on-merge 已配置（PR #74 之后多数分支 squash-merge 后自动删除）；若未启用，R7/R8 的 `git push origin --delete` 项更多。
- 假设 `bp-roundtrip-a2` 当前 dirty 内容确实来自本 session 而非并发 agent；执行 R5 前需 `git status --porcelain` + `stat -f "%Sm"` 验证 mtime 集中在本 session 时间窗。
- 假设 R1 验证可以在 ≤2 小时完成；若 6 个 commit 全要单独跑全套 pytest 会超时，则用 touched-files 子集 + 关键 invariant tests（footprint / monolith-budget / r9-extension）。

## Outstanding Questions

### Resolve Before Planning

无。所有 product 决策已锁定。

### Deferred to Planning

- [Affects R1][Needs research] A 类 6 commit 的 author email / committer 是否一致？可能能用 `git log --format="%ae %ce"` 看 commit author 推断是不是同一 agent run 产物，进一步判定可信度。
- [Affects R3][Technical] PR #110 (banner-image-gen) 已 land webui 改动；`60437c3 cover-image wiring` 是否与之冲突需 `git diff <60437c3> origin/main -- webui_app/` 比对决定 R3 顺序是否要调到最后。
- [Affects R5][Technical] Plan 003 Phase A.2 的 writer.py emission 代码是否触发 monolith ceiling（当前 340 经 PR #99 已 bump 到 360）；若仍超需要同 PR 调 ceiling + rationale ≥80 字符。
- [Affects R9][Technical] `bp-banner-image-gen` 有 `fc267be save: banner-image-gen WIP before cleanup` 这条 commit；squash 是否覆盖了它的全部内容需 `git diff main feat/banner-image-gen --stat -- :!docs` 验证。
- [Affects R11][Needs research] `backlink-publisher/AGENTS.md` 是否已有 "Worktree cleanup" 段；如已有则补充措辞、如未有则新增。

## Next Steps

→ `/ce:plan` for structured implementation planning（11 个 R 跨多个 PR，需要 plan 拆 unit + 排冲突依赖 + 写 verification commands）
