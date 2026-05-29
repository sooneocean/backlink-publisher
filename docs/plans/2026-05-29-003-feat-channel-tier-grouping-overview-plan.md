---
title: "feat: 渠道綁定總覽按自动化分层分组"
type: feat
status: completed
date: 2026-05-29
origin: docs/brainstorms/2026-05-29-channel-grouping-by-automation-requirements.md
claims: {}
---

# feat: 渠道綁定總覽按自动化分层分组

## Overview

把 WebUI `/settings` 的「渠道綁定總覽」面板从一条平铺的 16 渠道卡片列表,改造成 3 个**独立可折叠**的自动化分层分组(开箱即用 / 填凭证即自动 / 需浏览器登录态),组内已绑定渠道优先。分层由 `auth_type` 推导,纯展示层改动,不触碰任何绑定/验证/发布逻辑、路由或配置表单。CSDN/掘金已在 2026-05-28 移除(仅存在于 `_REJECTED_PLATFORMS`),本次为确认性收尾,不做物理删除 (see origin)。

## Problem Frame

总览面板内 `{% for name, status in dashboard_channels %}` 把全部 16 个活跃渠道平铺渲染,用户无法一眼分辨「哪些开箱即用、哪些要填凭证、哪些要登录态」,需逐张读徽章判断从哪开始。按自动化程度分组 + 组内已绑定优先,直接服务用户「我现在能发哪些 / 下一步配哪个最省事」的决策 (see origin: docs/brainstorms/2026-05-29-channel-grouping-by-automation-requirements.md)。

**目标面(已与用户确认):** 仅「渠道綁定總覽」面板(`settings.html` 的 `#overview-panel` 内循环)。`#section-channels` 下的每渠道配置卡栈(6 张硬编码卡 + `_settings_cardless_channels.html`)**不在本次范围**——改动大、回归面广,留待后续按需求驱动。

## Requirements Trace

- R1. 总览面板按 3 个自动化分组渲染,每组为独立可折叠区块(Bootstrap collapse,**无** `data-bs-parent`)。
- R2. Tier 1「开箱即用」默认展开,Tier 2/3 默认折叠;`show` class 与 `aria-expanded` 由同一条件派生。注:总览面板 `#overview-panel` 自身默认折叠(本次不改),故 R2 的「默认展开」指**用户展开总览面板后**,Tier 1 即处于展开态,非页面加载即可见。
- R3. 每组标题显示该组渠道总数与就绪数量(如「填凭证即自动 · 就绪 3/9」;就绪=已绑定或免绑定 anon)。
- R4. 自动化等级仅由 `auth_type(name)` 推导(anon→T1;token/token_fields/oauth/userpass→T2;paste_blob/live_browser→T3);单一集中纯函数,无 per-channel 覆盖、无新 manifest 字段。
- R4a. `auth_type` 为 `None` 时归入 Tier 2,绝不从所有分组消失;需测试覆盖。
- R5. 组内已绑定优先,未绑定在后,两段间轻量视觉分隔。
- R6. 段内按 `active_platforms()` 返回序(字母序)稳定排列,避免刷新跳动。
- R7. 现有每渠道徽章(auth/dofollow/publish-backend)、verify/dry-run/绑定按钮全部保留,仅改分组与排布。
- R10. 折叠状态会话内跨重渲染/verify 保持(需 Unit 4 新增小量 JS,泛化现有 `#overview-panel` 持久化 pattern)。
- R11. 每组标题附一行等级含义副文案。
- R12. 零成员组隐藏;零绑定组正常显示「0/N」+完整未绑定列表。
- R8/R9. 确认 CSDN/掘金不出现在任何分组(未注册,`active_platforms()` 从源头排除);保留 `_REJECTED_PLATFORMS` 条目与 `test_registry_rejected_platforms.py`,不物理删除。

## Scope Boundaries

