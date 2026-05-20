---
title: AI banner image generation wired into the publish pipeline
type: feat
status: active
date: 2026-05-20
claims: {}
---

# AI banner image generation wired into the publish pipeline

## Overview

为每篇外链文在 `plan-backlinks` 阶段生成一张 1200×630 OG banner，内容由文案驱动。复用 operator 配置的 OpenAI 兼容图像 endpoint（`POST <base_url>/images/generations`），banner 下载到本地、上传到目标平台自己的 CDN，再嵌进 body。本 plan **不是** greenfield — 现有 `frw_image_gen.py` (28 行 stub) + `plan_backlinks/core.py:531-547` 已有半成品但有多处硬伤；plan 的核心是**收敛半成品**而不是从零搭建。

## Problem Frame

Operator 给的 base URL 是 `https://la-sealion.inaiai.com/v1`（同事自建的 OpenAI 兼容 LLM gateway），key 形如 `sk_xxx` 已经发放。当前实现：

1. `frw_image_gen.py` 硬编码 `https://api.frw.ai/v1/images/generations`（注释直说 "Assuming standard FRW interface"，实际从未跑通过）
2. `LLMProviderConfig.image_gen_api_key` 直接放 `config.toml`（违反 [[reference_telegraph_adapter_credential_rotation_pattern]] SEC-3，且 memory 显示这俩字段还是 local uncommitted 状态）
3. `body = f"![{title}]({cover_image_url})\n\n" + body` 直接 hotlink 外部 CDN — 几周后外链文图片必烂
4. `size: "1024x1024"` 不是 banner 该用的比例
5. 没有费用闸（image-gen 单价 ≫ text）
6. 没有 0 个测试 + 静吞所有 `Exception`
7. 跨 8 个 adapter 没考虑 per-platform media upload 差异（Telegraph 要 `uploadFile`、Hashnode/velog 要 GraphQL mutation、writeas 完全不支持）

**关键安全事项（必须先做）：用户在对话里贴出了一个真实 `sk_xxx` key。在 plan 落地前 operator 必须先去 la-sealion gateway 后台 revoke 该 key 并重新生成。Plan 里所有引用都不带真实值。**

## Requirements Trace

- R1. 在 `plan-backlinks` 阶段，依据 `(title, body)` 通过 LLM 生成英文 image prompt，再调 `<base_url>/images/generations` 生成 1200×630 banner（用户已选 OG 比例）
- R2. API key 走 0600 JSON 文件（`~/.config/backlink-publisher/frw-token.json`），**不**进 `config.toml`
- R3. Banner 下载到本地 `webui_store/banners/<YYYY-MM>/<sha>.png` 长期保存；发布时调每个平台的 media upload API 拿到平台自己的 CDN URL 再 embed（用户已选）
- R4. 每日 cap + 每次 `plan-backlinks` 运行 cap 两道闸；超出后 skip-生图 + warn（用户已选）
- R5. 8 个已注册 adapter（blogger / medium ×3 / telegraph / velog / ghpages / hashnode / writeas）任意一家 banner upload 失败 → 主体发布不应被牵连（degraded mode：body 照发，banner 跳过 + warn）
- R6. 现有 `frw_image_gen.py` stub 必须替换 — endpoint 硬编码到 `api.frw.ai` 在 main 上从未跑通
- R7. 测试覆盖：response shape × url/b64_json 两种返回 / size cap / 401 fail-loud / 429 retry / cap 命中 / storage 幂等

## Scope Boundaries

**Non-goals:**
- 不引入新的 image-gen provider 抽象（暂只支持 OpenAI 兼容 endpoint；fal.ai 异步 polling / Replicate webhook 等异步模型留待 v1.1）
- 不做 banner 编辑/裁剪/水印（直接落库 endpoint 返回的原图）
- 不做 banner A/B 选优（一次 prompt 只取 `n=1`）
- 不做反向追踪：banner 不参与 anchor footprint / lex-tie-break gate
- 不改 `report-anchors` / `footprint` / `phase0-seal` CLI（banner 只影响 plan + publish 两个阶段）
- 不做 Mastodon adapter 的 banner upload — Phase 4 才上 mastodon（[[project_channel_binding_dashboard_plan_006]]），本 plan 落地时还没注册
- WebUI 不做 banner re-generate 按钮（draft-queue 用户可重跑 plan-backlinks）

## Context & Research

### Relevant Code and Patterns

- `src/backlink_publisher/publishing/adapters/frw_image_gen.py` — 现有 stub，必须重写（endpoint 错 / 无 retry / 静吞 exception）
- `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py:156` — `generate_image_prompt(title, content)` 已实现并工作；本 plan 复用，不动
- `src/backlink_publisher/cli/plan_backlinks/core.py:531-547` — 现有的 cover-image 集成点；本 plan 把 `body = f"![](url)" + body` 改成 plan 输出 payload 新增 `banner` 字段
- `src/backlink_publisher/publishing/adapters/telegraph_api.py` — **credential-rotation 规范实现**（见 [[reference_telegraph_adapter_credential_rotation_pattern]]）：path resolver + fail-loud load + atomic write + flock + bootstrap-under-lock + verify 不探网 + 防御性 `.get()`；frw-token.json 完全 mirror 这套
- `src/backlink_publisher/config/types.py:74` — `LLMProviderConfig` dataclass，现有 `use_image_gen` / `image_gen_api_key` 字段（local-uncommitted）需要 deprecate 并迁移
- `src/backlink_publisher/config/parsers/llm.py` — `_parse_llm_anchor_provider`；新 image-gen 配置走独立 parser
- `src/backlink_publisher/publishing/adapters/__init__.py` — registry 模式（adding a platform = one `register()` line；本 plan 不加平台只加 banner upload protocol，遵循 R9 extension-readiness）
- `src/backlink_publisher/events/projector.py` — 事件投影（monolith ceiling 580），新加 `image_gen_invoked` / `image_gen_capped` 两个事件类型
- `webui_app/templates/settings.html:363-368` — 现有 image-gen UI 字段；需重组成「endpoint + key + 限额」三组
- `tests/conftest.py` — 4 个 autouse fixture（config 沙盒 / URL pass / content fetch pass / sockets blocked）；image-gen 测试默认走 mock，新增 `@pytest.mark.real_image_gen` opt-in marker 跟 `real_ssrf_check` / `real_content_fetch` 对齐

### Institutional Learnings

- [[reference_telegraph_adapter_credential_rotation_pattern]] — telegraph_api.py 是 repo 内 credential rotation 的 canonical reference；frw-token.json 完全 mirror 6 组件（path resolver / fail-loud load / atomic write / flock+jitter / orphan archive μs / rotate-under-lock）
- [[feedback_test_first_for_credential_rotation_misses_bootstrap]] — credential 路径必须枚举 **所有** state-mutation 站点（rotation + bootstrap + migration），每个写 `threading.Barrier` 测试，不能只测 rotation
- [[feedback_config_paths_must_respect_env_var]] — `~/.config` 任何常数路径必须每次走 function re-resolve `BACKLINK_PUBLISHER_CONFIG_DIR`，否则 conftest session-autouse fixture 覆盖不住会洗真实文件
- [[feedback_never_smoke_test_real_save_endpoints]] — Test Connection 按钮**不能**对运行中 webui 用空表单 curl；必须 `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/...` 起独立 webui
- [[feedback_webui_store_config_dir_frozen]] — `webui_store/__init__.py:32` 写死 `Path.home()`；banner 存储目录走 `webui_store._config_dir()` 函数（[[project_pr94_webui_store_env_isolation]] 已修），不要再造常量路径
- [[project_webui_llm_integration]] — WebUI LLM 双 AJAX toggle pattern（article gen / image gen）已搭好；本 plan 复用同一套 toggle UI 不重新发明
- [[project_pr99_config_subsection_preservation]] — `_canon_subsection_key` + `_preserve_unknown_sections` 已经为 LLM/image-gen 这种 nested 配置打好底；新加 `[image_gen]` 段会自动被 round-trip 保留
- [[feedback_invert_drift_check_when_invariant_becomes_dynamic]] — 注册新事件类型时模块级 drift 断言要小心，看 R9e 实战做反转
- [[feedback_plan_claims_gate]] — 本 plan 落 PR 时所有 `(file_path, ce:)` 必须 grandfather 之后实存（[[reference_plan_check_cli]]）；exit 9 deferred features 显式标注

### External References

