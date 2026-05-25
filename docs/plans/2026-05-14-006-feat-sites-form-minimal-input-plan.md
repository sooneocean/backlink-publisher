---
title: /sites 表单极简化 — 只输入 main_url，其余服务端派生
type: feat
status: completed
date: 2026-05-14
completed: 2026-05-14
---

# /sites 表单极简化 — 只输入 main_url，其余服务端派生

## Overview

PR #9 在 `webui.py` 落下的 `/sites` 三 URL 表单当前要求用户填 4 个必填字段：`main_url` + `list_url` + 三个非空 anchor pool（branded/partial/exact），加上 2 个可选字段（work_urls / work_anchor_templates）和 2 个杂项（count / insecure_tls）。**用户使用过的"昨天的 UI"（GET `/` + POST `/ce:plan`）只需要一个 URL 输入**，所有其它配置在后续步骤自动推断或在 dispatch 时由 session 状态承载。

本 plan 把 `/sites` 表单简化到与"昨天的 UI"等价的输入门槛：**只 `main_url` 必填**，其余字段在用户留空时由服务端通过现有的 `fetch_full_tdk` + `fetch_work_urls_from_list` + 域名 label 派生默认值，并在保存后通过回显告诉用户每个字段被设了什么值，以便后续覆盖。

核心约束：**不破** `ThreeUrlConfig` 在 `load_config` 时"三 pool 非空"的契约（src/backlink_publisher/config.py:104-106, 426-441）——所有派生发生在表单 save 之前，写到磁盘的 TOML 段仍然满足现有 schema，磁盘契约 0 改动。

## Problem Frame

`/sites` 是 PR #9 的核心 operator 入口，但**入口摩擦过大** 是用户的实际反馈：

- 表单 4 个必填 + 2 个 textarea，新用户首次配置一个目标域需要查文档 / 拼凑 pool。
- 现有 auto-discovery 通道（sitemap → work_urls）只覆盖了 work_urls 一个字段。
- list_url / 三个 pool 都有现成的派生源（TDK + domain label），但服务端没有用。
- 用户已习惯昨天的 UI 的"贴一个 URL → 走"的工作流，今天的入口变成了一个"先编辑一遍 SEO 配置才能开始"的拦路虎。

任务边界明确：UX 极简，不重新设计 dispatch 流程，不动磁盘 schema，不动现有 `target_three_url` consumer 的语义。

## Requirements Trace

- R1. 用户在 `/sites` 只输入 `main_url`（HTTPS host-root + 单一尾斜杠）并提交，表单保存成功，磁盘 config.toml 出现一条完整的 `[targets."<main_url>"]` 段（含非空 list_url + 非空三 pool + 可选 work_urls）。
- R2. `main_url` 仍是表单上唯一的 HTML `required` 字段；list_url / branded_pool / partial_pool / exact_pool / work_urls 失去 `required`。
- R3. 服务端在用户留空时按下表派生默认值：
  - `list_url` ← `main_url`（host-root 本身就是大多数站点的入口列表页）
  - `work_urls` ← `fetch_work_urls_from_list(list_url, main_url=main_url, max_candidates=10)` 的返回；失败或为空时存空 list（既有契约允许，scraper 会在 dispatch 时再次尝试）
  - `branded_pool` ← `[derived_from_tdk_title or domain_label]`，至少 1 项非空
  - `partial_pool` ← TDK description 切分（句号/逗号/分号），保留前 3 项；失败时退到 `[domain_label]`
  - `exact_pool` ← `[domain_label]`（永远非空）
- R4. 保存成功后跳转到 `/sites?saved=<domain>&autofilled=<csv>`，页面顶部 banner 列出每个被自动填的字段及其值，附"编辑这些值"链接（聚焦该字段的 query string anchor）。
- R5. 用户在表单中部分覆盖默认（例如填了 branded_pool 留空 partial_pool）时，**只对留空字段**派生默认；用户填的值 0 改动。
- R6. 派生发生在 `/sites/save-three-url` 处理流程内，磁盘上的 TOML 段写出后**满足现有 `_parse_target_three_url` 的 schema 校验**（即 `load_config` 读回不丢条目）。
- R7. 现有 PR #9 测试（`tests/test_webui_three_url.py`，所有 save_three_url 测试用例）继续全绿——因为它们都填了完整 6 字段，派生路径不命中。

