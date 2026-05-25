---
title: "feat: 发布历史双维度筛选（状态 + 平台）"
type: feat
status: completed
date: 2026-05-18
completed: 2026-05-18
---

# feat: 发布历史双维度筛选（状态 + 平台）

## Overview

在「草稿 & 历史」标签页的发布历史区块顶部加入两组 chip 风格筛选器，让用户按
**状态**（草稿 / 已发布 / 失败）与**平台**（Blogger / Medium / 其他）即时筛选历史
记录。筛选完全由前端 JS 完成，不刷新页面、不改动后端路由。

## Problem Frame

`webui_app/templates/index.html` line 1176-1247 渲染的「发布历史」目前会平铺所有
记录。当历史超过 50+ 条（用户截图显示 80+ 条单日记录）时，找特定状态或平台的
记录需要肉眼扫读，体验差。

每条记录已经携带 `item.status`（drafted / published / success / failed）和
`item.platform`（blogger / medium / unknown）两个字段，但 UI 没有暴露任何筛选
入口。

## Requirements Trace

- R1. 用户可一键将列表筛选为「仅草稿」「仅已发布」「仅失败」「全部」。
- R2. 用户可按平台（Blogger / Medium / 其他）筛选。
- R3. 状态与平台筛选可同时生效（AND 逻辑）。
- R4. 筛选纯前端实现，无需后端改动，刷新或切换标签页后回到「全部 + 全部」。
- R5. 隐藏的记录不影响计数（顶部 chip 显示各状态实际数量）。

## Scope Boundaries

- 不持久化用户的筛选选择（刷新即重置）。
- 不引入分页、虚拟滚动、搜索框。
- 不改动 `webui_app/routes/history.py` 或 `webui_store/`。
- 不改动数据结构、不新增字段。
- 「草稿队列」区块（line 1072-1170）不在本次范围内。

## Context & Research

### Relevant Code and Patterns

- `webui_app/templates/index.html` line 1176-1247 — 发布历史卡片渲染
- `webui_app/templates/index.html` line 1183-1238 — `{% for item in history %}` 循环
  与 `.history-item` div
- `webui_app/templates/index.html` line 390-410 — 现有 `.history-item` CSS
- `webui_app/routes/history.py` — `_history_store.load()` 已返回完整记录列表，
  字段含 `status`、`platform`、`target_url`、`created_at`、`article_urls`、`error`
- 模板内已使用 Bootstrap 5 + Bootstrap Icons + inline `<style>`，沿用即可

### Institutional Learnings

- 现存模板已大量使用 inline JS + `data-*` 属性驱动 UI 交互（如标签页切换、确认
  对话框），新增筛选逻辑沿用同一风格即可，无需引入新依赖。

## Key Technical Decisions

- **筛选 chip 用 `data-filter-status` / `data-filter-platform` 属性 + 单一 JS 函数
  统一过滤**：避免为每个 chip 注册独立 handler；新增/移除筛选项只需调模板。
- **筛选状态用两个 module-level 变量** `currentStatus = 'all'`、`currentPlatform =
  'all'`：状态简单到不值得 `URLSearchParams` 或 `<form>`。
- **`.history-item` 渲染时挂 `data-status` / `data-platform` 属性**：JS 直接读
  attribute，避免重复解析 DOM 文本。
- **平台 chip 列表写死为 `blogger / medium / other`**：`item.platform` 在 `'blogger'`、
  `'medium'` 之外的取值（包括 `'unknown'`、空字符串、错拼）一律归入 `other`，
  归一化在模板渲染时完成。这样新增第三个真平台时只需改模板的 chip 列表与归一
  化白名单一处。
- **空态文本随筛选切换**：当过滤后可见数为 0 时显示「当前筛选无匹配记录」，
  而非误导性的「暂无历史记录」。

## Open Questions

### Resolved During Planning

- chip 计数是否需要实时更新？→ 是，初始渲染时由 JS 计算并填充，每次筛选不改
  动计数（计数代表"该状态/平台共有 N 条"，而非"当前可见 N 条"，更符合直觉）。
- 平台筛选与状态筛选的关系？→ AND（既是草稿、又是 blogger）。

### Deferred to Implementation

- chip 的具体配色与图标：实现时参考 `.status-badge.success/pending/error` 与
  `var(--primary/secondary/success/warning/danger)` 现有调色板即可，不预先指定。

## Implementation Units

- [ ] **Unit 1: 模板筛选条 + data 属性 + 空态占位**

**Goal:** 在「发布历史」card-body 顶部插入双行 chip 筛选条，并为每个
`.history-item` 挂载 `data-status` 与 `data-platform` 归一化属性；新增「无匹配
记录」空态占位。

**Requirements:** R1, R2, R5

**Dependencies:** 无

**Files:**
- Modify: `webui_app/templates/index.html`（line 1176-1247 区块，仅模板与 inline
  `<style>` 改动）

**Approach:**
- 在 `<div class="card-body">`（line 1181）下、`{% if history %}`（line 1182）上方
  插入筛选条 DOM：两行 chip group，第一行状态（全部 / 草稿 / 已发布 / 失败），
  第二行平台（全部 / Blogger / Medium / 其他）。
- 每个 chip 是 `<button type="button" class="filter-chip" data-filter-group="status"
  data-filter-value="drafted">草稿 <span class="chip-count">0</span></button>`，
  默认 `data-filter-value="all"` 的 chip 带 `.active` class。
- 在 `<div class="history-item ...">`（line 1185）追加：
  - `data-status="{{ s }}"` （已有的 `{% set s = item.status %}`，已 normalize：
    success→published，缺失值需在 Jinja 端兜底为空串 → 由 JS 端归类）
  - `data-platform="{{ item.platform if item.platform in ['blogger','medium']
    else 'other' }}"`