- 不做外部 framework-docs 研究：endpoint 是 operator 同事自建的 OpenAI 兼容 gateway，OpenAI `/v1/images/generations` 契约就是规范；可执行的 docs URL 在 Unit 0 spike 中由 operator 提供
- Telegraph `uploadFile`: https://telegra.ph/api#uploadFile（已在 `telegraph_api.py` 调用过 — 复用）
- Hashnode `uploadMedia` mutation: 本 repo 已 land 的 hashnode adapter PR #102 已有引用
- velog `image_upload_url` mutation: 已在 `velog_graphql.py` 引用

## Key Technical Decisions

- **Endpoint contract**: OpenAI 兼容 `POST <base_url>/images/generations`，`Authorization: Bearer <token>` header（operator gateway 既然发 `sk_xxx` 就走 OpenAI 兼容形态）。Rationale: 不引入新 provider 抽象、复用现有 OpenAI client 经验、Unit 0 spike 验证后立即可写 adapter。
- **Banner size = 1200×630**: 用户选定。如果 endpoint 不支持非方比例（OpenAI 历史上 DALL-E 2 只支持 256/512/1024 方形），spike 阶段必须确认；不支持就退一档到 `1792x1024` 并在 README 注明 — 不要静默拉伸。
- **API key 落 0600 文件**: `~/.config/backlink-publisher/frw-token.json`，遵循 SEC-3 + telegraph_api.py 6 组件。不放 `config.toml`。`LLMProviderConfig.image_gen_api_key` 字段标记 deprecated 一个版本后删（warn on read）。
- **Banner artifact = bytes + sha + mime, 不是 url**: adapter 返回 `BannerArtifact` dataclass 含 `data: bytes / mime / source_url / prompt_sha`，**不**返回 external URL。下载在 adapter 内完成。Rationale: FRW gateway CDN 必然有 TTL；外链文寿命 ≫ CDN TTL。
- **Content-addressed storage**: 文件名 `<prompt_sha[:16]>.png`，同 prompt 不重新生图（每篇外链文的 prompt 已是 LLM 输出，本来就 deterministic 程度低，去重命中率不高，但够防御 plan-backlinks rerun 时重复扣费）。
- **Plan 输出 schema 加 `banner` 字段，不动 body**: `plan-backlinks` JSONL 每行新增 `banner: {path, alt, mime, sha} | null`。Body markdown **不**预置 `![](...)`。Rationale: 让 per-adapter 自己决定要不要嵌图 + 用平台 hosted URL 嵌（writeas 无 media upload → 退回 inline `![](source_url)` + warn；其他平台都用 platform-hosted URL）。
- **`BannerEmbedder` Protocol，不是 abstract class**: 每个 adapter 自带 `def embed_banner(self, artifact: BannerArtifact) -> str | None`（可选实现），dispatcher 在 publish 前调一次拿 platform URL 再 prepend 到 body。adapter 不实现该方法 = 没有 banner 能力 = 跳过 + warn。Rationale: 遵循 R9 extension-readiness，新平台加 banner 能力 = 一个方法，不动 dispatcher。
- **Daily cap + per-run cap via events**: `events/projector.py` 加 `image_gen_invoked` 事件；`image_gen.caps` 模块读「今日已发」+「本次 run 已发」两个数字与 config 比较。Rationale: 复用 events 子系统不再造 counter；事件可被 `report-anchors` 输出列「本月生图次数」无额外接线。
- **Auto-disable safety**: 连续 ≥ N 次 (默认 5) 401/5xx → 写一条 `image_gen_disabled_auto` 事件 + 当 run 内不再调 endpoint；不动 config.toml `use_image_gen` 标志（持久化由 operator 显式决定关）。Rationale: 防 key revoke 后无限重试烧配额；不能擅自改持久化配置（feedback_solutions_category_frontmatter 风格 — 不主动改用户配置）。
- **Degraded mode = always-on**: banner upload 失败 / endpoint 失败 / cap 命中 → **body 照发** + plan-result `banner_status = "skipped:<reason>"`。`config.toml [image_gen] strict = false` 默认。Operator 显式 `strict = true` 才让 banner 失败拒发整篇外链文。
- **CLI: `frw-login`**: 新增 entry-point + `frw-login` alias，遵循 PR #75 `velog-login` / [[project_medium_graphql_phase1_pr88]] `medium-login` 套路。提示 operator 粘 key，写入 0600 文件。
- **Monolith ceiling bump**: `plan_backlinks/core.py` 当前 1270；新增 banner 输出 + caps 调用估 +25-40 行 → bump 到 1320 with rationale ≥ 80 chars。Unit 4 同 PR 内 bump。

## Open Questions

### Resolved During Planning

- **是否新建 image-gen provider 抽象？** 不。OpenAI 兼容 endpoint 一家先打通，异步 polling 模型留 v1.1（参见 Scope Boundaries）
- **Banner 是 plan 阶段生成还是 publish 阶段生成？** Plan 阶段。Rationale: publish 阶段再生图 = 每次 retry/refallback 都重扣费；plan 阶段一次性产 artifact 后 publish 只复用本地 bytes
- **失败时是 body 照发还是整篇拒发？** 默认 body 照发（degraded mode），operator 显式 `strict = true` 才拒发
- **配置住在 `[llm_anchor_provider]` 段下还是新开 `[image_gen]` 段？** 新开 `[image_gen]`。Rationale: image-gen 与 LLM-text 完全可解耦（不同 endpoint / key / cap 全独立），混在一段会被 [[feedback_invert_drift_check_when_invariant_becomes_dynamic]] 式 partial drift 折磨

### Deferred to Implementation

- **OpenAI gateway 是否支持非方比例？** Unit 0 spike 决定。如果不支持，banner_size 字段限制成 `Literal["1024x1024", "1792x1024", "1024x1792"]` 并 README 说明
- **Response shape 是 `data[].url` 还是 `data[].b64_json`？** Unit 0 spike 输出；adapter 两个都实现，运行时分支
- **`prompt_sha` 算哪些字段？** Implementation-time：候选是 `sha256(title + body[:500])` 或 `sha256(prompt_text)`；倾向后者（LLM 已生成 prompt，prompt 本身就是 dedup key），Unit 3 落地时实验
- **Telegraph `uploadFile` 是否能直接吃 PNG bytes 而非 url？** 现有 `telegraph_api.py` 已经用过该 API；Unit 5 实现 telegraph embedder 时复读现有调用代码
- **WebUI Test Connection 用什么调用？** GET `<base_url>/models` 最便宜（OpenAI 兼容 gateway 大概率支持）；Unit 0 spike 顺便确认。如果不支持，退而调一个 1-token 的 `/chat/completions` 探活，**不要**调 `/images/generations`（那会真扣费）

## High-Level Technical Design

> *This illustrates the intended approach and is directional guidance for review, not implementation specification. The implementing agent should treat it as context, not code to reproduce.*

```text
plan-backlinks
   │
   ├─ build (title, body) 已有
   ├─ generate_image_prompt(title, body) → english prompt   [llm_anchor_provider, 已有]
   ├─ image_gen.caps.check()                                  [新, Unit 3]
   │     ├─ daily_cap reached?  → skip + event
   │     └─ per_run_cap reached? → skip + event
   ├─ image_gen.adapter.generate(prompt, size)                [新, Unit 2]
   │     ├─ POST <base_url>/images/generations
   │     ├─ if data[].url     → GET that url → bytes
   │     ├─ if data[].b64_json → base64 decode → bytes
   │     └─ retry/backoff on 5xx,429; fail-loud on 401
   ├─ image_gen.storage.save(bytes, prompt_sha) → Path        [新, Unit 3]
   └─ output JSONL line += {banner: {path, alt, mime, sha}}   [改, Unit 4]

publish-backlinks
   │
   ├─ read JSONL line; if banner is None → 直接现有流程
   └─ if banner exists:
         ├─ adapter.embed_banner?(BannerArtifact) → platform_url  [新 Protocol, Unit 5]
         │     ├─ telegraph: uploadFile     → telegra.ph CDN
         │     ├─ hashnode:  uploadMedia    → hashnode CDN
         │     ├─ velog:     image_upload_url
         │     ├─ ghpages:   commit /assets/banners/<sha>.png → raw.githubusercontent.com
         │     ├─ writeas:   no upload      → fall back inline source_url + warn
         │     ├─ blogger:   images.upload
         │     └─ medium-*:  inline (API auto-upload) or Playwright file_chooser
         ├─ on success: body = f"![{alt}]({platform_url})\n\n" + body
         └─ on failure: if strict → raise; else body 不动 + emit banner_skipped event
```

**Decision matrix — per platform banner mode:**

