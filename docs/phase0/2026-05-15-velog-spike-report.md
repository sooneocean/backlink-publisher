---
title: "Phase 0 spike 报告 — velog.io 适配器可达性 / token TTL / 市场相关性"
date_started: 2026-05-18
date_p0_3_24h: 2026-05-19
date_p0_3_72h: 2026-05-21
date_p0_5_review: 2026-06-01
owner_ops: <运营填写姓名>
owner_eng: <工程填写姓名>
status: paused  # in_progress | pass | fail | pivot_to_alt_platform | paused
paused_reason: "2026-05-18 — Velog Phase 0 steps P0-1..P0-5 全部硬阻塞于运营社交登录(Google/GitHub/Facebook)。优先级让位给 telegra.ph Phase 0(PR #36 进行中)。telegra.ph 6/8 出结果后重新评估:Pass → 推 velog;Fail → 直接 pivot dev.to/hashnode brainstorm,velog 永久搁置"
paused_at: 2026-05-18
related_brainstorm: docs/brainstorms/2026-05-15-velog-adapter-requirements.md
---

> ⚠ **PAUSED 2026-05-18** — see frontmatter `paused_reason`. Spike skeleton + P0-2 helper script remain available for resumption; do not run before re-evaluation post telegra.ph 6/8 verdict.


# Phase 0 spike — velog.io 适配器可达性实测

## 0. 目的

回填 brainstorm `R8 / R9 / R10 / R11` 的 deferred 问题；决定 `velog` 是进入 `/ce:plan` 还是 pivot 到 dev.to / hashnode。

## 1. 达标线（AND；任一 Fail → pivot）

| 编号 | 指标 | 阈值 | 实测 | Pass? |
|---|---|---|---|---|
| G1 | P0-1 端到端 mutation 成功且页面公开可见 | mutation 返回 `data.writePost.url_slug` + 公开 URL 200 + body 含目标外链 | — | ☐ |
| G2 | P0-3 access_token TTL | ≥ 24h 单次登录可连续 mutation | — | ☐ |
| G3 | P0-4 市场相关性决断 | 运营给出 `用韩语` / `用英语` / `不接入` 三选一明确结论 | — | ☐ |
| G4 | P0-5 14 天索引率 | ≥ 70%（5 篇中 ≥ 4 篇被 Google 索引） | — | ☐ |

**最终判定**：四项皆 Pass → `status=pass`，进入 `/ce:plan`；任一不达 → `status=fail` 或 `status=pivot_to_alt_platform`，启动 dev.to / hashnode brainstorm。

---

## 2. P0-1 — 端到端最小 mutation 实测

**目标**：用真实账号 + 手抓 cookie + headers，curl 复现一次成功的 `writePost`。

### 2.1 浏览器抓包步骤

1. Chrome 打开 https://velog.io ，社交登录（Google / GitHub / Facebook 任一）
2. 写一篇空 post，DevTools → Network 过滤 `graphql`，点 "출간하기"（发布）
3. 找到 `POST https://v3.velog.io/graphql` 请求，记录：
   - 全部 request headers（特别注意：`Cookie`、`Origin`、`Referer`、`User-Agent`、`X-XSRF-Token` 或 `Csrf-Token` 类）
   - request body（GraphQL mutation + variables，原样保留）
   - response body

### 2.2 curl 复现

把上一步 headers + body 转 curl，重新 POST 一次（修改 title 避免重复）。记录：

| 项 | 值 |
|---|---|
| 必需 headers（去掉一个就 401/403 的） | — |
| 多余 headers（去掉无影响） | — |
| `User-Agent` 是否敏感（与登录浏览器不一致是否失败） | — |
| `Origin` / `Referer` 是否必需 | — |
| CSRF token 来源（cookie 中字段名 / 是否需要回填到 header） | — |
| mutation 名称（`writePost` 或其他） | — |
| 必需 variables（title / body / tags / is_markdown / is_temp / is_private / url_slug / meta / series_id?） | — |
| 成功响应字段（`data.writePost.id` / `url_slug` / `user.username` 等） | — |
| 公开 URL 模板（`https://velog.io/@<username>/<url_slug>`?） | — |
| 公开 URL 200 + body 含外链 | ☐ |

### 2.3 失败模式速记

| 移除项 | 响应状态 | 错误码 / message |
|---|---|---|
| Cookie | — | — |
| Origin | — | — |
| Referer | — | — |
| X-XSRF-Token（若 P0-1 中存在） | — | — |
| User-Agent 改为 curl 默认 | — | — |

---

## 3. P0-2 — JWT 存储位置确认

跑：`python scripts/velog_spike/dump_session.py --output /tmp/velog-session.json`

脚本会有头打开 velog.io，运营手工登录，然后 dump `context.cookies()` + `localStorage`，识别 JWT 候选字段。

