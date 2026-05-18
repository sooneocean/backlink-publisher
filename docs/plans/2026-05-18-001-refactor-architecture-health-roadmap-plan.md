---
title: 架构健康度重构 — webui / config / 领域分包路线图
type: refactor
status: active
date: 2026-05-18
origin: docs/brainstorms/2026-05-18-architecture-health-refactor-requirements.md
---

# 架构健康度重构 — webui / config / 领域分包路线图

## Overview

按 P0 → P1 → P2 分三阶段重构 `backlink-publisher` 的三处结构压力点：`webui.py` (4904 行, 56.5% 是内联 HTML) 拆为 `webui/` 包；`config.py` (1567 行, 39 个 def) 拆为 `config/` 子包；30+ 平铺模块按领域归到 `anchor/` / `content/` / `linkcheck/` / `publishing/` 四个子包。**核心承诺：不改变任何外部行为**（CLI 接口、TOML 格式、HTTP 端点契约、token 文件格式、import 路径全部兼容）。

## Problem Frame

源文档 `docs/brainstorms/2026-05-18-architecture-health-refactor-requirements.md` 已量化三处技术债热点。本次规划要回答的"HOW"是：

1. 怎么切片才让每个 PR 既小到可审、又大到值得审？
2. 怎么保证 51 个现有测试零修改通过？特别是 `tests/` 中已有 20+ 个 flat module 的直接 import 点，跨 15 个模块。
3. 怎么避免 `save_config()` 类型的 sneaky bug — 引用 `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md`：测试绿但配置静默丢段的历史告诉我们，纯单元测试不足以保证 config 重构无回归。

## Requirements Trace

源文档需求 R1–R12 一一映射到实施单元：