## Scope Boundaries

- **不在范围**：重新设计 `/sites` 页面布局或视觉风格。本 plan 只动字段 required 性 + help 文案 + 一个 banner。
- **不在范围**：动 `ThreeUrlConfig` dataclass 或 `_parse_target_three_url` 的契约。磁盘 schema 0 改动。
- **不在范围**：在 dispatch 时（POST `/sites/run`）做派生。所有派生发生在 save 时，磁盘上永远是完整 6 字段。这避免了 dispatch 时的 fetch_full_tdk HTTP 延迟。
- **不在范围**：用 LLM 改写派生值。`feedback_no-runtime-llm.md` 是项目硬约束。
- **不在范围**：让 `/sites` 取代昨天的 UI。GET `/` + POST `/ce:plan` 路线继续存在，用户可继续用。本 plan 只让 `/sites` 入口的输入门槛跟昨天的 UI 对齐。
- **不在范围**：派生 work_anchor_templates。已有 DEFAULT_WORK_TEMPLATES 兜底，且这字段表单也已经可选。
- **不在范围**：缓存 fetch_full_tdk 结果。第一次 save 就同步 fetch；如果用户同 main_url 反复 save，每次都重新 fetch（acceptable —— save 不频繁）。

## Context & Research

### Relevant Code and Patterns

- `webui.py:4264 sites_save_three_url` — 当前 save handler，要改的核心位置。请求字段读取 / errors 字典 / `ThreeUrlConfig` 构造 / `save_config(target_three_url=merged)` 调用都在这。
- `webui.py:4018 _SITES_HTML` 模板 — 表单 HTML，需要移除 `required` 属性 + 更新 help 文案 + 加 banner 占位。
- `webui.py:2345 fetch_full_tdk(url)` — 已存在，返回 `{'title': str, 'description': str, 'keywords': str}`，`verify=False`、15s timeout、Mozilla UA。是派生 branded/partial pool 的数据源。
- `webui.py:2302 fetch_url_metadata(url)` — 同源更轻量，只返回 title + description。可作为 TDK 失败时的二级 fallback。
- `src/backlink_publisher/work_scraper.py:311 fetch_work_urls_from_list` — sitemap.xml → sitemap_index.xml → HTML `<a href>` 三级回退，带 host filter + path blocklist + max_candidates。signature 已有 `insecure_tls` 支持。
- `src/backlink_publisher/config.py:104-106 ThreeUrlConfig` — pool 字段类型是 `list[str]`，三个 pool 必须非空（load_config 校验）。
- `src/backlink_publisher/config.py:402, 426-441 _parse_target_three_url` — 磁盘段 → ThreeUrlConfig 的解析路径，必须满足。
- `src/backlink_publisher/url_utils.py validate_main_domain_url / validate_https_url` — 现有 URL 规范化函数，main_url 校验沿用。
- `webui.py:2725 GET /` + `webui.py:2749 POST /ce:plan` — "昨天的 UI" 的实现，single-URL-in 工作流参考。本 plan 不改它，只把 `/sites` 对齐到它的输入门槛。

### Institutional Learnings

- `feedback_standalone-page-vs-retrofit.md` — webui.py 现在 4510 行，本 plan 严格在现有 `/sites` 路由内修改，不新开 sibling page。这是局部 retrofit（修改 4 个相邻函数），不是全表单重做。
- `feedback_no-runtime-llm.md` — 派生策略必须 LLM-free。本 plan 的派生纯字符串处理 + 一次 TDK HTTP fetch。
- `feedback_test-autouse-verify-mock.md` — 派生路径会触发 fetch_full_tdk 的 HTTP 调用，新测试需要 mock 该函数避免触发 pytest-socket 防线。
- `feedback_jinja2-banner-text-collision.md` — banner 文案如果与 JS 常量重叠，测试用 run_id / 唯一字符串识别。本 plan 的 autofilled banner 用 query string `autofilled=` 而不是 session flash，避免 banner-blindness。
- `feedback_brainstorm-prompt-as-desired-state.md` — 用户的"只填 main_url"是 desired state；本 plan 验证它在现有 schema 下可达（确认：可达，靠服务端派生）。