| Platform | Upload mode | Fallback if upload fails |
|---|---|---|
| telegraph | `uploadFile` REST | warn-skip (strict=false) / fail (strict=true) |
| hashnode | `uploadMedia` GraphQL | warn-skip / fail |
| velog | `image_upload_url` GraphQL | warn-skip / fail |
| ghpages | git commit `/assets/banners/<sha>.png` | warn-skip / fail |
| writeas | **none** (no API support) | inline `![](source_url)` w/ warn |
| blogger | Blogger API `media.insert` | warn-skip / fail |
| medium (API) | inline markdown — Medium auto-uploads | already inline |
| medium (Brave/Browser) | Playwright `set_input_files` on cover-image picker | warn-skip / fail |

## Implementation Units

- [ ] **Unit 0: Operator endpoint spike (BLOCKER)**

**Goal:** 用 operator 提供的 base URL `https://la-sealion.inaiai.com/v1` + revoked-and-rotated key 确认实际 endpoint 契约，避免重蹈现有 stub「Assuming standard FRW interface」覆辙。

**Requirements:** R1, R6

**Dependencies:** Operator 必须先 revoke 已暴露的 key + 在 gateway 后台重发一把新 key（Spike 前提）

**Files:**
- Create: `docs/brainstorms/2026-05-20-banner-image-gen-spike.md`

**Approach:**
- Operator 本地执行（不写到 repo 任何 secret）：
  - `curl -H "Authorization: Bearer $KEY" https://la-sealion.inaiai.com/v1/models` → 拿支持的模型列表
  - `curl -H "Authorization: Bearer $KEY" -X POST https://la-sealion.inaiai.com/v1/images/generations -d '{"model":"<from /models>","prompt":"test","size":"1200x630","n":1}'` → 验非方比例
  - 同上但 `"size":"1024x1024"` → fallback baseline
  - 同上但 `"response_format":"b64_json"` → 验是否支持 base64 返回
- 记录到 spike 文档：
  - 实际可用的 model 名（写进 config.example.toml `image_gen.model` 默认值）
  - 实际可用的 size 列表（决定 Unit 2 schema Literal 类型）
  - Response shape：`data[].url` or `data[].b64_json`，URL TTL（如果 url 模式）
  - Rate limit headers (`x-ratelimit-*`) 是否存在 → 决定 Unit 3 daily cap 是 client-side 还是依赖 server hint
  - 401 / 429 / 5xx 错误响应体格式 → 决定 Unit 2 fail-loud message
- Spike 文档结论作为 Unit 2-5 的 input

**Execution note:** 这是纯 operator 任务，agent 只负责记录 + 起草 spike 文档骨架。不要在 CI 跑这一 unit。

**Test scenarios:**
Test expectation: none — pure operator research unit, no code change.

**Verification:**
- `docs/brainstorms/2026-05-20-banner-image-gen-spike.md` 存在且填了 4 个字段：model name / supported sizes / response shape / error envelope
- Unit 2 PR 描述里引用本 spike 文档

---

- [ ] **Unit 1: 0600 token file + frw-login CLI + config schema**

**Goal:** 把 image-gen API key 从 `config.toml` 迁出，落到 `~/.config/backlink-publisher/frw-token.json`（0600），并提供 `frw-login` CLI 交互写 token。

**Requirements:** R2

**Dependencies:** Unit 0（决定 token 字段是否需要带 `base_url` / `model` — spike 后可能 token file 里就放 key，base_url/model 留 config.toml）

**Files:**
- Create: `src/backlink_publisher/cli/frw_login.py`
- Create: `tests/test_frw_login.py`
- Create: `tests/test_image_gen_token_rotation.py`
- Modify: `src/backlink_publisher/config/types.py` — 新增 `@dataclass(frozen=True) ImageGenConfig(base_url, model, banner_size, daily_cap, per_run_cap, timeout_s, max_retries, strict, auto_disable_threshold)`，**不**含 api_key 字段
- Modify: `src/backlink_publisher/config/types.py` — `Config` 加 `image_gen: ImageGenConfig | None = None`
- Modify: `src/backlink_publisher/config/parsers/` — 新建 `image_gen.py` 含 `_parse_image_gen()`
- Modify: `src/backlink_publisher/config/loader.py` — wire `_parse_image_gen` 进 `load_config`
- Modify: `src/backlink_publisher/config/__init__.py` — re-export `ImageGenConfig`
- Modify: `src/backlink_publisher/config/types.py:74` `LLMProviderConfig` — `image_gen_api_key` 字段保留但 deprecated（read 时 warn，下版本删）；`use_image_gen` 字段保留作为「整体 toggle」语义，**不**复用作 image-gen on/off — 该作用迁到 `image_gen` 段是否存在
- Modify: `pyproject.toml` — `[project.scripts]` 加 `frw-login = "backlink_publisher.cli.frw_login:main"`
- Modify: `config.example.toml` — 新增 `[image_gen]` 段示例（base_url, model, banner_size, daily_cap=50, per_run_cap=10, strict=false）
- Modify: `src/backlink_publisher/_util/secrets.py` — 如果该 module 已存在，加 `frw_token_path()` / `load_frw_token()` / `rotate_frw_token()` 三函数 mirror 现有 telegraph helpers；如果不存在则把 telegraph_api.py 里的 6 组件复制重命名（**复制** 不抽公共，避免抽象时机过早 — 等第三个 token 文件出现再 lift）

**Approach:**
- 完全 mirror `telegraph_api.py` 的 6 组件 credential-rotation pattern（path resolver / fail-loud load / atomic write / flock+jitter / orphan archive μs / rotate-under-lock），见 [[reference_telegraph_adapter_credential_rotation_pattern]]
- Path resolver 走 `_config_dir()` 函数每次 re-resolve `BACKLINK_PUBLISHER_CONFIG_DIR`（[[feedback_config_paths_must_respect_env_var]]）
- `frw-login` 行为镜像 `velog-login` / `medium-login`：interactive prompt → 写入 0600 文件 + 打印 banner stderr 提示 path
- LLM provider config 的 `image_gen_api_key` 字段在 `_parse_llm_anchor_provider` 里：若 toml 有值 → `warnings.warn(DeprecationWarning, "image_gen_api_key in config.toml is deprecated; use frw-login to store in 0600 file")` 同时 still parse（grandfather）。下一版本（v1.1）删字段
- `Config` 后向兼容：`config.image_gen is None` 表示 image-gen 完全未配置；`use_image_gen` toggle 仅在 `image_gen != None` 且 frw-token.json 存在时生效

**Execution note:** test-first — [[feedback_test_first_for_credential_rotation_misses_bootstrap]] 提示先写 rotation + bootstrap + migration 三个 `threading.Barrier` 测试，再写代码。

**Patterns to follow:**
- `src/backlink_publisher/publishing/adapters/telegraph_api.py` 凭证 6 组件
- `src/backlink_publisher/cli/velog_login.py` / `src/backlink_publisher/cli/medium_login.py` — alias banner + interactive prompt 模板
- `src/backlink_publisher/cli/__main__.py` — `python -m backlink_publisher frw-login` 入口（[[feedback_python_dash_m_needs_main_module_after_pkg_split]]）

**Test scenarios:**
- Happy path — `frw-login` 写 `frw-token.json`，文件存在 + 权限 0600 + 内容是 `{"api_key": "..."}` JSON
- Happy path — `load_frw_token()` 在文件存在时返回 dict；fail-loud `RuntimeError` 在文件不存在时（NOT silently None — 区别于 telegraph token 的 None-default，因为 image-gen 是 opt-in，调用方应已先查 `config.image_gen is not None`）
- Edge case — `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/x` 时 `frw_token_path()` 返回 `/tmp/x/frw-token.json`，**不**读 `~/.config/`（防 [[feedback_config_paths_must_respect_env_var]] 同类事故）
- Edge case — `frw_token_path()` 父目录不存在 → `frw-login` 自动 `mkdir -p` 同时 chmod 0700（mirror PR #99 [[project_pr99_config_subsection_preservation]] 的 0700 parent chmod）
- Edge case — 文件存在但权限是 0644 → load 时 warn（不 fail），retry 0600 chmod
- Concurrent — `threading.Barrier(3)` 同时 3 个 rotate_frw_token() → 通过 flock 串行，最终文件只有最后一个写入者的内容，文件锁释放干净（无 stale `.lock`）
- Concurrent — `Barrier(2)` 一个 bootstrap（首次写）+ 一个 rotate（已存在覆盖），两边 flock 同 path 串行
- Migration — `config.toml` 含 deprecated `[llm_anchor_provider] image_gen_api_key = "sk_..."` → `load_config` warns DeprecationWarning + 仍读到 `LLMProviderConfig.image_gen_api_key`（grandfather）
- Migration — 同时有 deprecated toml field + `frw-token.json` → 0600 文件**赢**（precedence: 0600 > toml）
- Error path — `frw-token.json` JSON 解析失败 → fail-loud `RuntimeError("frw-token.json malformed at <path>")`
- Error path — `frw-login` 输入空 key → `UsageError` exit 1（**不**用 argparse choices=，[[feedback_argparse_choices_vs_usage_error_exit_code_clash]]）
- Integration — `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/sandbox python -m backlink_publisher frw-login` 模拟 stdin 输入 → 文件落 `/tmp/sandbox/frw-token.json` 且 0600

