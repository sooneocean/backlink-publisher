---
title: "fix: WebUI Medium browser-login crash hardening (Playwright lifecycle + error catch)"
type: fix
status: completed
date: 2026-05-20
completed: 2026-05-20
claims: {}
---

# fix: WebUI Medium browser-login crash hardening

## Overview

`webui_app/medium_login.py` had two co-located bugs that combined to crash
the entire WebUI process (Flask debug `500` → operator manually restarts
`python webui.py`) whenever an operator used **Settings → Medium →「打开浏览器
登录」** and then closed the Chromium window mid-flow. The session leading
into this plan reproduced the crash via log archaeology (3 process deaths
inside 10 minutes on 2026-05-20 afternoon, pids 96322 → 25962 → 34273 →
48592), then surgically fixed both bugs and restarted the server (now pid
64376).

This plan formalizes the work already shipped in-tree (Units 1–3, code
already edited but **not yet committed**) and tracks the residual hardening
needed: regression tests for the new error paths, plus two adjacent risks
surfaced during the scan that should not block but should be on record.

## Problem Frame

**Symptom**: Operator clicks 「打开浏览器登录」, Chromium opens, operator
closes the window without completing login → WebUI server returns Flask
debug 500, browser shows yellow error page; subsequent requests to
`/settings/medium/*` fail until manual restart.

**Origin trace** (from `/private/tmp/webui-main-validate.log`):
```
POST /settings/medium/launch-browser-login → 500
playwright._impl._errors.Error: Target page, context or browser has been closed
…
During handling of the above exception, another exception occurred:
AttributeError: 'Playwright' object has no attribute '__exit__'
```

**Root cause split**:
1. `page.wait_for_url(...)` raises `playwright.sync_api.Error` (NOT
   `TimeoutError`) when the user-closed window kills the navigation
   future. Only `_PWTimeout` was caught.
2. The `finally:` block called `pw.__exit__(None, None, None)` on the
   `Playwright` *instance*, but `__exit__` lives on the
   `PlaywrightContextManager` returned by `sync_playwright()` — not on
   the instance returned by `.__enter__()`. So the `finally` itself
   crashed, masking the original exception.

Because debug=True is on the dev server, the Flask debugger captures the
crash and the *process* keeps running — but the leaked Playwright child
process holds the medium-browser file lock, so subsequent requests block
on `_FileLock` or fail with stale-context errors until the operator kills
the parent and restarts.

## Requirements Trace

- R1. 用户在 Medium 登录窗口手动关闭后，WebUI 不应 500；应回退到带 flash
  消息的 `/settings` 页面。
- R2. `Playwright` 实例的生命周期管理必须用正确的对象调 `__exit__` —
  不再二次崩溃 finally 块。
- R3. 新增的 error path 必须有 regression test，防止下次重构时静默回归。
- R4. 同模块的 `probe_login_status` 与 `launch_login_window` 行为对齐
  （都可能被同一类异常击中）。

## Scope Boundaries

- **Out of scope**: 改造 Playwright 调用方式（如换成 `.start()/.stop()`）。
- **Out of scope**: 引入异步路由 / Celery 隔离 Playwright 子进程。
- **Out of scope**: 修复并发 agent 留下的 `/api/seo/anchors` coverage gap
  —— 记录但归属另一个 plan。
- **Out of scope**: Medium liveness probe (`medium_liveness.py`) —— 已用
  `with` 语句，安全。

## Context & Research

### Relevant Code and Patterns

- `webui_app/medium_login.py:138-159` — `_playwright_context()` helper
- `webui_app/medium_login.py:160-195` — `launch_login_window()` 入口
- `webui_app/medium_login.py:196-225` — `probe_login_status()` 入口
- `webui_app/routes/medium_login.py:65-83` — Flask route handler，已正确
  catch `DependencyError` + `ExternalServiceError` → flash redirect
- `webui_app/medium_liveness.py:166` — 对照范式：`with sync_playwright() as pw:`
- `src/backlink_publisher/cli/_bind/driver.py:453` — 另一对照范式：
  `pw = sync_playwright().start()` + `pw.stop()` (manual lifecycle)

### Institutional Learnings

- `~/.claude/memory/feedback_bind_channel_diagnostic_playbook.md` ——
  Playwright bind 失败诊断五条铁律。这次调试用 sync Bash + timeout=600000
  的纪律，没用 `run_in_background`，所以 terminal event 没被吞。
- 之前对类似 bug 处理（PR #84 / #85 — Codex P1 8 handle leaks）已经
  示范过 `try/finally` 中再次抛错会 mask 原因 的常见错法。

### External References

- Playwright Python docs: `sync_playwright()` 是 ContextManager 工厂；
  `.__enter__()` 返回的是 `Playwright` 实例，本身没有 `__exit__`。

## Key Technical Decisions