### External References

无。所有派生策略都是字符串处理 + 现有 HTTP fetch 调用，无第三方依赖。

## Key Technical Decisions

- **派生在 save 时同步发生，不延迟到 dispatch**。理由：用户保存后看到的磁盘状态是"完整的"，dispatch 行为可预测、可调试；分两阶段会引入"半填"状态。代价：save 操作多一个 15s timeout 的 HTTP 调用（fetch_full_tdk），acceptable for save frequency。
- **派生失败也得有非空 pool**。理由：`load_config` 见到空 pool 直接 skip 整条 target，破坏 R1。退化路径：TDK fetch 失败 → `domain_label` 单元素列表（永远非空，永远派生得出）。
- **autofilled 状态用 query string 而非 session**。理由：用户可分享/书签该 URL，状态可重现；session-based banner 在用户刷新页面后丢失提示。query string 同时让 e2e 测试可断言。
- **不引入新的 helper module**。理由：派生函数仅 4 个小函数（每个 10–30 行），全部进 `webui.py` 与现有 `_parse_lines` / `_check_csrf_or_abort` helpers 同层。引入新模块只为 4 个内部函数是过度抽象。
- **不缓存 fetch_full_tdk**。理由：save 操作低频；缓存会增加状态管理复杂度（失效策略 / 跨 process）；webui 单进程内可后续加 lru_cache 但不是 P0。
- **domain_label 派生算法**：`urlparse(main_url).netloc` → 去掉 `www.` 前缀 → 取第一个非 TLD 的标签（用 `.` split 后第 0 个）。`https://www.51acgs.com/` → `51acgs`。如果第 0 段是 `www`，取第 1 段。这跟现有 `get_main_domain` 不冲突（后者保留 host 完整体）。

## Open Questions

### Resolved During Planning

- **list_url 默认值是不是 `main_url` 而不是 `main_url + "list"` 之类？** 是。大多数站点的 host-root 本身就是入口列表；scraper 的 sitemap.xml 路径解析也从 host-root 推导。如果用户站点的 list 在某个子路径，他们会手动填。
- **要不要为 partial_pool 派生从 description n-gram？** 不需要。description 切句即可，过度抽取会产生噪音 anchor。
- **要不要支持"用户只填 main_url 但点击预览"按钮？** 不需要。本 plan 保留现有 `/sites/scrape-preview` JSON 端点；用户可手动用，但 save 流程不强制预览。
- **fetch_full_tdk 抛错怎么办？** save handler 内 try/except 捕获，记录 PipelineLogger.warn("tdk_fetch_failed", url=main_url, reason=...)，派生路径全部退到 domain_label 兜底。不让 TDK 失败阻塞 save。

### Deferred to Implementation

- **partial_pool 切句的分隔符精确边界**（句号 / 中文句号 / 分号 / 逗号 / 「、」）— 实现时按实际 TDK 样本调；初步用 `re.split(r"[。.；;，,、]+", description)`。
- **derived 值的长度截断**（branded ≤ 30 字符？）— 防御长 title 污染 anchor pool；实现时按现有 `_clean_pool` 的字符约束对齐。
- **autofilled banner 多语言** — 当前 UI 中文为主，仅中文版即可；未来 i18n 时统一处理。
- **派生路径的可观测性**：是否需要 `PipelineLogger.recon` 一条 "sites_save_autofilled" 事件，还是只在 banner 显示？倾向 recon 一条，operator 永远可见，跟 `feedback_recon-level-for-always-on-signals.md` 对齐。