**Verification:**
- `frw-login --help` 出现并显示 SOP
- `pytest tests/test_frw_login.py tests/test_image_gen_token_rotation.py` 全过
- `grep -r "image_gen_api_key" src/backlink_publisher/cli/` — 除 deprecation warning 外不再使用该字段
- `config.example.toml` 新加的 `[image_gen]` 段被 `_preserve_unknown_sections` round-trip 保留（依赖 [[project_pr99_config_subsection_preservation]] 已 land）
- `ImageGenConfig` 在 `from backlink_publisher.config import ImageGenConfig` 可 import（re-export 正确）

---

- [ ] **Unit 2: OpenAI-compatible image-gen adapter (rename frw_image_gen → image_gen)**

**Goal:** 重写现有 28 行 stub 为完整的 OpenAI 兼容 `/images/generations` adapter，返回 `BannerArtifact(data: bytes, mime, source_url, prompt_sha)`。

**Requirements:** R1, R6

**Dependencies:** Unit 0（endpoint contract）+ Unit 1（`ImageGenConfig` + `load_frw_token`）

**Files:**
- Delete (rename): `src/backlink_publisher/publishing/adapters/frw_image_gen.py`
- Create: `src/backlink_publisher/publishing/adapters/image_gen/__init__.py`
- Create: `src/backlink_publisher/publishing/adapters/image_gen/adapter.py` — `class ImageGenAdapter` + `generate(prompt: str) -> BannerArtifact`
- Create: `src/backlink_publisher/publishing/adapters/image_gen/types.py` — `BannerArtifact` dataclass
- Create: `tests/test_image_gen_adapter.py`
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py:26` — import 改 `from ...image_gen.adapter import ImageGenAdapter`
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py:531-547` — 改为：拿 prompt → 调 `ImageGenAdapter.generate` → 把 `BannerArtifact` 留作 plan output payload（**不**预置 markdown 到 body）；具体接线在 Unit 4
- Modify: `monolith_budget.toml` — 不动 ceiling（Unit 4 才 bump）

**Approach:**
- `ImageGenAdapter(base_url, model, banner_size, api_key, timeout_s, max_retries)`
- `generate(prompt)` 流程：
  1. POST `<base_url>/images/generations` body `{model, prompt, size=banner_size, n=1, response_format="url"|"b64_json"}`（按 Unit 0 spike 输出选）
  2. `retry_transient_call` (复用 `publishing/adapters/retry.py`) — 5xx/429 重试，401/400 fail-loud
  3. Response：
     - `data[].url` 模式 → 二次 `httpx.get(url, timeout)` 拿 bytes；MIME 嗅探（`python-magic` 或简单 PNG/JPEG/WebP magic bytes 比对）
     - `data[].b64_json` 模式 → `base64.b64decode` 直接拿 bytes
  4. Response size cap: 默认 5 MB，超 raise `ExternalServiceError("banner exceeds 5 MB")`
  5. 计算 `prompt_sha = sha256(prompt).hexdigest()[:16]`
  6. 返回 `BannerArtifact(data, mime, source_url, prompt_sha)`
- Auth header: `Authorization: Bearer <api_key>`（OpenAI 标准）
- 不实现 SSRF guard — base_url 是 operator 自配的 trusted endpoint，且 conftest socket-block 已保护测试；real_image_gen marker 才走真网
- 错误分类（structured-error）：
  - `401` → `RuntimeError("image-gen 401: rotate token via frw-login")` (fail-loud, not retryable)
  - `429` → `_is_retryable=True`，retry up to max_retries
  - `5xx` → retryable
  - 4xx (non-401) → fail-loud
  - timeout → retryable
  - response shape miss (`"data" not in resp_json`) → fail-loud with response excerpt

**Execution note:** 复用 `retry.py` `retry_transient_call` + `_is_retryable` 模式（已被 llm_anchor_provider.py 使用）；不重新发明 retry。

**Patterns to follow:**
- `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py:145-150` — `retry_transient_call(lambda: self._post_chat_completions(body), is_retryable=_is_retryable, adapter="...")` 调用形态
- `src/backlink_publisher/publishing/adapters/retry.py` — `_is_retryable` 实现
- `src/backlink_publisher/_util/errors.py` — `ExternalServiceError` / `DependencyError` / `RuntimeError` 选用

**Test scenarios:**
- Happy path (url mode) — mock POST 返回 `{"data": [{"url": "https://cdn/banner.png"}]}` + mock GET 返回 PNG bytes → `BannerArtifact.data` = bytes / `.mime` = `image/png` / `.source_url` 匹配 / `.prompt_sha` = sha256(prompt)[:16]
- Happy path (b64 mode) — mock POST 返回 `{"data": [{"b64_json": "<base64 of 1px png>"}]}` → 同上但 `source_url` = None
- Happy path — large prompt (>1000 chars) 不影响调用（prompt 是 LLM 输出的 < 50 词，但 adapter 不该有长度上限）
- Edge case — `n` 默认是 1；response `data` 长度 == 1
- Edge case — response 是 PNG 大小恰好 5 MB → 不 raise（恰好等于阈值）；5 MB + 1 → raise
- Edge case — MIME 嗅探：PNG magic `\x89PNG` / JPEG `\xff\xd8\xff` / WebP `RIFF...WEBP` 三种正确识别；未知 magic → fail-loud
- Error path — POST 返回 401 + 任意 body → `RuntimeError` 含 "frw-login" 字样（指引 operator rotate），**不** retry
- Error path — POST 返回 429，前 2 次 5xx 后 1 次 200 → retry 3 次后成功；max_retries=2 时 retry 2 次后 raise `ExternalServiceError`
- Error path — POST 200 但 response missing `"data"` key → fail-loud `RuntimeError` 带 response 前 200 字符
- Error path — url-mode 二次 GET 返回 404 → `ExternalServiceError("source_url unreachable")`，**不**回退到 source_url 字符串（必须有 bytes 或失败）
- Error path — url-mode 二次 GET 返回 6 MB content → raise size cap exceeded
- Error path — request timeout (httpx.TimeoutException) → retryable，超 max_retries 后 raise
- Integration (real_image_gen marker) — `@pytest.mark.real_image_gen` 测试调真 `la-sealion.inaiai.com/v1`（仅 operator 本地 + 显式 `pytest -m real_image_gen` 跑）

**Verification:**
- `pytest tests/test_image_gen_adapter.py` 全过（默认 socket-blocked，全 mock）
- `grep frw_image_gen src/ tests/` 0 命中（旧 stub 完全替换）
- `python -m py_compile src/backlink_publisher/publishing/adapters/image_gen/*.py` 通过
- `radon raw -s src/backlink_publisher/publishing/adapters/image_gen/adapter.py` < 200 SLOC（新文件，可不入 monolith_budget.toml）

---

- [ ] **Unit 3: Content-addressed storage + daily/per-run caps**

**Goal:** Banner bytes 落本地 `webui_store/banners/<YYYY-MM>/<sha>.png`；events 子系统记 image-gen 调用计数；config 闸值命中 → skip + warn。

**Requirements:** R3, R4

**Dependencies:** Unit 2（`BannerArtifact`）+ Unit 1（`ImageGenConfig.daily_cap` / `per_run_cap`）

**Files:**
- Create: `src/backlink_publisher/publishing/adapters/image_gen/storage.py`
- Create: `src/backlink_publisher/publishing/adapters/image_gen/caps.py`
- Create: `tests/test_image_gen_storage.py`
- Create: `tests/test_image_gen_caps.py`
- Modify: `src/backlink_publisher/events/projector.py` — 新增事件类型 `image_gen_invoked` / `image_gen_capped` / `image_gen_disabled_auto`（注意 [[feedback_invert_drift_check_when_invariant_becomes_dynamic]]：projector.py 当前 ceiling 580，新增 3 事件类型 ≈ +60 行 → 同 PR 改成 ceiling 640 with rationale ≥ 80 chars）
- Modify: `monolith_budget.toml` — `events/projector.py` 580 → 640 with rationale: "Unit 3 adds three image_gen_* event types + handlers + 3 dataclasses; estimated +60 SLOC over existing 580 baseline."
- Modify: `webui_store/banners/.gitkeep` — create empty (webui_store/ 整体 gitignored，但保留目录骨架)

