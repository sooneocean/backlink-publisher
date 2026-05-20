---
title: Land 6 PRs + Worktree/Branch Cleanup R2 — Post-Afternoon Ship Burst
type: refactor
status: completed
date: 2026-05-20
deepened: 2026-05-20
claims: {}
---

# Land 6 PRs + Worktree/Branch Cleanup R2 — Post-Afternoon Ship Burst

## Overview

**主体工作：land 今天下午一波 6 个 OPEN PR**（5 banner + 1 legacy-bridge），cleanup 是收尾副产品而非中心目标——单元分配为 5/8 个 unit/requirement 是 land 动作，3/8 是 cleanup。Plan 005 已收尾今天上午的 debris；这是同款 pattern 的 R2 应用。Lightweight scope：5 个 banner PR 全 `CLEAN/SUCCESS` 待 merge、1 个 legacy-bridge PR 仅卡 plan-claims-gate（已知 `claims:{}` 套路），加上 1 个 #119 squash 后未删的 origin head。

## Problem Frame

实测状态（fetch 后）：

| Surface | 数量 | 状态 |
|---|---|---|
| OPEN PR | 6 | 5 CLEAN+green / 1 UNSTABLE 仅 plan-claims-gate FAILURE |
| 本地 feature branch | 6 | 全部对应 OPEN PR head，零 dirty |
| Worktree | 7（含 main） | 全部 clean，零 dirty |
| origin feature branch | 7 | 6 对应 OPEN PR + 1 #119 squash 后 undeleted head |
| origin protected (telegraph-staged) | 4 | 不动 |

具体：

- **6 OPEN PR**：#118 banner-u2-telegraph / #120 banner-u3-blogger / #121 banner-u4-hashnode / #122 banner-u5-velog / #123 banner-u6-ghpages 全 CLEAN/MERGEABLE/checks `SKIPPED,SUCCESS`；#124 delete-legacy-import-bridge MERGEABLE/UNSTABLE，唯一红是 `plan-claims-gate` job `26147141871/76905247475`。
- **PR 间冲突面**：5 banner PR 共改 `docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md`（checkbox flip），代码侧各自摸自己的 adapter + test 文件，**零代码冲突**；#124 不动任何 banner adapter 文件，**与 5 banner PR 全独立**。
- **#124 失败 root cause**：`docs/plans/2026-05-20-006-refactor-delete-legacy-import-bridge-plan.md` 日期 `2026-05-20`（cutoff 当天），未带 `claims:` 块；`plan-check` 触发 exit 8 drift gate。
- **origin debris**：`origin/feat/medium-graphql-spike-scaffold` 已 squash-merge 为 `ba74bd2`（#119 LANDED 2026-05-20 06:22 UTC），head 未删；其他 6 个 origin feature head 等 PR squash 时 `--delete-branch` 一次性清。

## Requirements Trace

- R1. **修 #124 `plan-claims-gate` FAILURE 并 push** — block 主道路上唯一红灯。
- R2. **5 个 banner PR squash-merge** —— 任意顺序 OK（零代码冲突 + plan-doc checkbox flip 自动 merge）。
- R3. **#124 squash-merge** —— R1 后；与 R2 互相独立，可并行 or 任意先后。
- R4. **每次 squash 用 `gh pr merge --squash --delete-branch`** —— 自动清 origin head 避免 R5 残留。
- R5. **prune `origin/feat/medium-graphql-spike-scaffold`** —— 唯一需要手动 `git push origin --delete` 的残留 head。
- R6. **退所有 6 个 banner+legacy worktree**（merge 后无价值）。
- R7. **保留 `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条** —— [[reference-phase0-local-rehearsal-branches]] 6/01 Pass ship load-bearing，硬约束。
- R8. **最终 invariant 验证** —— `git branch -r --no-merged origin/main` 仅 telegraph 4 条；`git worktree list` 仅 main；pytest 结果 `failed == 0`（baseline SHA + quad-counter 由 Unit 1 记录、Unit 5 比对，详见 Key Technical Decisions §R8）。

## Scope Boundaries

- 不动 `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条（R7 锁定）。
- 不 review banner PR 内容本身（已在 PR 时段评审通过 CI；R2 仅做 merge 动作）。
- 不修复 #124 之外的任何 plan doc / 代码内容。**plan-doc conflict 处理铁律（统一 Scope/Approach/Test 三段）**：(a) checkbox-only conflict（同一行 `- [ ]` ↔ `- [x]` flip）→ 手动 resolve，保留全 5 个 checkbox 都 flipped 的最终态，继续 merge；(b) 任何 non-checkbox conflict（heading 改动 / 段落改写等）→ 立即停手 + 输出 conflict diff 给 user，不顺手 refactor。
- 不重新触发 #124 之外的 plan-claims-gate 修复路径；其他 5 个 banner PR 的 plan-claims-gate 已绿。
- 不在 R2 PR 之间做 sequencing 优化（任何顺序都 OK，按 review 完成时间自然顺序即可）。
- 不为下一波（Plan 003 Phase B/C、Plan 007）做任何前置工作。

