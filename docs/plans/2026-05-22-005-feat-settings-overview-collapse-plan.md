---
title: "feat(settings): 渠道綁定總覽 可折叠面板"
type: feat
status: completed
date: 2026-05-22
claims: {}
---

# feat(settings): 渠道綁定總覽 可折叠面板

## Overview

在 settings 页面的「渠道綁定總覽」区块加折叠/展开功能，减少初始屏占，用户可随时手动折叠。
折叠状态持久化到 `localStorage`，刷新后保持不变。

## Problem Frame

settings 页面首屏展示所有渠道的绑定状态卡片，当渠道数量 ≥5 时占据大量垂直空间，
用户每次进入 settings 都要向下滚动才能看到配置区块。
渠道总览在日常操作中属于参考信息，不是主要操作目标，适合默认折叠。

## Requirements Trace

- R1. 渠道綁定總覽区块可一键折叠/展开
- R2. 折叠状态在页面刷新后保持不变（localStorage 持久化）
- R3. 折叠时仍显示标题行，并可见折叠状态指示（chevron icon）
- R4. 沿用项目已有 Bootstrap collapse + `.chevron` 转动动效，视觉一致
- R5. 不改动 `_channel_card_macro.html` 宏和 `dashboard_channels` 上下文逻辑

## Implementation Units

- [x] **Unit 1: settings.html — 添加折叠结构**

  h2 加 `.overview-heading` flex 容器 + `overview-collapse-toggle` button，
  `.card mb-4` 包裹进 `#overview-panel.collapse`

- [x] **Unit 2: settings_main.js — localStorage 持久化**

  DOMContentLoaded 恢复状态；`show.bs.collapse` / `hide.bs.collapse` 写/删 `settings:overviewOpen`

- [x] **Unit 3: settings.css — toggle button 样式**

  `.overview-heading` flex、`.overview-collapse-toggle` reset、`[aria-expanded="true"] .chevron` 旋转

## Sources & References

- Pattern: `settings.html` channel-card collapse pattern (lines 67-84)
- CSS: `settings.css` `.chevron` 动效（line 55）
- JS: `settings_main.js` sessionStorage reopen pattern (lines 371-387)