| 项 | 值 |
|---|---|
| Cookie 中 JWT 字段名（`access_token` / `refresh_token` / 其他） | — |
| LocalStorage 中 JWT 字段名（若有） | — |
| Cookie 的 `Secure` / `HttpOnly` / `SameSite` 标记 | — |
| Cookie 的 `domain` 是 `.velog.io` 还是 `v3.velog.io` | — |
| Cookie `Expires` 字段（用于交叉验证 P0-3 TTL） | — |
| **结论**：持久化文件结构（R9） | ☐ cookie-jar / ☐ playwright `storage_state` 全量 |

---

## 4. P0-3 — token TTL 实测

登录后立即开始计时。每个 checkpoint 用 P0-1 中验证过的 curl + 当前 cookie/storage 文件，发一次小 mutation（标题加 `[ttl-probe T+Nh]`），记录是否成功。

| 时刻 | 绝对时间 | mutation 成功? | 错误码（若失败） | access_token 是否过期 | refresh_token 是否过期 |
|---|---|---|---|---|---|
| T+0（登录后即刻） | 2026-05-18 HH:MM | — | — | — | — |
| T+1h | 2026-05-18 HH:MM | — | — | — | — |
| T+6h | 2026-05-18 HH:MM | — | — | — | — |
| T+24h | 2026-05-19 HH:MM | — | — | — | — |
| T+72h | 2026-05-21 HH:MM | — | — | — | — |

| 衍生结论 | 值 |
|---|---|
| access_token TTL 估计区间 | — |
| refresh_token TTL 估计区间 | — |
| 是否需要主动刷新 endpoint（找到 mutation 名/URL） | ☐ 有 / ☐ 无 |
| 「批跑无人值守」可用窗口 | — h |

**关键判定**：若 access_token TTL < 24h 且无 refresh 路径 → G2 Fail。

---

## 5. P0-4 — 市场相关性决断

| 项 | 答案 |
|---|---|
| velog 受众主语种 | 韩语 |
| 目标 backlink 关键词的韩语相关性（运营评估） | — |
| 候选发文语种：韩语 / 英语 / 双语 | — |
| 若选韩语：是否有翻译 / 母语撰稿能力 | ☐ 有 / ☐ 无（本轮不在 scope） |
| 若选英语：在非母语社区是否仍能产生目标站 GSC referring domain 信号（运营判断 + 一个对照案例） | — |
| **运营决断**（G3） | ☐ 用韩语 / ☐ 用英语 / ☐ 不接入 |

---

## 6. P0-5 — 14 天索引实验

发 5 篇 velog post，每篇含 1 个目标域外链。

| # | URL | 发布日 | 外链目标域 | 语种 | rel_t0 | indexed_t14 | GSC_referring_t14 |
|---|---|---|---|---|---|---|---|
| 1 | https://velog.io/@<user>/<slug-1> | 2026-05-18 | — | — | — | — | — |
| 2 | https://velog.io/@<user>/<slug-2> | 2026-05-18 | — | — | — | — | — |
| 3 | https://velog.io/@<user>/<slug-3> | 2026-05-18 | — | — | — | — | — |
| 4 | https://velog.io/@<user>/<slug-4> | 2026-05-18 | — | — | — | — | — |
| 5 | https://velog.io/@<user>/<slug-5> | 2026-05-18 | — | — | — | — | — |

T+14（2026-06-01）：
- indexed_count = __ / 5（≥ 4 即 G4 Pass）
- referring URL 注册数 = __

---

## 7. P0-6 — Deferred 问题回填（来自 brainstorm Outstanding Questions）

| brainstorm 项 | 影响 R | spike 答案 |
|---|---|---|
| GraphQL 错误返回格式（`errors[].extensions.code` 取值） | R8 | — |
| `DependencyError` vs `ExternalServiceError` 映射规则 | R8 | — |
| HTTP 客户端选择（`requests` vs `httpx`/`gql`） | R8-R11 | — |
| GraphQL `HTTP-200-with-errors[]` 与 `retry.py.is_retryable` 对接 | R8-R11 | — |
| Schema 漂移监测（daily smoke `writePost` canary 是否设） | R8 | — |
| 限频参数（每日上限 / 抖动下限）依据 | R18 | — |

---

## 8. 最终结论

> 全部 checkpoint 完成后填写。

- G1 mutation 成功：☐ Pass / ☐ Fail
- G2 token TTL ≥ 24h：☐ Pass / ☐ Fail
- G3 市场决断：☐ Pass / ☐ Fail
- G4 索引率：☐ Pass / ☐ Fail

**判定**：☐ Pass → `/ce:plan` 进入 Phase 1 / ☐ Fail → 启动 dev.to brainstorm / ☐ pivot → 启动 hashnode brainstorm

**Sign-off**：
- 运营 owner：_______________ 日期：____
- 工程 owner：_______________ 日期：____

---

## 9. Followups

- （若 G2 Fail：`docs/brainstorms/<date>-velog-token-refresh-followup.md` —— 评估是否值得做 refresh 路径）
- （若 G1 Fail：`docs/brainstorms/<date>-velog-graphql-fallback-followup.md`）
- （若 G3 = 不接入 / G4 Fail：`docs/brainstorms/<date>-devto-or-hashnode-pivot.md`）