## Context & Research

### Relevant Code and Patterns

- `scripts/prune-stale-worktrees.sh` + `scripts/_worktree_safety.sh` —— Plan 005 已实战；脚本 detect "branch-tip ancestor-of origin/main"，pull `--force` 退 worktree + 删 local branch。**关键限制**：脚本只删 local branch，不 push 删 origin head。
- `gh pr merge --squash --delete-branch <N>` —— Plan 005 Unit 5 验过；自动 squash + 删 origin head + 留 reflog。
- `docs/plans/2026-05-20-006-refactor-delete-legacy-import-bridge-plan.md` —— #124 的 plan doc，需补 `claims: {}` 或对应 SHAs。模式参考 PR #115 `2412566`（plan 003 Phase A canary plan-claims hotfix）：1 行 frontmatter 改动即可。
- `docs/plans/2026-05-20-005-refactor-worktree-branch-cleanup-plan.md` —— R1 的 verification 风格 + R5 origin prune 流程的直接模板。

### Institutional Learnings

- [[feedback-plan-doc-on-cutoff-needs-claims-block]] —— `< 2026-05-20` 才 grandfather；plan-doc 日期 `= 2026-05-20` 必须 `claims:{}` opt-out 或对应 SHA。**写完 frontmatter 改动 push 前** 跑 `plan-check; echo $?` 验。
- [[feedback-gh-merge-delete-branch-egg-info-noise]] —— `gh pr merge --squash --delete-branch` 后可能打印 "未暫存的變更" warning（post-merge worktree switch hit dirty egg-info from `pip install -e .`），**merge 本身已成功**；用 `gh pr view <N> --json state` 验。
- [[reference-phase0-local-rehearsal-branches]] —— `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条 6/01 Pass ship load-bearing；R5 prune 不许动。
- [[feedback-verify-external-commits-before-push]] —— prune origin branch 前 `gh pr list --search "head:<branch>" --state all` 验对应 PR 已 merged。
- [[feedback-worktree-concurrent-switching]] —— 退 worktree 前 `git status --porcelain` 验 clean；本 plan 实测 6 worktree 全 clean，无并发 agent 残留。
- [[reference-plan-check-cli]] —— `plan-check` 在 plan-claims-gate.yml 跑；exit 8 = drift / exit 7 = grandfather miss。

## Key Technical Decisions

- **#124 走 frontmatter `claims: {}` opt-out**：legacy-bridge 这种 mass refactor 触及 70+ 文件，列具体 paths/shas 比 opt-out 更脆（squash 后 SHAs 消失 → exit 7）。Plan doc 不锁 grandfather 范围，opt-out 是正确语义。
- **R2 banner merge 不强制内部顺序**：5 PR 零代码冲突，plan-doc 在不同 line 上的 checkbox flip 是 disjoint hunks（feasibility 验过 ~30+ 行间隔），3-way merge handles。按 CI/review 自然顺序合即可。
- **Unit 2/3 land 顺序锁 Unit 3 → Unit 2**（详见 Open Questions §B 决策记录）：Unit 1 done → Unit 3（#124 mass refactor）squash 进 main → Unit 2 在新 main 之上 squash 5 个 banner PR。理由：#124 blast radius 大（73 文件）→ stale window 越短越好；banner PR 小（~3 文件/PR）→ rebase 干净，落 #124 之上无副作用。
- **R5 单独手动 prune**：`origin/feat/medium-graphql-spike-scaffold` 不在任何 PR head 上（#119 已 squash），`gh pr merge --delete-branch` 没机会清；需要 `git push origin --delete feat/medium-graphql-spike-scaffold` 一次性收。
- **R6 worktree 退方式**：优先 `bash scripts/prune-stale-worktrees.sh --dry-run` 再 `--force`，沿用 Plan 005 [[2026-05-20-005]] Unit 2 已验过的主路径。脚本 header + `_worktree_safety.sh:33-59` 明确文档化"只可能 false-negative（漏清），不可能 false-positive（误清 live work）"——dirty + unmerged-tip 双重 guard。若 dry-run 输出不包含全部 6 个目标 worktree（不预期），fallback 为逐个 `git worktree remove <path>` 显式删除。
- **R8 验证用 `0 fail` 而非 pass-count 比较**：pytest 通过数是 scalar，5 banner PR 各 +1 测试文件 + #124 删旧测试文件后，pass-count 会同时变大变小，scalar 比较失去信号。R8 改用三联绑：(a) baseline SHA = `git rev-parse origin/main` 记 Unit 1 起点，(b) baseline quad = passed/failed/xfailed/xpassed 四元组，(c) Unit 5 verification 唯一硬 gate = `failed == 0`；passed-count 漂移仅作 informational 日志。

## Open Questions

### Resolved During Planning

- #124 红灯是什么？→ 仅 `plan-claims-gate` job `76905247475`，单源 fixable。
- 5 banner PR 谁先合？→ 任意顺序，零代码冲突，plan-doc auto-merge。
- #124 与 banner PR 互锁？→ 否，零文件交集。
- `origin/feat/medium-graphql-spike-scaffold` 是 dead head 还是 active？→ #119 已 squash `ba74bd2`，dead head，可删。
- worktree 是否有 dirty WIP？→ 实测 6 个全 clean。

### Deferred to Implementation

- #124 修后 plan-claims-gate 是否一次通过？若 plan doc 还隐含其他 drift（比如 `paths:` 字段 typo），可能 exit 8 复发；**escalation**：本地 `plan-check` exit ≠ 0 时输出 stderr 给 user，不进 git push。
- banner 5 PR 是否在 squash 过程中真出现 plan-doc conflict？tripwire grep (Unit 1) 已预验 disjoint hunks；**escalation**：若仍 conflict，按 Scope Boundaries 铁律分流（checkbox-only 自动 resolve / 其他停手）。
- pytest baseline quad（R8 比对用）—— Unit 1 跑一次记 `pytest-baseline.sha` + `pytest-baseline.quad`；**escalation**：若 baseline 自身 failed > 0（main 已断），停手不进 Unit 2 升级 risk 给 user。

### §B Land Order Locked — Unit 3 (#124) FIRST, then Unit 2 (banner)

**Decision (post-review)**：deepening 阶段锁定 **Unit 3 → Unit 2** 顺序。

**Rationale**：
- #124 是 73-file mass refactor，blast radius 大；stale-window 越短，与未知并发 PR 冲突概率越低（adversarial 论据）。
- Banner PR 各 ~3 文件，rebase 干净，落 #124 之上无副作用。
- Unit 1 完成后，#124 状态转 CLEAN 即可 merge；不必等 banner 5 个全 merge 才动 #124。
- Unit 2 在 Unit 3 后 land，origin/main 已含 legacy-bridge 删除，banner adapter 已用 canonical imports（feasibility 验过），squash 干净。

## Implementation Units

- [ ] **Unit 1: Baseline + #124 plan-claims-gate 修复**

**Goal:** 跑 pytest 锁 baseline；在 `bp-delete-legacy-bridge` worktree 补 `claims: {}` 到 plan doc frontmatter，本地 `plan-check` 验绿，push 触发 #124 CI 重跑。

**Requirements:** R1, R8 (baseline 部分)

**Dependencies:** 无。

**Files:**
- Read: `docs/plans/2026-05-20-006-refactor-delete-legacy-import-bridge-plan.md`（确认 frontmatter 现状）
- Modify (in `bp-delete-legacy-bridge`): `docs/plans/2026-05-20-006-refactor-delete-legacy-import-bridge-plan.md`（frontmatter 加 `claims: {}` 一行）
- Read-only: `scripts/plan-check`（CLI 入口验证）

**Approach:**
- **Plan-008 self-check**（先于一切）：`plan-check docs/plans/2026-05-20-008-refactor-worktree-branch-cleanup-r2-plan.md; echo $?` 期望 exit 0；不通过则先修本 plan-doc 再继续（per [[feedback-plan-doc-on-cutoff-needs-claims-block]]）。
- **Baseline 三联绑**：主 worktree 跑 `git rev-parse origin/main > pytest-baseline.sha` + `pytest tests/ -q 2>&1 | tail -1 > pytest-baseline.quad`（最后一行含 passed/failed/xfailed/xpassed 四元组）；两件 artifact 同时落盘，Unit 5 比对用。
- **Tripwire grep**（plan 假设验证）：`for pr in 118 120 121 122 123; do gh pr diff $pr -- docs/plans/2026-05-20-004-feat-per-adapter-embed-banner-plan.md | head -3; done` 确认每 PR 只动一行（disjoint hunks），印证零代码冲突假设。
- `cd bp-delete-legacy-bridge`；编辑 plan-006 frontmatter，**在 `date:` 行下 insert 一行 `claims: {}`（新 key，不修改任何现有 key）**——plan-006 当前 frontmatter 无 `claims:` 字段（feasibility 已验），参照 PR #115 `2412566` 的修法。
- 本地 `plan-check docs/plans/2026-05-20-006-refactor-delete-legacy-import-bridge-plan.md; echo $?`，期望 exit 0。
- `git add` + `git commit -m "fix(plan-claims): add explicit claims: {} opt-out for legacy-bridge plan"` + `git push`。
- 等 GitHub Actions 上 `plan-claims-gate` 重跑变绿（约 1-3 分钟）。

**Patterns to follow:**
- PR #115 `2412566` ([[project-pr113-url-derive-v1]])：单行 frontmatter 改动 + commit message 模式。

**Test scenarios:**
- Happy path：plan-008 + plan-006 `plan-check` 均 exit 0；tripwire grep 输出 5 个 disjoint hunk；pytest quad 中 failed=0；push 后 GitHub plan-claims-gate 从 FAILURE 转 SUCCESS；#124 状态从 UNSTABLE 转 CLEAN。
- Error path（plan-006）：`plan-check` 仍 exit 8 → 看 stderr 是否提示其他 drift 项（`paths:` typo 等）；execution 期决定补救路径（补 paths / 改 SHAs / 进一步 opt-out）。
- Error path（plan-008 self-check）：本 plan 自己 exit ≠ 0 → 先修自己再开 Unit 1；不通过 self-check 不应进 main 路径。
- Edge case：pytest baseline failed > 0 → 暂停升级 risk 给用户，不进 Unit 2（避免在已断 main 上叠 6 个 merge）。
- Edge case：tripwire grep 输出某 PR 在 plan-doc 上多于 1 行 diff（或与他 PR 撞行）→ 暂停 + 列 diff，重审"零代码冲突"假设。

**Verification:**
- `pytest-baseline.sha` + `pytest-baseline.quad` 双 artifact 落盘。
- #124 `gh pr view 124 --json mergeStateStatus` 返 `CLEAN`，所有 checks `SUCCESS/SKIPPED`。
- plan-008 self-check 历史留痕（exit 0 复跑得证）。

- [ ] **Unit 2: 5 个 banner PR squash-merge**

**Goal:** 把 #118/#120/#121/#122/#123 全部 squash-merge 到 main，自动删 origin head 与本地 branch。

**Requirements:** R2, R4

**Dependencies:** Unit 1 完成（baseline 落盘）+ Unit 3 完成（#124 已 squash 到 main）。新 main 含 legacy-bridge 删除，banner PR rebase 干净再 merge。

**Files:**
- No file edits（merge 动作）。
- 退后会消失：local branch `feat/banner-u{2,3,4,5,6}-*` + origin head 同名。

**Approach:**
- **Atomic merge loop（推荐 shape）**：
  ```bash
  for pr in 118 120 121 122 123; do
    gh pr merge $pr --squash --delete-branch || break
    state=$(gh pr view $pr --json state -q .state)
    [ "$state" = "MERGED" ] || { echo "PR #$pr state=$state, abort"; break; }
    gh pr view $pr --json mergeCommit -q .mergeCommit.oid >> banner-squash-shas.txt
  done
  ```
  逐 PR 验 state + 记 squash SHA；任一 PR fail 立即 break，已合的不回退，下一次 resume 直接续跑 loop。
- 若中途某 PR auto-rebase 失败（不预期，tripwire grep 已确认 disjoint hunks），按 Scope Boundaries 的 plan-doc conflict 铁律处理：checkbox-only 自动 resolve，否则停手。
- 全 5 个 merged 后 `git fetch origin --prune` 同步本地视图。

**Patterns to follow:**
- Plan 005 Unit 5（PR-merge prune 流程）。
- [[feedback-gh-merge-delete-branch-egg-info-noise]]：merge 后忽略 "未暫存的變更" warning（来自 `pip install -e .` 的 egg-info），用 `gh pr view ... --json state` 验。

**Test scenarios:**
- Happy path：5 PR 全 state=MERGED；`git branch -r` 不再含 `origin/feat/banner-u*`；本地 `git branch` 不再含 5 条 banner branch。
- Edge case：某 PR auto-rebase 失败 → 停下记录该 PR 编号，其余 4 个继续，最后 Unit 5 verification 时单独处理。
- Failure：`gh pr merge` 报 protected branch / required check missing → 不预期；暂停升级 risk 给用户。

**Verification:**
- 5 个 PR 全 state=MERGED，5 个 squash SHA 落盘。
- `git fetch --prune` 后 `git branch -r | grep banner-u` 空输出。
- `git branch | grep banner-u` 空输出。

- [ ] **Unit 3: #124 legacy-bridge squash-merge**

**Goal:** Unit 1 绿后 squash-merge #124，自动删 origin/local branch。

**Requirements:** R3, R4

**Dependencies:** Unit 1 完成（#124 状态转 CLEAN）。**land 顺序锁定为先于 Unit 2**（§B 决策）——优先收窄 73-file mass refactor 的 stale window。

**Files:**
- No file edits（merge 动作）。
- 退后消失：`feat/delete-legacy-import-bridge` local + origin。

**Approach:**
- `gh pr view 124 --json mergeStateStatus` 验 CLEAN（Unit 1 应已确保）。
- **Delete-branch permission preflight**（第一次 merge 前一次性验，6 PR 共享同 protection rule）：`gh api "repos/$(gh repo view --json nameWithOwner -q .nameWithOwner)/branches/feat/delete-legacy-import-bridge/protection" 2>&1 | head -3`——404/Not Found = no protection（预期），任何 200 + restricted-deletion 字段 → 停手记账，本 plan 路径假设不成立。
- `gh pr merge 124 --squash --delete-branch`。
- `gh pr view 124 --json state mergeCommit` 记 squash SHA。
- **Post-merge head-deletion assertion**：`git ls-remote origin feat/delete-legacy-import-bridge` 应空输出。非空 → origin head 未删（permission silently failed），记入 Unit 4 batch prune 范围。

**Patterns to follow:**
- 同 Unit 2 流程。

**Test scenarios:**
- Happy path：state=MERGED；origin/feat/delete-legacy-import-bridge 自动消失。
- Edge case：merge 时 auto-rebase 因为 Unit 2 banner PRs 改了某个 adapter 文件 → 不预期（banner PR 不动 `__init__.py`/legacy import 路径）；若发生则停手分析。
- Failure：merge 后下游 main CI（3.11/3.12/footprint/monolith）红 → 暂停，记 squash SHA 给用户决定 revert 路径。

**Verification:**
- #124 state=MERGED。
- `git fetch --prune` 后 `git branch -r | grep delete-legacy-import-bridge` 空。

- [ ] **Unit 4: prune `origin/feat/medium-graphql-spike-scaffold` 残留 head**

**Goal:** 删唯一一条 squash-merge 后 origin 未自动删的 head。

**Requirements:** R5

**Dependencies:** 无（可在 Unit 1/2/3 任何时候做，但放最后避免和 fetch 时序竞争）。

**Files:**
- No file edits。
- 退后消失：`origin/feat/medium-graphql-spike-scaffold`。

**Approach:**
- 验对应 PR #119 已 squash-merge：`gh pr view 119 --json state mergeCommit` → state=MERGED。
- **Concurrent-agent turf guard**：`git worktree list | grep -i 'medium-graphql-spike' && echo "ABORT: concurrent worktree exists, skip prune" || git push origin --delete feat/medium-graphql-spike-scaffold` —— 若并发 agent 在该 branch 上有 worktree（例：尝试 resume Phase B spike），不删 origin head 避免他们 orphan。
- `git fetch origin --prune`。

**Test scenarios:**
- Happy path：`git push` 报 `- [deleted]`；fetch --prune 后 `git branch -r | grep medium-graphql-spike` 空。
- Edge case：origin 报 branch 不存在（被并发 cleanup 抢先删）→ no-op 接受。
- Failure：报 protected branch → 不应发生（非 main/telegraph）；暂停。

**Verification:**
- `git branch -r` 不含 `origin/feat/medium-graphql-spike-scaffold`。

- [ ] **Unit 5: Worktree 退 + 最终 invariant 验证**

**Goal:** 6 个 banner+legacy worktree 全退；跑 R8 全套 invariant。

**Requirements:** R6, R7, R8

**Dependencies:** Unit 2 + Unit 3 完成（PR 已 merged 才能安全退对应 worktree）。

**Files:**
- Remove (worktree paths)：`bp-banner-u2-telegraph` / `bp-banner-u3-blogger` / `bp-banner-u4-hashnode` / `bp-banner-u5-velog` / `bp-banner-u6-ghpages` / `bp-delete-legacy-bridge`。

**Approach:**
- **优先路径**：`bash scripts/prune-stale-worktrees.sh --dry-run` 看输出是否覆盖全部 6 个 banner+legacy worktree（PR 已 squash → branch-tip ancestor-of origin/main → 脚本应判 stale）；覆盖完整则 `bash scripts/prune-stale-worktrees.sh --force` 一次性收。
- **Fallback 路径**（dry-run 漏某些 worktree）：逐个 `git worktree remove <path>`。若 `git worktree remove` 报 dirty 但 diff 全是 `*.egg-info/` / `__pycache__/`（`pip install -e .` 副产物），用 `--force` 安全（build artifact 不是源）；diff 含真改动则停手列 diff。
- 全退后 `git worktree list` 仅含主 worktree。
- **跑 R8 invariant**（pytest quad 比对）：
  - `git branch -a` 仅 main + `origin/main` + `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条（R7 锁定）。
  - `git worktree list` 仅 1 项。
  - `pytest tests/ -q 2>&1 | tail -1 > pytest-post.quad`，验 `failed == 0`（硬 gate）；passed/xfailed/xpassed 与 Unit 1 baseline 对比仅作 informational log（5 banner PR 加 ~5 测试文件 + #124 删 legacy 测试，passed 数预期净变化为正但具体值不可预算）。