## High-Level Technical Design

> *本节用 ASCII 流程图展示派生路径与表单交互的形状。directional guidance for review；不是实现规范。*

```
POST /sites/save-three-url
       │
       ▼
   CSRF check ──fail──► 403 (unchanged)
       │
       ▼
   read form fields
       │
       ▼
   main_url validate ──fail──► 422 re-render (unchanged path)
       │
       ▼                          ┌──────── tdk = None ──────────────┐
   any pool / list_url empty?     │  fetch_full_tdk(main_url)        │
       │                          │  on exception: log + tdk = None  │
       ├─── yes ────────►─────────┤                                  │
       │                          └──────────────────────────────────┘
       │                                       │
       │                                       ▼
       │              fields_derived = {} (track for banner)
       │              if list_url empty: list_url = main_url ; mark "list_url"
       │              if branded_pool empty:
       │                  branded_pool = derive_branded(main_url, tdk)
       │                  mark "branded_pool"
       │              if partial_pool empty:
       │                  partial_pool = derive_partial(main_url, tdk)
       │                  mark "partial_pool"
       │              if exact_pool empty:
       │                  exact_pool = [domain_label(main_url)]
       │                  mark "exact_pool"
       │              if work_urls empty:
       │                  work_urls = fetch_work_urls_from_list(...)  # already-allowed empty
       │                  mark "work_urls" (only if returned non-empty)
       │                                       │
       ├──── no ─────────────────────────────► │ (派生跳过)
       │                                       ▼
       ▼                              ThreeUrlConfig(...)  ← merged
   既有 save_config(target_three_url=merged)
       │
       ▼
   redirect /sites?saved=<domain>&autofilled=<csv of marked fields>
       │
       ▼
   GET /sites renders banner from query string
```

派生函数群（webui.py 内私有）：

```python
def _domain_label(main_url: str) -> str:
    """https://www.51acgs.com/ → '51acgs'"""

def _derive_branded(main_url: str, tdk: dict | None) -> list[str]:
    """tdk.title (trimmed, ≤30 chars) → 1 entry; else domain_label."""

def _derive_partial(main_url: str, tdk: dict | None) -> list[str]:
    """re.split tdk.description on punctuation, keep first 3 non-empty
    trimmed phrases; else [domain_label]."""

def _derive_exact(main_url: str) -> list[str]:
    """[domain_label] — always non-empty."""
```

## Implementation Units

- [ ] **Unit 1: Default-derivation helpers in webui.py**

**Goal:** 4 个私有派生函数 + 1 个 domain_label helper，都放在 `webui.py` 现有 helper 块（接近 `_parse_lines` / `_check_csrf_or_abort` 一带），不引入新模块。

**Requirements:** R3, R6

**Dependencies:** 无。

**Files:**
- Modify: `webui.py`（新增 ~50 LOC helpers）
- Test: `tests/test_webui_three_url.py`（新增 unit-level tests）

**Approach:**
- `_domain_label(main_url)`：urlparse 提取 netloc → strip `www.` → split `.` 取第 0 段。对 IDN 域 idna decode 一次（既有 `validate_main_domain_url` 已做规范化，此处只需 string split）。
- `_derive_branded(main_url, tdk)`：tdk and tdk.get("title") → strip + 截断到 30 字符 → list[str]；否则 `[_domain_label(main_url)]`。永远返回非空。
- `_derive_partial(main_url, tdk)`：tdk and tdk.get("description") → `re.split(r"[。.；;，,、]+", desc)` → strip + 过滤空串 + 截断单项到 60 字符 → 取前 3 项；空则 `[_domain_label(main_url)]`。永远返回非空。
- `_derive_exact(main_url)`：`[_domain_label(main_url)]`。永远返回非空。
- 所有派生纯字符串处理，无 HTTP/IO。

**Patterns to follow:**
- 现有 `_parse_lines` 的 strip + 过滤空串模式。
- 现有 `validate_main_domain_url` 的 urlparse 模式。