- 仅改总览面板;不改 `#section-channels` 配置卡栈、不改任何 partial 表单。
- 不改后端绑定/验证/发布逻辑、路由、配置 schema。
- 不新增渠道、不下线渠道、不物理删除 csdn/juejin 痕迹。
- 不引入前端框架、不引入拖拽/搜索/过滤、不拆分 Tier 2 子组(仅当实测过长才在后续评估)。
- 不改总览面板 `#overview-panel` 自身的默认折叠态(分层默认态作用于面板内部)。

## Context & Research

### Relevant Code and Patterns

- **渠道列表来源** `webui_app/helpers/contexts.py:307-323` `_settings_context()`:`dashboard_channels = [(name, get_channel_status(name, cfg)) for name in active_platforms()]`。状态 dict 已带 `auth_type`、`bound`、`dofollow`、`publish_backend`(`webui_app/binding_status.py:105-134`)。分组/排序所需数据已齐,**无需新增后端查询**。
- **目标循环** `webui_app/templates/settings.html:75-78`(`#overview-panel` 内,调用 `dashboard_channel_card` 宏)。宏 `_channel_card_macro.html` 为纯展示,**分组/排序必须发生在宏调用之前**(模板循环或上下文层),保持宏可复用。
- **auth_type 取值域** `registry.auth_type(name)`(`src/.../publishing/registry.py:427`)返回 `_AUTH_TYPE_VALUES`(anon/token/token_fields/paste_blob/userpass/oauth/live_browser)之一或 `None`。`platforms_by_auth_type()`(同文件 L439,「live from active_platforms(),never cached」)是「按 auth_type 反查」的既有先例,本次分层函数对齐其「不缓存、运行时计算」风格。
- **active_platforms()** (`_registry_manifest.py:77-93`)返回 `sorted()` 且仅 `visibility=="active"`——`linkedin`(experimental)与 csdn/juejin 已被排除,故实际 16 渠道。
- **独立折叠 + 持久化**:
  - 折叠 HTML 惯例:`<button type="button" data-bs-toggle="collapse" data-bs-target="#<id>" aria-expanded=... aria-controls=...>` + `<div id="<id>" class="collapse">`(`settings.html:56-63` 总览面板、`:88-104` 渠道卡)。
  - **持久化目前是单元素硬编码,不是通用机制**(`settings_main.js:386-402`):仅对 `#overview-panel` 读写 `localStorage('settings:overviewOpen')`,恢复 `show`/`aria-expanded` + 监听 `show/hide.bs.collapse`。**tier 面板要会话内持久化(R10)必须新增 JS(Unit 4),按此 pattern 泛化到 `#tier-1/2/3`。**
  - 深链 `_openCollapseForHash()`(`settings_main.js:354-367`)对任意 `.collapse[id]` 生效(仅按 hash 展开,不持久化)——tier 面板有稳定 id 即免费获得深链。
  - chevron 旋转:基础 `.chevron` 过渡 + `prefers-reduced-motion` 已存在(`settings.css:134-136`);每个 toggle 类需自己的 `[aria-expanded="true"] .chevron` 旋转选择器(现有 `.channel-toggle` L135、`.overview-collapse-toggle` L150、`.llm-pro-toggle` L151 为镜像样板)。

### Institutional Learnings

- `docs/plans/2026-05-18-011-refactor-settings-channel-collapse-plan.md`:独立折叠(非 accordion)惯例来源;toggle 必须是 `<form>` 外的独立 `<button>`,结构隔离避免事件冒泡污染 Loading Overlay 全局 submit 监听。
- `docs/plans/2026-05-28-010-feat-llm-pro-mode-collapse-plan.md`:**`aria-expanded` 必须与服务端渲染的 `show` class 同源**,否则 Bootstrap 首次点击会反向(本计划 Tier 1 默认展开正中此坑);chevron 用 `bi-chevron-right` + CSS `[aria-expanded="true"]` 旋转,每个 toggle 类需自己的 CSS 选择器,基础 `.chevron` 过渡与 `prefers-reduced-motion` 已存在。
- `docs/plans/2026-05-22-005-feat-settings-overview-collapse-plan.md`:per-session 折叠持久化机制即上文 `_initCollapsePersistence`,本计划直接复用。
- 测试约定:`tests/test_settings_dashboard_rendering.py` 已把期望渠道列表同步到 `active_platforms()`(不重复过滤逻辑);R4a 的 `None→Tier 2` 兜底测试加在此处。

