---
date: 2026-05-20
topic: homepage-url-autoderive-and-ui-polish
supersedes: 2026-05-14-homepage-three-tier-url-requirements (auto-derive out-of-scope clause)
status: ship-ready
---

# Homepage URL 自动派生 + 三栏 chip 化

## Problem Frame

2026-05-14 brainstorm 把 homepage `/` 改成三栏（主网域/分类页/作品页），但明确把"自动派生"列为 out-of-scope —— 操作员每次仍要手填三栏。操作员实际是从浏览器复制一个作品页 URL（例 `https://51acgs.com/comic/6`），三个层级的值都包含在这一个 URL 里。手填 3 次摩擦大、错栏机率不低（估算 ~15%）。

本次补两件事：(1) 单框粘贴 → JS 自动派生三栏；(2) chip 化 UI + 状态可视化（部分延后到 v1.1）。

## v1.0 Requirements

**派生算法**
- **R1**: 路径深度动态判（0/1/2+ 段）+ 尾段 token 检测（letters-only `^[a-z]{3,15}$`）。粘贴/blur/Enter/pre-submit 触发；未锁 chip 被覆盖
- **R2**: 规范化 owner = 派生函数；scheme→https、trailing slash、query/fragment drop、host 保留
- **R3**: 并发 fetch 验证（v1.0 实际改为 serial 避免同 host 节流冲突），closed-enum reason
- **R3.5**: 强化检测 — title 两两比对（前端）+ body size <2KB 后端检测（防 SPA fallback）
- **R3.6**: `max_age_seconds=0` 绕过 `content_fetch._CACHE`
- **R5**: paste_url 输入框是新入口，不被派生回填
- **R7**: `/ce:plan` handler 零改动；form 字段名不变
- **R8a-g**: SSRF gate + CSRF + rate-limit + 闭枚举 reason + 服务端 URL 再校验 + 5s timeout + `BACKLINK_NO_FETCH_VERIFY=1` → 204

**Threat Model**: SSRF 内网探测、云元数据外泄、抓取站滥用 — 三类全 mitigation。

## Resolved Strategic Questions (SQ1-SQ5)

- **SQ1=A** 路径启发式 + R3.5 验证兜底（不做 platform 白名单）
- **SQ2=B** v1 cut R9 "已配置主域下拉"（3 reviewer 一致）
- **SQ3=A** 锁 chip 用 toast 通知（避免静默吞 paste）
- **SQ4=B** 422 错误前端 pre-submit 校验（守 R7 invariant）
- **SQ5=B** 拆 v1.0 / v1.1（先验证派生 hit 率再投 UI 抛光）

## v1.1+ Deferred

R4 lock state machine、R4.1 unlock UX、R5.1 toast、R6 chip cards (+R6.1-R6.4)、R10 advanced drawer、R11 chip pending state、R12 422 a11y。R9 "configured-domains dropdown" cut entirely。

## Scope Boundaries

- 不在范围：服务端派生、智能 sniff、subdomain 智能识别、批量粘贴、`url_new` 删除、移动端优化、浏览器扩展、bookmarklet、clipboard 自动监听、`[sites.*].last_used_at` 字段

## See Also

- Plan: `docs/plans/2026-05-20-002-feat-homepage-url-autoderive-v1-plan.md`
- v1.0 ship branch: `feat/homepage-url-autoderive-v1`
- Predecessor brainstorm: `docs/brainstorms/2026-05-14-homepage-three-tier-url-requirements.md`