**Test scenarios:**
- Happy path - `_domain_label("https://51acgs.com/")` → `"51acgs"`。
- Happy path - `_domain_label("https://www.51acgs.com/")` → `"51acgs"`（去 www）。
- Edge case - `_domain_label("https://a.b.c.com/")` → `"a"`（取第 0 段）。
- Happy path - `_derive_branded("https://x.com/", {"title": "X 漫画首页", "description": ""})` → `["X 漫画首页"]`。
- Edge case - `_derive_branded("https://x.com/", None)` → `["x"]`（TDK 缺失退到 domain_label）。
- Edge case - `_derive_branded("https://x.com/", {"title": "A" * 50})` → `["AAAA…(30 chars)"]`（截断）。
- Happy path - `_derive_partial("https://x.com/", {"description": "免费阅读漫画。最新更新, 海量资源；ACG爱好者社区"})` → 3 项切片（"免费阅读漫画", "最新更新", "海量资源" 或近似）。
- Edge case - `_derive_partial("https://x.com/", {"description": ""})` → `[domain_label]`。
- Edge case - `_derive_partial("https://x.com/", None)` → `[domain_label]`。
- Happy path - `_derive_exact("https://51acgs.com/")` → `["51acgs"]`。
- 结构保证 - 每个 _derive_* 返回的 list 都非空（所有 None / 空 TDK 输入均触发兜底）。

**Verification:**
- `pytest tests/test_webui_three_url.py::test_derive_helpers -q` 通过。
- mypy 不引入新错误（如果项目跑 mypy）。

---

- [ ] **Unit 2: 表单 HTML 简化 — 移除 required，更新 help 文案，留 banner 占位**

**Goal:** `/sites` 表单 5 个非 main_url 字段失去 `required` 属性；help 文案明确"留空即由系统派生"；模板顶部留一个 `{% if autofilled %}` banner 块。

**Requirements:** R2, R4

**Dependencies:** 无。

**Files:**
- Modify: `webui.py`（`_SITES_HTML` 模板字符串）
- Test: `tests/test_webui_three_url.py` 新增 form-render 断言。

**Approach:**
- 找到 `name="list_url"` / `name="branded_pool"` / `name="partial_pool"` / `name="exact_pool"` 四个 `<input>` / `<textarea>`，删除 `required` 属性。
- 更新 help 文案为"留空：系统将基于 main_url 的站点元数据（title/description）+ 域名 label 自动派生"。
- 在 `<form>` 之前插入一个 `{% if autofilled %}` jinja2 块，渲染 "已自动填的字段：<list>"，每项给出值。autofilled 值从 GET query string 解析，传入 render 上下文。
- 仍保留所有现有的 error 处理路径（422 re-render）。

**Patterns to follow:**
- 现有 `{% if errors.main_url %}` jinja2 模式。
- 现有 `saved=<domain>` query string 解析模式。

**Test scenarios:**
- Happy path - GET `/sites` 渲染：HTML 中只有 main_url 那个 `<input>` 带 `required` 属性，其它 4 个不带。
- Happy path - GET `/sites?saved=x.com&autofilled=branded_pool,list_url` 渲染 banner 文本含 "branded_pool"、"list_url"。
- Edge case - GET `/sites?saved=x.com&autofilled=`（空字符串）不渲染 banner。
- Edge case - GET `/sites`（无 autofilled query）不渲染 banner。

**Verification:**
- 浏览器实测 http://127.0.0.1:8888/sites，5 字段无 `*` 标识或浏览器 native required 提示。
- `pytest tests/test_webui_three_url.py::TestSitesFormRender -q` 全绿。

---

- [ ] **Unit 3: save handler 服务端派生 + autofilled query string**

**Goal:** `/sites/save-three-url` 在 main_url 通过校验后，对每个空字段触发派生；构造 autofilled csv；写盘后 redirect 带 query string。

**Requirements:** R1, R3, R5, R6

**Dependencies:** Unit 1 (派生 helpers 必须先存在)。