## Key Technical Decisions

- **分组发生在上下文层,不在宏内**:在 `_settings_context()` 产出预分组结构 `dashboard_channel_tiers`,模板只做嵌套循环。保持宏纯展示、可复用,且分层/排序逻辑可被纯函数单测覆盖(比模板内联逻辑更易测)。
- **分层映射放 WebUI 层而非 registry**:tier 是 UI/UX 分组概念,不是发布域概念;放 `webui_app/helpers/`(依赖方向 webui→registry 合法)。registry 只保留 `auth_type` 真值域,不渗入 UI 分组语义。
- **复用通用折叠持久化,不写新 JS**:给分组面板 `tier-1/tier-2/tier-3` 稳定 id 即满足 R10;首次访问无 localStorage 记录时保留服务端默认(Tier 1 展开),之后尊重用户选择——恰好实现「默认态只在首次决定」。
- **「已就绪」判定单一口径**:`_is_ready(status) = (status.auth_type == 'anon') or status.bound`。anon 渠道(免绑定)计入「已绑定/就绪」段,与宏现有「免綁定·就緒」徽章一致;R3 计数与 R5 排序共用此判定,杜绝两套「bound」定义。
- **确认性收尾,不动 csdn/juejin 护栏**:护栏价值 > 清痕迹;`test_registry_rejected_platforms.py` 保留 (see origin R9)。

## Open Questions

### Resolved During Planning

- 分层落在哪个面? → 仅总览面板(用户确认)。配置卡栈留待后续。
- R10 需要新 JS 吗? → **是**,需小量 JS:现有持久化仅硬编码 `#overview-panel`(`settings:overviewOpen`),非通用机制;Unit 4 按同 pattern 泛化到 tier 面板。
- 分层映射放哪? → WebUI helper 纯函数(`webui_app/helpers/channel_tiers.py`)。
- 计数与排序口径? → 统一 `_is_ready()`(anon 视为就绪),由 helper 附 `ready` 标记;计数文案用「就绪 X/Y」。

### Deferred to Implementation

- 分隔已绑定/未绑定两段的具体视觉(分隔线 vs「未配置」子标签)由实装时按现有 `settings.css` 风格定,二者皆满足 R5。
- 分组标题副文案(R11)的最终中文措辞在实装时定稿。
- 是否拆分 Tier 2(9 渠道):仅当实测展开后超约 1.5 屏才在后续计划评估,本次单组。

## High-Level Technical Design

> *以下为方向性示意,供 review 验证思路,非实现规范。实现 agent 应视作上下文,而非照抄的代码。*

数据流(分组发生在宏调用之前):

```
active_platforms()  ──sorted, 仅 active, 已排除 csdn/juejin/linkedin
   │  [(name, status{auth_type,bound,dofollow,...}), ...]   ← 现有 dashboard_channels
   ▼
group_channels_by_tier(dashboard_channels)        ← 新纯函数 (channel_tiers.py)
   │   按 TIER_BY_AUTH_TYPE 分桶(None→T2 兜底)
   │   每桶内: ready 优先, 段内保持入参(字母)序; 给每项附 ready 标记
   ▼
dashboard_channel_tiers = [
   { key:'tier-1', label:'开箱即用', subtitle:'无需任何配置即可发布',
     total:3, ready:3, open:True,  channels:[(name,status,ready), ...] },
   { key:'tier-2', label:'填凭证即自动', ..., open:False, ... },
   { key:'tier-3', label:'需浏览器登录态(半自动)', ..., open:False, ... },
]   ← 零成员组在此过滤掉 (R12)
   ▼
settings.html #overview-panel: {% for g in dashboard_channel_tiers %}
   折叠头(label + 就绪 ready/total + 副文案 + chevron) → <div id="{{g.key}}"
   class="collapse{% if g.open %} show{% endif %}" aria-同源> 内: 就绪段 → 分隔 → 未就绪段
   → 每渠道仍调用 dashboard_channel_card(name, status, ...)
   ※ tier 面板持久化由 Unit 4 JS 按 id 接管
```