**Patterns to follow:**
- Plan 005 Unit 5（同款 verification 风格）。

**Test scenarios:**
- Happy path：6 个 worktree 全退；`git branch -r` 仅 main + 4 telegraph；pytest 通过数 ≥ baseline。
- Edge case：`pytest` 通过数 < baseline → main 上有 regression（不预期，因 6 个 PR 全 CI 绿）；暂停记账给用户决定 revert 哪个 squash。
- Failure：某 worktree `git worktree remove` 报 dirty 且 diff 非 egg-info → 暂停，列 diff 给用户决定。

**Verification:**
- `git branch -a | wc -l` 应是 1 (main) + 1 (origin/HEAD) + 1 (origin/main) + 4 (telegraph) = 7 行。
- `git worktree list | wc -l` = 1。
- `pytest-post.quad` 含 `failed=0`（硬 gate）；passed delta 与 Unit 1 baseline 记为 log，**不作 gate**。
- 主 worktree `git status` clean。

## System-Wide Impact

- **Interaction graph:** 影响面在 git remote/local state，不动任何 prod code。仅 Unit 1 改 1 个 plan-doc frontmatter（无 code path 影响）。
- **Error propagation:** Unit 1 失败 block Unit 3 但不 block Unit 2；Unit 2 失败可暂停记账继续 Unit 3；Unit 4 完全独立；Unit 5 必须 Unit 2+3 都完成才跑。
- **State lifecycle risks:** 删 branch + 退 worktree 后 commit 仍在 `git reflog` / `git fsck --unreachable` 30 天。6 个 worktree 退前已 baseline 实测 clean，无未保存 WIP 风险。
- **API surface parity:** 零 prod API 改动；R9 extension contract / `_DOFOLLOW_BY_CHANNEL` / 6 monolith ceiling 全不动。
- **Integration coverage:** Unit 5 跑 `pytest tests/` 全套；plan-claims-gate / 3.11 / 3.12 / footprint regression / monolith budget 全部走 PR squash 时的 CI（已在 PR 阶段绿）。
- **Unchanged invariants:** `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条（R7 / [[reference-phase0-local-rehearsal-branches]]）；R9 adapter registry；Plan 005 已建立的 cleanup pattern。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| #124 plan-claims-gate 修后仍报其他 drift | Unit 1 本地 `plan-check` 预跑；exit ≠ 0 时看 stderr 决定补救路径（补 `paths:` 字段 / 改 SHAs / 进一步 opt-out） |
| 5 banner PR squash 时 plan-doc auto-merge 失败 | 不预期（仅 checkbox flip）；若发生手动 resolve 保留全 5 checkbox flipped 态，不顺手改其他段落 |
| #124 squash 后 main CI 红（mass refactor 70+ 文件） | 已在 PR 阶段过 CI；若 squash 后红，记 squash SHA 准备 revert PR；不在 R2 plan 内处理 follow-up |
| `gh pr merge --delete-branch` 报 egg-info dirty warning | [[feedback-gh-merge-delete-branch-egg-info-noise]] 已知噪声；用 `gh pr view --json state` 验真实 merge state |
| 误删 `origin/local/telegraph-unit*-staged` | R7 锁定 + Unit 4 仅显式 delete `medium-graphql-spike-scaffold`，不批量循环 |
| worktree 退时报 dirty | 实测 6 个 worktree 全 clean；若 squash 过程产生 egg-info 噪声，先看 diff 内容再决定 `--force` |
| 并发 agent 在执行期再开新 worktree / PR | 执行前**identity check**（非 count check）：枚举预期 6 个 PR 编号 (#118/#120/#121/#122/#123/#124) + 6 个 worktree path；任一缺失 → 停手 turf check；任何额外 PR/worktree（如 #125）→ 忽略不动，本 plan 只处理列出的 6 对象 |
| 本 plan doc 日期 2026-05-20 触发 plan-claims-gate exit 8 | frontmatter 已 `claims: {}` opt-out（与 #124 的修法同源）；Unit 1 完成后顺手 `plan-check docs/plans/2026-05-20-008-*.md; echo $?` 验 |

## Documentation / Operational Notes

- 本 plan 完成后不更新 AGENTS.md（Plan 005 已建立 cleanup pattern，R2 是其延续，无新 pattern 引入）。
- Unit 1 commit message 模板：`fix(plan-claims): add explicit claims: {} opt-out for legacy-bridge plan`。
- Unit 2/3 squash 都走 `gh pr merge --squash --delete-branch`，让 GitHub 写 squash commit message（不自定义）。
- Unit 4 `git push --delete` 后无需补 commit。
- 若 Unit 5 pytest 出 regression，记 squash SHA 序列给用户，**不在本 plan 内**做 revert 决策。

## Sources & References

- **Origin reference:** [docs/plans/2026-05-20-005-refactor-worktree-branch-cleanup-plan.md](2026-05-20-005-refactor-worktree-branch-cleanup-plan.md)（R2 的直接前驱，pattern 来源）
- Related PRs (待 R2 land):
  - #118 / #120 / #121 / #122 / #123 — Banner Unit 5 全套
  - #124 — Legacy import bridge 删除（Plan 006）
- Related PRs (R2 模板):
  - PR #115 `2412566` — plan-claims `claims: {}` opt-out 修法
  - PR #119 `ba74bd2` — spike scaffold squash（origin head 残留源头）
- Memory:
  - [[feedback-plan-doc-on-cutoff-needs-claims-block]] — #124 修复路径
  - [[feedback-gh-merge-delete-branch-egg-info-noise]] — merge 时 egg-info 噪声判读
  - [[reference-phase0-local-rehearsal-branches]] — telegraph 保留依据
  - [[reference-plan-check-cli]] — plan-claims-gate semantics
  - [[feedback-worktree-concurrent-switching]] — worktree 退前 clean 验证
