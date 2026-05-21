---
title: "Chrome lifecycle spike — Plan 2026-05-21-001 Unit 0 pre-Unit-1 validation"
date: 2026-05-21
plan: docs/plans/2026-05-21-001-feat-chrome-cdp-multi-channel-publish-plan.md
unit: 0
status: complete
platform: darwin (macOS, Chrome 一般稳定通道)
script: scripts/spike_chrome_lifecycle.py
raw_logs: /tmp/spike-out/probe{1,2,3,4}.{jsonl,log}
---

# Chrome lifecycle spike — Unit 0 实证报告

## 目的

在 Unit 1 (`BrowserPublishRecipe` + `ChromeAttachSession` foundation) 提交前，落地验证 plan body 列出的 4 项 Chrome lifecycle 假设。结果用来 (a) 决定 Unit 1 是否需要 fallback / opt-out 设计；(b) 校正与既有 memory / plan 文字不符的事实。

`docs/refs/2026-05-2N-...` 路径与仓库现有 spike 报告 convention (`docs/spikes/<date>-...`) 不一致 — 报告落 `docs/spikes/`，plan 在 Unit 0 Files 行已带过相应修正建议。

## TL;DR — 4 项结论

| Probe | 假设 / 关注点 | 实证结果 | Unit 1 implication |
|---|---|---|---|
| 1 | `start_new_session=True` + 显式 `killpg` 是 macOS 上回收 Chrome helper 树并立刻释放 SQLite profile lock 的必要手段 | **部分否定**：`proc.terminate()` 单独已让所有 helper exit + profile reuse 立刻成功；但 `killpg` 从我们这个父进程发出会拿到 `EPERM`，本质上**派不上用场** | Unit 1 用 `proc.terminate()` + `proc.wait(timeout=…)` 反应链；**不要** 把 `killpg` 写进契约，会假阳；profile lock 释放在 helper exit 时就完成 |
| 2 | `lsof -i:<port>` + `ps` 能跨 macOS / Linux 把 CDP listener PID 反查到真实 Chrome 二进制，可作 "attach to existing CDP 是否真是我的 Chrome" 的身份校验 | **肯定（带平台限制）**：lsof 正确定位 listener PID；macOS `ps -o comm=` 截断到 15-16 字符（`'/Applications/Go'`）不能用；`ps -o command=` 提供完整 cmdline，可以 substring 对照 chrome_bin + profile dir。Linux 还有 `/proc/<pid>/exe`，macOS 没有该路径 | Unit 1 attach-mode 用 `ps -o command=` 做 substring 匹配 (chrome_bin path **和** profile dir 都在 cmdline 里)；不要 rely on `comm`；`/proc/<pid>/exe` 列为 Linux-only enhancement |
| 3 | macOS SIP / sandbox 下 `chmod 0o700` 可能失败，需要 fail-soft fallback | **完全否定**：$TMPDIR 下 `chmod 0o700` 直接成功，subdir 创建 + 文件写入正常，`chmod 0o755` 也成功 | Unit 1 直接保持现有 `os.chmod(profile, 0o700)` + OSError → `chrome_profile_locked` 路径；不要为臆想中的 SIP 故障写 fallback |
| 4 | Plan D3 + memory `[[chrome_backend_per_channel_profile_via_env]]` 声称 `_chrome_profile_dir()` 已 honor `BACKLINK_PUBLISHER_BIND_CHANNEL` env 切 `<config_dir>/real-chrome-profile/<channel>/` | **完全否定（main 上不存在）**：baseline / telegraph / velog 三组 env 跑下来 `_chrome_profile_dir()` 返回同一路径 `<config_dir>/real-chrome-profile`，env var 不被读取 | **Unit 1 必须实现 per-channel 分流**（D3 不是已存在的 foundation，是 Unit 1 的 net-new 工作）；memory entry `[[chrome_backend_per_channel_profile_via_env]]` 反映的是 PR #138 branch 不是 main，需要重写 |

整体判定：4 项里 1 项肯定 (Probe 2)、3 项否定但每条都给出明确替代设计 → 满足 plan 对 Unit 0 的 exit criteria ("4 项全部至少一个明确实证 work 或实证需 fallback 结论")。**Unit 1 ship 不需要带猜测。**

---

## 设备与版本

