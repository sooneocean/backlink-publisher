---
title: "feat: SEO-friendly anchor text from config keyword pool + target=\"_blank\" on all rendered links"
type: feat
status: active
date: 2026-05-12
origin: backlink-publisher/docs/brainstorms/2026-05-12-anchor-text-and-blank-target-requirements.md
---

# feat: 外链锚文本 SEO 化与新窗打开

## Overview

把生成文章里指向 target 站的反链锚文本从裸域名换成**有 SEO 意义的关键词**（来自每个 target 站 config 中维护的 `anchor_keywords` 池，按 `url_mode + 位置序号`确定性选取），并在 HTML 渲染阶段为**所有** `<a>` 标签注入 `target="_blank" rel="noopener"`。

## Problem Frame

当前所有指向 target 站的反链统一使用裸域名（如 `xhssex.com`）作为锚文本：
- SEO 价值低 —— 搜索引擎拿不到关键词信号
- 渲染后的 `<a>` 也没有 `target="_blank"`，用户点击直接离开宿主页

来源需求见 `backlink-publisher/docs/brainstorms/2026-05-12-anchor-text-and-blank-target-requirements.md`。

## Requirements Trace

- R1. 每个 target 站在 config 中维护 `anchor_keywords` 列表（人工填写）
- R2. 所有原本输出 `[{domain}]({main_domain})` 的位置改为 `[<keyword>]({main_domain})`
- R3. 选取策略为 `keywords[(position_index + url_mode_offset) % len(keywords)]`，其中 `url_mode_offset = {A:0, B:1, C:2}`（见 origin 决策）
- R4. `anchor_keywords` 缺失/空时回退到当前裸域名行为，并在生成日志 WARN 一次（不阻断）
- R5. 渲染后 HTML 所有 `<a>` 含 `target="_blank" rel="noopener"`
- R6. 不引入 `nofollow`，保持现有"反链必须 dofollow"约定

## Scope Boundaries