- **R1** WebUI 拆包（≤500 行/文件）→ Unit 3
- **R2** 内联 HTML 抽 Jinja2 模板 → Unit 4
- **R3** `JsonStore` 统一状态持久化 → Unit 2
- **R4** 路由按区域分组 (≥5 个 routes/*.py) → Unit 3
- **R5** `config.py` 拆 `config/{loader,parsers/,writer,tokens}` → Unit 5
- **R6** `Config` dataclass 顶层 API 不变 → Unit 5
- **R7** TOML→Config 等价回归测试 → Unit 5
- **R8** 30+ 平铺模块归到 4 个领域子包 → Unit 6
- **R9** 公共 utils 下沉 `_util/` → Unit 6
- **R10** 顶层 re-export 兼容层（覆盖 tests/ 已用的 15 模块） → Unit 6
- **R11** `Publisher` ABC + dispatcher registry → Unit 7
- **R12** `JsonStore` 预留 SQLite 实现位 → Unit 8

成功标准 S1–S6（见源文档）：单文件行数 ≤500/≤400、内联 HTML ≤5%、顶层 .py ≤6、51 测试零修改、39 路由端点契约不变、CLI 三件套不变。

## Scope Boundaries

继承源文档 Scope Boundaries 全部 6 条：不改 CLI / 不引前端工具链 / 不切存储引擎 / 不改 Medium 三路径回退策略 / 不动锚文本算法 / 不增运行时依赖 / 不改 OAuth 与 token 格式。

附加的规划期边界：

- 本计划不引入新的运行时依赖；Jinja2 已是 Flask 传递依赖（`from flask import render_template` 直接可用）
- 本计划不重写 Medium 适配器逻辑，仅在 Unit 7 重构其 dispatch 入口
- 本计划不替换 APScheduler，仅把它的初始化和回调收敛到 `webui/scheduler.py`

## Context & Research

### Relevant Code and Patterns

- `webui.py:99–2332` — 第一块巨型 `HTML = '''...'''` 字符串 (~70KB)，对应主页路由群
- `webui.py:1655` — `SETTINGS_HTML` 内联模板
- `webui.py:4288 _SITES_HTML` / `webui.py:4445 _RESULT_HTML` — 两块剩余模板
- `webui.py:2548–2593` — 4 个 JSON 文件路径常量 + `_draft_lock = threading.Lock()`
- `webui.py:_load_history / _append_history / _load_profiles / _save_profiles / _load_draft_queue / _save_draft_queue / _load_schedule_settings / _save_schedule_settings` — 8 个对称的 load/save 函数，几乎相同的"打开→json.load/dump→原子写"骨架，这是 `JsonStore` 抽象的天然提取点
- `src/backlink_publisher/io_utils.py:atomic_write_json` — 已有的原子写工具，`JsonStore` 复用
- `src/backlink_publisher/adapters/__init__.py:publish()` — dispatcher 中已有清晰的"API → Brave → Browser"回退链注释，是 registry 重构的良好起点
- `src/backlink_publisher/adapters/base.py:AdapterResult` — 既有的统一返回类型，`Publisher` ABC 围绕它构造
- `tests/test_adapter_dispatcher.py` — dispatcher 已有专项测试，重构后必须仍然通过
- `tests/test_webui_three_url.py` + `tests/test_webui_checkpoint.py` — 仅有 2 个 WebUI 集成测试，意味着 WebUI 改动的回归网很薄

### Institutional Learnings

- `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — `save_config()` 曾静默丢 `[sites.*]` / `[anchor.proportions]` 多个 section 数周，测试一直绿。**直接影响 Unit 5 设计**：必须用真实 fixture TOML 做端到端 round-trip 而不是单元测试。
- `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — WebUI 历史上塞过 15 秒 blocking subprocess 调用；重构时把现有同步调用挪到新文件即可，**不在本计划内改成异步**（出了范围）。
- 已知的 dev-dep CI trap（hypothesis / flask）— Unit 4 抽 Jinja2 不会引入新依赖，但 Unit 1 的 smoke 测试若引入 `pytest-flask` 或新测试库需要明确加入 `[project.optional-dependencies].dev`。倾向于**只用 Flask 原生 `app.test_client()`**，不引新测试依赖。

### External References

- 已跳过外部研究：Flask 蓝图 (Blueprint) + Jinja2 模板是 Flask 标准模式；Python 包重组通过 `__getattr__` 做 deprecation re-export 是标准做法（PEP 562）。本仓库 18 份历史 plan 提供充分的本地模式参考。

## Key Technical Decisions

- **D1. 拆 webui 不拆 config 在 Phase 1 完成**：源文档 D1 已决策。Phase 1 (P0) 只动 webui；Phase 2 (P1) 再拆 config + 领域分包。两件大事并行会让 PR 回归面叠加。
- **D2. JsonStore 抽象**先于路由拆分**（Unit 2 在 Unit 3 之前）**：现有 8 个 `_load/_save` 函数耦合到模块级常量路径；先抽出 `JsonStore` 类，路由层调 `self.store.history.append(...)` 而不是 `_append_history(...)`，这样路由拆分就只是搬运注册，不再涉及"哪个文件知道哪个 path"。
- **D3. 路由分组用 Flask Blueprint**：每个 `webui/routes/<area>.py` 导出一个 `bp = Blueprint("<area>", __name__)`，`webui/app.py` 在工厂函数中 `app.register_blueprint(bp)`。这是 Flask 原生模式，无需新依赖。
- **D4. HTML 抽 Jinja2 模板 = 直接物理迁移**：不重写 HTML、不引前端组件化、不抽公共 layout。每块 `'''...'''` 字符串 → `templates/<area>.html` 文件，所有 `{{ var }}` 占位语法保留（Flask 字符串模板与 Jinja2 文件模板兼容）。Layout 抽取放 P2 之后。
- **D5. config 拆分用真实 fixture 做 round-trip**：从 `fixtures/` 或 `config.example.toml` 取一份完整 TOML，做"旧 loader 加载 → 拆分后 loader 加载 → 两个 Config 对象深度相等"。`save_config()` 已被同一份 sneaky bug 烧过，单元测试不够。
- **D6. 领域分包 + `__getattr__` 兼容 re-export**：`src/backlink_publisher/__init__.py` 用 `__getattr__(name)` lazy 转发到新位置（PEP 562），覆盖 tests/ 已使用的 15 个模块名 + 任何外部脚本可能用的名字。**不**用文件级 `from .new_path import *`（会立即触发所有领域包加载，破坏启动延迟）。
- **D7. 不引入 schema 验证库（pydantic / dataclasses-json）**：源文档约束"不增运行时依赖"。`Config` 保留 dataclass，解析期错误抛 `InputValidationError`（已有）。
- **D8. P2 的 Publisher ABC 不立即应用到现有 3 个 Medium 适配器**：只引入 ABC + registry 接口，现有适配器实现 ABC 即可（已经有匹配的 `publish()` 方法签名），dispatcher 改用 registry 查询。**不**重写适配器内部逻辑。
- **D9. SQLite 实现位（R12）= 接口抽象而非接口预留**：`JsonStore` 在 Unit 2 已是 protocol-shaped 抽象；Unit 8 的额外工作仅是"明确 JsonStore 协议、文档化"，不写 SQLite 后端代码。如果未来需要换，加 `SqliteStore(JsonStore)` 即可。

## Open Questions

### Resolved During Planning

- **Q (源文档 deferred): PR 切片粒度 — routes-first 还是 services-first？** 解：按 **Unit 1 → Unit 2 → Unit 3 → Unit 4** 切，每个单元一个 PR。Unit 1 (smoke 基线) 单独 PR，Unit 2 (JsonStore) 独立 PR，Unit 3 (routes + services 一起拆) 一个较大但同质 PR，Unit 4 (HTML→Jinja2) 一个机械性 PR。原因：Unit 3 把 services 和 routes 分开拆会造成"中间状态没人调"的尴尬，一次拆透更干净。
- **Q (源文档 deferred): config snapshot 是独立 `snapshot.py` 还是合并 `writer.py`？** 解：合并到 `writer.py`。`_snapshot_config` (~60 行) 是 `save_config()` 的内联前置步骤，独立成文件徒增 import 跳转。Unit 5 在 `writer.py` 内用一节 `# --- snapshot ---` 注释区分即可。
- **Q (源文档 deferred): tests/ 中 flat module import 数量？** 解：grep 结果为 20+ 个 import 点跨 15 个模块（errors=20, config=16, verify_publish=7, anchor_profile=6, markdown_utils=4, work_scraper/cli/anchor_scheduler/anchor_resolver/anchor_metrics=3, language_check/content_fetch/checkpoint=2, work_themed_generator/url_utils=1）。**Re-export 兼容层是 Unit 6 的硬要求**，不是可选项。
- **Q (源文档 deferred): pydantic？** 解：不引入（见 D7）。
- **Q (源文档 deferred): Publisher ABC 方法集？** 解：暂只导出 `publish(payload, mode, config) -> AdapterResult`。`verify_adapter_setup` 留作模块级函数（现状），下次加新平台时再决定是否提升到 ABC。

### Deferred to Implementation

- **路由模块边界划分细节**：当 Unit 3 实施时，按 `/sites/*`、`/ce:plan,generate,validate,publish,history,draft/*`、`/settings/*`、根路由 `/` 大致 4 组，最终边界由实际行数决定（每个 `routes/*.py` 目标 ≤300 行）。
- **`webui.py` 中 9 个 `_*_HTML` 块对应的模板文件命名**：实施时按"主路由对应的视图名"决定，可能落到 `templates/{index, settings, sites, results, history, drafts}.html`。
- **`__getattr__` 中是否输出 DeprecationWarning**：实施时如果 grep 出仅有少量内部调用方，直接 raise 是更好的清理压力；如果有外部脚本可能依赖，则先 DeprecationWarning 一个版本。
- **Unit 7 的 registry 表存放位置**：可能是 `adapters/__init__.py` 内部字典，也可能是模块级 `@register("medium")` 装饰器。实施时按代码体量定，倾向字典（更简单）。

## High-Level Technical Design

> *此节为设计意图的方向性示意，非实现规约。实施 agent 应将其视为上下文而非可复制代码。*

### 目标目录结构（Phase 1+2 完成后）

```
src/backlink_publisher/
├── __init__.py            # re-export shim (__getattr__ PEP 562)
├── _util/
│   ├── __init__.py
│   ├── url.py             # from url_utils
│   ├── markdown.py        # from markdown_utils
│   ├── jsonl.py
│   ├── io.py              # from io_utils
│   ├── logger.py
│   └── errors.py
├── anchor/
│   ├── __init__.py
│   ├── lang.py            # from anchor_lang
│   ├── metrics.py         # from anchor_metrics
│   ├── profile.py         # from anchor_profile
│   ├── resolver.py        # from anchor_resolver
│   └── scheduler.py       # from anchor_scheduler
├── content/
│   ├── __init__.py
│   ├── fetch.py           # from content_fetch
│   ├── scraper.py         # from work_scraper
│   └── themed_gen.py      # from work_themed_generator
├── linkcheck/
│   ├── __init__.py
│   ├── http.py            # from linkcheck
│   ├── language.py        # from language_check
│   └── verify.py          # from verify_publish
├── publishing/
│   ├── __init__.py        # exports publish(), verify_adapter_setup()
│   ├── adapters/          # 现 adapters/ 整体迁入
│   │   ├── base.py
│   │   ├── blogger_api.py
│   │   ├── medium_api.py
│   │   ├── medium_brave.py
│   │   ├── medium_browser.py
│   │   ├── retry.py
│   │   └── ...
│   └── registry.py        # Unit 7: Publisher ABC + 表驱动 dispatch
├── config/
│   ├── __init__.py        # 顶层导出 Config, load_config, save_config 等
│   ├── loader.py          # TOML 读 + 解析器调度
│   ├── writer.py          # 原子写 + snapshot 历史 + 权限警告
│   ├── tokens.py          # blogger / medium token 读写
│   └── parsers/
│       ├── __init__.py
│       ├── target.py
│       ├── three_url.py
│       ├── anchor.py
│       ├── llm.py
│       └── alarm.py
├── checkpoint.py
├── footprint.py
├── schema.py
└── cli/                   # 不变
    └── ...

webui/                     # 新包，位于仓库根（与 src/ 同级，或 src/backlink_publisher/webui/）
├── __init__.py            # Flask app factory
├── app.py                 # create_app() + scheduler 启动
├── scheduler.py           # APScheduler 配置 + 任务恢复
├── store/
│   ├── __init__.py
│   ├── base.py            # JsonStore 抽象
│   ├── history.py
│   ├── profiles.py
│   ├── drafts.py
│   └── schedule.py
├── services/
│   ├── plan.py
│   ├── generate.py
│   ├── validate.py
│   ├── publish.py
│   └── sites.py
├── routes/
│   ├── __init__.py        # register_all_blueprints(app)
│   ├── main.py            # /
│   ├── pipeline.py        # /ce:plan, /ce:generate, /ce:validate, /ce:publish
│   ├── history.py         # /ce:history*
│   ├── drafts.py          # /ce:draft/*
│   ├── sites.py           # /sites/*
│   └── settings.py        # /settings/*
└── templates/
    ├── base.html
    ├── index.html         # from HTML
    ├── settings.html      # from SETTINGS_HTML
    ├── sites.html         # from _SITES_HTML
    └── result.html        # from _RESULT_HTML

webui.py                   # 保留为 thin entrypoint：from webui import create_app; app = create_app()
```

### JsonStore 抽象（Unit 2 设计示意）

```python
# webui/store/base.py — 方向性示意

class JsonStore:
    """Single-process JSON-backed key/list store with atomic writes."""

    def __init__(self, path: Path, default_factory: Callable[[], Any]):
        self._path = path
        self._default = default_factory
        self._lock = threading.Lock()  # 单进程内并发保护

    def load(self) -> Any: ...
    def save(self, data: Any) -> None: ...           # 复用 io_utils.atomic_write_json
    def update(self, fn: Callable[[Any], Any]) -> Any: ...  # load → fn → save 原子组

# webui/store/history.py
history_store = JsonStore(_HISTORY_FILE, default_factory=list)

# 路由层使用：
history_store.update(lambda items: items + [new_record])
```

### 兼容 re-export shim（Unit 6 设计示意）

```python
# src/backlink_publisher/__init__.py — 方向性示意

_REEXPORT_MAP = {
    "anchor_lang": "backlink_publisher.anchor.lang",
    "anchor_metrics": "backlink_publisher.anchor.metrics",
    "content_fetch": "backlink_publisher.content.fetch",
    "verify_publish": "backlink_publisher.linkcheck.verify",
    # ... 全部 15 个 flat 名
}

def __getattr__(name: str):  # PEP 562
    if name in _REEXPORT_MAP:
        module = importlib.import_module(_REEXPORT_MAP[name])
        return module
    raise AttributeError(...)
```

## Implementation Units

### Phase 1 — P0：WebUI 拆解

- [ ] **Unit 1: WebUI 行为基线 smoke 测试**

**Goal:** 在拆 `webui.py` 之前，为 39 个路由建立"端点契约"回归网，确保后续重构改动可以被 CI 立刻发现。

**Requirements:** S5 (端点契约不变), S4 (51 测试零修改通过)

**Dependencies:** 无

**Files:**
- Create: `tests/test_webui_route_contract.py`

**Approach:**
- 用 Flask 原生 `app.test_client()`（不引入 `pytest-flask`，避免新 dev-dep）
- 对 39 个路由的每一个，写一个 minimal request → status code + (有 redirect 时) Location 的断言
- POST 路由用最小合法表单 + 一个故意非法的表单各一次
- 不断言 HTML 内容，避免与 Unit 4 的 Jinja2 迁移产生测试-代码绑死
- 使用 `monkeypatch` 把 `~/.config/backlink-publisher/*.json` 路径指向 tmp_path，确保测试隔离

**Execution note:** 测试驱动 — 这一单元的目的是 *建立* 回归网，Unit 2-4 完成时必须保证这些 contract test 全绿。

**Patterns to follow:**
- `tests/test_webui_three_url.py` — 已有的 Flask test_client 用法
- `tests/conftest.py` — 已有的 fixture 与 monkeypatch 模式

**Test scenarios:**
- Happy path：每个 GET 路由返回 200 或预期 redirect (3xx)
- Happy path：每个 POST 路由用合法表单返回 200/302
- Error path：POST 路由用空 / 缺字段表单返回 400 或 422 或 redirect 回表单页
- Edge case：根路由 `/` 在 `~/.config/...` 文件不存在时不崩溃（首次启动场景）
- Edge case：`_HISTORY_FILE` 文件存在但为空字符串时不崩溃

**Verification:**
- 测试运行 `pytest tests/test_webui_route_contract.py` 全绿
- 39 个路由都至少有 1 个断言覆盖
- 整体测试时间 ≤ 5 秒（不能引入慢测试）

---

- [ ] **Unit 2: 抽出 `JsonStore` + 状态持久化层**

**Goal:** 把 webui.py 中 8 个 `_load_* / _save_*` 函数 + 4 个 `_*_FILE` 常量 + `_draft_lock` 统一到 `webui/store/` 包。

**Requirements:** R3, R12（仅抽象，不实现 SQLite）

**Dependencies:** Unit 1（需要 contract test 保护）

**Files:**
- Create: `webui/__init__.py`（空 / app 工厂壳）
- Create: `webui/store/__init__.py`
- Create: `webui/store/base.py` — `JsonStore` 类
- Create: `webui/store/history.py`, `profiles.py`, `drafts.py`, `schedule.py` — 各暴露一个模块级 store 实例 + 现 `_load/_save/_append/_update/_get/_delete` 函数迁移
- Modify: `webui.py` — 把 `_load_history / _append_history / _load_profiles / _save_profiles / _load_draft_queue / _save_draft_queue / _get_draft_item / _update_draft_item / _delete_draft_item / _load_schedule_settings / _save_schedule_settings / _draft_lock / _HISTORY_FILE / _PROFILES_FILE / _DRAFT_FILE / _SCHEDULE_SETTINGS_FILE` 全部迁出，在 `webui.py` 顶部 `from webui.store import history_store, profiles_store, drafts_store, schedule_store`
- Create: `tests/test_webui_store.py`

**Approach:**
- `JsonStore` 用 `threading.Lock` 做单进程串行化（保留现有 `_draft_lock` 行为）
- `load()` 内部捕获 `FileNotFoundError` 时调 `default_factory`（保留现状）
- `save(data)` 复用 `io_utils.atomic_write_json`
- `update(fn)` 是 load → fn → save 的原子组，替换现有"读 → mutate → 写"模式
- `_calc_next_available` / `_publish_draft_job` 等业务函数留在 webui.py，等 Unit 3 一起迁

**Patterns to follow:**
- `src/backlink_publisher/io_utils.py:atomic_write_json`
- 现有 `threading.Lock` 使用方式（`webui.py:2593`）

**Test scenarios:**
- Happy path: `JsonStore(tmp_path / "x.json", list).save([1,2]) → load() == [1,2]`
- Happy path: `update(lambda xs: xs + ["new"])` 原子地追加
- Edge case: 文件不存在 → `load()` 返回 `default_factory()` 的结果
- Edge case: 文件存在但内容是空字符串或非法 JSON → 当前行为（崩溃 vs 返回默认）必须被显式测试覆盖以锁定语义。建议改为返回默认 + 警告，但**保留现有行为**留到后续 PR。
- Error path: `save` 调用时 `atomic_write_json` raise OSError → 异常上抛，不静默
- Integration: 两个线程并发 `update` 同一个 store，最终 state 反映两次更新（无丢失）

**Verification:**
- Unit 1 的 contract test 全部仍通过
- `tests/test_webui_store.py` 全绿
- `grep -E "_HISTORY_FILE|_PROFILES_FILE|_DRAFT_FILE|_SCHEDULE_SETTINGS_FILE|_load_history|_save_profiles" webui.py` 返回 0 行（全部迁出）

---

- [ ] **Unit 3: 抽业务 services + 路由分组到 Blueprint**

**Goal:** 把 webui.py 的路由处理逻辑分成 services（业务）+ routes（HTTP 薄壳），按区域注册 Blueprint。

**Requirements:** R1（≤500 行/文件）, R4（≥5 个 routes 模块）

**Dependencies:** Unit 2（JsonStore 已就位）

**Files:**
- Create: `webui/app.py` — `create_app() → Flask`，注册 Blueprint，启动 scheduler
- Create: `webui/scheduler.py` — APScheduler 配置 + `_restore_scheduled_jobs` 迁入
- Create: `webui/routes/main.py` — `/`, `/ce:clear`
- Create: `webui/routes/pipeline.py` — 16 个 `/ce:*` 路由的 7 个流水线类（plan/generate/validate/publish）
- Create: `webui/routes/history.py` — `/ce:history*`
- Create: `webui/routes/drafts.py` — `/ce:draft/*`
- Create: `webui/routes/sites.py` — `/sites/*`
- Create: `webui/routes/settings.py` — `/settings/*`
- Create: `webui/services/plan.py`, `generate.py`, `validate.py`, `publish.py`, `sites.py`, `settings.py` — 业务函数从 webui.py 迁入
- Modify: `webui.py` — 收缩为 `from webui.app import create_app; app = create_app(); if __name__ == "__main__": app.run(...)`，目标 ≤ 80 行
- Modify: `启动 WebUI.command` — 路径若有 `webui.py` 硬编码需更新（确认不需要）

**Approach:**
- 每个 route handler 在 routes/*.py 中尽量保持 ≤15 行，仅做：解析 form/args → 调 service → 返回 render_template 或 redirect
- services/*.py 是纯函数，输入参数 dict，输出 dict 或 raise `InputValidationError`
- 路由对模板的引用从 `render_template_string(HTML, ...)` 改为 `render_template("index.html", ...)`（Jinja2 文件查找路径由 Flask app 配置 `template_folder="templates"`）
- HTML 字符串先**原样**搬到 `webui/templates/*.html`（Unit 4 再做语法清理）；Flask 的 `render_template_string` 与 `render_template` 都用 Jinja2，差别只是源在哪
- `_render` 辅助函数（`webui.py:2708`）迁入 `webui/services/__init__.py` 或拆到具体 service

**Patterns to follow:**
- Flask Blueprint 标准模式：`bp = Blueprint("history", __name__, url_prefix="")`
- 现有 `webui.py:_render` 的 template 变量注入方式

**Test scenarios:**
- Happy path: Unit 1 的 39 路由 contract test 全部仍绿
- Happy path: `from webui import create_app; app = create_app()` 不报错
- Integration: 启动 app + 模拟 GET `/` 返回 200，HTML 中包含预期标识字符串
- Integration: POST `/sites/save` 触发 sites service → site config 写入文件 → 重定向回 `/sites`
- Integration: APScheduler restore 在 app 启动时执行，已存在的 schedule-settings.json 中的 job 被注册
- Edge case: `webui.py` 直接被 `python webui.py` 启动仍正常（双击 .command 路径）

**Verification:**
- `wc -l webui.py` ≤ 80
- `wc -l webui/routes/*.py webui/services/*.py webui/app.py webui/scheduler.py` 每个 ≤ 500
- 全部 51 个原有测试 + Unit 1 contract + Unit 2 store 测试全绿
- 双击 `启动 WebUI.command` 仍可启动 WebUI（手动验证一次）

---

- [ ] **Unit 4: 内联 HTML → Jinja2 模板文件**

**Goal:** 把 5 块 `'''...'''` 三引号 HTML（共 ~117KB）抽成 `webui/templates/*.html` 文件，使内联 HTML 字节占比降到 ≤5%。

**Requirements:** R2, S1（HTML 占比 ≤5%）

**Dependencies:** Unit 3（routes 已用 `render_template`）

**Files:**
- Create: `webui/templates/index.html` — 从 `HTML`（webui.py:99）
- Create: `webui/templates/settings.html` — 从 `SETTINGS_HTML`（webui.py:1655）
- Create: `webui/templates/sites.html` — 从 `_SITES_HTML`（webui.py:4288）
- Create: `webui/templates/result.html` — 从 `_RESULT_HTML`（webui.py:4445）
- 其余小 HTML 字符串视情况合并到上述文件或新建模板
- Modify: `webui.py` / `webui/services/__init__.py` — 删除 `HTML`, `SETTINGS_HTML`, `_SITES_HTML`, `_RESULT_HTML` 等模块级字符串常量

**Approach:**
- 一次一个模板：移动字符串到文件 → 删除 Python 中的常量 → 跑 contract test → 提交
- **不重构 HTML 内部结构**（不提取 base.html、不抽公共 nav、不重命名 CSS class）
- 如果 `_render()` 现在用 `**kwargs` 透传，Flask 的 `render_template("x.html", **ctx)` 是直接替换

**Patterns to follow:**
- Flask 标准 `render_template("name.html", var=value)` 用法

**Test scenarios:**
- Happy path: contract test 仍全部绿
- Happy path: `grep -cE "'''.*<html|\"\"\".*<html" webui.py webui/**/*.py` 接近 0
- Integration: 实际渲染一次 `/` 主页，HTML 字节数应与重构前相同（容许 trailing whitespace 差异）；可通过快照对比

**Verification:**
- `python -c "from pathlib import Path; t = Path('webui.py').read_text() + ''.join(p.read_text() for p in Path('webui/').rglob('*.py')); import re; html = sum(len(b) for b in re.findall(r\"'''.*?'''\", t, re.S) if '<' in b); print(html / (len(t) or 1))"` 输出 ≤ 0.05
- 手动浏览器访问 `/`, `/settings`, `/sites`, 任意结果页，UI 视觉一致

### Phase 2 — P1：config 拆分 + 领域分包

- [ ] **Unit 5: `config.py` 拆为 `config/` 子包**

**Goal:** 把 1567 行 `config.py` 拆成 `config/{loader, writer, tokens, parsers/}`，保留所有顶层 API。

**Requirements:** R5, R6, R7, S2（≤400 行/文件）

**Dependencies:** 无（与 webui 重构正交）；如果想避免合流冲突，建议在 Phase 1 全部 PR 合入后再开始

**Files:**
- Convert: `src/backlink_publisher/config.py` → `src/backlink_publisher/config/` 包
- Create: `src/backlink_publisher/config/__init__.py` — 顶层 re-export：`from .loader import load_config; from .writer import save_config, merge_site_url_categories; from .tokens import ...; from .types import Config, BloggerOAuthConfig, MediumOAuthConfig, ThreeUrlConfig, LLMProviderConfig, AnchorAlarmConfig, AnchorAlarmOverride`
- Create: `src/backlink_publisher/config/types.py` — 所有 dataclass（Config, *OAuthConfig, ThreeUrlConfig, LLMProviderConfig, AnchorAlarmConfig, AnchorAlarmOverride）
- Create: `src/backlink_publisher/config/loader.py` — `load_config`, `_config_dir`, `_cache_dir`, `_warn_if_loose_config_permissions`, 解析器调度
- Create: `src/backlink_publisher/config/writer.py` — `save_config`, `_atomic_write_text`, `_snapshot_config`, `_preserve_unknown_sections`, `_toml_*`, `merge_site_url_categories`, `upgrade_target_to_threeurl`
- Create: `src/backlink_publisher/config/tokens.py` — `load_blogger_token`, `save_blogger_token`, `load_medium_token`, `save_medium_token`
- Create: `src/backlink_publisher/config/parsers/__init__.py`
- Create: `src/backlink_publisher/config/parsers/target.py` — `_parse_target_anchor_keywords`, `_parse_target_anchor_pools_v2`, `_clean_pool`
- Create: `src/backlink_publisher/config/parsers/three_url.py` — `_parse_target_three_url`, `_parse_site_url_categories`, `_domain_label`, `_normalize_domain_key`
- Create: `src/backlink_publisher/config/parsers/anchor.py` — `_parse_anchor_proportions`, `get_anchor_pool_v2`, `get_anchor_keywords`
- Create: `src/backlink_publisher/config/parsers/llm.py` — `_parse_llm_anchor_provider`
- Create: `src/backlink_publisher/config/parsers/alarm.py` — `_parse_anchor_alarm`, `_coerce_threshold`
- Create: `src/backlink_publisher/config/blog.py`（或归入 `loader.py`）— `resolve_blog_id`, `get_three_url_config`
- Create: `tests/test_config_roundtrip.py` — D5 决策的 round-trip 等价测试

**Approach:**
- **D5 防御性步骤**：在动任何代码之前，先在新文件 `tests/test_config_roundtrip.py` 中用 `fixtures/` 或 `config.example.toml`（已是完整示例）固化"加载 → 序列化字段 → 再加载 → 字段相等"的 baseline。这个测试在拆分前必须先在旧 `config.py` 上跑通，然后拆分。
- 拆分顺序：先 `types.py`（无依赖）→ `parsers/*.py` → `loader.py` → `writer.py` → `tokens.py`
- 每搬一个解析器，跑一次 `pytest tests/test_config* tests/test_config_roundtrip.py`
- `Config` 的 import path `from backlink_publisher.config import Config, load_config` 必须不变（R6）；通过 `__init__.py` re-export 实现

**Execution note:** Characterization-first — 先写 `test_config_roundtrip.py` 锁定当前 `save_config(load_config(x)) == load_config` 的行为，再拆。理由：参考 `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md`，config 是历史 sneaky-bug 高发区。

**Patterns to follow:**
- 现有 `tests/test_config_three_url.py` 的 fixture 加载方式
- `src/backlink_publisher/io_utils.py:atomic_write_json`（虽然 config 用 text 而非 json）

**Test scenarios:**
- Happy path: 完整 `config.example.toml` → `load_config` → 关键字段（`blogger_oauth`, `medium_integration_token`, `target_three_url`, `anchor_pools_v2`, `llm_anchor_provider`, `anchor_alarm`）均非默认值
- Happy path: `save_config(cfg, tmp_path) → 再 load_config(tmp_path)` 得到等价 Config（depth equal 或字段逐一断言）
- Edge case: 缺失任意一个 section 的 TOML 不崩溃，对应字段为 None / 默认值
- Edge case: TOML 中有未知 top-level section（如 `[future_feature]`）→ `save_config` 写回时保留这个 section（`_preserve_unknown_sections` 现有行为）
- Error path: 非法权限 (0o644) → warning 触发但不阻塞
- Error path: 非法 anchor_proportions 数值 → 抛 `InputValidationError`
- Integration: `from backlink_publisher.config import Config, load_config, save_config` 全部仍可用；现有 16 个测试文件中 `from backlink_publisher.config import ...` 0 修改通过

**Verification:**
- `wc -l src/backlink_publisher/config/*.py src/backlink_publisher/config/parsers/*.py` 每文件 ≤ 400
- 全部 16 个 `test_config*` 测试 + 新增 round-trip 测试全绿
- 全套 51+ 测试零修改通过
- `python -c "from backlink_publisher.config import Config, load_config, save_config, ThreeUrlConfig"` 不报错

---

- [ ] **Unit 6: 领域分包 + 顶层 re-export 兼容层**

**Goal:** 把 30+ 平铺模块归到 `anchor/` / `content/` / `linkcheck/` / `publishing/` / `_util/` 五个子包，旧 import path 通过 `__getattr__` lazy re-export 全部保留。

**Requirements:** R8, R9, R10, S3（顶层 .py ≤ 6）

**Dependencies:** Unit 5（先做 config，避免合流冲突）

**Files:**
- Move (preserve git history with `git mv`):
  - `anchor_lang.py` → `anchor/lang.py`
  - `anchor_metrics.py` → `anchor/metrics.py`
  - `anchor_profile.py` → `anchor/profile.py`
  - `anchor_resolver.py` → `anchor/resolver.py`
  - `anchor_scheduler.py` → `anchor/scheduler.py`
  - `content_fetch.py` → `content/fetch.py`
  - `work_scraper.py` → `content/scraper.py`
  - `work_themed_generator.py` → `content/themed_gen.py`
  - `linkcheck.py` → `linkcheck/http.py`
  - `language_check.py` → `linkcheck/language.py`
  - `verify_publish.py` → `linkcheck/verify.py`
  - `adapters/` 整体 → `publishing/adapters/`
  - `url_utils.py` → `_util/url.py`
  - `markdown_utils.py` → `_util/markdown.py`
  - `jsonl.py` → `_util/jsonl.py`
  - `io_utils.py` → `_util/io.py`
  - `logger.py` → `_util/logger.py`
  - `errors.py` → `_util/errors.py`
- Create: 每个子包的 `__init__.py`（薄壳，转发公开 API）
- Modify: `src/backlink_publisher/__init__.py` — 实现 `__getattr__` PEP 562 lazy re-export，覆盖至少这 15 个 flat 名（实际清单见 D6 + grep 结果）
- Modify: 所有 `src/backlink_publisher/**/*.py` 内部相对 import — `from .config import X` 保持；`from ..anchor_lang import Y` 改为 `from ..anchor.lang import Y` 等
- 保留：`checkpoint.py`, `footprint.py`, `schema.py`, `cli/`, `__init__.py`, `config_echo.py` 在顶层（5 个 .py + 子包们）
- Modify: `webui.py` / `webui/` 中的 import — 仍用旧 path 通过 re-export 透明工作；可在另一 PR 中清理

**Approach:**
- 按子包顺序逐个搬，每个子包一个 commit / 一个 PR section：
  1. `_util/` 先搬（最底层，依赖少）→ 跑全套测试 → 提交
  2. `linkcheck/` → 测试 → 提交
  3. `anchor/` → 测试 → 提交
  4. `content/` → 测试 → 提交
  5. `publishing/` → 测试 → 提交
  6. 最后实现 `__getattr__` re-export + 确认 tests/ 中 20+ flat import 全部仍通过
- **不修改 tests/ 中的 import path** — 这是 R10 的硬约束：现有测试零修改通过
- 用 `git mv` 而非删除+新建，保留 blame 历史

**Execution note:** 增量 + 测试网托底 — 每搬一个子包跑一次完整 `pytest`，立即发现遗漏的相对 import 修复。

**Patterns to follow:**
- PEP 562 `__getattr__` lazy import 模式（标准库 `numpy` v1.20+, `pandas` v1.3+ 均使用）

**Test scenarios:**
- Happy path: `from backlink_publisher.anchor_lang import check_anchor_language` 仍工作（lazy re-export）
- Happy path: `from backlink_publisher.anchor.lang import check_anchor_language` 也工作（新 path）
- Happy path: `from backlink_publisher.config import Config` 直接命中真实模块（不经 `__getattr__`）
- Edge case: `from backlink_publisher import nonexistent_module` 抛 `AttributeError`，错误消息提示可能的新位置
- Edge case: `import backlink_publisher.anchor_lang` form 也工作（lazy import + `sys.modules` 注册）
- Edge case: 双向 re-export 不形成 import 循环
- Integration: 51 个测试 + 新 contract / store / round-trip 测试全部零修改通过
- Integration: CLI 五件套 `plan-backlinks / validate-backlinks / publish-backlinks / report-anchors / footprint` 仍可用

**Verification:**
- `ls src/backlink_publisher/*.py | wc -l` ≤ 6
- `pytest tests/` 全绿，无任何 test 修改
- `grep -rn "from backlink_publisher\." tests/ | wc -l` 与重构前数字一致（验证测试侧 0 改动）
- `plan-backlinks --help` / `publish-backlinks --help` 不报错

### Phase 3 — P2：适配器 registry + 存储扩展位

- [ ] **Unit 7: `Publisher` ABC + dispatcher registry**

**Goal:** 把 `adapters/__init__.py:publish()` 中的 `if plat == "blogger" / elif "medium"` 链替换为表驱动 registry；引入 `Publisher` ABC 让新平台只改注册表。

**Requirements:** R11

**Dependencies:** Unit 6（adapters 已迁入 `publishing/`）

**Files:**
- Create: `src/backlink_publisher/publishing/registry.py` — `Publisher` ABC + `register()` + `dispatch()`
- Modify: `src/backlink_publisher/publishing/__init__.py` — `publish()` 改用 `dispatch()`，行为不变（含 Medium API → Brave → Browser 回退）
- Modify: `src/backlink_publisher/publishing/adapters/blogger_api.py` — `BloggerAPIAdapter` 显式继承 `Publisher`（方法签名已匹配）
- Modify: `src/backlink_publisher/publishing/adapters/medium_api.py`, `medium_brave.py`, `medium_browser.py` — 同上
- Modify: `tests/test_adapter_dispatcher.py` — 不改测试，但确认仍通过

**Approach:**
- `Publisher` ABC 暴露 `publish(payload, mode, config) -> AdapterResult` 单一方法（D8）
- Registry 结构：`{"blogger": [BloggerAPIAdapter], "medium": [MediumAPIAdapter, MediumBraveAdapter, MediumBrowserAdapter]}` + Medium 的平台限制（Brave 仅 Darwin）通过 adapter 自身的 `available_on(platform)` classmethod 表达
- `dispatch()` 按 fallback 链依次尝试，行为完全等同现有 `publish()`
- 不重写适配器内部，仅添加 ABC 继承声明

**Patterns to follow:**
- 现有 `src/backlink_publisher/adapters/__init__.py:publish()` 的 fallback 逻辑（保留语义）
- Python ABC 标准用法（`abc.ABC` + `@abstractmethod`）

**Test scenarios:**
- Happy path: Blogger 平台 → `BloggerAPIAdapter().publish()` 被调用，结果一致
- Happy path: Medium 平台 + token 配置 → `MediumAPIAdapter` 命中
- Happy path: Medium 平台 + 无 token + Darwin → `MediumBraveAdapter`
- Happy path: Medium 平台 + 无 token + Brave DependencyError + Darwin → `MediumBrowserAdapter`
- Error path: Medium 平台 + `MediumAPIAdapter` raise `ExternalServiceError` (401/429) → 不 fall through，异常上抛
- Edge case: 未知 platform → `ExternalServiceError("unsupported platform: ...")`
- Edge case: `dry_run=True` → 返回 sentinel `AdapterResult`，不调任何 adapter
- Integration: `tests/test_adapter_dispatcher.py` 现有用例全绿 0 修改

**Verification:**
- `tests/test_adapter_*.py` 全绿
- `grep -E "^if plat ==|^elif plat ==" src/backlink_publisher/publishing/__init__.py` 返回 0 行（不再有硬编码 if/elif）
- `python -c "from backlink_publisher.publishing import publish, Publisher; assert issubclass(...BloggerAPIAdapter, Publisher)"`

---

- [ ] **Unit 8: `JsonStore` 协议文档化 + SQLite 后端预留**

**Goal:** 把 Unit 2 已存在的 `JsonStore` 形式化为 protocol，文档说明未来 SQLite 后端如何介入，但**不**写 SQLite 实现。

**Requirements:** R12

**Dependencies:** Unit 2

**Files:**
- Modify: `webui/store/base.py` — 把 `JsonStore` 类提取为 `Store` Protocol + `JsonStore(Store)` 实现
- Create: `webui/store/README.md`（或在 `base.py` 顶部 docstring）— 说明 protocol，"将来加 `SqliteStore(Store)` 替换实例即可"
- 不写 SQLite 代码

**Approach:**
- `from typing import Protocol` 定义 `Store(Protocol)` 暴露 `load() / save() / update()`
- `JsonStore` 标记 `class JsonStore: ` 实现 protocol（duck typing）
- 文档列出：未来切换路径（"新建 `webui/store/sqlite.py:SqliteStore`，改 `webui/store/__init__.py` 实例化"）

**Test scenarios:**
- Test expectation: none — 这是文档+类型签名调整，不引入新行为。Unit 2 的 store 测试已覆盖 `JsonStore` 实际语义。

**Verification:**
- `mypy src/backlink_publisher webui --strict` 不报新错（如果未来加 mypy）
- `webui/store/base.py` 含 Protocol 定义和 docstring

## System-Wide Impact

- **Interaction graph:**
  - WebUI 拆分（Unit 1-4）影响 39 个 HTTP 路由 + APScheduler 后台任务 + 双击 `.command` 入口
  - 领域分包（Unit 6）通过 re-export 影响**所有** import `backlink_publisher.*` 的代码：51 测试、5 个 CLI 入口、webui.py、外部脚本（不可见）
- **Error propagation:**
  - JsonStore.save 出错（磁盘满 / 权限）→ 现状是异常冒泡到 Flask 500；保留此行为
  - re-export shim 找不到模块 → `AttributeError`，错误消息**必须**提示新位置（否则 grep 不到的外部脚本无从 debug）
  - config 拆分后，解析器抛 `InputValidationError` 路径不变
- **State lifecycle risks:**
  - 4 个 JSON 文件的并发：Unit 2 的 `threading.Lock` 仅保护单进程；如果用户在两个 shell 里启 webui（罕见但可能），写竞争仍存在 — 这是**遗留问题，本计划不修复**（源文档 Scope Boundaries 明确不切存储引擎）
  - APScheduler 持久化 job：现状是从 `schedule-settings.json` 重启时恢复；Unit 3 把恢复逻辑挪到 `webui/scheduler.py:_restore_scheduled_jobs`，必须保证启动顺序"store 实例化 → scheduler 启动 → 恢复"
- **API surface parity:**
  - `from backlink_publisher.config import *` ✓ 通过 R6 保证
  - `from backlink_publisher.<flat_module> import *` ✓ 通过 R10 re-export shim
  - 39 个 HTTP 路由 URL / 方法 / 表单字段 ✓ 通过 Unit 1 contract test 保证
  - 5 个 CLI entry point 名称与参数 ✓ 通过 scope boundary 保证
- **Integration coverage:**
  - Unit 1 的 contract test 覆盖路由级；Unit 5 的 round-trip 测试覆盖 config 端到端；Unit 6 的 lazy re-export 验证通过现有 51 测试零修改通过
- **Unchanged invariants:**
  - TOML 文件格式不变 — `~/.config/backlink-publisher/config.toml` 的 schema 与读写语义完全保留（含未知 section 保留）
  - Token 文件格式不变 — `blogger-token.json`, `medium-token.json` 字节级兼容
  - CLI 命令、参数、stdin/stdout JSONL 格式不变
  - Medium 3 路径回退顺序不变（API → Brave on Darwin → Playwright）
  - WebUI 默认 bind `127.0.0.1` 不变；`BIND_HOST=0.0.0.0` 环境变量行为不变

## Risks & Dependencies

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| HTML 抽 Jinja2 时 `{{ var }}` 占位与 Python f-string 残留混淆，导致渲染错位 | 中 | 中 | Unit 1 contract test 覆盖每个路由 200 回归；Unit 4 单模板提交，每次跑 contract |
| Unit 6 re-export shim 漏掉某个 flat 模块名，外部脚本沉默失败 | 中 | 中 | `_REEXPORT_MAP` 用清单显式枚举（不是黑盒 `__getattr__`），AttributeError 消息含建议路径 |
| config 拆分静默改变 `save_config` 行为（历史已被烧过） | 中 | 高 | Unit 5 **必须**先在旧代码上固化 round-trip 测试 baseline，再开始拆 |
| Unit 3 拆 webui 时遗漏一个全局变量 / 模块级初始化，导致路由懒加载失败 | 中 | 中 | Unit 1 contract test 在 Unit 3 中每次小步搬迁后都跑；保留 `webui.py` 作为薄入口让 `python webui.py` 启动路径不变 |
| `_draft_lock` 行为细微差异（Unit 2 把单例锁变为 store 内部锁），导致 draft 并发回归 | 低 | 中 | Unit 2 测试场景显式包含两线程并发 update |
| APScheduler 在 Unit 3 重组后启动顺序错乱，导致已存在 schedule 不恢复 | 中 | 高 | Unit 3 用集成测试覆盖"已有 schedule-settings.json + app 启动 → 任务被注册" |
| 双击 `启动 WebUI.command` 路径硬编码 `webui.py` 在重构后断 | 低 | 高 | Unit 3 保留 `webui.py` 作为 80 行薄入口；Unit 3 验证步骤含手动启动一次 |
| 51 测试中某些用 `from backlink_publisher import X` 顶层 import（未在 grep 中显式可见）→ re-export shim 未覆盖 | 低 | 中 | Unit 6 完整测试套件运行作为最终验证 |
| Phase 1 PR 链很长（4 个 PR），合流冲突累积 | 中 | 中 | Phase 1 期间不接受其他大改动同时 land；每 unit 1-2 天落地 |
| 外部脚本 / 用户自定义 cron 任务依赖现有 import path | 低 | 低 | Re-export shim 保留至少 v0.3 + 1 个版本周期；变更在 README 记录 |

## Phased Delivery

### Phase 1（P0 — 1-2 周）
4 个 PR 顺序合入：Unit 1 (contract test) → Unit 2 (JsonStore) → Unit 3 (services + routes) → Unit 4 (Jinja2 templates)。期间不并行 config 改动。

### Phase 2（P1 — 1 周）
2 个 PR：Unit 5 (config 拆包) → Unit 6 (领域分包 + re-export)。两者均不动 webui，可与 Phase 1 在不同时间窗执行。

### Phase 3（P2 — 0.5 周，可选）
2 个 PR：Unit 7 (Publisher registry) → Unit 8 (Store protocol)。仅在加新平台或换存储后端的预案变实时再做。

## Documentation Plan

- Phase 1 结束：更新 `README.zh.md` 的"快速启动"中可能涉及 webui.py 位置的描述（实际 `python webui.py` 命令不变，因此可能无需改）
- Phase 2 结束：在 `README.zh.md` 增加一节"模块布局"，说明新目录结构；标注旧 import path 通过 re-export 继续可用，但建议新代码用新路径
- Phase 3 结束：在 `docs/` 新建 `publisher-extension.md`，说明加新平台的步骤（实现 `Publisher` + 注册）

## Operational / Rollout Notes

- 本仓库无生产部署 — local-first 工具，用户自行 `git pull && pip install -e .`
- 每个 PR 单独 land 单独跑 CI；不预期需要 feature flag
- 用户视角的 "rollout" = `git pull` 后双击 `启动 WebUI.command`，若失败可回滚到上一个 tag

## Sources & References

- **Origin document:** [docs/brainstorms/2026-05-18-architecture-health-refactor-requirements.md](../brainstorms/2026-05-18-architecture-health-refactor-requirements.md)
- Related code:
  - `webui.py` (4904 行)
  - `src/backlink_publisher/config.py` (1567 行)
  - `src/backlink_publisher/adapters/__init__.py:publish()` dispatcher
- Related solutions:
  - `docs/solutions/test-failures/inverted-negative-assertion-enshrined-config-save-data-loss-2026-05-14.md` — config sneaky-bug 历史
  - `docs/solutions/ui-bugs/webui-blocking-subprocess-and-missing-progress-feedback-2026-05-12.md` — webui 历史问题
- Prior refactor plans:
  - `docs/plans/2026-05-14-004-refactor-pr-landing-roadmap-plan.md` — refactor 类 plan 风格参考
  - `docs/plans/2026-05-11-001-feat-publisher-adapters-rewrite-plan.md` — adapters 重构历史
- External docs:
  - PEP 562 (lazy module `__getattr__`)
  - Flask Blueprint 标准文档（已在 Flask 依赖中，无需额外引入）
