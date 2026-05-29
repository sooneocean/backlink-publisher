---
date: 2026-05-28
topic: livejournal-prod-enablement-bloglovin-retire
---

# LiveJournal 生产启用 + Bloglovin 正式退役

## Problem Frame

Operator 把 LiveJournal 和 Bloglovin 当作"还没打通的两个平台"放在桌面上要求"优先启用"。
仓库扫描显示前提与现状有出入：

- **LiveJournal**：适配器（`livejournal_api.py`，290 行）、registry 注册
  （`dofollow="uncertain"`、`referral_value="high"`）、WebUI userpass 绑定卡片
  （`_settings_binding_userpass.html` + `channel_bind_save.py` module-dispatch）、
  manifest（`LIVEJOURNAL_MANIFEST` 含 `bind=[BindDescriptor(backend="token-paste",
  storage_state_path="<config_dir>/livejournal-credentials.json")]`）都已 ship；
  缺的只是 operator-execution：**绑定一个一次性账号、跑一次 fresh canary、读
  `link_attr_verification`、按结果开 register-flip PR**。runbook 早已写好：
  `docs/runbooks/2026-05-25-dofollow-canary-closeout.md`。
- **Bloglovin**：Phase 0 probe（`docs/spike-notes/2026-05-25-dofollow-tiering-phase0-probes/findings.md`）
  实证已**事实弃站**（2018 改名 Activate、2021 起停更、首页 Cloudflare 403 拒爬虫、
  无 blog-post 服务）；从未注册过 adapter，也不应该注册。问题不是"打通"，是
  **正式记录退役**让它别再被 operator 反复问起。

此外操作员问到的"生产轮换 / quota wiring"——本仓**不存在自动跨平台分配机制**：
WebUI 每次 publish 由 operator 在 `<select name="platform">` 里手动选一个，
而该 dropdown 由 `bound_platforms`（registry-driven，filter 已绑定渠道）自动填充。
LiveJournal 一旦绑成功，**自动出现在选项里**。runbook 末尾说的 "unscoped quota
wiring" 在本仓语义下是个伪问题——已隐含解决。

## User Flow

```
1. Operator 准备 throwaway LiveJournal 账号（已确认有）
       │
       ▼
2. WebUI 设置页 → LiveJournal 卡片 → 输入 username+password
   → channel_bind_save 走 livejournal_api 模块 dispatch
   → MD5(password) → hpassword
   → safe_write.atomic_write 到 livejournal-credentials.json (0o600)
       │
       ▼
3. WebUI 主面板 platform dropdown 自动出现 LiveJournal（bound_platforms driven）
       │
       ▼
4. 单 seed canary publish：
   plan-backlinks → validate-backlinks → publish-backlinks（--publish, NOT --resume）
   → 输出含 link_attr_verification 字段
       │
       ▼
5. 打开发出的 canary URL → 找指向 target 的 <a> → 看 rel 属性
       │
       ├── dofollow ───► 开 PR：register() 改 dofollow=True
       │                + 删 _R["livejournal"]
       │                + 加 regression test pin dofollow_status("livejournal") is True
       │
       └── nofollow ───► 接受 dofollow=False（referral_value="high" 留它做转介渠道）
                        + 改 register() 删 dofollow="uncertain"
                        + 记录 rationale 到 _R["livejournal"]
                        + 加 regression test 锁住
       │
       ▼
6. 顺手清 _manifests.py:306-307 过期注释
   ("# No settings card today; binding lives in CLI.")
       │
       ▼
7. Bloglovin 文档化退役：
   docs/notes/retired-platforms/bloglovin.md（指向 findings.md 的实证 + 决议日期）
```

## Requirements

**LiveJournal — execution（runbook-driven，无新代码）**
- R1. Operator 用 WebUI 设置页的 LiveJournal 卡片绑定 throwaway 账号；绑定成功后
  `~/.config/backlink-publisher/livejournal-credentials.json` 以 `0o600` 存在
  且 schema 为 `{username, hpassword}`（不存明文密码）。
- R2. 跑一次单 seed 的 fresh canary publish（绝**不**用 `--resume`，checkpoint
  不持久化 verdict），并捕获 publish 输出里的 `link_attr_verification` 字段。
- R3. 打开发出的 canary 文章 URL，**只看指向 target 的那个 `<a>` 的 `rel`**
  （不要看 `verify_link_attributes.nofollow_detected` 的页面级聚合，那是 nav/footer 噪声），
  把判定写进 PR 描述。
- R4. 根据 R3 verdict 一次 PR 把 register-flip 落地：
  - dofollow → 改 `register(..., dofollow=True)`、删 `_R["livejournal"]`、
    drop `rationale=` / `referral_value=` 多余参数（如适用）；
  - nofollow → 改 `register(..., dofollow=False)`、保留 `_R["livejournal"]`
    并补 rationale、保留 `referral_value="high"`；
  - 两种情况都**新增 regression test**钉住最终状态（不允许 silent regress 回
    `"uncertain"`）。
- R5. 同 PR 删 `_manifests.py:306-307` 的过期注释
  `# No settings card today; binding lives in CLI.`。