分层映射(权威):

| auth_type | Tier |
|---|---|
| anon | tier-1 |
| token / token_fields / oauth / userpass / **None** | tier-2 |
| paste_blob / live_browser | tier-3 |

## Implementation Units

- [x] **Unit 1: 分层纯函数 + 单测**

**Goal:** 新增把 `dashboard_channels` 分组为有序分层结构的纯函数,含 None 兜底、已绑定优先、段内稳定序、零成员组过滤。

**Requirements:** R4, R4a, R5, R6, R12, R3(计数), R11(label/subtitle 数据)

**Dependencies:** 无

**Files:**
- Create: `webui_app/helpers/channel_tiers.py`
- Test: `tests/test_channel_tiers.py`

**Approach:**
- `TIER_BY_AUTH_TYPE: dict[str|None, str]` 单一映射;含 `None: 'tier-2'` 兜底;未知 auth_type 同样落 tier-2。
- 三个分组的有序元数据(key、label、subtitle、默认 open):`tier-1` open=True,其余 False。
- `_is_ready(status)`:`status.get('auth_type')=='anon' or status.get('bound')`。
- `group_channels_by_tier(dashboard_channels)`:遍历分桶 → 桶内 `sorted(key=lambda: not ready)`(stable,保持入参字母序)→ 给每项附 `ready` 标记(`(name, status, ready)` 或在 status 内打标),供模板分段不再二次推导 → 计算 total/ready 计数 → 过滤 total==0 的组 → 返回有序 list[dict]。**不缓存**,每次计算(对齐 `platforms_by_auth_type` 风格)。

**Patterns to follow:** `registry.platforms_by_auth_type()`(运行时计算、不缓存);分桶数据形状参考 `dashboard_channels` 的 `(name, status)` 元组。

**Test scenarios:**
- Happy path: 给定全 16 渠道的 `(name,status)` 列表,返回 3 组,各组成员与权威映射表完全一致。
- Happy path: 每组 `total`/`ready` 计数正确;anon 渠道计入 ready;tier-1 `open=True`,tier-2/3 `open=False`。
- Edge case: 组内已绑定渠道全部排在未绑定之前,且两段内部各自维持字母序(传入乱序也稳定输出)。
- Edge case (R4a): `auth_type=None` 的渠道落入 tier-2(不丢失);未知/未来 auth_type 值同样落 tier-2。
- Edge case (R12): 某 tier 无成员时该组被过滤,不出现在返回列表;某 tier 全未绑定时 `ready=0` 且成员完整保留。
- Edge case: 空输入 → 返回空 list(不抛错)。
- Happy path: 输出每项带正确 `ready` 标记(anon→True、bound→True、未绑定非 anon→False),模板可直接据此分段。

**Verification:** `tests/test_channel_tiers.py` 全绿;函数对全渠道集合输出与 origin 分层表一一对应。

- [x] **Unit 2: 上下文接线**

**Goal:** 在 `_settings_context()` 用 Unit 1 函数产出 `dashboard_channel_tiers` 上下文键,供模板消费;`dashboard_channels` 保留(`_settings_cardless_channels.html` 仍用)。

**Requirements:** R1, R3