- 在 `{% endfor %}` 之后、`{% else %}` 之前插入隐藏的「当前筛选无匹配」占位 div
  `<div id="historyEmptyFiltered" style="display:none;...">无匹配记录</div>`。
- 在文件顶部 `<style>` 区（line 405 附近）加入 `.filter-chip`、`.filter-chip.active`、
  `.chip-count`、`.filter-bar` 几条样式，沿用现有 `var(--primary)` 调色板。

**Patterns to follow:**
- 现有 `.history-item` 卡片样式（line 390-410）
- 现有 `.status-badge.success/pending/error` 配色逻辑（line 1194-1198）
- 现有 inline `<style>` 风格（不引入外部 CSS 文件）

**Test scenarios:**
- Happy path: 模板渲染时，每条 history-item 的 `data-status` 与 `data-platform`
  与 `item.status` / `item.platform` 一致（platform ∉ {blogger, medium} 时归
  `other`）。
- Edge case: `item.platform` 为空字符串、`None`、或 `"unknown"` 时，`data-platform`
  归为 `"other"`，不抛 Jinja 异常。
- Edge case: history 为空时，筛选条不渲染（包在 `{% if history %}` 内），保留原
  「暂无历史记录」空态。

**Verification:**
- 浏览器查看 DOM，每个 `.history-item` 都带 `data-status` 与 `data-platform`
  属性。
- 「无匹配记录」占位默认 `display:none`，不影响视觉。

- [ ] **Unit 2: 前端筛选 JS + chip 计数**

**Goal:** 实现 chip 点击切换状态/平台筛选，AND 逻辑应用到所有 `.history-item`；
初始化时填充每个 chip 的计数；空筛选结果时显示 Unit 1 的占位。

**Requirements:** R1, R2, R3, R4, R5

**Dependencies:** Unit 1

**Files:**
- Modify: `webui_app/templates/index.html`（在文件底部既有 `<script>` 区块内追加
  一个 IIFE 或在 historyPanel 渲染后绑定事件）

**Approach:**
- 单一 IIFE `(function initHistoryFilter(){ ... })()` 包住全部逻辑，置于现有
  `<script>` 块末尾。
- 模块状态：`let currentStatus = 'all', currentPlatform = 'all';`
- `applyFilter()`：遍历 `document.querySelectorAll('.history-item[data-status]')`，
  每条根据 `currentStatus === 'all' || item.dataset.status === currentStatus`
  （`success` 与 `published` 视为同一类——在 dataset 归一化时统一写
  `published`）与平台条件 AND 决定 `style.display = '' / 'none'`；统计可见数，
  若为 0 显示 `#historyEmptyFiltered`，否则隐藏。
- chip 点击 handler：根据 `data-filter-group` 更新 `currentStatus` 或
  `currentPlatform`，同组其他 chip 移除 `.active`，自己加 `.active`，调用
  `applyFilter()`。
- 初始化：扫描 `.history-item` 计算每个 `(group, value)` 计数，写入对应 chip
  内的 `.chip-count`；`all` chip 计数 = 全部条目数；运行一次 `applyFilter()`
  确保初始状态正确。
- 边界：若 `historyPanel` 不存在（无 history 时）直接 return。

**Patterns to follow:**
- 现有 `<script>` 内 `document.querySelectorAll(...)` + `addEventListener('click', ...)` 风格。
- 不引入 jQuery 或新依赖。

**Test scenarios:**
- Happy path: 点击「草稿」chip，仅 `data-status="drafted"` 的卡片可见；点击
  「全部」恢复。
- Happy path: 状态选「草稿」+ 平台选「Blogger」时，仅同时满足两条件的卡片
  可见。
- Edge case: 筛选组合无匹配结果时，`#historyEmptyFiltered` 显示，所有
  history-item 隐藏。
- Edge case: 刷新页面或切换到「新建外链」标签页再切回时，筛选重置为「全部 +
  全部」（因为状态只活在 JS 内存中，符合 R4）。
- Integration: chip 上的计数在初始化后等于实际匹配条目数；点击不同 chip 计数
  保持不变（计数代表总量，非可见量）。

**Verification:**
- 浏览器打开「草稿 & 历史」标签页，点击各 chip 组合，列表实时响应；无 JS 报
  错；切换到其它标签页再回来，筛选已重置。

## System-Wide Impact

- **Interaction graph:** 本改动仅触碰 `index.html` 模板与其内嵌的 `<style>` /
  `<script>`。`/ce:history/update-status` 与 `/ce:history/delete` 表单提交仍会
  整页重渲染，重渲染后 JS 重新初始化，筛选状态回到默认——这与 R4 一致。
- **Error propagation:** 纯前端筛选，DOM 不可见 ≠ 删除，重渲染时一切恢复。
- **State lifecycle risks:** 无持久化、无网络请求，无新风险。
- **Unchanged invariants:** `history.py` 三条路由、`webui_store/`、`item.status` /
  `item.platform` 数据结构、`/ce:history/update-status` 表单提交流程均不变。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| `update-status` 提交后整页重渲染导致用户失去筛选上下文 | 接受为已知限制——本计划范围明确不持久化筛选；如果未来有真实痛点，再讨论 query string 方案 |
| 平台白名单写死，未来新增第三个平台时遗漏 | 归一化与 chip 列表集中在 Unit 1 的模板段落，搜 `'blogger','medium'` 一次修改 |

## Sources & References

- Modify target: `webui_app/templates/index.html` line 1176-1247
- Data source: `webui_app/routes/history.py` line 22-27（`_history_store.load()`）
- Field semantics: `webui_store/__init__.py` 单例 `history_store` 持久化到
  `~/.config/backlink-publisher/publish-history.json`