| Decision | Rationale |
|---|---|
| 保留 `pw_cm = sync_playwright()` 引用而不是改用 `.start()/.stop()` | 改动面最小，与 `webui_app/medium_liveness.py:166` 的 `with` 范式保持精神一致（都依赖 ContextManager），不引入第三套生命周期范式 |
| `_PWError` import 走 try/except 兜底（无 Playwright 时 fallback 到 `Exception`） | 与既有 `_PWTimeout` fallback 一致。在 Playwright 缺席时 `_playwright_context()` 第一时间 `raise DependencyError`，根本不会到达 `except _PWError` 分支，所以理论上「太宽」的 fallback 实际无害 |
| 「Target closed」当作 user-cancel，返回 `ExternalServiceError` 友好消息 | 与 `_PWTimeout` 的处理对齐；route handler 已 catch 此异常并 flash danger redirect；不需要新加 error type |
| `ctx.close()` 在 finally 块用 `try/except _PWError` 包裹 | 用户关窗后 context 已死，重复关会再抛；按 [[feedback-bind-channel-diagnostic-playbook]] 的 finally 块抗噪声原则容忍 |

## Open Questions

### Resolved During Planning

- **`_PWError = Exception` fallback 是否会误吞业务异常？** 不会，因为
  `_playwright_context()` 在 Playwright 缺席时第一时间 raise
  `DependencyError`，永远不会进入 `try:` 块。
- **是否要把整个 `_playwright_context()` + 调用对包成 `@contextmanager`？**
  暂不。当前 fix 改动面 +40/-9 已足够小且 25 tests pass；统一抽象
  留给未来需要第三个 Playwright 路由时再做。

### Deferred to Implementation

- **Mock Playwright 的策略**：U4 regression test 需要 mock
  `sync_playwright`、`pw_cm`、`page` 三层。具体 mock 抽象层级在写测试
  时再定（看现有 `tests/test_medium_login_*.py` 怎么 mock）。

## Implementation Units

- [x] **Unit 1: `_playwright_context()` 返回 ContextManager 而不是 Playwright 实例**

**Goal:** 修复 `pw.__exit__` AttributeError 根源 —— 保留 `pw_cm`
（`PlaywrightContextManager`）引用，让调用方用它调 `__exit__`。

**Requirements:** R2

**Files:**
- Modify: `webui_app/medium_login.py:138-159`

**Approach:**
- `pw_cm = sync_playwright()`，然后 `pw = pw_cm.__enter__()` 拿
  `Playwright` 实例做实际工作
- `return pw_cm, ctx`（不再返回 `pw`，因为调用方不需要 root Playwright 实例
  本身，只需要持有可调 `__exit__` 的对象）
- docstring 标注 ContextManager / instance 的区别

**Verification:** 已完成。`grep "pw\.__exit__" webui_app/medium_login.py`
返回 0，所有 `__exit__` 调用都打在 `pw_cm` 上。

- [x] **Unit 2: catch `_PWError` for user-closed window**

**Goal:** Medium 窗口被手动关闭后，`page.wait_for_url` 抛
`playwright.sync_api.Error`；新加 `except _PWError` 转 `ExternalServiceError`。

**Requirements:** R1, R4

**Files:**
- Modify: `webui_app/medium_login.py:29-36` (import block — 加
  `_PWError = Error` + ImportError fallback)
- Modify: `webui_app/medium_login.py:175-195` (launch_login_window except)
- Modify: `webui_app/medium_login.py:205-225` (probe_login_status except)

**Approach:**
- 检测错误消息含 `"closed"`（case-insensitive）→ 友好提示「登录窗口已关闭」
- 其他 `_PWError` 子类型 → 通用「Medium 登录失败：{msg}」
- 两个入口（launch + probe）同样处理，避免不对称

**Verification:** 已完成。25 个 `test_medium_login_*` 测试 pass。

- [x] **Unit 3: finally 块 `ctx.close()` 容忍二次错误**

**Goal:** 用户关窗后 context 已死，`ctx.close()` 会再抛 `_PWError`；
finally 块包 try/except 防止淹没原始异常。

**Requirements:** R1, R2

**Files:**
- Modify: `webui_app/medium_login.py` 两处 finally 块

**Verification:** 已完成。`py_compile` OK。

- [x] **Unit 4: regression tests for new error paths** ✅ DONE

**Goal:** 给 U2 + U3 加测试，防止下次有人去掉 `_PWError` catch 后静默
回归到 500。

**Requirements:** R3

**Dependencies:** Unit 1–3（已 done）

**Files:**
- Extended: `tests/test_medium_login_routes.py` (+5 new test classes,
  +10 tests, +urllib.parse import)

**Approach (as shipped):**
- 用既有 `_make_mock_pw()` factory 注入 fake `sync_playwright`；mock
  factory 已经支持 `pw_cm.__enter__/__exit__` MagicMock 接口
- 用 `page.wait_for_url.side_effect = _PWError(...)`（launch）/
  `page.goto.side_effect = _PWError(...)`（probe）触发新 except 分支
- Route-level integration 用 `urllib.parse.unquote(resp.headers["Location"])`
  解码 Chinese flash_msg 后做 substring 断言

**Shipped test classes:**

