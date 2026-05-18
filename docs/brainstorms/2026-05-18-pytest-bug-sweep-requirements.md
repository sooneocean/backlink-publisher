---
date: 2026-05-18
topic: pytest-bug-sweep
---

# Pytest Bug Sweep — 全套测试失败用例依序 debug

## Problem Frame
backlink-publisher 仓库当前在 `main`，过去几周合并了多个 feature 分支（adapter retry、content-fetch、anchor entropy、homepage form、webui refactor 等），并行有 PR #36/#37/#38 在飞，Telegraph Phase 0 也在跑。tests/ 下 53 个测试文件，目前不清楚整套 pytest 是否仍然是绿的。需要一次"全面体检"：跑完整套测试，把所有失败/错误用例拎出来，依序定位根因并修掉，最后交一份报告。

## Requirements

**测试收集**
- R1. 在仓库根目录跑一次完整 `pytest`（不带 `-x`），收集所有 `FAILED`、`ERROR`、`SKIPPED` 用例。
- R2. 把结果按 (a) 失败模块、(b) 报错类型、(c) 疑似依赖层次（基础工具 → adapter → CLI/webui）分类。
- R3. 对每个失败用例，给出一句话根因猜测和"是 src 实现 bug 还是 test 本身过时"的判断。

**Debug 执行**
- R4. 依序修复：优先修底层模块（config / utils / fetch），再修上层（adapter、CLI、webui）。底层修好后重跑相关测试，确认不会"修一个炸三个"。
- R5. 默认修 src/，不改测试。仅当测试本身的断言或 fixture 明显过时（例如硬编码了已变更的 API 形状），才改测试，且在最终报告里 **逐条标记** "test 改动 + 理由"。
- R6. 修复后必须本地重跑该测试 + 整套测试，确认不引入回归。
- R7. 每个修复独立 commit，commit message 用 conventional 格式（`fix(<module>): <one-liner>`），英文。

**报告输出**
- R8. 全部修完后交一份 `docs/bug-sweep-2026-05-18.md` 报告：每个失败用例的根因、修复 diff 摘要、是否动了测试、是否引入新依赖、剩余无法修的项目（含理由）。
- R9. 给出"修完后整套 pytest 的 pass/fail/skip 计数对比"。

## Success Criteria
- 整套 pytest 从初始失败计数收敛到 **0 failure / 0 error**（skip 允许保留，但要在报告里逐条解释）。
- 没有引入新的 SKIP 来"绕过" failure。
- 没有为修测试而改业务逻辑（反向因果）。
- 最终报告读完后，用户能在 5 分钟内复核每一处改动是否合理。

## Scope Boundaries
- **OUT**：lint / type / 安全静态扫描（ruff / mypy / bandit），除非它直接导致 pytest 失败。
- **OUT**：重构、性能优化、新功能、文档更新（CHANGELOG/README 除外）。
- **OUT**：测试文件本身的风格清理、参数化重写、fixture 抽取。
- **OUT**：依赖外部网络或真实第三方服务（Blogger / Medium / Google OAuth）的真集成测试 —— 这类如果在本地环境本来就跑不通，记录到报告里，不强求修。
- **OUT**：CI 配置、`pyproject.toml` 依赖升级，除非某个 failure 的根因就是依赖版本不兼容。
- **OUT**：webui_store、fixtures/ 下的数据修改。

## Key Decisions
- **隔离工作分支**：不在 `main` 上直接动手。新建 `fix/pytest-bug-sweep-2026-05-18` 分支或独立 worktree（基于过往教训：外部进程会切分支并擦未 commit 修改）。**Rationale**：避免和 PR #36/#37/#38、Telegraph Phase 0 routine 互相干扰。
- **批量自主 debug**：用户授权一次跑完，最后整体 review。中途不打断确认，但每个 commit 原子化，便于事后 revert 单点。
- **底层优先排序**：依赖层从内向外修，避免上层修复在底层 fix 后被推翻。
- **测试改动需要解释**：默认信任测试；改测试视为可疑动作，必须在报告里单独列出并给理由。

## Dependencies / Assumptions
- 假设本地 Python 3.11+ 环境已装 dev 依赖（`pip install -e ".[dev]"`）。如果第一轮 pytest 因为 ImportError / ModuleNotFoundError 大面积红，先补依赖再说，并在报告里记。
- 假设 `pytest-socket` 默认禁网，需要联网的测试应有 `@pytest.mark.enable_socket` 或类似标记 —— 没有标记却试图联网的测试，按"环境性失败"处理而非"代码 bug"。
- 假设 conftest.py 提供的 SSRF/网络 bypass fixture 仍然有效。

## Outstanding Questions

### Resolve Before Planning
- （无）

### Deferred to /ce:work
- [Affects R1][Needs first run] 当前整套 pytest 实际有多少 failure / error / skip？需要先跑一次才能知道规模。如果数量 > 30，需要重新评估是不是该缩范围。
- [Affects R4][Technical] 是否存在"测试之间互相污染"（如全局状态、checkpoint 文件、`webui_store/` 残留）导致顺序敏感失败？需要在跑的时候用 `-p no:randomly` 和 `--forked` 之类对比验证。
- [Affects R5][Technical] 某些 PR 是否已经知会了 main 上的测试会临时失败？需要 `git log --since` 排查最近 commit message 里有没有 "known failing" / "skip later" 之类的暗示。

## Next Steps
→ `/ce:plan` for structured implementation planning（推荐，因为有 53 个测试文件、3 个 deferred 技术问题需要在 plan 阶段先用 collect-only 摸清规模）