**Approach:**
- `storage.save(artifact: BannerArtifact) -> Path`:
  - Path = `_config_dir() / "banners" / "YYYY-MM" / f"{artifact.prompt_sha}.{ext}"`（ext 从 mime 推：image/png → png / image/jpeg → jpg / image/webp → webp）
  - 注意：banner 存到 `_config_dir()` 而**不是** `webui_store/`，因为 CLI 调用方（plan-backlinks）可能在 webui 之外跑（pure CLI 用户），且 `_config_dir()` 已尊重 env var
  - Idempotent：如果文件已存在且 size 匹配 → 直接返回 path（同 prompt 不重新写）
  - Atomic write: `Path.write_bytes(data)` 走 `tmp + os.replace` 模式（mirror config writer pattern）
- `storage.path_for(prompt_sha: str) -> Path | None`：纯查询，不 IO
- `caps.check_caps(events_log, config: ImageGenConfig, run_counter: int) -> CapDecision`:
  - 读今天的 `image_gen_invoked` 事件计数（events_log API 应该已有 query helper）
  - if today_count >= daily_cap → return `CapDecision(allowed=False, reason="daily_cap")`
  - if run_counter >= per_run_cap → return `CapDecision(allowed=False, reason="per_run_cap")`
  - else return `CapDecision(allowed=True, reason=None)`
- `caps.record_invocation(events_log, prompt_sha)` → append `image_gen_invoked` event with `{prompt_sha, ts}`
- `caps.record_cap_hit(events_log, reason)` → append `image_gen_capped` event
- Auto-disable safety: `caps.AutoDisableTracker(threshold=5)` 类实例化 per run，记 consecutive 401/5xx；超阈 → `record_auto_disable_event` + `tracker.disabled = True`；后续 generate 调用前先查 `tracker.disabled`

**Patterns to follow:**
- `src/backlink_publisher/events/projector.py` — 现有事件类型注册 + handler 套路
- `src/backlink_publisher/_util/markdown.py` 当前 ceiling 320 — 文件原子写参考
- `src/backlink_publisher/config/writer.py` — atomic write pattern

**Test scenarios:**
- Happy path — `save(artifact)` 第一次 → 文件存在 + bytes 完整 + path 返回值正确
- Happy path — `save(artifact)` 第二次同 sha → 不重新写（mtime 不变）+ 返回同 path
- Edge case — `BACKLINK_PUBLISHER_CONFIG_DIR` 变了 → save 写到新目录，不漏到 `~/.config/`（[[feedback_config_paths_must_respect_env_var]]）
- Edge case — 父目录不存在 → 自动 `mkdir -p` 0700
- Edge case — 文件已存在但 corrupted（size 不匹配 artifact.data 长度）→ 覆盖重写
- Edge case — 文件名月份切换（YYYY-MM）→ 跨月正确分目录
- Error path — disk full / `OSError` → fail-loud raise（**不**静吞）
- Error path — atomic write 中 process 被 kill → tmp 文件残留 但目标文件未损坏 (next save 通过 tmp+replace 自然清理或新生)
- Happy path (caps) — daily_cap=10, today_count=9, per_run=0 → allowed
- Happy path (caps) — daily_cap=10, today_count=10 → not allowed, reason="daily_cap"
- Edge case (caps) — daily_cap=10, today_count=11 (somehow over) → not allowed (>=, not ==)
- Edge case (caps) — daily_cap=10, per_run_cap=3, today_count=2, run_counter=3 → not allowed, reason="per_run_cap"
- Edge case (caps) — cap = 0 → 总是 not allowed (effectively disabled)
- Edge case (caps) — config.image_gen=None → caps 模块不该被调用；guard 在 plan_backlinks/core.py 里
- Integration — `record_invocation` 三次 + `check_caps` → today_count=3 反映
- Integration — auto-disable: 5 次连续 record_failure → tracker.disabled=True；第 6 次 `caps.allowed_by_auto_disable()` 返回 False；中间穿插 1 次 success → counter reset 到 0
- Integration — events log durable: 跨 process 重启 events_log 还能查到今天的 `image_gen_invoked` 计数（依赖 events/projector.py 已有持久化）

**Verification:**
- `pytest tests/test_image_gen_storage.py tests/test_image_gen_caps.py` 全过
- `python -m pytest tests/test_no_monolith_regrowth.py -k projector` 通过（ceiling 640 已正确 bump）
- `radon raw -s src/backlink_publisher/events/projector.py` <= 640

---

- [ ] **Unit 4: plan-backlinks integration (banner as separate payload field, NOT inline markdown)**

**Goal:** `plan-backlinks` 在生成每条 plan 时，拿 prompt → check caps → 调 adapter → save bytes → 在 JSONL 输出行新增 `banner: {path, alt, mime, sha} | null` 字段。Body **不**预置 `![](url)`。

**Requirements:** R1, R3, R5

**Dependencies:** Unit 2 + Unit 3