**Dependencies:** Unit 1

**Files:**
- Modify: `webui_app/helpers/contexts.py`(`_settings_context()` 返回 dict 增加 `dashboard_channel_tiers`)
- Test: `tests/test_settings_dashboard_rendering.py`(扩展)

**Approach:**
- 复用现有 `dashboard_channels` 计算结果,调用 `group_channels_by_tier(dashboard_channels)` 赋给新键。
- 包在现有 `try/except`(渲染不得因分组失败而炸)——失败回退空 list,模板自然不渲染分组。

**Patterns to follow:** `contexts.py:307-323` 现有 `dashboard_channels` 构造与 `except` 兜底风格。

**Test scenarios:**
- Happy path: `_settings_context()` 返回含 `dashboard_channel_tiers`,3 组,成员并集 == `active_platforms()`,无重复、无遗漏。
- Integration (R4a): 该测试经真实 Flask client `GET /settings`(非手搓 context dict),故用 `monkeypatch` 打桩 `registry.auth_type`(或 `binding_status.get_channel_status`)使某活跃渠道 `auth_type=None`,断言其落入 tier-2(不消失)——沿用 `test_settings_dashboard_rendering.py` 中 `TestGracefulDegradation` 的 monkeypatch 套路(L166-173)与「期望列表同步 active_platforms()」约定。
- Integration: csdn/juejin 不出现在任何组(`active_platforms()` 源头保证)。
- Error path: `group_channels_by_tier` 抛错时 `dashboard_channel_tiers` 回退为 `[]`,`_settings_context()` 不抛。

**Verification:** `/settings` GET 200;新键存在且结构正确;现有 dashboard 渲染测试不回归。

- [x] **Unit 3: 总览面板模板分组 + CSS**

**Goal:** 把 `#overview-panel` 内的平铺循环改为嵌套分层折叠;每组折叠头含计数与副文案;组内已绑定/未绑定分段;Tier 1 默认展开;CSS 加分组 toggle 的 chevron 旋转选择器。

**Requirements:** R1, R2, R3, R5, R7, R10, R11, R12

**Dependencies:** Unit 2

**Files:**
- Modify: `webui_app/templates/settings.html`(替换 `:75-78` 平铺循环为分层嵌套循环)
- Modify: `webui_app/static/css/settings.css`(新增 `.tier-toggle[aria-expanded="true"] .chevron` 旋转等)
- Test: `tests/test_settings_dashboard_rendering.py` / `tests/test_webui_route_contract.py`(DOM 结构断言)

**Approach:**
- **先把 `{% set _carded_channels = [...] %}` 上提到 tier 循环之外**(现状定义在总览块 L74,是模板局部 set;移入嵌套循环会未定义)。`binding_channels` 是上下文键,天然可用。
- `{% for g in dashboard_channel_tiers %}`:折叠头用 `<button type="button" class="tier-toggle" data-bs-toggle="collapse" data-bs-target="#{{ g.key }}" aria-expanded="{{ 'true' if g.open else 'false' }}" aria-controls="{{ g.key }}">`,显示 `g.label · 就绪 {{ g.ready }}/{{ g.total }}` + `g.subtitle` 副文案 + chevron。
- 面板:`<div id="{{ g.key }}" class="collapse{% if g.open %} show{% endif %}">`——`show` 与 `aria-expanded` 同源(规避首点反向坑)。
- 组内:用 helper 附的 `ready` 标记分段——先渲染就绪段,插入轻量分隔(分隔线或「未配置」小标签),再渲染未就绪段;**模板不再二次推导 ready**;每渠道仍调用 `dashboard_channel_card(name, status, bindable=(name in binding_channels), has_card=(name in _carded_channels or status.auth_type not in ('live_browser','oauth',None)))`——**verbatim 保留现有 `bindable` / `has_card` 表达式**。
- toggle 是 `#overview-panel` 内、各自 `<form>` 外的独立 `<button>`(结构隔离,事件冒泡不影响 Loading Overlay)。
- 稳定 id `tier-1/2/3` 不与现有 `overview-panel`/`channel-<name>`/`llm-pro-mode-collapse` 冲突 → 免费获得深链(`_openCollapseForHash`);**会话内持久化由 Unit 4 JS 接管**(现有持久化仅覆盖 `#overview-panel`)。