**Files:**
- Modify: `webui.py`（`sites_save_three_url` 函数体）
- Test: `tests/test_webui_three_url.py`（`TestSaveThreeUrl` 类新增多个 case）

**Approach:**
- main_url 校验失败仍走 422 路径，签名不变。
- 校验通过后：
  - 一次性调用 `fetch_full_tdk(main_url)`，try/except 兜底，失败时 `tdk = None` 并 `PipelineLogger.warn("tdk_fetch_failed", url=main_url, reason=type(exc).__name__)`。
  - `fields_derived: list[str] = []`，按 list_url / branded / partial / exact / work_urls 顺序：每个字段先用现有 parse 结果，空时调对应 _derive_* 并 append `"<field_name>"` 到 fields_derived。
  - work_urls 留空时调 `fetch_work_urls_from_list`，try/except 兜底；只有返回非空列表时 mark "work_urls"。失败 / 空 list 不 mark（用户磁盘 work_urls 仍是空，dispatch 时 scraper 再试）。
  - 派生 work_urls 时仍用本字段的 `insecure_tls` flag。
  - `PipelineLogger.recon("sites_save_autofilled", main_url=main_url, fields=fields_derived)` —— 总是输出，operator 永远可见。
  - 构造 ThreeUrlConfig + save_config 调用 0 改动（pool 已经派生为非空 list）。
  - redirect 改成 `/sites?saved=<domain>&autofilled=<urlencoded csv of fields_derived>`。
- 用户填了 partial 值（例如手填 branded_pool 但留空 partial_pool）的情况下，派生**只对空字段**触发；用户填的 branded_pool 0 改动。

**Patterns to follow:**
- 现有 `errors: dict[str, str] = {}` 模式（用 `fields_derived: list[str] = []` 镜像）。
- 现有 `_check_csrf_or_abort()` + `_parse_lines` 顺序不变。
- `feedback_recon-level-for-always-on-signals.md` 的 recon 使用。

**Test scenarios:**
- Happy path - POST 只填 main_url + CSRF token：响应 302，Location 含 `?saved=` 和 `?autofilled=list_url,branded_pool,partial_pool,exact_pool`（顺序 stable）；磁盘 config.toml 新增 `[targets."<main>"]` 段含 3 个非空 pool + list_url == main_url + work_urls = []。
- Happy path - 部分填：POST 含 main_url + branded_pool（有内容）+ 其它空：autofilled csv 不含 "branded_pool"；磁盘 branded_pool == 用户输入。
- Happy path - 完整填（现有 PR #9 test 场景）：autofilled csv 为空；磁盘内容 0 变化；redirect 不带 autofilled 或带 `autofilled=`（empty）。
- Edge case - TDK fetch raise（mocked 抛 RequestException）：派生仍成功，所有空 pool 退到 `[domain_label]`；recon 日志含 reason；磁盘所有 pool 等于 `[domain_label]`；HTTP 302 正常。
- Edge case - fetch_work_urls_from_list raise：work_urls 派生跳过（磁盘空 list），其它字段派生不受影响；recon 日志含 work_urls_fetch_failed。
- Edge case - main_url 校验失败：行为同 PR #9（422 + 表单 re-render），派生路径不进入。
- Integration - 派生写盘 + load_config 读回：load_config 读到的 `target_three_url[domain]` 含 3 个非空 pool，pass `_parse_target_three_url` 的 skip 检查（R6）。
- Regression - 现有 `TestSaveThreeUrl` 已有 7 个 case 全绿。

**Verification:**
- Integration test 写盘 + 读回，断言所有 pool 非空（R6 magic check）。
- Manual smoke：浏览器在 http://127.0.0.1:8888/sites 只填 `https://51acgs.com/` + submit，redirect 后 banner 列出 4 项 autofilled；`config.toml` 含完整 `[targets."https://51acgs.com"]` 段。
- recon 日志含 `sites_save_autofilled` 一条。

---