| Class | Tests | Covers |
|---|---|---|
| `TestPlaywrightLifecycle` | 2 | U1 — `pw_cm.__exit__` 真的被调（不是 instance 的） |
| `TestPWErrorCatchLaunch` | 3 | U2 launch — closed-window + generic _PWError + 锁释放 |
| `TestPWErrorCatchProbe` | 2 | U2 probe — 同样两条路径 |
| `TestCtxCloseTolerant` | 1 | U3 — finally `ctx.close()` 二次错误不掩盖原异常 |
| `TestPWErrorRouteIntegration` | 2 | Route-level — 302 + 正确 flash_type（danger/warning） |

**Verification (actually run):**
- `PYTHONPATH=src pytest tests/test_medium_login_routes.py -v` →
  **29 passed in 0.58s** (19 existing + 10 new)
- 跨文件回归 `tests/test_medium_login_*.py
  tests/test_webui_token_paste.py tests/test_generic_channel_api.py` →
  **58 passed**, 0 regressions
- **Tripwire 验证**：临时 `sed` 去掉两处 `except _PWError as e:` 块
  → **8 tests failed** (plan 目标 ≥3, 超出)；恢复后 29 pass。证明测试
  不是 tautology，真能抓到回归

- [ ] **Unit 5: commit + push the fix**

**Goal:** 把 Units 1–4 落到 git history；当前 4 行改动 + 新测试还停在
working tree。

**Requirements:** —（运营性）

**Dependencies:** Unit 4

**Files:**
- Stage: `webui_app/medium_login.py`、新测试文件
- **Do not stage**: `webui_app/binding_status.py`、`routes/__init__.py`、
  `routes/settings_basic.py`、`templates/settings.html`、
  `_channel_card_macro.html`、`static/js/channel-binding.js` 等并发 agent
  的 WIP —— 不属于本 plan scope

**Approach:**
- 单独的 commit 信息聚焦 medium_login crash fix：
  `fix(webui): medium browser-login Playwright lifecycle + closed-window catch`
- 在 commit message 引用 `/private/tmp/webui-main-validate.log` 里的
  traceback 作为「why」证据

**Verification:**
- `git status` 仅显示我自己的 medium_login.py + 新测试改动
- 并发 agent 的 WIP 文件保留在 working tree 未 stage

## System-Wide Impact

- **Interaction graph**: `webui_app/routes/medium_login.py:65-83` ←
  `webui_app/medium_login.py:launch_login_window` / `probe_login_status`
  ← `_playwright_context`。修复全在最内层；route handler 已正确 catch
  `ExternalServiceError`，不需要改动。
- **Error propagation**: `_PWError` → `ExternalServiceError` (raise) →
  route catches → 302 redirect with flash。和 `_PWTimeout` 完全对称。
- **State lifecycle risks**: 文件锁 `medium-browser.lock` (fcntl.flock)
  在 `_FileLock` context exit 时释放，与 Playwright 修复无关；本次
  fix 不动锁逻辑。
- **API surface parity**: 同模块 `clear_browser_profile` 路径不碰
  Playwright，无对称要求。
- **Integration coverage**: U4 测试用 mock 覆盖 happy + edge + error +
  integration 四类。
- **Unchanged invariants**: route handler 的 `DependencyError` /
  `ExternalServiceError` 双重 catch 不动；flash 消息格式不动；CSRF
  双提交 cookie 不动。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Playwright API 升级后 `Error` 类的 import path 改变 | import 已包 try/except；fallback 到 `Exception` 是兜底，外加 `_playwright_context()` 先 raise `DependencyError` 防进入 |
| Mock 写法和真实 Playwright 行为不一致（mock 抛错但真实路径触发不了） | Unit 4 写完后做一次 manual smoke：真实 webui 启动 → 点「打开浏览器登录」→ 关 Chromium 窗口 → 看到红色 flash「登录窗口已关闭」 |
| 并发 agent WIP 已 import `seo_viz` blueprint 但测试覆盖缺失 | 单独 plan 处理；本 plan 不 stage 那些文件 |

## Documentation / Operational Notes

- 重启 webui 命令：`kill $(lsof -iTCP:8888 -sTCP:LISTEN -t) && nohup
  .venv/bin/python webui.py > /private/tmp/webui.log 2>&1 &`
- 当前运行实例：pid 64376（含 Units 1–3 修复），端口 8888
- 故障复现路径：Settings → Medium 卡片 → 「打开浏览器登录」→ Chromium
  打开后立即关闭 → 应见红色 flash「登录窗口已关闭」而非 500

## Sources & References

- 触发本 plan 的 traceback: `/private/tmp/webui-main-validate.log`（pid
  96322 的死亡前最后日志）
- Related code: `webui_app/medium_login.py`, `webui_app/routes/medium_login.py`
- Related memory: `[[feedback-bind-channel-diagnostic-playbook]]`,
  `[[project-medium-login-bind-attempt-2026-05-20]]`
- Related PR history: PR #83/#84/#85 (Medium browser-bind hardening)