- Platform: `darwin` (macOS, user-level)
- Python: 3.11.15
- Chrome bin: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` (operator's installed Chrome — spike 全程用 temp profile + 动态 port 与 operator's running Chrome 隔离)
- 主仓 `chrome_backend.py` 版本: `393 行 (main, PR #129 a8e42f2 ships 的版本)`。**未** 用 PR #138 的 526 行版本（PR #138 被 operator 决定跳过，见 plan 实施变更说明）。

---

## Probe 1 — 进程组 teardown 与 profile 锁释放

### 协议

每次迭代：

1. `subprocess.Popen([chrome_bin, --remote-debugging-port=<动态>, --user-data-dir=<temp>, ...], start_new_session=True)`
2. 等 `/json/version` 200 → 记 spawn-to-ready 时间、`pgid_of(pid)`、`pgrep -P` 全树
3. 调 `proc.terminate()` → 等 2 秒 → 记 parent 与 helper alive 状态
4. 如 parent 或 helpers 仍 alive：`killpg(pgid, SIGTERM)` 5s → `killpg(pgid, SIGKILL)` → 各步记 alive 状态
5. 重新 spawn 第二个 Chrome，**复用同一 profile**，记 reuse spawn-to-ready 时间（是否被 SQLite lock 卡住）

5 轮，原始 JSONL 在 `/tmp/spike-out/probe1.jsonl`。

### 关键观察（5/5 轮一致）

```
iter N:
  spawn_to_ready_s = 0.6 – 2.2 s  (iter 0 最慢 2.232，后续 ≈ 0.6-0.9)
  descendant_count = 5 – 7
  after terminate (wait 2 s):
    parent_alive = True   ← 仍报 alive
    descendants_alive = 0 ← 全部已退出
  killpg SIGTERM err = PermissionError(1, 'Operation not permitted')
  killpg SIGKILL err = PermissionError(1, 'Operation not permitted')
  profile reuse spawn_to_ready_s = 0.6 – 0.9 s  ← 锁立即可复用
```

### 分析

**为什么 `killpg` 拿 EPERM**：父 Python 进程不在 child Chrome 的 session 里。`start_new_session=True` 让 child 成为新 session + pgroup 的 leader (`sid=pid, pgid=pid`)。POSIX 允许 `killpg` 当且仅当发送者与目标共 session、或目标完全是发送者的子代、或 root。macOS 在 task-port 层级加了更严格的 sanity check，让"我是你直接父进程"也不够。**`killpg` 在我们这条调用链上对 macOS Chrome 无效。**

**为什么 `parent_alive=True` 但 helpers 已死 + profile 可复用**：`os.kill(pid, 0)` 对 zombie / defunct 进程也返回 True（exit 后等被父进程 `wait()`）。Helpers 是 Chrome 自身 fork 的子进程，由 Chrome 主进程的 SIGTERM handler graceful 关闭；Chrome 主进程退出后变 zombie，等 `proc.wait()` 收尸。SQLite profile lock 在主进程 release 描述符的瞬间释放（zombie 已没有 open fd），所以新 Chrome 可立即复用。

**`proc.terminate()` 已足够**：5 轮里全部 descendant 在 2 秒内归零。这与"Chrome 在 macOS 上需要 killpg 才能干净 teardown"的直觉相反 — 大概是因为 Chrome 实现了 trap SIGTERM 后通知所有 helper 退出的逻辑。

### Unit 1 implications

1. `ChromeAttachSession.__exit__` 用 `proc.terminate()` + `proc.wait(timeout=5.0)` 收 zombie；**不要** 调 `os.killpg`，那是 macOS 假阳。Linux 上 `killpg` 可能 work，但 Linux 也不需要它（Chrome 同样会 graceful exit）。
2. 不需要在 teardown 后插入"等 SQLite lock 释放"的 sleep — 实测 0 延迟。
3. spawn-to-ready 实测 0.6–2.2 s，Unit 1 的 `_CONNECT_TIMEOUT_S = 10.0` (现 chrome_backend.py 已是) 是合理 ceiling。

---

## Probe 2 — CDP listener identity verification

### 协议

1. Spawn Chrome with `--remote-debugging-port=<port>`，等 ready
2. `lsof -iTCP:<port> -sTCP:LISTEN -Fp -n -P` → parse `pX...` 行取 PID
3. `ps -o comm=,command= -p <pid>` → 取 comm + cmdline
4. 比对 `chrome_bin in command` + `profile_dir in command`
5. 负向测试：bind 一个无关 socket 到另一 port → 验证 lsof 不会把它误归到 Chrome

### 关键观察

```
listener_pid = 66788  expected_pid = 66788  ← 完全一致
comm = "/Applications/Go"  ← 15 字符截断
command = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --remote-debugging-port=63742 --user-data-dir=/var/folders/.../spike-probe2-... --no-first-run --no-default-browser-check about:blank"
command_contains_chrome_bin = True
command_contains_profile = True
comm_matches = False  ← 因为截断

ghost listener (我们的 python 占的另一 port):
  ghost_pid = 66704
  ghost_describe.command = "/Users/dex/.pyenv/.../python scripts/spike_chrome_lifecycle.py 2"
  same_as_chrome = False  ← 正确区分
```

### 分析

- **lsof 在 macOS 上稳定输出 listener PID** — 单条 `-Fp` 行就能拿到。前提是 lsof 装了（macOS 系统自带 `/usr/sbin/lsof`，但要处理 PATH 异常 → `FileNotFoundError`）。
- **`ps -o comm=` 在 macOS 上截断**（实测 `/Applications/Go`）。Linux 通常返回基名（`google-chrome`）不截断到这么短。**不能用 `comm` 做身份判别**。
- **`ps -o command=` 给完整 cmdline**，含 chrome 二进制路径 + `--user-data-dir=` 路径 → 两项 substring 同时命中 = 高置信度 "是我们启动的 Chrome"。
- **`/proc/<pid>/exe`** macOS 不存在；Linux 存在并可 `os.readlink` 拿到真实 exe 路径 — 当 Linux fallback 多一层身份证据。

### Unit 1 implications

1. attach-existing-CDP 模式（`BACKLINK_PUBLISHER_REAL_CHROME_ATTACH=1`）安全实现：
   ```
   def _verify_listener_is_our_chrome(port: int, chrome_bin: str, profile: Path) -> bool:
       pid = _lsof_listener_pid(port)
       if pid is None:
           return False  # lsof 没装或没听到 → 拒绝 attach
       cmdline = _ps_command(pid)
       if cmdline is None:
           return False
       return chrome_bin in cmdline and str(profile) in cmdline
   ```
2. **不要 rely on `ps -o comm=`** — macOS 截断 + Linux 取 basename，两边不一致。
3. lsof 未装时（不太可能但要 graceful）→ 退化为"无身份校验，但接受 attach"模式可以由 env var `BACKLINK_PUBLISHER_REAL_CHROME_ATTACH_TRUST_LISTENER=1` 显式 opt-in；默认拒绝。
4. Ghost listener 区分能力实证 → 不需要为"我们的 python 自己被误识别"写额外防御。

---

## Probe 3 — Profile 权限

### 协议

- `tempfile.mkdtemp(...)` 创建 profile dir
- `stat` 记 mode / uid / gid
- `chmod 0o700` → 记 result + error
- 在 profile 下创建 `Default/` + 写 `Default/Cookies` 文件
- `chmod 0o755` → 记 result + error

### 关键观察

```
initial mode = 0o700, uid = 501, gid = 20  ← mktemp 默认就是 0o700
chmod 0o700: applied = True, err = None
subdir create Default/: success, err = None
file write Default/Cookies: success, err = None
chmod 0o755: applied = True, err = None  ← 也能放宽
```

### 分析

`$TMPDIR` 下 chmod / mkdir / write 在 macOS user-level **没有 SIP 或 sandbox 干扰**。Plan body Probe 3 描述"SIP / sandbox 下 chmod 行为"主要适用：(a) 把 profile 放在受保护路径如 `~/Library/Application Support/`、(b) 在 macOS sandbox 子进程内、(c) 给 root-owned dir chmod。**正常 backlink-publisher 调用栈不踩这些。**

### Unit 1 implications

1. 保持 `os.chmod(profile, 0o700)` + OSError → `chrome_profile_locked` 现状。不写额外 fallback。
2. 如果未来某用户的 `BACKLINK_PUBLISHER_CONFIG_DIR` 落到受保护路径并触发 chmod EPERM — error message 已经够清楚 (`ChromeLaunchError("chrome_profile_locked")`)，operator 调整 config dir 即可。

---

## Probe 4 — Per-channel profile env var (D3)

### 协议

```
saved env
unset BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR (避免覆盖默认)
set BACKLINK_PUBLISHER_CONFIG_DIR=<temp>
for env in (no_channel, channel=telegraph, channel=velog):
    record _chrome_profile_dir() return
expect: telegraph 与 velog 路径不同，且各自含 channel segment
```

### 关键观察

```
baseline (no env):   <fake_config>/real-chrome-profile           contains_channel_segment = False
channel=telegraph:   <fake_config>/real-chrome-profile           differs_from_baseline = False  contains_channel_segment = False
channel=velog:       <fake_config>/real-chrome-profile           differs_from_baseline = False  differs_from_channel_a = False  contains_channel_segment = False

plan D3 assumption:
  expected_telegraph = <fake_config>/real-chrome-profile/telegraph
  actual_telegraph   = <fake_config>/real-chrome-profile
  matches            = False
```

### 分析

- main 上 `_chrome_profile_dir()` 只看 `BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR` 与 fallback `_config_dir() / "real-chrome-profile"`。`BACKLINK_PUBLISHER_BIND_CHANNEL` 完全没被读。
- memory 索引项 `[[chrome_backend_per_channel_profile_via_env]]` ("`_profile_dir()` 已 honor `BACKLINK_PUBLISHER_BIND_CHANNEL` env → `<config_dir>/real-chrome-profile/<channel>/` subdir") 在 main 上是错的。该 entry 是基于 PR #138 branch 状态写的（被 operator 决定跳过 / 暂不 merge）。
- Plan 2026-05-21-001 D3 ("Per-channel profile isolation — reuses bind backend's existing `BACKLINK_PUBLISHER_BIND_CHANNEL` env var") 把 net-new 的工作误叙述成 "已存在的 foundation"，是基于同一 memory 误证。

### Unit 1 implications (most consequential of the 4 probes)

1. **Unit 1 必须新增 per-channel profile 分流**。建议落在 `_chrome_profile_dir()` 自身：

   ```python
   def _chrome_profile_dir() -> Path:
       raw = os.environ.get("BACKLINK_PUBLISHER_REAL_CHROME_PROFILE_DIR")
       if raw:
           return Path(raw).expanduser()
       base = _config_dir() / "real-chrome-profile"
       channel = os.environ.get("BACKLINK_PUBLISHER_BIND_CHANNEL")
       if channel:
           # whitelist 防 path traversal
           if not re.fullmatch(r"[a-z0-9_-]+", channel):
               raise ChromeLaunchError("chrome_invalid_bind_channel")
           return base / channel
       return base
   ```
2. Unit 1 测试要 cover 三种状态：(a) env 缺省 → 旧路径（向后兼容）；(b) env=有效 channel → subdir；(c) env=路径注入恶意值 (`"../etc"`) → `ChromeLaunchError`.
3. 测试需要 mock `_config_dir()` + setenv/delenv，参照 `tests/test_bind_channel_chrome_backend.py` 已有 fixture 风格。
4. **更新 memory** `[[chrome_backend_per_channel_profile_via_env]]` 以反映：在 PR #138 (closed/未 merge) 上是 true；在 main 上是 false；Unit 1 实施完后再标回 true。

---

## 对 plan 文字的最终建议（如果 plan 本 PR 还要改）

1. **Unit 0 Files 行** `docs/refs/2026-05-2N-...` → `docs/spikes/2026-05-21-chrome-lifecycle-spike.md` (real path)。
2. **D3 描述** 把 "reuses bind backend's existing `BACKLINK_PUBLISHER_BIND_CHANNEL` env var" 改成 "extends `_chrome_profile_dir()` to honor `BACKLINK_PUBLISHER_BIND_CHANNEL` env var (net-new in Unit 1; main 上目前不存在)"。
3. **Unit 1 Approach** 加 sub-bullet："per-channel profile 分流：扩展 `_chrome_profile_dir()` 读 `BACKLINK_PUBLISHER_BIND_CHANNEL`，[a-z0-9_-]+ whitelist 防注入，缺 env 兼容旧路径。引用 `docs/spikes/2026-05-21-chrome-lifecycle-spike.md` § Probe 4。"
4. **Unit 1 Approach** 加 sub-bullet："teardown 用 `proc.terminate()` + `proc.wait(timeout=5)`；不要 `os.killpg`。Probe 1 实证 macOS 上 `killpg` 拿 EPERM 且不必要。"
5. **Unit 1 Approach** 加 sub-bullet："attach-mode listener identity 通过 `lsof -iTCP:<port> -Fp` + `ps -o command=` 双查；substring 命中 chrome_bin 与 profile path。`comm` 不用（macOS 截断）。Probe 2 实证。"
6. **Decision matrix / D3** 移除 "(spike 验证 plan D3 后已在 chrome_backend.py per-channel 分流)" 之类暗示已存在的措辞（如果有）。

---

## 后续 (在 plan 内继续)

Unit 0 出口准则达成，Unit 1 可启动。

**memory cleanup TODO（spike 报告 ship 后）**：

- 改写 `[[chrome_backend_per_channel_profile_via_env]]` body 说明 main 上不存在；Unit 1 实施后再翻回 true。
- 新增 `[[chrome-teardown-killpg-eperm-on-macos]]` (feedback) 记 Probe 1 发现 — 避免未来 ce:plan 再写 "用 killpg 干净 teardown" 这种 false invariant。
- 新增 `[[lsof-ps-command-cdp-listener-attribution]]` (reference) 记 Probe 2 方法 — 供后续 attach-mode 设计直接 lookup。