**Bloglovin — 正式退役归档**
- R6. 新建 `docs/notes/retired-platforms/bloglovin.md`，单页：链回 Phase 0 findings
  + 列退役证据（rebrand 时间线、Cloudflare 403、无 blog-post 服务）+ 决议日期；
  **不**注册 adapter，**不**写 `register("bloglovin", ...)`，**不**进 retire-channels 工具
  （那是给已注册但要下线的渠道用的）。
- R7. 在 `docs/notes/retired-platforms/` 目录写一个 `README.md`，列出未来不再
  考虑的平台（首批：bloglovin），让"为什么没有 X 平台"的问题有唯一文档出口。

## Success Criteria

- WebUI 上能在 30 秒内绑定 LiveJournal、能在主面板 platform dropdown 里选到它、
  能完整跑通一轮 plan/validate/publish 单 seed 流，且 LiveJournal 上能用浏览器
  打开看到 canary post + 目标外链。
- registry 里 `dofollow_status("livejournal")` 返回值不再是 `"uncertain"`，
  且被 regression test pin 住。
- Operator（或未来的 Operator）问"Bloglovin 怎么没接？"时，能在
  `docs/notes/retired-platforms/bloglovin.md` 找到一页有日期、有证据的答复。

## Scope Boundaries

- **不**做跨平台自动 quota / 轮换分配系统——本仓无此机制，且 operator 手选
  platform 已是设计意图（每条 seed 行已带 `platform`）。
- **不**重新 probe Bloglovin（已 4-source 实证，二次 probe 低 ROI；如果未来确有
  需要由独立 spike 立项）。
- **不**做 SPA 浏览器 fallback、不动 justpaste.it / teletype.in 等其他 Phase 0
  CONDITIONAL-deferred 平台——它们不在本次诉求里。
- **不**因为 Bloglovin 退役就去找替代平台——除非操作员之后明确开新 brainstorm。
- LiveJournal canary 仅跑 **1 个 seed**——这是 runbook 设计，不是省事。多 seed
  在 dofollow 未确认前是浪费 throwaway 账号信誉。

## Key Decisions

- **Bloglovin 接受 Phase 0 NO-GO 结论**：rebrand+abandoned+CF-403+无 blog 服务，
  四条证据互证；不二次 probe；不在 registry 注册；只产文档化归档。
- **LiveJournal 走 WebUI 绑定而非 CLI**：`_manifests.py:306` 注释过期，userpass
  卡片+save route 已 ship（Plan 009），CLI 路径反而是历史路径；用 WebUI 顺便
  验证 binding 通路。
- **runbook 决定 fresh 不 resume**：`--resume` checkpoint 不持久化
  `link_attr_verification`，resume 后 verdict 字段会缺。
- **dofollow 翻牌 PR 与 canary 跑齐**：把"读 verdict + 改 register + 加 test"
  打包一个 PR，因为分两个 PR 会出现"verdict 已知但 register 还显示 uncertain"
  的尴尬窗口（runbook 暗示但未明说，这里固化）。
- **既不 dofollow 也不 nofollow 时**：若 anchor 完全没出现（比如 LiveJournal
  渲染剥掉了 `<a>`），按未 ship、视为不可用，回 runbook step 1 重测；不
  registry-flip。

## Dependencies / Assumptions

- Operator 已自备 throwaway LiveJournal 账号（**已确认**）。
- `BACKLINK_PUBLISHER_CONFIG_DIR` 走默认 `~/.config/backlink-publisher/`。
- 该 throwaway 账号的密码永远只用于此场景（hpassword=MD5(password) 不可
  rotate；账号泄漏只能改密 → 改密前所有已发文章会失去 ownership 控制）。
- WebUI 用 `python webui.py` 跑在本地（dev mode），canary publish 不需要
  off-loopback 网络授权（adapter 本身要出网，但 WebUI host 不需特殊 env）。

## Outstanding Questions

### Resolve Before Planning

（已清空——operator 选择在 plan 阶段答两个问题，brainstorm 不阻塞。）

### Deferred to Planning

- [Affects R2][User decision] canary 用哪一行 seed？需要 `(target_url, main_domain,
  url_mode, target_language)` 四元组——planner 启动时第一件事问 operator 拍板，
  不要替 operator 选。
- [Affects R5][User decision] register-flip PR 的命名约定走当前 PR convention 还是
  专门加 `livejournal/canary-closeout` 前缀？planner 阶段对照 `git log` 近 10 个
  PR 的命名风格再问 operator。
- [Affects R1][Technical] 验证 livejournal-credentials.json 在写入时确实走
  `safe_write.atomic_write`（PR #140 后契约）而不是历史 hand-rolled writer；
  如有遗漏，同 PR 修。
- [Affects R3][Needs research] 确认 LiveJournal 默认主题的文章渲染会保留
  `<a rel="noopener noreferrer">`——Phase 0 探测是单点；若用户用了自定义
  CSS/template，可能行为不同；canary 前先看默认主题。
- [Affects R4][Technical] regression test 放在哪个测试文件最合适
  （`tests/test_registry_dofollow.py`？还是 `test_r9_extension_readiness.py`
  附近？）；planner 决定。
- [Affects R7][Technical] `docs/notes/retired-platforms/` 这个路径是新建的，
  确认不与 `docs/solutions/` 等既有目录语义冲突；如冲突改用更明确路径。

## Next Steps

→ `/ce:plan` for structured implementation planning（operator 已授权 defer
两个 user-decision 到 plan 阶段）。