**Files:**
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py:531-547` — 替换 `cover_image_url` 临时变量为 `banner_artifact: BannerArtifact | None`；不再 `body = f"![](...)" + body`；改为 plan output row 新增 `banner = {path: str(saved_path), alt: title, mime, sha}` 或 `banner = None`
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py` — 增加 per-run `image_gen_run_counter` 计数器（dict scoped to `plan_main()` invocation）
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py` — `caps.check_caps` 失败 → log warn + skip banner，继续 plan-row 输出（degraded mode）
- Modify: `src/backlink_publisher/cli/plan_backlinks/core.py` — 错误分类：401 / cap_hit / generate_failed → 三种 `banner_status` 记录到 `banner = {path: None, status: "<reason>"}` 让下游 `publish-backlinks` 能感知
- Modify: `monolith_budget.toml` — `cli/plan_backlinks/core.py` 1270 → 1320 with rationale ≥ 80 chars: "Unit 4 inlines image_gen ImageGenAdapter wiring, caps gate, per-run counter, banner artifact JSONL emission, and three banner_status branches into plan_main; estimated +50 SLOC over current 1270 baseline."
- Modify: `src/backlink_publisher/cli/validate_backlinks.py` — schema 加 optional `banner` field validation；如果 banner.path 设了则文件必须存在（fail validate 1 行）
- Modify: `tests/test_plan_backlinks.py` — 既有 golden 测试更新（加 `banner: null` 默认）
- Create: `tests/test_plan_backlinks_banner.py` — banner 注入路径专项

**Approach:**
- 流程（mirror Unit 2 design）：
  ```
  if config.image_gen and frw_token_exists():
      tracker = caps.AutoDisableTracker(threshold=config.image_gen.auto_disable_threshold)
      run_counter = 0
      for seed in seeds:
          ... 既有 plan logic ...
          banner = None
          if config.image_gen.use_image_gen and not tracker.disabled:
              cap_decision = caps.check_caps(events_log, config.image_gen, run_counter)
              if not cap_decision.allowed:
                  banner = {"path": None, "status": f"capped:{cap_decision.reason}"}
                  caps.record_cap_hit(events_log, cap_decision.reason)
              else:
                  try:
                      prompt = llm_p.generate_image_prompt(title, body)
                      artifact = adapter.generate(prompt)
                      saved = storage.save(artifact)
                      banner = {"path": str(saved), "alt": title, "mime": artifact.mime, "sha": artifact.prompt_sha}
                      caps.record_invocation(events_log, artifact.prompt_sha)
                      run_counter += 1
                  except RuntimeError as e:  # 401, fail-loud
                      tracker.record_failure()
                      banner = {"path": None, "status": "auth_failed"}
                  except ExternalServiceError as e:
                      tracker.record_failure()
                      banner = {"path": None, "status": "gen_failed"}
                  # NOTE: degraded mode — never raise；strict mode 在 publish-backlinks 决定
          row["banner"] = banner
  ```
- `banner: None` (config.image_gen=None or use_image_gen=False) vs `banner: {path: None, status: "..."}` (尝试了但失败) 是不同状态 — 让 publish 阶段区分日志
- `events_log` 实例从既有 `plan_main()` 已有的 events context 拿（已存在）

**Patterns to follow:**
- `src/backlink_publisher/cli/plan_backlinks/core.py:489-521` — 现有 LLM article gen 的「try-except + 静默 fallback」模板（本 unit 是 mirror 但增加 status 字段）
- `src/backlink_publisher/cli/validate_backlinks.py` — optional field validation 套路

**Test scenarios:**
- Happy path — `image_gen` 配置完整 + 1 row → JSONL output 含 `banner.path` 指向真实文件 + 文件存在 + `banner.alt` == title
- Happy path — 同 prompt 跑两次 plan-backlinks → 第二次复用 storage cache，文件 mtime 不变
- Edge case — `config.image_gen = None` → `banner = None`；不 import image_gen 模块的 adapter（lazy import 避免无关 import 失败）
- Edge case — `use_image_gen = False` → `banner = None`（跟 image_gen=None 输出一致）
- Edge case — daily_cap=2，3 个 seed 跑 plan → row 1/2 有 banner，row 3 `banner.status = "capped:per_run_cap"`（per_run 先触发）；如果 per_run > daily 则 row 3 `capped:daily_cap`
- Edge case — `frw-token.json` 缺失 + `config.image_gen` 配置了 → load_frw_token raise → plan_main fail-loud 在启动前（**不**等到第一个 seed 才 surprise）
- Error path — adapter 401 第一次 → `banner.status="auth_failed"`，tracker counter=1；连续 5 次 → tracker.disabled=True，第 6 个 seed `banner = null` (类似 use_image_gen=False)
- Error path — adapter 超时 → `banner.status="gen_failed"`，body 不变
- Error path — adapter 成功但 storage save 失败（disk full）→ `banner.status="storage_failed"` (Unit 3 fail-loud 但 plan_main 必须包一层捕获，否则整 plan crash)
- Integration — validate-backlinks 读到 banner.path 不存在的 row → exit 1 + 报 missing banner file
- Integration — validate-backlinks 读到 banner.path 存在 + size==0 → exit 1
- Integration — validate-backlinks 读到 banner=null → 通过
- Integration — events log 有 `image_gen_invoked` 计数 == 成功生图数（capped/失败的不计）

**Verification:**
- `pytest tests/test_plan_backlinks.py tests/test_plan_backlinks_banner.py tests/test_validate_backlinks.py` 全过
- `pytest tests/test_no_monolith_regrowth.py` 通过（ceiling 1320 已 bump）
- 现有 golden snapshot 测试（如果有）已更新带 `banner: null`
- `python -m backlink_publisher plan-backlinks < seeds.jsonl > plans.jsonl` 烟测：plans.jsonl 每行含 `banner` 字段

---

- [ ] **Unit 5: BannerEmbedder protocol + per-adapter implementations**

**Goal:** `publish-backlinks` 读到 `banner.path` 时，调当前 adapter 的 `embed_banner()` 拿平台 hosted URL，把 `![alt](platform_url)` prepend 到 body 后再发布。Adapter 未实现 = 跳过 + warn。

**Requirements:** R3, R5

**Dependencies:** Unit 4

**Files:**
- Create: `src/backlink_publisher/publishing/adapters/banner_embed.py` — `class BannerEmbedder(Protocol): def embed_banner(self, artifact_path: Path, alt: str) -> str | None`
- Modify: `src/backlink_publisher/publishing/adapters/telegraph_api.py` — 加 `embed_banner` 方法走 `https://telegra.ph/upload`
- Modify: `src/backlink_publisher/publishing/adapters/hashnode.py` — 加 `embed_banner` 方法走 `uploadMedia` GraphQL
- Modify: `src/backlink_publisher/publishing/adapters/velog_graphql.py` — 加 `embed_banner` 方法走 `image_upload_url` mutation（参考 [[project_velog_adapter_pr75]]）
- Modify: `src/backlink_publisher/publishing/adapters/ghpages.py` — 加 `embed_banner` 方法：commit banner 到 repo 的 `/assets/banners/<sha>.<ext>`，返回 `https://raw.githubusercontent.com/<owner>/<repo>/<branch>/assets/banners/<sha>.<ext>`
- Modify: `src/backlink_publisher/publishing/adapters/writeas.py` — 加 `embed_banner` 方法返回 `None`（无 API），让 dispatcher 走 inline `![](source_url)` fallback + warn（参见 Unit 4 `banner.path` 为 None 时跳过；但 banner 仍有 `sha` 可拉本地文件 + 没有 source_url 时 warn-skip）
- Modify: `src/backlink_publisher/publishing/adapters/blogger_api.py` — 加 `embed_banner` 方法走 `media.insert` Blogger API
- Modify: `src/backlink_publisher/publishing/adapters/medium_api.py` — `embed_banner` 返回 `None`（API 直接接收 markdown content，inline 已经被 Medium 自动上传 — adapter 在 publish 时把 `![alt](file:///<local_path>)` 替换成 base64 data URI 或直接 inline 让 Medium API 拉外链）；具体策略 spike 时验
- Modify: `src/backlink_publisher/publishing/adapters/medium_browser.py` — `embed_banner` 走 Playwright `page.set_input_files()` 选 cover-image picker 上传 local file
- Modify: `src/backlink_publisher/publishing/adapters/medium_brave.py` — 不实现 `embed_banner`（AppleScript 路径无法可靠上传文件）→ 返回 `None` + warn-skip
- Modify: `src/backlink_publisher/cli/publish_backlinks.py` — dispatcher 在调用 adapter 前：if row.banner.path → 调 `adapter.embed_banner(Path(banner.path), banner.alt)`；返回 URL → `body = f"![{alt}]({platform_url})\n\n" + body`；返回 None → warn + body 不变（除非 strict）
- Create: `tests/test_banner_embed_telegraph.py` / `_hashnode.py` / `_velog.py` / `_ghpages.py` / `_writeas.py` / `_blogger.py` / `_medium.py`
- Create: `tests/test_publish_with_banner.py` — cross-adapter integration

**Approach:**
- `BannerEmbedder` 是 `typing.Protocol`（duck-typing）；adapter 不实现 = `hasattr(adapter, "embed_banner") is False` → dispatcher 跳过
- 实现方法 per adapter 的 contract：
  ```python
  def embed_banner(self, artifact_path: Path, alt: str) -> str | None:
      # 上传本地 file → 返回 platform-hosted URL
      # 不可恢复失败 → raise；可降级失败 → return None
  ```
- Dispatcher 逻辑（`publish_backlinks.py`）：
  ```
  if row.banner and row.banner.path:
      try:
          platform_url = adapter.embed_banner(Path(row.banner.path), row.banner.alt) if hasattr(adapter, "embed_banner") else None
      except Exception as e:
          if config.image_gen.strict:
              raise
          log.warn(f"banner embed failed: {e}; publishing without banner")
          platform_url = None
      if platform_url:
          payload["body"] = f"![{row.banner.alt}]({platform_url})\n\n" + payload["body"]
      elif row.banner.source_url:  # writeas fallback
          payload["body"] = f"![{row.banner.alt}]({row.banner.source_url})\n\n" + payload["body"]
          log.warn(f"using ephemeral source_url for banner ({adapter.platform}); link may rot")
  ```

**Patterns to follow:**
- `src/backlink_publisher/publishing/adapters/telegraph_api.py` 已有 `uploadFile` 调用代码 — 复用
- `src/backlink_publisher/publishing/adapters/velog_graphql.py` 已有 `image_upload_url` mutation — 复用
- `src/backlink_publisher/publishing/adapters/hashnode.py` 已有 GraphQL client — 加 mutation 即可
- R9 extension-readiness ([[project_r9_plan_recovered]]) — 不动 `__init__.py` / `registry.py` / `schema.py`；新 adapter 加 banner 能力 = adapter 文件内一个方法

