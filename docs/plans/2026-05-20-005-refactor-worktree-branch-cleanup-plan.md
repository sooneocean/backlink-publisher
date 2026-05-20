---
title: Worktree / Branch / PR Cleanup — Post-2026-05-20 Ship Burst
type: refactor
status: completed
date: 2026-05-20
origin: docs/brainstorms/2026-05-20-worktree-branch-pr-cleanup-requirements.md
claims: {}  # opt-out: cleanup plan, no code SHAs to anchor; tracked
            # by reflog of the ship-burst PRs (#108..#127) which already
            # individually plan-claim their own deliverables.
---

# Worktree / Branch / PR Cleanup — Post-2026-05-20 Ship Burst

## Overview

清理 2026-05-20 一日 13 PR squash 之后的本地/远端 debris。**Planning 阶段验证翻盘**：brainstorm 假设 A 类 7 个 commit 全部未合并，实测 **5 个已通过 `git merge --no-ff` 直接 land 到 main**（commit `8a00a8f` / `c924b25` / `6e2062f` / `ee245e0` / `3f45aa5`），只剩 chore-catchall 2 个 commit 真未合并 + bp-roundtrip-a2 active Plan 003 Phase A.2 WIP。整体降到 Lightweight scope。

## Problem Frame

- 10 个本地 branch ahead of main：其中 5 个 tip 已 ancestor-of main（直接 merge 已发生），2 个对应已 PR-merged（#113/#115），1 个是真未合 chore-catchall，2 个是 active session 本身（main + bp-roundtrip-a2）。
- 14 个远端 branch ahead of main：4 个 telegraph rehearsal（保留 per [[reference-phase0-local-rehearsal-branches]]）+ 多个 squash-merge 已删 head 未清。
- 3 个 worktree：主 worktree 有 2 untracked docs；`bp-banner-image-gen` orphan；`bp-roundtrip-a2` dirty Plan 003 Phase A.2 WIP（HEAD `57ce984` + writer.py 改动 + 新测试未 commit）。
- AGENTS.md 第 223-230 行已有 "Worktree Cleanup" 段 + `scripts/prune-stale-worktrees.sh` + `BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1`——R11 从"新增"变成"使用现有工具"。

## Requirements Trace

- R1. A 类 commit 验证 gate (see origin) — **planning 期完成**，结论：5/7 已合并，2/7 真未合并（chore-catchall），无可疑 foreign-agent regression（A 类 5 commit 已 land 且 main 后续工作建立其上无故障）。
- R2. 通过 R1 的 commit 逐个 rebase + 独立 PR (see origin) — **scope 大幅缩小**：5 个已合不需 PR；剩 chore-catchall 2 commit 拆 3 个原子 PR/commit。
- R3. PR 顺序按冲突依赖 (see origin) — 因 R2 缩小，仅余 chore PR 顺序（adapter test fixes 优先因为可能挡 CI）。
- R5/R6. bp-roundtrip-a2 完成 Plan 003 Phase A.2 (see origin) — 独立 unit。
- R7/R8. C 类 prune (see origin) — 全 batch 删除。
- R9. bp-banner-image-gen 退出 (see origin) — 已确认 PR #110 squash 覆盖等价内容。
- R10. D 类 telegraph rehearsal 不动 (see origin)。
- R11. AGENTS.md "Worktree cleanup" 段已存在 — **不新增，改为 audit 现有 prune-stale-worktrees.sh 是否成功 detect 当前 5 个 direct-merged branch**；若 detect 失败补 patch；若成功则跑一次。

## Scope Boundaries

- 不动 `origin/local/telegraph-unit{2,4,5,6}-staged` 4 条（[[reference-phase0-local-rehearsal-branches]] 6/01 Pass ship load-bearing）。
- 不做 chore-catchall 内容的 refactor / 风格改动 / 测试补充——3 adapter test fixes as-is land 或丢弃。
- 不触碰 4 个 post-hoc plan doc 内容（comprehensive-optimization-proposal / autoderive-and-ui-polish-requirements / banner-image-gen-plan / autoderive-v1-plan）——它们对应 feature 都已 ship，docs 只做归档 commit 不修订。
- 不追求 `git branch -r` 完全空——telegraph rehearsal + 当前 active PR head 保留是预期态。
- 不重新 land 已在 main 的 5 个 direct-merge commit（726330e / d186f3b / bb6b48b / 1bc3054 / 60437c3）。
- 不修复 chore-catchall Makefile `test:` target 的 `PYTHONPATH=.` 错误（应是 `PYTHONPATH=src` per [[feedback-pythonpath-src-for-sibling-worktree]]）——见 Unit 3。