- [ ] **Unit 4: 集成测试 + 手工 smoke 一条**

**Goal:** 一条端到端测试覆盖"只填 main_url → 派生 → 写盘 → load_config 读回 → 满足 _parse_target_three_url"链路；一条手工 smoke 文档。

**Requirements:** R1, R6

**Dependencies:** Unit 3 (派生在 save handler 内必须先 wire)。

**Files:**
- Modify: `tests/test_webui_three_url.py`（新增 `TestSaveThreeUrlMinimalInput` class）

**Approach:**
- 用现有 `tests/conftest.py` 的 autouse mocks 保证无网络。
- 测试新增 fixture：mock `webui.fetch_full_tdk` 返回 `{"title": "Test Site", "description": "免费内容。海量资源；专业社区", "keywords": ""}` 和返回 raise 两种。
- mock `backlink_publisher.work_scraper.fetch_work_urls_from_list` 返回 `["https://x.com/a", "https://x.com/b"]` 和返回 raise 两种。
- 测试断言序列：
  - HTTP 302 + Location 含 `autofilled=` 正确字段集。
  - tmp config.toml 磁盘内容含期望的 6 字段段。
  - `load_config(tmp_path)` 读回的 `target_three_url[main_url.rstrip("/")]` 是 valid `ThreeUrlConfig`（非 None，3 pool 非空）。
- 不在此 unit 内新增 helpers——所有 helpers 已经在 Unit 1。

**Patterns to follow:**
- 现有 `TestSaveThreeUrl::test_happy_path_writes_config_and_redirects_with_saved_query`。
- 现有 `tests/conftest.py` 的 autouse pattern (`feedback_test-autouse-verify-mock.md`)。

**Test scenarios:**
- Integration - 端到端只填 main_url：磁盘段满足 `_parse_target_three_url`，load_config 读回成功，3 pool 非空，list_url == main_url，work_urls == fetch_work_urls_from_list 的 mock 返回。
- Integration - TDK + work_scraper 都 raise：磁盘段仍满足 schema，3 pool 均为 `[domain_label]`，work_urls 为 []。
- Integration - 用户填 branded_pool=["MyBrand"]、其它空 + TDK 成功：磁盘 branded_pool == ["MyBrand"]，partial_pool 来自 TDK 切句，exact_pool == [domain_label]，list_url == main_url。

**Verification:**
- `pytest tests/test_webui_three_url.py -q` 全绿（含新增 3 个 case + 现有 23 个 case → 26 个）。
- 手工 smoke：在浏览器 http://127.0.0.1:8888/sites 提交一个真实 host_root URL（如 `https://example.com/`），观察 redirect URL、banner、`~/.config/backlink-publisher/config.toml` 段内容三者一致。

## System-Wide Impact