**Test scenarios:**
- Happy path (telegraph) — `embed_banner` POST PNG bytes 到 telegra.ph/upload → 返回 `https://telegra.ph/file/abc.png`；body prepend 正确
- Happy path (ghpages) — `embed_banner` 写文件到 repo `/assets/banners/<sha>.png` + commit + push → 返回 raw URL；后续 `git log` 含该 commit
- Happy path (hashnode) — GraphQL `uploadMedia` 成功 → 返回 hashnode CDN URL
- Happy path (velog) — `image_upload_url` mutation 取 presigned URL → PUT bytes → 返回最终 URL
- Edge case (writeas) — `embed_banner` 返回 None + banner.source_url 存在 → fallback inline，body 含 source_url + warn 日志
- Edge case (writeas) — `embed_banner` 返回 None + banner.source_url 也是 None（FRW 用 b64_json 模式时无 source URL） → body 不动 + warn
- Edge case (medium_brave) — `embed_banner` 不存在 → dispatcher 跳过 + warn
- Error path (telegraph) — uploadFile 500 → retryable，retry 后仍 500 → raise `ExternalServiceError`；strict=False 则 dispatcher 捕获 + warn + 继续
- Error path (ghpages) — push 失败（permission denied） → fail-loud raise，strict=False 则 dispatcher 降级到 inline source_url 或跳过
- Error path (cross-adapter) — banner.path 指向不存在的文件 → 每个 embed_banner 必须 fail-loud `FileNotFoundError`（**不**静吞）
- Integration — banner.path = banner 文件大小 5.5 MB → telegraph uploadFile 拒（telegra.ph 5MB 上限）→ raise；strict=False 降级 inline source_url
- Integration — 8 adapter 每家跑一次 publish with banner → 全部成功嵌图 except writeas+medium_brave 走 fallback；medium_api 测 Medium 自动拉外链行为
- Integration — `config.image_gen.strict=true` + telegraph embed_banner 失败 → publish exit 非零（整篇拒发）
- Integration — `config.image_gen.strict=false` (默认) + telegraph embed_banner 失败 → publish 仍成功 + warn

**Verification:**
- `pytest tests/test_banner_embed_*.py tests/test_publish_with_banner.py` 全过
- `pytest tests/test_r9_extension_readiness.py` 仍通过（没动 `__init__.py` / `registry.py` / `schema.py`）
- `grep -n "embed_banner" src/backlink_publisher/publishing/adapters/__init__.py` 0 命中（确认 dispatcher 走 hasattr 不走 explicit register）
- Banner pipeline end-to-end smoke: `python -m backlink_publisher plan-backlinks ... | python -m backlink_publisher publish-backlinks --dry-run` 输出含 `embed_banner_called` 痕迹（每 adapter dry-run 报告里有）

---

- [ ] **Unit 6: WebUI Settings UI + Test Connection + monolith ceiling docs + README**

**Goal:** WebUI Settings 页面拆出 `[image_gen]` 独立 section，加 Test Connection 按钮（调 `<base_url>/models` 而非 `/images/generations` 避免扣费），帮助文案明示「key 在 0600 文件」+ 显示 frw-token.json 路径。补 README 章节 + AGENTS.md 更新。

**Requirements:** R2（UI 反映 0600 storage）

**Dependencies:** Unit 1（frw_token_path + ImageGenConfig）+ Unit 2（adapter 的 verify-only path）

**Files:**
- Modify: `webui_app/templates/settings.html:355-380` — 重组 `[image_gen]` 三组字段：endpoint (base_url, model, banner_size) / cap (daily_cap, per_run_cap, strict) / token (read-only `frw_token_path()` 显示路径 + "Run `frw-login` to set" 提示)
- Modify: `webui_app/templates/settings.html` — `image_gen_api_key` 输入框**删除**（迁到 CLI `frw-login`）
- Modify: `webui_app/helpers.py:58-59` — `image_gen_api_key` / `use_image_gen` 字段从 `_llm_settings_path()` `llm-settings.json` 移到 `_image_gen_settings_path()` `image-gen-settings.json`（独立文件，遵循 [[feedback_webui_store_config_dir_frozen]]）
- Create: `webui_app/routes/image_gen.py` — `/save-image-gen` POST + `/test-image-gen` POST 两路由
- Modify: `webui_app/__init__.py` `create_app()` — register `image_gen` blueprint
- Modify: `webui_app/templates/settings.html` — AJAX Test Connection 按钮，POST `/test-image-gen` → JSON `{ok, model_count?, error?}`
- Create: `tests/test_webui_image_gen.py` — save / test routes（mock adapter 的 list-models）
- Modify: `README.md` — 新章节「AI Banner Generation」介绍 4 步（base_url 配置 / frw-login / use_image_gen toggle / daily cap）
- Modify: `AGENTS.md` — 在 "Adding a new publisher adapter" recipe 后加一节 "Adding banner embedding to an adapter"（3 行：`hasattr` 风格、return URL or None、何时 raise）
- Modify: `config.example.toml` — 注释里补 banner section 例子
- Create: `docs/solutions/2026-05-20-banner-image-gen-cookbook.md` — 操作 cookbook（**不**含真实 endpoint / key — [[feedback_solutions_category_frontmatter]] 风格）

**Approach:**
- Test Connection 调 `GET <base_url>/models` （OpenAI 兼容）：成功 → JSON `{ok: true, model_count: N}`，401 → `{ok: false, error: "auth_failed: rotate via frw-login"}`，其他 → `{ok: false, error: "<status_code>:<excerpt>"}`
- **不**调 `/images/generations` — 那会真扣费（[[feedback_never_smoke_test_real_save_endpoints]]）
- 如果 spike (Unit 0) 显示 gateway 不支持 `/models`，退而用 1-token `/chat/completions` 探活；**不**用 image-gen
- Save 路由：parse form → write `image-gen-settings.json`（0600）+ 写 `[image_gen]` 段进 config.toml（不写 api_key 字段）
- 显示 frw-token.json 路径 + last-modified 时间 (`stat -f "%Sm"` 等价 Python 实现) 让 operator 知道何时该 rotate

**Patterns to follow:**
- `webui_app/routes/` 现有 12 个 blueprint - 复制一个最简的（例如 `webui_app/routes/llm.py` 如果存在）
- `webui_app/helpers.py:_llm_settings_path()` 函数 — `_image_gen_settings_path()` 完全 mirror
- LLM Integration session 已 land 的双 AJAX toggle pattern ([[project_webui_llm_integration]])
- [[project_pr94_webui_store_env_isolation]] — `_refresh_paths()` helper 处理 conftest monkeypatch

**Test scenarios:**
- Happy path (UI) — settings 页加载，banner section 默认显示 toggle off + 路径提示
- Happy path (save) — POST `/save-image-gen` 表单 → image-gen-settings.json 内容正确 + config.toml `[image_gen]` 段 round-trip 保留（依赖 [[project_pr99_config_subsection_preservation]]）
- Happy path (test) — POST `/test-image-gen` 用 mock 返回 200 → JSON `{ok: true, model_count: N}`
- Edge case — POST `/test-image-gen` 时 `frw-token.json` 不存在 → JSON `{ok: false, error: "no_token: run frw-login"}`，不抛 500
- Edge case — POST `/test-image-gen` 时 `image_gen.base_url` 未配 → JSON `{ok: false, error: "no_base_url"}`
- Edge case — POST `/save-image-gen` 空表单（[[feedback_never_smoke_test_real_save_endpoints]] 防御）→ **不**清空既有 image-gen-settings.json，**不**清空 config.toml [image_gen]；返回 400 + 错误提示
- Error path — POST `/test-image-gen` mock 返回 401 → JSON `{ok: false, error: "auth_failed: rotate via frw-login"}`
- Error path — POST `/test-image-gen` mock 返回 500 → JSON `{ok: false, error: "5xx: ..."}` (不 retry 在 webui 同步路径上，把 retry 留给 plan-time)
- Integration — `BACKLINK_PUBLISHER_CONFIG_DIR=/tmp/x python webui.py` 起独立 webui (避[[feedback_never_smoke_test_real_save_endpoints]])，save → /tmp/x 下落文件，**不**洗用户 prod 文件
- Integration — `frw_token_path()` mtime 变化 → settings 页面 reload 后显示更新

**Verification:**
- `pytest tests/test_webui_image_gen.py` 全过
- `python webui.py` (with BACKLINK_PUBLISHER_CONFIG_DIR=/tmp 隔离) → `http://localhost:8888/settings` 渲染 banner section 不报错；点 Test Connection 按钮（mock 模式）有反馈
- `README.md` 新章节通过 markdown 渲染（GFM）
- `AGENTS.md` 新章节顺序在 "Adding a new publisher adapter" 后
- `pytest tests/test_no_monolith_regrowth.py` 全过（Unit 4 已 bump、本 unit 不动）

## System-Wide Impact

- **Interaction graph**: 新链路 = `plan-backlinks → image_gen.adapter → image_gen.storage → events_log → JSONL out` + `publish-backlinks → adapter.embed_banner? → platform CDN`；不动既有 6 个 CLI 间的 stdin/stdout 契约（banner 只是 plan output 新增 optional 字段，downstream backward-compat）
- **Error propagation**: image-gen 全失败 → degraded mode（body 照发，banner 跳过）。`strict=true` 才让 banner 失败拒发。401 永远 fail-loud（不 retry），其他失败计入 auto-disable counter
- **State lifecycle risks**:
  - `frw-token.json` rotate-under-write race → flock 防御（mirror telegraph）
  - `webui_store/banners/<sha>` 与 events log 不一致 → save 在 record_invocation 之前；如果 save 后 process 死 → 下次 plan-backlinks 通过 storage cache 直接复用已落库 file 不重复扣费
  - 跨 process events log 并发追加 → 依赖 events/projector.py 已有的串行化（不在本 plan 改）
