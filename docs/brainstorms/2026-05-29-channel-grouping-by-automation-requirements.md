---
date: 2026-05-29
topic: channel-grouping-by-automation
---

# 渠道按自动化程度分组 + CSDN/掘金彻底移除收尾

## Problem Frame

WebUI 渠道页目前把 16 个活跃渠道平铺成一长串卡片,没有任何语义分组。用户(运营者)无法一眼分辨"哪些开箱即用、哪些要填凭证、哪些要登录态",每次都要逐张卡片去看徽章才能判断从哪开始配置,体验差、上手慢。

同时用户要求"彻底移除 CSDN、掘金"。经核查:**这两个渠道已于 2026-05-28 从活跃渠道硬删除**,适配器、manifest、auth 映射均已不存在;`active_platforms()` 也不会再把它们渲染到 UI。代码层面已干净,本次仅做收尾确认。

本次核心工作 = **把剩余渠道按"自动化程度"分成可折叠的分组,改善渠道页的可读性与上手体验**。

## 自动化分层模型

分层主轴 = **自动化程度**(用户拍板)。等级可直接由现有的 `auth_type(name)` 推导,无需新增数据结构。

> 注:`linkedin` 当前 `visibility="experimental"`,会被 `active_platforms()` 排除,不出现在渠道页(且其 `auth_type` 为 None),故不计入下表。若日后转正为 `active`,按 R4 的 None 兜底规则归入 Tier 2。

| 分组 | 含义 | 默认状态 | 包含渠道(共 16) |
|---|---|---|---|
| **Tier 1 · 开箱即用** | 无需凭证 / 一键即发,API 直发 | **默认展开** | telegraph、txtfyi、rentry |
| **Tier 2 · 填凭证即自动** | 填 token / 字段 / OAuth / 账密后全自动,API 直发 | 默认折叠 | devto、writeas、ghpages、notion、wordpresscom、hashnode、tumblr、blogger、livejournal |
| **Tier 3 · 需浏览器登录态(半自动)** | 需登录态 / Cookie 粘贴 / 浏览器发布 | 默认折叠 | velog、medium、mastodon、substack |

**术语定义(全文统一)**
- **已绑定** = 该渠道凭证已录入且最近一次 verify 通过(或属无需凭证的 anon 渠道,默认视为已就绪)。全文 R3/R5/R6 与成功标准均以此口径为准,不再混用"可用/已配置好"等措辞。

## Requirements

**分组与折叠**
- R1. 渠道页按上述 3 个自动化分组渲染,每组为一个可折叠区块(accordion)。
- R2. 首次访问时 Tier 1「开箱即用」默认展开;Tier 2、Tier 3 默认折叠。Tier 1 全部为 anon 渠道、默认即"已绑定",故默认展开恒有可操作内容。
- R3. 每组标题显示该组渠道总数与已绑定数量(如「填凭证即自动 · 已绑定 3/10」),折叠态也能看到该进度。
- R4. 渠道的自动化等级**仅由 `auth_type(name)` 推导**(anon→Tier 1;token/token_fields/oauth/userpass→Tier 2;paste_blob/live_browser→Tier 3),上方分层表为权威映射。不依赖"发布后端"字段(代码中无该可查询字段),发布后端徽章仅作展示保留。映射由一个集中的纯函数 `auth_type → tier` 承载(单一 dict 查表,与现有 `auth_type()`/`dofollow_status()` helper 同模式,**无扩展钩子、无 per-channel 覆盖**),不在每渠道 manifest 新增需手工维护的分类字段。
- R4a. **兜底:** 若 `auth_type(name)` 返回 `None`(渠道未登记在 auth 映射中),该渠道归入 Tier 2,**绝不从所有分组中静默消失**;实现需有测试覆盖此兜底路径。

**组内排序**
- R5. 每个分组内,**已绑定的渠道排在前**,未绑定的排在后,两段之间有轻量视觉分隔(分隔线或「未配置」子标签),让用户一眼看到"我现在能发哪些"的边界。
- R6. 段内按现有注册顺序(`active_platforms()` 返回序)作稳定次序,避免每次刷新跳动。