- 不做锚文本 AI 生成/自动抓取
- 不为 supporting/category/detail/extra 链接配置关键词池（这些链接的锚文本由模板自然语言提供，不涉及 target 站权重）
- 不修改适配器 publish 逻辑
- 不修改 `verifier.py`（已确认只比对 URL 集合，不校验锚文本字符串）

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/markdown_utils.py:11` — `render_to_html`，使用 `markdown-it-py` 默认 renderer（待定制 `link_open` 规则）
- `src/backlink_publisher/markdown_utils.py:33` — `format_link_md`（生成 `[anchor](url)`，无需改动）
- `src/backlink_publisher/markdown_utils.py:77-173` — `_en_body_a/b/c`、`_zh_body_*`、`_ru_body_*`，每个函数 body 内有 2 处 `[{domain}]({main_domain})`
- `src/backlink_publisher/cli/plan_backlinks.py:127-143` — `_build_links` 中 `main_domain` 和 `target` kind 的 anchor 字段构造
- `src/backlink_publisher/cli/plan_backlinks.py:45-115` — 各语言 title/body/seo_desc/tags 模板（仅 body 模板内的 `[{domain}]({main_domain})` 需替换；title/tags 中的 `{domain}` 是标题用语，不改）
- `src/backlink_publisher/config.py:53` — `Config` dataclass + `load_config`（待新增 `target_anchor_keywords` 字段与解析）
- `src/backlink_publisher/config.py:217` — `save_config` 序列化（需要支持往 toml 写回 `[targets]` 段，**或**显式不支持，并在文档说明手工编辑）
- 所有 4 个适配器调用 `render_to_html`：`adapters/blogger_api.py:128`、`adapters/medium_api.py:111`、`adapters/medium_browser.py:70`、`adapters/medium_brave.py:324` —— 改一次全覆盖

### Institutional Learnings

- 既有 brainstorm/plan 历史（`docs/brainstorms/`、`docs/plans/`）显示项目偏好"小而专"的实现单元，配独立 test 文件
- 现有 `tests/test_markdown_render.py` 已有 `test_link_no_nofollow`，新增 `target="_blank"` 断言应直接加在该文件

### External References

- `markdown-it-py` 自定义 renderer 规则：`md.renderer.rules['link_open'] = lambda tokens, idx, options, env: ...`。标准能力，文档：<https://markdown-it-py.readthedocs.io/en/latest/using.html#renderer-rules>。无新依赖。

## Key Technical Decisions

- **Config schema：新增 `[targets]` 顶层 section，键为 main_domain，值为 dict 含 `anchor_keywords`**：与现有 `[blogger]` 段（`domain → blog_id`）形态一致，扩展性好。例：
  ```toml
  [targets."https://xhssex.com"]
  anchor_keywords = ["小黄书", "小黄书漫画", "成人漫画在线阅读", "免费漫画"]
  ```
- **`anchor_keywords` 在 `Config` 中存为 `dict[str, list[str]]`**：和 `blogger_blog_ids` 一致的 normalize 规则（strip 尾斜杠）。
- **选取策略放在新模块 `markdown_utils.py` 内**而非 `plan_backlinks.py`：被 body 模板函数与 `_build_links` 共用，避免跨模块循环依赖。
- **Body 模板函数签名重构**：从 `(domain, main_domain)` 改为 `(anchors: list[str], main_domain: str)` —— `anchors` 已是 2 元素的预选关键词列表；模板内用 `{anchors[0]}` 和 `{anchors[1]}` 替换原 `{domain}`。`{domain}` 在 title/excerpt 等非反链位置仍保留（不锚定 target 站权重的地方不需要 SEO 优化）。
- **HTML 渲染层定制 `link_open`**：在 `render_to_html` 内重写规则给所有 `<a>` 加 `target="_blank" rel="noopener"`。**不区分**内外链 —— origin 决策为"所有 `<a>` 外链"，模板中除 target 站外只有 supporting URL（也是外链），不存在页内锚点。
- **回退路径触发位置**：在 `plan_backlinks` 调用选取函数处判断 keywords 为空，落到 `domain_label`；并通过 `logging.warning(...)` 一次性输出 `"anchor_keywords missing for <main_domain>, falling back to bare domain"`。WARN 在 article 级别只输出一次（用 `logged_warnings: set[str]` 短路）。
- **`save_config` 暂不支持写回 `[targets]` 段**：当前 `save_config` 只服务 OAuth/token 持久化，没人调用它写 targets。改动越少越好，文档说明 anchor_keywords 需用户手工编辑 config.toml。

## Open Questions

### Resolved During Planning

- **Q: keyword pool 长度上限？** A：不在代码侧硬约束。`config.example.toml` 示例给出 5 个，README 建议 5–10 个。空列表等同于缺失 → 走 R4 回退。
- **Q: Medium HTML 属性剥离 (`target="_blank"` 是否会被 Medium 后端吃掉)？** A：接受 best-effort。Medium 输出 HTML 由 Medium 自有处理；本插件只保证生成端正确。如线上验证发现 Medium 剥离，后续可在 `MediumAPIAdapter` 文档补一条已知限制；不在本 plan 范围。
- **Q: 选取公式定稿。** A：`keywords[(position_index + url_mode_offset) % len(keywords)]`。`position_index` 从 1 起算（第 1 个反链 → index=0 等价表达：`(position-1+offset) % n`）。`url_mode_offset = {"A":0,"B":1,"C":2}`。

### Deferred to Implementation

- 具体的 logging 通道（项目用 stdlib `logging` 还是项目自有 logger）—— 让实现者依现有 `cli/plan_backlinks.py` 已有的日志风格定夺
- `Config` dataclass 中 `target_anchor_keywords` 是否需要 normalize 大小写、Unicode（NFKC 等）—— 先按"原样存储 + 按 `rstrip('/')` 归一化 key"实现；如出现真实歧义再加

## High-Level Technical Design

> *以下是方向性示意，非可粘贴的实现代码。实现者请按现有代码风格落地。*

数据流：

```text
config.toml
  └─ [targets."<main_domain>"] anchor_keywords = ["kw1","kw2",...]
       ↓ load_config()
Config.target_anchor_keywords: dict[main_domain → list[str]]
       ↓ plan_backlinks() 收到 Config
       ↓ 对该篇文章预选 2 个 anchor (按 url_mode + position 1/2)
       ↓ 把 anchors 传给：
          (a) body 模板函数  →  在 markdown 中插入 [kw](main_domain)
          (b) _build_links()  →  写入 links[].anchor 字段（main_domain / target kind）
       ↓ content_markdown 落到 payload
       ↓ adapter.publish() → render_to_html(content_markdown)
                                ↓ 定制的 link_open 规则
                                ↓ 每个 <a> 注入 target="_blank" rel="noopener"