## Context & Research

### Relevant Code and Patterns

- `scripts/prune-stale-worktrees.sh` — 检测 worktree merged into origin/main，flags `--dry-run` `--force`；exit 2 on failure；测试 `tests/scripts/test_prune_stale_worktrees.py`。
- `scripts/install-post-merge-hook.sh` — 安装 post-merge hook，`BACKLINK_PUBLISHER_WORKTREE_AUTOREMOVE=1` 自动清理。
- `scripts/_worktree_safety.sh` — 共享安全函数（dirty check / merge-base ancestor check）。
- `monolith_budget.toml` — 当前 ceiling：`publish_backlinks.py=820` (post-d186f3b)，`plan_backlinks/core.py=960`，`config/writer.py=360`。
- Plan 003 Phase A.1 已 ship (PR #114 squash `14d3651`)；Phase A.2 是 `[ghpages]/[hashnode]/[writeas]` 加入 `_SAVE_CONFIG_KNOWN_ROOTS`，HEAD 已有 commit `57ce984 feat(config): register ghpages/hashnode/writeas as managed roots in save_config`，dirty diff 是 writer.py emission + 新测试。
- 3 个 adapter test 文件已在 main 上 expect 老的 `{"token": "..."}` shape；chore-catchall 改成 `{"token": "...", "token_rev": 1}` 对齐 1bc3054 的 `token_rev` 抽取——**需 pytest 验证当前 main 这 3 个 test 是否 fail**。

### Institutional Learnings

- [[feedback-foreign-agent-wip-spreads-as-broken-replace]] — 12-min 窗口 + 空 body + 同 author email 三联号是并发 agent fingerprint；但 5/7 已 land 证明这些 commit 是 real 工作而非破坏性 WIP。
- [[feedback-verify-external-commits-before-push]] — push 前 grep memory；A 类 5 commit 通过 `8a00a8f`/`c924b25` 等 merge commit landed 是合法历史。
- [[feedback-pythonpath-src-for-sibling-worktree]] — Makefile 里 `PYTHONPATH=.` 是 bug，按 src/ 安装的 editable layout 应是 `PYTHONPATH=src`；但本 plan scope 不修。
- [[reference-plan-check-cli]] — plan-claims-gate exit 8 在 plan-doc 日期 `>= 2026-05-20` 触发；本 plan 文件日期 2026-05-20 → 需 `claims: {}` opt-out 或对应 SHAs。
- [[feedback-plan-doc-on-cutoff-needs-claims-block]] — 写完本 plan 立刻 `plan-check; echo $?` 验。

### External References

无——纯内部 cleanup。

## Key Technical Decisions

- **5 个 direct-merge branch 仅删除不重 land**：merge-base ancestor 验证已通过，无 archeology 价值。
- **chore-catchall 不整体 land**：3c4f360 + 8a831f0 包含 9 个文件，混合了 (a) 3 个 adapter test 修复（真 fix）、(b) Makefile + scripts/check-all.sh（low value 但 cheap）、(c) 4 个 plan doc（post-hoc 归档）+ 1 个 plan 003 副本（已在主 worktree untracked，**避免冲突**）。拆 3 个原子动作。
- **Plan 003 Phase A.2 单独 PR**：bp-roundtrip-a2 的 active WIP 是真新功能（3 个 channel 加入 managed roots），独立可 review，不混在 cleanup 里。
- **prune-stale-worktrees.sh 信任优先**：脚本已经测试过、AGENTS.md 文档化、design 就是 detect this case；先 `--dry-run` 看输出是否包含 5 个 direct-merge branch + 2 个 PR-merged branch + bp-banner-image-gen orphan worktree，如果 detect 正确则一次 `--force` 收尾。
- **plan-claims-gate opt-out 用 `claims: {}`**：本 plan 不引用任何 grandfather SHA / PR / file path（cleanup 的对象是 branch 不是 code path），无 drift gate 触发面，opt-out 是正确语义。

## Open Questions

### Resolved During Planning

- A 类 7 commit 哪几个已 merged？→ 5 个已 merged（726330e/d186f3b/bb6b48b/1bc3054/60437c3），2 个未 merged（3c4f360/8a831f0）。
- A 类 commit 是否是 foreign-agent regression？→ 否，5 个已 land + 后续 PR 建立其上无故障，证明是 real 工作；签名（12-min 窗口/同 author email/空 body）是 parallel agent batch 但非 broken。
- chore-catchall 怎么拆？→ 3 个原子 PR/commit 分别处理 adapter tests / scripts+Makefile / plan docs，避免 single-point revert 难的问题。
- AGENTS.md R11 写哪里？→ 第 223-230 行已存在，本 plan 改 audit 现有脚本。
- 3 个 adapter test fix 是否真 fix？→ **deferred** — bp-roundtrip-a2 Phase A.2 完成后跑 pytest 看是否真 fail，再决定是否需要 land。
- bp-banner-image-gen worktree 是否有遗漏？→ Unit 5 Verification 包含 `git diff origin/main..feat/banner-image-gen --stat -- :!docs :!*.md` 比对；空 stat 即确认。

### Deferred to Implementation

- chore-catchall Makefile `PYTHONPATH=.` bug — 现在 land 进 main 会引入即时已知缺陷；execution 期决定要么 land 时同 PR 改成 `PYTHONPATH=src`、要么干脆丢弃 Makefile 部分。
- `scripts/check-all.sh` 13 行内容是 `py_compile + pytest` wrapper 还是其他？execution 期 `git show 8a831f0 -- scripts/check-all.sh` 看具体内容再决定 land 还是丢。
- 现存 `scripts/prune-stale-worktrees.sh` 在 `--dry-run` 输出是否包含 5 个 direct-merge branch？如未包含，可能是脚本只检测 worktree 不检测纯 branch；execution 期看 dry-run 输出决定是否需要补 `git for-each-ref` 一段。

## Implementation Units

- [x] **Unit 1: Audit `scripts/prune-stale-worktrees.sh --dry-run` 输出 + 跑 `pytest tests/`**

**Goal:** 建立 cleanup baseline + 验证现有 pruner 覆盖范围 + 看 main 上有无 already-failing 测试（3 个 adapter token test）。

**Requirements:** R1, R11

**Dependencies:** 无。

**Files:**
- Read: `scripts/prune-stale-worktrees.sh`
- Read: `tests/scripts/test_prune_stale_worktrees.py`

**Approach:**
- `bash scripts/prune-stale-worktrees.sh --dry-run` 看输出包含哪些 worktree/branch；记 stdout。
- `pytest tests/` 跑全套（必带 PYTHONHASHSEED=0；editable install 内）；记失败列表。
- 如果 3 个 adapter test (`test_adapter_ghpages.py::TestGhpagesTokenIO::test_save_and_load_round_trip` 及类似 hashnode/writeas) **真 fail** → 标记 Unit 3a 必做。
- 如果 dry-run **未** 输出 5 个 direct-merge local branch（`feat/browser-login-service` 等），记入 Unit 2 手动 prune 范围。

**Test scenarios:**
- Happy path: dry-run exit 0 + stdout 包含至少 1 个候选 + pytest baseline ≥ 2604 passed（[[project-pr94-webui-store-env-isolation]] 基线）。
- 边界: pytest fail 仅限那 3 个 adapter token test → Unit 3a 路径生效；fail 数 ≠ 3 → 暂停升级 risk 给用户。

**Verification:**
- `dry-run-output.txt` + `pytest-baseline.txt` 两件 artifact 落盘（临时位置即可）。
- 已知：剩多少 candidate 给 Unit 2、3 adapter test 是否 fail、bp-banner-image-gen 是否被 pruner detect。

- [x] **Unit 2: Prune 5 direct-merge + 2 PR-merge local branch + orphan worktree**

**Goal:** 删 7 个本地 branch + 1 个 orphan worktree，使 `git branch` 仅余 `main` + `feat/save-config-extend-managed-roots`。

**Requirements:** R7, R9

**Dependencies:** Unit 1（决定 pruner 走 auto 还是手动）。

**Files:**
- Delete: 7 个本地 branch（`feat/browser-login-service`、`feat/concurrent-publish-leases`、`feat/exception-classification`、`feat/persistence-safe-write`、`feat/webui-cover-image-wiring`、`feat/homepage-url-autoderive-v1`、`fix/plan-claims-opt-out-url-derive`、`feat/banner-image-gen`）。
- Remove: worktree `bp-banner-image-gen/`。

**Approach:**
- 优先路径：`bash scripts/prune-stale-worktrees.sh --force`（若 Unit 1 确认 detect 正确）。
- 备用路径：手动 `for b in <list>; do git branch -D "$b"; done` + `git worktree remove --force bp-banner-image-gen`。
- bp-banner-image-gen 退出前先 `git diff origin/main..feat/banner-image-gen --stat -- :!docs :!*.md`，stat 空（或仅含已知 squash-internal 文件）→ 确认无遗漏 → 退。

**Test scenarios:**
- Happy path: `git branch` 仅余 2 条；`git worktree list` 仅余 2 项；`git fsck --dangling` 无新 orphan commit。
- 边界: bp-banner-image-gen `git diff` 非空 stat → 暂停，输出 diff 给 user 决定。
- 失败：`git worktree remove` 报 dirty → 是 Unit 4 走错路径（应只剩 bp-roundtrip-a2 dirty），暂停。

**Verification:**
- `git branch` output 仅 `main` + 当前 active feature branch。
- `git worktree list` output 仅 main worktree + bp-roundtrip-a2。
- pytest 仍 pass（Unit 1 baseline 一致）。

- [x] **Unit 3: chore-catchall 3 commit 拆分处置** *(并发 agent 在 commit `5bb27b2` 完成 3a 适配测试 + 3c plan 归档；3b Makefile/scripts 按 plan 决策丢弃)*

**Goal:** 从 `chore/debris-catchall-2026-05-20` 抽出真 value 内容到独立 commit/PR，删除 catch-all branch。

**Requirements:** R2, R4

**Dependencies:** Unit 1（决定 3a 是否必做）。

**Files:**
- Affected (3a): `tests/test_adapter_ghpages.py`、`tests/test_adapter_hashnode.py`、`tests/test_adapter_writeas.py`。
- Affected (3b): `Makefile`（新增，**带 `PYTHONPATH=.` → `PYTHONPATH=src` 修复**）、`scripts/check-all.sh`（新增）。
- Affected (3c): `docs/plans/2026-05-20-001-feat-banner-image-gen-plan.md`、`docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1-plan.md`、`docs/brainstorms/2026-05-20-comprehensive-optimization-proposal.md`、`docs/brainstorms/2026-05-20-homepage-url-autoderive-and-ui-polish-requirements.md`。
- Delete: branch `chore/debris-catchall-2026-05-20`（含 commit `3c4f360`、`8a831f0`）。

**Approach:**
- **3a (conditional, only if Unit 1 confirms 3 test fail)**: cherry-pick 8a831f0 `tests/test_adapter_*.py` 3 文件部分到 main，开 PR `fix(tests): align adapter token tests with token_rev shape`。独立 atomic PR 因为这是真 fix 可能挡 CI。
- **3b (decision needed)**: Makefile + scripts/check-all.sh。Execution 期 `git show 8a831f0 -- scripts/check-all.sh` 看具体内容；若是简单 wrapper → 一个 chore PR 同时修 Makefile `PYTHONPATH=.` → `PYTHONPATH=src`；若内容 thin / 价值不明 → 丢弃。
- **3c**: 4 个 plan/brainstorm doc 是 post-hoc 归档（对应 feature 都已 ship）。**直接 commit 到 main**（不开 PR）作为 archival，commit message `docs: archive post-hoc plans for shipped features (#110, #113)`。
- 全部抽取完成后 `git branch -D chore/debris-catchall-2026-05-20`。

**Execution note:** 3a/3b/3c 三步独立完成，任一失败不阻塞其他；3a 优先因可能挡 CI。

**Test scenarios:**
- 3a happy path: cherry-pick + PR 后 3 个 token round_trip test 从 fail 翻 pass；其他 test 数不变。
- 3a 反例: pytest 显示 6 个 token test fail（不止 3 个）→ 暂停，可能 token_rev 渗透更广。
- 3b 边界: scripts/check-all.sh 内容是 `set -euo pipefail; python -m py_compile src/...; pytest tests/` 之类合理 wrapper → land；若是空 stub / 风格不符 → 丢。
- 3c happy path: 4 个 doc commit clean；plan-claims-gate exit 0（doc 日期 `>= 2026-05-20` + 无 SHA 引用 + `claims: {}` opt-out）。
- 3c 反例: plan-claims-gate exit 8 → 给 4 个 doc 各加 `claims: {}` 重 commit。

**Verification:**
- 如 3a 跑：PR merged 后 `pytest tests/test_adapter_ghpages.py tests/test_adapter_hashnode.py tests/test_adapter_writeas.py` 全 pass。
- 如 3b 跑：`make test` 在 main 上能跑通；或 chore PR 描述明确丢 Makefile 原因。
- 3c 完成后：`git status` clean；`docs/plans/` + `docs/brainstorms/` 含 4 个新 doc。
- 最终 `git branch -a` 不含 `chore/debris-catchall-2026-05-20`。

- [x] **Unit 4: bp-roundtrip-a2 完成 Plan 003 Phase A.2 + ship** *(并发 agent ship 为 PR #116 squash `b23c87e`；后续 ceiling 修正 `a4b95f6` 385→370)*

**Goal:** bp-roundtrip-a2 dirty WIP commit + push + 开 PR + ship。

**Requirements:** R5, R6

**Dependencies:** Unit 2 前完成（避免 worktree count 干扰 prune 检测）；Unit 1 之后（已知 baseline）。

**Files:**
- Modify: `src/backlink_publisher/config/writer.py`（dirty diff，加 emission for `[ghpages]/[hashnode]/[writeas]` 在 `_SAVE_CONFIG_KNOWN_ROOTS`）。
- Modify: `monolith_budget.toml`（writer.py ceiling 360 → 可能需 bump，看 emission 加多少 SLOC）。
- Create: `tests/test_save_config_new_channel_roots.py`（已存在但 untracked，需 commit）。
- 主 worktree untracked: `docs/plans/2026-05-20-003-feat-portfolio-roundtrip-spike-quality-plan.md`、`docs/runbooks/RUNBOOK-2026-05-20-operator-gated.md` — 应在 bp-roundtrip-a2 worktree 重新 stage 或 main 单独 commit。

**Approach:**
- 在 bp-roundtrip-a2 worktree：`git status --porcelain` + `stat -f "%Sm"` 跨改动文件验 mtime 集中在本 session 时间窗（[[feedback-multi-agent-turf-check]] 子模式：确认是本 session WIP 不是并发 agent 残留）。
- `pytest tests/test_save_config_new_channel_roots.py` 全过 → commit。
- `python -m radon raw -s src/backlink_publisher/config/writer.py` 看 SLOC；超 360 → 同 commit bump ceiling + rationale ≥80 字符（[[reference-plan-check-cli]] 配套 monolith policy）。
- Plan 003 doc + RUNBOOK：在主 worktree 单独 commit（**先于** bp-roundtrip-a2 PR，因为 [[feedback-plan-doc-on-cutoff-needs-claims-block]] 要求 plan-doc 日期 `>= 2026-05-20` 必带 `claims: {}` opt-out 或对应 SHA 列表；Plan 003 已有 frontmatter，验下是否 opt-out）。
- 开 PR `feat(config): Plan 003 Phase A.2 — register ghpages/hashnode/writeas as save_config managed roots`，等 CI 绿后 squash-merge。

**Patterns to follow:**
- PR #99 (`feat/config-subsection-fix`) 引入 `_canon_subsection_key` + `_toml_heading_path` + `known_subsections` 的 pattern；Phase A.2 应在此基础上加 emission 不改 contract。
- `tests/test_save_config_subsection_preservation.py` 的 fixture / 写法可以复用到 `test_save_config_new_channel_roots.py`。

**Test scenarios:**
- Happy path: 新 channel 在 config.toml 有 section → `save_config` round-trip 不丢；新 channel 无 section → emission step skipped 不 inject 空段。
- Edge case: 操作员手加 `[ghpages.routing]` depth-2 subsection → 经 `save_config` 仍保留（PR #99 contract）。
- Error path: 3 个新 channel 的 PAT/token **不写入 TOML**（per Plan 2026-05-19-006 SEC-3，token 在 0600 sidecar JSON）→ 测试断言 TOML output 不含任何 `token` key。
- Integration: 全套 `pytest tests/test_save_config*.py` + `pytest tests/test_no_monolith_regrowth.py` 都 pass。

**Verification:**
- PR squash-merged 到 main，CI 3.11+3.12+plan-claims-gate 全绿。
- 主 worktree `git status` clean（Plan 003 + RUNBOOK 已 commit）。
- bp-roundtrip-a2 worktree 退或保留为下一 Phase 的 base（看 plan 003 是否还有 Phase A.3+）。

- [x] **Unit 5: 收尾 — prune origin remnants + telegraph 保留确认 + 最终 invariant 验**

**Goal:** 删除 origin 上的 C 类 squash-merge 残留 branch；写最终 audit report。

**Requirements:** R8, R10

**Dependencies:** Unit 2、3、4 全完成。

**Files:**
- Delete (origin): `feat/config-subsection-fix`、`fix/plan-check-claims-coerce-and-recon-doc`、其他 squash-merged 但 origin 未自动删的 branch。
- Read: `git ls-remote origin` 列单。

**Approach:**
- `git fetch origin --prune` 先同步。
- 对 `git branch -r --no-merged origin/main` 输出每条：
  - `origin/local/telegraph-unit{2,4,5,6}-staged` → **跳过保留**。
  - 其他 → `gh pr list --search "head:<branch>" --state all` 看是否对应 merged PR；merged → `git push origin --delete <branch>`。
- 最终 `git branch -r` 仅余 `origin/main` + telegraph 4 条 + 当前活跃 PR head（Unit 3a/3b 或 Unit 4 的 PR branch）。

**Test scenarios:**
- Happy path: prune 完后 `git branch -r --no-merged origin/main` 输出仅 telegraph 4 + 当前活跃。
- 边界: 某 origin branch 找不到对应 PR（可能从未推过 PR）→ 不删，输出列表给 user 决定。
- 失败: `git push origin --delete` 报 protected branch → 不删（main / telegraph 不应在这里出现，出现就是误删尝试）。

**Verification:**
- `git branch` 数量 ≤2；`git branch -r` 数量 = 1 (main) + 4 (telegraph) + 至多 1 (active PR)。
- `git worktree list` 数量 ≤2。
- `pytest tests/` 通过数与 Unit 1 baseline 一致或更多（不能 regression）。
- 主 worktree `git status` clean。

## System-Wide Impact

- **Interaction graph:** 主要影响 `scripts/prune-stale-worktrees.sh`（被 Unit 1 read + Unit 2 调用）；Unit 3a 改的 3 个 adapter test 文件无 prod code dependency。Unit 4 改 `config/writer.py` 影响 6 个 monolith file 之一 + 全套 `tests/test_save_config*.py`。
- **Error propagation:** Unit 2 prune 失败不阻塞后续 unit；Unit 3a CI fail 必须 hold（adapter test 是必跑 CI gate）；Unit 4 monolith ceiling bump 错会被 `tests/test_no_monolith_regrowth.py` R4 hard-fail。
- **State lifecycle risks:** Unit 2 删 branch 后无法恢复（commit 仍在 reflog / `git fsck --unreachable` 30 天）；orphan worktree remove 也无关键状态丢失（per Unit 5 pre-check）。
- **API surface parity:** Unit 4 触碰 `save_config` 公共 API，必须保 PR #99 / PR #114 已建立的 contract（depth-2 subsection 保留 + 5-branch taxonomy）。
- **Integration coverage:** Unit 4 PR CI 必须含 plan-claims-gate（[[reference-plan-check-cli]]）+ 3.11 + 3.12 + footprint regression + monolith budget。
- **Unchanged invariants:** R9 extension contract（`registered_platforms()`）不动；`_DOFOLLOW_BY_CHANNEL` 不动（[[feedback-grep-dofollow-map-before-shipping-adapter]]）；Telegraph Phase 0 rehearsal 4 条 branch 不动。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `prune-stale-worktrees.sh` 未 detect 纯 branch（无 worktree）→ 5 个 direct-merge branch 不被清 | Unit 1 dry-run audit 先看输出；若不 detect，Unit 2 走手动 `git branch -D` 备用路径。 |
| Unit 4 monolith ceiling bump 不够，CI R4 hard-fail | Plan 003 Phase A.1 已加 emission scaffold；A.2 增量 SLOC 应 <30；如超则同 PR bump + 写 ≥80 字符 rationale。 |
| chore-catchall Makefile `PYTHONPATH=.` bug 引入即时债 | Unit 3b 决策点：要么修后 land，要么丢 Makefile 部分（plan 已记入 deferred）。 |
| Unit 3c plan-doc commit 触发 plan-claims-gate exit 8 | 4 个 doc 各加 `claims: {}` opt-out；[[feedback-plan-doc-on-cutoff-needs-claims-block]] PR #113→#115 路径实证。 |
| bp-roundtrip-a2 dirty diff 含本 session 未授权的 foreign agent WIP | Unit 4 approach 第 1 步：`stat -f "%Sm"` 验 mtime + `git diff` 视检 + 跨 worktree grep。 |
| 删 origin branch 时误删 telegraph rehearsal | Unit 5 approach 明确 skip list；R10 锁定。 |
| Unit 5 跑 `gh pr list --search` 漏匹配某 origin branch | 不删该 branch，输出列表给 user 手动决；保守优于激进。 |
| 本 plan doc 日期 2026-05-20 触发 plan-claims-gate exit 8 | frontmatter 已 `claims:` 空（本 plan 不引用任何 grandfather SHA / PR / file path 锁定要 grandfather 的范围）；写完 `plan-check` 立刻验。 |

## Documentation / Operational Notes

- 本 plan 完成后无 AGENTS.md 更新（已有 Worktree Cleanup 段）。
- Unit 4 PR 描述需 link Plan 003 + 说明 Phase A.2 收尾。
- Unit 3c commit message 用 `docs: archive post-hoc plans for shipped features (#110, #113)`，避免误以为是新工作。

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-20-worktree-branch-pr-cleanup-requirements.md](../brainstorms/2026-05-20-worktree-branch-pr-cleanup-requirements.md)
- Related code:
  - `scripts/prune-stale-worktrees.sh` + `scripts/_worktree_safety.sh`
  - `src/backlink_publisher/config/writer.py`（Unit 4）
  - `monolith_budget.toml`（Unit 4 ceiling check）
- Related PRs:
  - PR #99 `fc4ca84` (config managed-root subsection preservation) — Unit 4 pattern source
  - PR #114 `14d3651` (Plan 003 Phase A.1 canary) — Unit 4 直接前驱
  - PR #110 `7d77410` (banner-image-gen) — Unit 5 bp-banner-image-gen orphan 验证基础
  - PR #113 `2f61057` + #115 `2412566` — Unit 2 已 PR-merged branch
- Memory:
  - [[reference-plan-check-cli]] — plan-claims-gate semantics
  - [[reference-phase0-local-rehearsal-branches]] — telegraph 保留依据
  - [[feedback-pythonpath-src-for-sibling-worktree]] — Makefile bug
  - [[feedback-multi-agent-turf-check]] — Unit 4 mtime/cluster 验证模式
  - [[feedback-foreign-agent-wip-spreads-as-broken-replace]] — A 类 5 已 land 推翻的假设