**交互状态**
- R10. 折叠/展开状态在同一会话内跨重渲染与 verify/dry-run 操作保持(如客户端按组记忆);默认折叠态只在首次访问时决定,不在每次操作后强制复位。
- R11. 每组标题附一行说明该等级含义的副文案(如「无需任何配置即可发布」),帮助新用户理解上手成本差异。
- R12. 定义零成员组(若未来 Tier 2 拆分出现空组)与零绑定组(计数 0/N)的渲染:零成员组隐藏;零绑定组正常显示标题计数 + 完整未绑定列表。

**保留现有信息**
- R7. 现有的每渠道徽章(auth 类型、dofollow、发布后端)、绑定/验证/dry-run 操作全部保留,仅改变其分组与排布方式。

**CSDN/掘金移除收尾**
- R8. 确认 CSDN、掘金不出现在任何分组中——它们**根本未注册到 registry**(仅存在于 `_REJECTED_PLATFORMS`),故不会进入 `active_platforms()`,从源头杜绝。
- R9. 保留 `_REJECTED_PLATFORMS` 中的 csdn/juejin 拒绝登记条目及其对应测试 `test_registry_rejected_platforms.py`,作为防误重新注册的护栏(真正的保证在此测试),**不做物理删除**。

## Success Criteria
- 打开渠道页,用户无需逐张读卡片即可分辨三类渠道,并立刻看到「开箱即用」组里可直接发布的渠道。
- 已配置好的渠道始终出现在各组顶部,用户一眼看到"我现在能发哪些"。
- CSDN、掘金在 UI 任何位置均不可见,且无法被误重新注册。
- 渠道页的视觉拥挤度较改造前明显下降(折叠后首屏只展示一组)。

## Scope Boundaries
- 不新增、不下线任何渠道(除已完成的 csdn/juejin 移除收尾外)。
- 不改动渠道的绑定/验证/发布逻辑,仅改 UI 的分组、排序、折叠呈现。
- 不引入用户可自定义的分组/拖拽排序。
- 不做按语言/地区、内容类型等其他维度的分组(本次只做自动化维度)。
- 不物理删除 csdn/juejin 的拒绝登记与归档文档。

## Key Decisions
- 分组主轴选「自动化程度」而非语言/内容类型:用户最关心的是"上手成本与能否立刻发布",自动化维度直接服务这个决策。
- 自动化等级仅由 `auth_type` 推导,不新建手工维护的分类字段:落地成本低、无长期维护负担(YAGNI)。tier→label 映射是一个小的集中辅助函数(非 schema 变更,但属新增代码)。
- 呈现用折叠分组而非 Tab:支持跨组对比、首屏聚焦「开箱即用」,与项目已有的 settings-channel-collapse 折叠模式一致(复用既有模式)。
- 组内「已绑定优先」:对齐用户高频意图"我现在能发哪些"。
- csdn/juejin 保留拒绝登记:护栏价值 > 清除痕迹的洁癖,防止未来误重新注册。

## Dependencies / Assumptions
- `auth_type(name)` 已在 registry 提供,值域(anon/token/token_fields/oauth/userpass/paste_blob/live_browser)与分层表一一对应,可在 WebUI 上下文层稳定读取。
- 复用现有折叠**交互机制**(展开/收起 toggle 的 CSS/JS),但分组容器为新增——现有 collapse 偏每卡片/区段级,非现成的多组 accordion(参见 docs/brainstorms/2026-05-18-settings-channel-collapse-requirements.md)。

## Outstanding Questions

### Deferred to Planning
- [Affects R1][Technical] Tier 2 含 9 个渠道,折叠后是否仍偏长 —— 是否把 Tier 2 再拆为「轻配置(仅 token)」与「重配置(多字段/OAuth/账密)」两个子组?基线为单一 Tier 2,仅当实测首屏/滚动出现问题(如展开后超过约 1.5 屏)才拆,勿投机性拆分。
- [Affects R3/R5][Technical] R3 的"已绑定 X/Y"计数与 R5 的"已绑定优先"排序**必须读同一个绑定状态 helper**(binding_status / get_channel_status 二选一统一),使"已绑定"只有一个定义;并确认该 helper 在页面渲染路径上调用足够廉价,避免引入缓存/刷新复杂度。
- [Affects R10][Technical] 折叠状态会话内持久化的具体落点(客户端 localStorage / sessionStorage vs 服务端),与现有 toggle 机制如何衔接。

## Next Steps
→ /ce:plan 进行结构化实现规划(无阻塞性待决问题)