- **API surface parity**: 6 个 CLI 中只有 `plan-backlinks` (output schema) + `publish-backlinks` (input schema) + `validate-backlinks` (schema check) 三个受影响；`report-anchors` / `footprint` / `phase0-seal` 不动
- **Integration coverage**: Unit 5 `tests/test_publish_with_banner.py` 跨 8 adapter 走 end-to-end；Unit 4 `tests/test_plan_backlinks_banner.py` 覆盖 plan-output 契约；adapter 间 banner upload 失败 fallback 流（writeas/medium_brave 无 upload 能力）必须显式测
- **Unchanged invariants**:
  - "published ⟹ 有 URL" 不变（banner 不影响 publish-history 写入路径，仍走 [[feedback_publish_history_invariant_helper]] `_push_history_per_row`）
  - R9 extension-readiness 不变（`__init__.py` / `registry.py` / `schema.py` 一行不动）
  - 6 个 CLI exit code table 0-6 不变（banner-related 失败映射到既有 exit code，不引入新 code）
  - PYTHONHASHSEED=0 footprint 测试不变（banner 不参与 footprint）
  - SEC-3 token 0600 invariant 加固（新 token 文件加入家族）
  - plan-claims-gate exit codes 不变（[[reference_plan_check_cli]]）

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| Operator 暴露的 sk_xxx key 被滥刷配额 | Plan 落地前先 revoke + rotate；frw-login 第一步即必要 |
| Operator gateway 实际不支持 1200×630 非方比例 | Unit 0 spike 必须先验；不支持则 banner_size schema 限 Literal + README 注明 |
| Operator gateway URL TTL 太短 (24h) → 即使下载到本地，老外链文 source_url 字段在 plan jsonl 里也会失效用作 fallback | Unit 5 writeas fallback 显式 warn "link may rot"；source_url 仅供 writeas 等无 upload 平台用，主流路径走 platform CDN |
| `events/projector.py` ceiling 580 → 640 bump 触发 [[feedback_invert_drift_check_when_invariant_becomes_dynamic]] 式 partial drift | Unit 3 同 PR 内 bump + rationale ≥ 80 chars；rerun `pytest tests/test_no_monolith_regrowth.py -k projector` 验 |
| `plan_backlinks/core.py` ceiling 1270 → 1320 bump | Unit 4 同 PR 内 bump + rationale；估算保守 +50 SLOC，留 10 SLOC 缓冲 |
| Per-platform banner upload contract 微小差异（telegraph 5MB / hashnode mime 限定 / velog presigned URL 一次性）→ Unit 5 跨 7 adapter 同 PR 体积大 | Unit 5 可拆 2-3 sub-PR：先 protocol + 2 个 adapter（telegraph + writeas fallback）land；再补其余 5 个 |
| Medium Browser/Brave Playwright `set_input_files` 选错文件选择器 | Unit 5 medium_browser embed_banner 测试用 fixture HTML 模拟 cover-image picker；Brave 不实现（路径不支持，warn-skip） |
| WebUI Test Connection 误调 `/images/generations` 真扣费 | Unit 6 测试断言 `/test-image-gen` 调用栈不含 `/images/generations`（path-grep 测试） + [[feedback_never_smoke_test_real_save_endpoints]] 防御 |
| 8+ 并发 worktree 改 image_gen 相关文件冲突 | Unit 0/1 独立先 land（无依赖）；Unit 2-6 严格串行；遵循 [[feedback_multi_agent_turf_check]] 动手前查 worktree list |
| LLMProviderConfig `image_gen_api_key` deprecation warning 触发既有 config.toml 用户警告噪声 | Unit 1 deprecation warn 包 `simplefilter("once")`；同时 PR 描述里写 "deprecated this version, removed in v1.1" |
| `data[].url` 二次 GET 经过 `la-sealion.inaiai.com` 子域 → 可能也需要 auth header | Unit 0 spike 验；Unit 2 adapter 二次 GET 默认带同 `Authorization` header，spike 显示不需要就去掉 |
| FRW gateway 返回的 image 可能是 webp/jpeg 而非 png | Unit 2 mime sniff 三种支持；存储扩展名跟 mime；embed_banner 各家平台对 mime 容忍度不同需测 |
| Plan-claims-gate ([[reference_plan_check_cli]]) 在 PR 落地时 grep 本 plan 引用的 file_path | 所有 unit 的 Files 字段路径在 grandfather cutoff < 2026-05-20 之后必须实存 + 路径形态对得上 |

## Documentation / Operational Notes

- **`frw-login` 必须文档化在 README "Setup" 章节**：操作员第一次配 image-gen 必走这条
- **`docs/solutions/2026-05-20-banner-image-gen-cookbook.md`** 记录 plan 落地后的 5 步走（base_url / login / toggle / test / first banner），但**不**写真实 endpoint 域名（[[feedback_solutions_category_frontmatter]] 风格 — solutions 是 promotion 去标识符化）
- **Rollout**: 全功能 opt-in（`config.image_gen` 默认 None + use_image_gen=False）；现有用户升版本不受影响（image-gen 是 no-op）
- **Monitoring**: `report-anchors` 输出新增「本月生图次数」列（依赖 events `image_gen_invoked` 已记），帮 operator 看费用
- **Auto-disable safety event** 触发时输出 stderr warn + plan-result `banner_status="auto_disabled"`；operator 看到后手动 frw-login rotate key + 重启 plan-backlinks
- **Key rotation 流程**：(1) 后台 revoke 旧 key (2) gateway 发新 key (3) operator `frw-login` 粘新 key (4) `frw-test-connection` (CLI alias 或 webui Test Connection 按钮) 验通 (5) 重跑 plan-backlinks
- **Banner CDN URL TTL 不重要**：因为本地副本是 source of truth；source_url 字段只是 writeas 等无 upload 平台的应急 fallback

## Sources & References

- **Related files (existing)**:
  - `src/backlink_publisher/publishing/adapters/frw_image_gen.py` (rewrite target)
  - `src/backlink_publisher/publishing/adapters/llm_anchor_provider.py:156` (reuse `generate_image_prompt`)
  - `src/backlink_publisher/cli/plan_backlinks/core.py:531-547` (rewrite target)
  - `src/backlink_publisher/publishing/adapters/telegraph_api.py` (canonical credential pattern)
  - `src/backlink_publisher/config/types.py:74` (LLMProviderConfig — deprecate fields)
  - `webui_app/templates/settings.html:355-380` (banner UI section)
  - `webui_app/helpers.py:58-59` (image-gen helpers stub — move out of llm path)
- **Memory references**:
  - [[reference_telegraph_adapter_credential_rotation_pattern]] — 6 组件 mirror 蓝本
  - [[project_webui_llm_integration]] — 双 AJAX toggle pattern 复用
  - [[project_pr99_config_subsection_preservation]] — `[image_gen]` round-trip 依赖
  - [[project_pr94_webui_store_env_isolation]] — `_refresh_paths()` 已防 env-blind 事故
  - [[feedback_test_first_for_credential_rotation_misses_bootstrap]] — Unit 1 三 race 测试套路
  - [[feedback_config_paths_must_respect_env_var]] — Unit 1/3 path resolver 函数化
  - [[feedback_webui_store_config_dir_frozen]] — Unit 6 settings 路径
  - [[feedback_never_smoke_test_real_save_endpoints]] — Unit 6 Test Connection 不调 generation 端点
  - [[feedback_publish_history_invariant_helper]] — Unit 5 不绕过 history helper
  - [[reference_plan_check_cli]] — PR 落地时 plan-claims-gate 自动验本 plan
- **Related PRs / branches**:
  - [[project_channel_binding_dashboard_plan_006]] Phase 3 wave (#102 hashnode / #103 writeas) 提供 hashnode / writeas embed 的 client 形态参考
  - [[project_velog_adapter_pr75]] velog_graphql.py 已有 `image_upload_url` 路径
  - [[project_medium_graphql_phase1_pr88]] medium adapter 演进背景
- **External references**:
  - OpenAI Images API contract: `POST /v1/images/generations` (operator gateway 是兼容实现，spike 确认细节)
  - Telegraph `uploadFile`: https://telegra.ph/api#uploadFile