```

选取函数伪代码（**仅作方向示意**）：

```python
def select_anchor_keywords(
    keywords: list[str],
    url_mode: str,
    count: int,
) -> list[str] | None:
    """Return `count` deterministically selected anchor keywords, or None if pool is empty."""
    if not keywords:
        return None
    offset = {"A": 0, "B": 1, "C": 2}.get(url_mode, 0)
    return [keywords[(i + offset) % len(keywords)] for i in range(count)]
```

`link_open` renderer 定制（**仅作方向示意**）：

```python
def _link_open(self, tokens, idx, options, env):
    tokens[idx].attrSet("target", "_blank")
    tokens[idx].attrSet("rel", "noopener")
    return self.renderToken(tokens, idx, options)
```

## Implementation Units

- [ ] **Unit 1: 扩展 Config schema 与 loader 支持 `[targets]` 段**

**Goal:** 让 `load_config` 解析 `[targets."<main_domain>"].anchor_keywords` 并暴露为 `Config.target_anchor_keywords`。

**Requirements:** R1

**Dependencies:** None

**Files:**
- Modify: `src/backlink_publisher/config.py`
- Modify: `config.example.toml`
- Test: `tests/test_config.py`

**Approach:**
- 在 `Config` dataclass 新增 `target_anchor_keywords: dict[str, list[str]] = field(default_factory=dict)`
- 在 `load_config` 中读取 `data.get("targets", {})`，对每个 entry 取出 `anchor_keywords`（必须是 `list[str]`，否则忽略并 WARN）；key 用 `rstrip("/")` 归一化
- `config.example.toml` 末尾追加注释示例段（含品牌词 + 行业词 + 长尾词混合的样例）
- `save_config` 不写 `[targets]` 段（决策见 Key Technical Decisions）。在 docstring 注明 anchor_keywords 需手工编辑

**Patterns to follow:**
- `config.py:131` 中 `blog_ids` 的 `dict[str, str]` 解析与归一化模式

**Test scenarios:**
- Happy path：toml 文件含 `[targets."https://example.com"]\nanchor_keywords = ["foo","bar"]` → `Config.target_anchor_keywords == {"https://example.com": ["foo", "bar"]}`
- Happy path：多个 target 共存，各自解析独立
- Edge case：trailing slash 归一化 —— `[targets."https://example.com/"]` 与 `"https://example.com"` lookup 都命中
- Edge case：缺省 `[targets]` 段 → `target_anchor_keywords == {}`，不报错
- Edge case：`anchor_keywords` 缺失或为空列表 → 该 entry 跳过（key 不出现在 dict 中或值为 `[]`，按"原样保留 + 调用方判空"）
- Error path：`anchor_keywords` 类型非 list（如 string）→ 忽略该 entry，logging.warning 但 `load_config` 不抛错（沿用现有"宽容解析"风格）

**Verification:**
- `pytest tests/test_config.py` 全绿
- `Config.target_anchor_keywords` 字段可被 `plan_backlinks` 后续单元直接读取

- [ ] **Unit 2: 选取函数 `select_anchor_keywords` + 渲染层 `target="_blank"` 定制**

**Goal:** 提供确定性选取函数；定制 markdown-it-py 的 `link_open` 规则给所有 `<a>` 加 `target="_blank" rel="noopener"`。

**Requirements:** R3, R5, R6

**Dependencies:** None（独立于 Unit 1，可并行实现）

**Files:**
- Modify: `src/backlink_publisher/markdown_utils.py`
- Test: `tests/test_markdown_render.py`（新增渲染相关断言）
- Test: `tests/test_markdown_render.py` 同文件新增 `select_anchor_keywords` 的测试（或拆 `tests/test_anchor_selection.py`，二选一由实现者按现有 test 模块粒度定夺）

**Approach:**
- 新增函数 `select_anchor_keywords(keywords, url_mode, count) -> list[str] | None`，公式见 High-Level Technical Design
- 修改 `render_to_html`：在 `MarkdownIt(...)` 实例上重写 `md.renderer.rules['link_open']`，给 token attr 写入 `target="_blank"` 和 `rel="noopener"`；保持现有"无 nofollow"约定不变
- 不修改 `format_link_md`（markdown 层语法限制，无法在 `[text](url)` 表达 attr；attr 注入只能在渲染层完成）

**Patterns to follow:**
- markdown-it-py renderer rule 习惯写法：参考其官方文档；模式简单，无 repo 内既有先例

**Test scenarios:**
- **select_anchor_keywords**
  - Happy path：`keywords=["a","b","c"], url_mode="A", count=2` → `["a","b"]`
  - Happy path：`url_mode="B", count=2` → `["b","c"]`（offset=1）
  - Happy path：`url_mode="C", count=2` → `["c","a"]`（offset=2 + 环绕）
  - Edge case：`count > len(keywords)` → 环绕回到起始，无重复抛错
  - Edge case：`keywords=[]` → `None`（触发 R4 回退路径的信号）
  - Edge case：`url_mode` 非 A/B/C → 当作 offset 0（不抛错）
  - Edge case：`count=0` → `[]`
- **render_to_html 新行为**
  - Happy path：`render_to_html("[anchor](https://example.com)")` 含 `target="_blank"` 与 `rel="noopener"`
  - Happy path：多个链接的 markdown → 每个 `<a>` 都带 `target="_blank" rel="noopener"`
  - Integration：`test_link_no_nofollow` 仍通过（不引入 nofollow）
  - Integration：`test_backlink_survives_rendering` 仍通过（href 不丢失）
  - Edge case：空 markdown 不报错

**Verification:**
- `pytest tests/test_markdown_render.py` 全绿
- `select_anchor_keywords` 确定性可复现（同入参输出严格相等）

- [ ] **Unit 3: Body 模板重构 + plan_backlinks 串联 anchor 选取与回退**

**Goal:** 让 `plan_backlinks` 从 `Config.target_anchor_keywords` 取出关键词、选 2 个，传给 body 模板和 `_build_links`；keywords 为空时回退到原裸域名行为并 WARN 一次。

**Requirements:** R2, R3, R4

**Dependencies:** Unit 1（Config 字段）、Unit 2（select_anchor_keywords）

**Files:**
- Modify: `src/backlink_publisher/markdown_utils.py`（body 模板函数签名）
- Modify: `src/backlink_publisher/cli/plan_backlinks.py`（_build_links + body 调用点）
- Test: `tests/test_plan_backlinks.py`

**Approach:**
- 重构 `_en_body_a/b/c`、`_zh_body_a/b/c`、`_ru_body_a/b/c`：签名从 `(domain, main_domain)` 改为 `(anchors: list[str], main_domain: str)`；body 内 2 处 `[{domain}]({main_domain})` 改为 `[{anchors[0]}]({main_domain})` 与 `[{anchors[1]}]({main_domain})`。**Title/excerpt/tags 模板中的 `{domain}` 保留不动**（这些不是反链锚点）。
- 在 `plan_backlinks` 调用 body 函数前，先用 `select_anchor_keywords(config.target_anchor_keywords.get(main_domain, []), url_mode, 2)` 拿到 anchors
- 若返回 `None` → 回退：`anchors = [domain_label, domain_label]`，并 `logging.warning(...)`（每篇文章只 warn 一次，用 article-scoped flag 防重复）
- `_build_links` 中 `main_domain` kind 的 `anchor` 字段用 `anchors[0]`；`target` kind 的 `anchor` 字段用 `anchors[1]`（保证 main_domain 和 target 两个反链使用不同关键词）
- supporting/category/detail/extra kind 不动

**Patterns to follow:**
- 现有 body 函数的 f-string 风格
- 现有 `_build_links` 内 dict 构造顺序与 kind 字段

**Test scenarios:**
- Happy path：config 含 keywords `["kw1","kw2","kw3"]`，url_mode="A" → 生成的 content_markdown 中 main_domain link 锚文本为 `kw1`，target link 锚文本为 `kw2`
- Happy path：url_mode="B" → 锚文本为 `kw2`、`kw3`（offset=1 环绕）
- Happy path：url_mode="C" → 锚文本为 `kw3`、`kw1`
- Edge case：keywords 为空 → 落回 domain_label（旧行为），logging.warning 触发恰好 1 次
- Edge case：config 中无该 main_domain 条目 → 同上回退
- Edge case：keywords 长度=1 → 两次反链均使用同一个 keyword（确定性，可接受）
- Integration：跨 3 篇 url_mode=A/B/C 同 target 文章，main_domain 链锚文本分布覆盖 ≥3 个不同关键词（origin 成功标准）
- Integration：生成的 payload 通过 `validate_output_payload`（schema 不变）；`main_domain '...' does not appear in content_markdown` 校验仍通过（URL 部分未变）
- Edge case：英文/中文/俄文 3 个语言变体的 body 函数都生效

**Verification:**
- `pytest tests/test_plan_backlinks.py` 全绿
- 手工抽检 1 篇生成文章：所有 `[xxx]({main_domain})` 中 `xxx` 为关键词字串而非裸域名

- [ ] **Unit 4: 文档与示例更新**

**Goal:** 补齐 README/示例配置，让用户知道如何配置 `anchor_keywords`。

**Requirements:** R1（可发现性）

**Dependencies:** Unit 1（schema 定稿）

**Files:**
- Modify: `README.md`（添加"SEO 锚文本配置"小节，含 toml 示例 + 选取策略简述 + 回退行为说明）
- Modify: `config.example.toml`（如 Unit 1 已添加，本 Unit 仅做语义校对）

**Approach:**
- README 新增一节，说明：
  - 在 `~/.config/backlink-publisher/config.toml` 添加 `[targets."<main_domain>"]` 段
  - 推荐 5–10 个关键词，混合品牌词 + 行业词 + 长尾词
  - 缺失会回退到裸域名 + 日志 WARN
  - 渲染后的 `<a>` 默认带 `target="_blank" rel="noopener"`（无需用户操作）
  - Medium 后端可能剥离 `target` 属性（best-effort 说明）

**Test scenarios:**
- Test expectation: none — pure documentation change, no behavioral code path

**Verification:**
- README 新章节可被 grep 到（关键词如 "anchor_keywords"、"target=\"_blank\""）
- `config.example.toml` 注释段在 fresh 安装路径下可被复制即用

## System-Wide Impact

- **Interaction graph:** `plan_backlinks` 现在依赖 `Config`（之前部分流程也已依赖，无新跨模块边界）；body 模板函数签名变化影响所有调用点（限于 `plan_backlinks.py` 内，受控范围）
- **Error propagation:** keyword pool 缺失 → WARN + 回退，**不**升级为异常；schema 解析错误沿用现有 `DependencyError`
- **State lifecycle risks:** 无持久化状态变化；config 仍只读
- **API surface parity:** `Config` dataclass 新增字段（默认空 dict），向后兼容 —— 既有依赖方不会因新字段失败
- **Integration coverage:** 4 个适配器的 `render_to_html` 调用点不需要改动；通过 Unit 2 的渲染层定制自动获益。建议 Unit 3 测试中包含一次端到端 markdown → HTML 流程，验证锚文本与 target="_blank" 同时正确出现
- **Unchanged invariants:**
  - `validate_output_payload` schema 不变
  - `verifier.py` 行为不变（不校验锚文本字符串，只比对 URL 集合）
  - "反链 dofollow" 约定保持（不加 nofollow）
  - 适配器 publish 逻辑、重试、CAPTCHA 处理等不动

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Body 模板签名重构遗漏某个语言变体 → 部分文章仍输出裸域名 | Unit 3 测试覆盖英/中/俄 3 语言 × A/B/C 3 模式 = 9 组合 |
| 用户未填 keywords → 全部回退到裸域名（无声退化） | logging.warning 每篇 1 次；README 明示填写步骤 |
| Medium 后端剥离 `target="_blank"` 导致线上行为不一致 | 已接受为 best-effort；后续可在 Medium adapter README 加注 |
| markdown-it-py 自定义 renderer rule 写错导致整个渲染崩溃 | Unit 2 测试覆盖空字符串、多链接、中俄混合等场景；引入失败应在 CI 阶段即被捕获 |
| 现有 43 测试在签名重构后局部失败 | Unit 3 完成后跑 full suite；任何回归在 PR 内修复，不延后 |

## Documentation / Operational Notes

- README 新增 "SEO 锚文本配置" 小节（Unit 4）
- `config.example.toml` 注释段提供可直接复制的样例
- 无 migration / 无 feature flag / 无监控变更
- 上线前应通知运维：为现有所有 target 站补齐 `anchor_keywords`，否则会继续走回退路径（功能可用但 SEO 收益为零）

## Sources & References

- **Origin document:** [backlink-publisher/docs/brainstorms/2026-05-12-anchor-text-and-blank-target-requirements.md](../brainstorms/2026-05-12-anchor-text-and-blank-target-requirements.md)
- Related code: `src/backlink_publisher/markdown_utils.py`, `src/backlink_publisher/cli/plan_backlinks.py`, `src/backlink_publisher/config.py`
- External docs: markdown-it-py renderer rules — <https://markdown-it-py.readthedocs.io/en/latest/using.html#renderer-rules>
- Prior plan (precedent for schema-touching feature work): `docs/plans/2026-05-12-003-fix-bare-url-hyperlinks-in-templates-plan.md`