**Execution note:** 改的是渲染聚合而非新行为,建议先跑 `test_webui_route_contract.py` 作为回归网,再调结构。

**Patterns to follow:** `settings.html:56-63` 总览 toggle 标记;`2026-05-28-010` 计划的 `aria-expanded`↔`show` 同源 + chevron 旋转 CSS 惯例;`.overview-collapse-toggle`/`.channel-toggle` 现有 CSS 选择器(镜像出 `.tier-toggle`)。

**Test scenarios:**
- Happy path: `/settings` 渲染出 3 个 `id="tier-1|2|3"` 的 `.collapse` 面板,各含正确渠道卡。
- Happy path (R2): `tier-1` 元素带 `show` class 且其 toggle `aria-expanded="true"`;tier-2/3 无 `show` 且 `aria-expanded="false"`(同源断言)。
- Happy path (R3): 每组折叠头文本含「就绪 X/Y」计数(非「已绑定」)。
- Edge case (R5): 某组内就绪渠道 DOM 顺序先于未就绪,且存在分隔元素。
- Edge case (R12,**强制**): 三个 `id="tier-N"` 面板内 `dashboard-channel-card` 总数 == `len(active_platforms())`,且无渠道出现在两个 tier(并集恰好一次)。注:现有 `test_dashboard_card_count_equals_registered_platform_count`(L145-158)只数总量、不验分组结构,会平凡通过,故本条 DOM 结构断言为必需而非可选。
- Integration (R7): 每张卡仍渲染 auth/dofollow/publish-backend 徽章与 verify/dry-run 按钮(宏未被破坏);现有 `dashboard_channel_card` 相关断言不回归。
- Integration (R2 同源): `tier-1` 面板带 `show` 且 toggle `aria-expanded="true"`;tier-2/3 反之——同源断言,防首点反向。

**Verification:** `/settings` GET 200;3 组折叠正常、可同时多开(无 `data-bs-parent`);展开总览面板后 Tier 1 即展开;现有渠道卡功能与回归清单不变。

- [x] **Unit 4: tier 折叠态会话内持久化(JS)**

**Goal:** 把现有仅覆盖 `#overview-panel` 的硬编码持久化,泛化到 tier 面板,使 verify/dry-run 重渲染后用户的展开/折叠选择保持(R10)。

**Requirements:** R10

**Dependencies:** Unit 3(需 tier 面板已带稳定 id 与 toggle 标记)

**Files:**
- Modify: `webui_app/static/js/settings_main.js`(`settings:overviewOpen` 块 ~L386-402 邻近)
- Test: `tests/test_settings_dashboard_rendering.py`(结构性断言:面板具 id + `data-bs-toggle` 标记,作为 JS 行为的可测代理)

**Approach:**
- 镜像现有 `#overview-panel` 持久化 pattern(`getItem/setItem/removeItem` + `show/hide.bs.collapse` 监听),泛化为遍历 `#tier-1/#tier-2/#tier-3`(或 `#overview-panel .collapse[id^="tier-"]`),按 id 用独立 key(如 `settings:collapse:<id>`)读写。
- 恢复逻辑:有记录 → 按记录设 `show`/`aria-expanded`;无记录 → **不动**服务端渲染默认(Tier 1 展开、其余折叠),实现「默认态只在首次访问决定」。
- 不改动现有 `settings:overviewOpen` 块行为(`#overview-panel` 自身持久化保持)。