- **交互图**：影响 `/sites` GET（模板 + banner）+ `/sites/save-three-url` POST（派生逻辑）。**不**影响 `/sites/scrape-preview`、`/sites/run`、`/sites/run/<id>/result`。**不**影响昨天的 UI（GET `/` + `/ce:*`）。
- **错误传播**：派生路径 try/except 兜底，永远不 raise 到 save handler 之外。fetch_full_tdk 失败 / fetch_work_urls_from_list 失败均 degrade 到 domain_label / empty list，不阻塞 save。CSRF + main_url 校验失败路径完全 0 改动。
- **State lifecycle**：磁盘 `[targets."<domain>"]` 段格式 0 变化；既有运行中 cron / 既有 `target_three_url` 读取 0 改动。session / cache / checkpoint 0 改动。
- **API surface**：表单 form-data 字段集 0 变化（仍接受全部 6 个字段）。query string 增加可选 `autofilled` 参数（向后兼容：旧链接无此参数走"不渲染 banner"分支）。
- **Integration coverage**：Unit 4 的端到端测试覆盖派生 → 写盘 → 读回 invariant。
- **Unchanged invariants**：磁盘 schema、`ThreeUrlConfig` 契约、CSRF 流程、`_check_csrf_or_abort`、`save_config` 调用签名、`load_config` 校验。

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| fetch_full_tdk 在 save 时引入 15s 同步阻塞；用户长期等待。 | Unit 3 用 `feedback_recon-level-for-always-on-signals.md` 的 recon 一条日志让 operator 知道 save 卡在 TDK fetch；可视性优先。后续可考虑 timeout 缩到 5s 或 lazy 派生。 |
| TDK 派生出无意义/低质 anchor pool（如英文站点 title 含特殊符号），污染 anchor distribution。 | Unit 1 的 _derive_* 都做 strip + 长度截断 + punctuation split；用户可在 redirect 后通过 banner 看到值，直接编辑表单覆盖。 |
| work_scraper fetch_work_urls_from_list 在 save 时网络慢，叠加 TDK 15s 后总 save 时间 > 30s。 | save handler 内 work_urls 派生用更紧 timeout（如 fetch_work_urls_from_list 已支持 timeout=15 参数，可压到 8s）。Unit 3 的 recon 日志记录每段耗时。 |
| `_domain_label` 对边缘域名格式（IDN、单段、port）出错。 | Unit 1 测试覆盖 IDN（mock `idna.decode` 路径）+ www. 前缀 + 多段子域。生产 main_url 已经过 `validate_main_domain_url` 校验，IDN/port 已被规范化为已知形状。 |
| 用户 expectation drift：原表单的 `required` 是 UX cue，去掉后用户可能反复 save 同一 main_url 看派生是否变。 | Unit 2 的 banner 文案明确告知"留空 = 系统派生"；Unit 3 的 recon 日志记录每次 save 的 autofilled fields。 |
| autofilled query string 长度上限（URL truncation）。 | autofilled 永远是有限字段名集合（list_url, branded_pool, partial_pool, exact_pool, work_urls），csv 最多 ~60 字符；URL 总长安全。 |
| 现有 PR #9 测试中 `branded_pool/partial_pool/exact_pool` 必填的 error 断言。 | 直接 grep 测试文件确认现有断言要么是"非空字段"而非"required HTML 属性 errors"。Unit 2 验证现有 23 case 全绿。 |

## Documentation / Operational Notes

- **CHANGELOG (downstream)**：BREAKING 否，additive 是。可写："/sites 表单 main_url 之外字段改为可选。留空时服务端基于 site TDK + 域名 label 自动派生，磁盘 schema 不变。"
- **手册更新**（如有）：`docs/` 下任何引用 `/sites` 表单填法的文档需要补充"留空即自动派生"提示。本 plan 不主动找/改文档，看 ship 时是否有人 grep 出来。
- **Operator-facing**：第一次升级后，operator 用同样的 URL 复 save 即触发派生写入新字段；既有 `[targets."<domain>"]` 段被 `save_config` 完整覆盖（用 in-memory state），不需要 migration。

## Sources & References

- Origin user feedback: 2026-05-14 session 内用户口头"现在这个太多要填写的 能否用我昨天的UI来进行规划 只要输入核心的 main url 其他都不是必填的"。
- 上游 PR：#9 (`feat: work-themed backlinks`) 引入 `/sites` 表单。
- Related code: `webui.py:2725 /` + `webui.py:2749 /ce:plan`（昨天的 UI 参照）；`webui.py:4264 sites_save_three_url`；`src/backlink_publisher/work_scraper.py:311 fetch_work_urls_from_list`；`src/backlink_publisher/config.py:104-106 ThreeUrlConfig` + `:402, 426-441 _parse_target_three_url`。
- Memory: `feedback_standalone-page-vs-retrofit.md`, `feedback_no-runtime-llm.md`, `feedback_test-autouse-verify-mock.md`, `feedback_jinja2-banner-text-collision.md`, `feedback_recon-level-for-always-on-signals.md`, `feedback_brainstorm-prompt-as-desired-state.md`.
- 关联 plan：`docs/plans/2026-05-13-004-feat-work-themed-backlinks-plan.md` (PR #9 原始设计文档).