**Execution note:** JS 行为难以纯单测;以模板结构断言 + 手动冒烟(展开 Tier 2 → 点某渠道 verify 触发重渲染 → Tier 2 仍展开)为验证主力。

**Patterns to follow:** `settings_main.js:386-402` 的 `#overview-panel` 持久化块;`localStorage` try/catch 包裹惯例。

**Test scenarios:**
- Happy path(结构代理): 渲染后 `#tier-1/2/3` 均为 `.collapse` 且有对应 `data-bs-toggle="collapse"` toggle —— 满足通用持久化所需的 DOM 契约。
- 手动冒烟(记录于 PR): 展开 Tier 2 → 任一渠道 verify → 页面重渲染后 Tier 2 仍展开;清 localStorage 后重载 → 回到 Tier 1 展开、Tier 2/3 折叠的默认态。

**Verification:** tier 面板展开态跨 verify/dry-run 重渲染保持;首次访问(无 localStorage)呈现服务端默认;`#overview-panel` 自身持久化不回归。

## System-Wide Impact

- **Interaction graph:** `_settings_context()` 新增一个只读上下文键 + `settings.html` 总览面板内 DOM 重排 + Unit 4 新增 tier 持久化 JS;不改路由、宏签名、partial、其余 JS 行为。Loading Overlay 全局 submit 监听因 toggle 结构隔离不受影响。
- **Error propagation:** 分组计算包在 `try/except`,失败回退空 list,`/settings` 仍可渲染(降级为无分组)。
- **State lifecycle risks:** Unit 4 新增 3 个 localStorage key(如 `settings:collapse:tier-1/2/3`),与现有 `settings:overviewOpen` 命名空间不冲突;不影响任何服务端状态。
- **API surface parity:** 不涉及 API/CLI;`#section-channels` 配置卡栈刻意保持原样(本次不追求两面一致)。
- **Unchanged invariants:** `dashboard_channels` 键、`dashboard_channel_card` 宏签名、所有 settings 路由、csdn/juejin 拒绝护栏(`test_registry_rejected_platforms.py`)均不变。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Tier 1 默认展开但 `aria-expanded` 与 `show` 不同源 → 首次点击反向 | 模板用同一 `g.open` 条件同时驱动 `show` class 与 `aria-expanded`;加同源断言测试 |
| 分组导致渠道卡丢失/重复 | Unit 1/2 断言成员并集 == `active_platforms()`、无重复;Unit 3 断言卡总数不变 |
| 未来新渠道 auth_type 未登记(None)被静默吞掉 | R4a 兜底落 tier-2 + 专项测试 |
| 改总览面板误伤配置卡栈或路由 | 范围限定 `#overview-panel` 循环;`test_webui_route_contract.py` 作回归网 |
| 分组 toggle 触发事件冒泡影响 Loading Overlay | 沿用既有结构隔离(独立 `<button>`,非嵌套 form 内) |

## Documentation / Operational Notes

- 纯前端展示改动,无迁移、无配置项、无回滚特殊处理;部署即生效。
- 若后续决定也分组 `#section-channels` 配置卡栈,另起计划(需处理 6 张硬编码卡)。

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-29-channel-grouping-by-automation-requirements.md](docs/brainstorms/2026-05-29-channel-grouping-by-automation-requirements.md)
- 渠道列表/状态:`webui_app/helpers/contexts.py:307-323`、`webui_app/binding_status.py:105-134`
- 目标循环/折叠惯例:`webui_app/templates/settings.html:75-78`、`:56-63`、`_channel_card_macro.html`
- 分层取值来源:`src/backlink_publisher/publishing/registry.py:427`(auth_type)、`platforms_by_auth_type` L439、`_registry_manifest.py:77-93`(active_platforms)
- 折叠持久化复用:`webui_app/static/js/settings_main.js:82-111`
- 相关计划:`docs/plans/2026-05-18-011-...`、`2026-05-22-005-...`、`2026-05-28-010-...`
