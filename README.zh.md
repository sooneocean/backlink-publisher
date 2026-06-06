# backlink-publisher

本地优先、终端原生的反向链接发布流水线。
跨 **20+ 平台**（Blogger、Medium、Telegraph、Velog、Substack、dev.to、Notion、GitHub/GitLab Pages 等）生成、验证并发布短篇反向链接文章——完全管道友好、Cron 安全、无需交互。

新增一个平台只需一行 `register("x", XAdapter)`——CLI、schema、限流闸门、分级矩阵都从适配器注册表动态读取（详见 [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter)）。另有 Flask **WebUI**（`python webui.py`）为偏好浏览器而非终端的操作者封装同一套流水线。

> English version: [README.md](README.md)

## 工作空间布局

这是规范的项目仓库。它位于一个父工作空间目录中，该目录本身**不是** git 仓库。名为 `bp-<topic>`（如 `bp-events-u4`、`bp-ko-html`）的兄弟目录是同一仓库在并行功能分支上的临时 `git worktree` 检出——它们与主检出共享 `.git/`。约定每个活动功能分支对应一个 `bp-<topic>` 工作树；分支合并后删除该工作树（`git worktree remove ../bp-<topic>`）。贡献者工作流程见 `AGENTS.md`。

## 快速开始

```bash
# 安装
pip install -e .

# 运行完整流水线（空运行/测试）
cat seeds.jsonl \
  | plan-backlinks \
  | validate-backlinks \
  | publish-backlinks --platform medium --mode draft --dry-run
```

## 前置要求

| 要求 | 详情 |
|---|---|
| **Python** | >= 3.11 |
| **Chromium** | 仅用于浏览器后备模式（如 Medium）：`playwright install chromium` |

> **无需 Node.js。** 发布直接使用各平台 API（Blogger API v3、Medium API 等）。仅当某平台无 API（或未配置令牌）时才需要 Chrome/Playwright 作为后备。

## 首次运行设置

```bash
# 1. 安装包及依赖
pip install -e .

# 2. 复制并编辑配置文件
cp config.example.toml ~/.config/backlink-publisher/config.toml
# 编辑：设置 Blogger blog_id 映射、OAuth 凭据、可选的 Medium 令牌

# 3. (可选) 安装用于浏览器后备的 Chromium
playwright install chromium
#    然后在 Playwright 管理的配置中登录一次目标平台：
#    open ~/.config/backlink-publisher/chrome-profile-default/
```

## 流水线命令

### 1. plan-backlinks

从 stdin 或 `--input` 读取种子 JSONL，为每一行生成一个文章载荷。

```bash
cat seeds.jsonl | plan-backlinks
cat seeds.jsonl | plan-backlinks -i /dev/stdin
```

**输入架构 (seed)：**

```json
{
  "target_url": "https://example.com/article",
  "main_domain": "https://example.com",
  "language": "en",
  "platform": "medium",
  "url_mode": "A",
  "publish_mode": "draft",
  "topic": "可选字符串",
  "seed_keywords": ["可选", "字符串"]
}
```

| 字段 | 必需 | 值 |
|---|---|---|
| `target_url` | 是 | 有效的 HTTPS URL |
| `main_domain` | 是 | 有效的 HTTPS URL |
| `language` | 是 | `en`, `zh-CN`, `ru` |
| `platform` | 是 | 任一已注册平台（见下方适配器表） |
| `url_mode` | 是 | `A`（仅主域）, `B`（主域+分类）, `C`（主域+详情） |
| `publish_mode` | 是 | `draft`, `publish` |
| `topic` | 否 | 字符串 |
| `seed_keywords` | 否 | 字符串数组 |

**输出要点：**

- 文章长度 100–200 字；每篇 6–8 个链接（主域 + 目标 + 模式相关 + 支撑链接）。
- `main_domain` 自然出现在正文中，而非开头或结尾。
- 支持简体中文、英语和俄语。

### 2. validate-backlinks

读取规划后的 JSONL，校验 schema + URL，并补充 `validation` 块。

```bash
cat planned.jsonl | validate-backlinks
cat planned.jsonl | validate-backlinks --no-check-urls   # 跳过 HTTP 检查
```

**执行的校验：** 必填字段及类型、每篇 6–8 链接、`target_url` 与所有链接可达（HTTP 200/301/302）、`main_domain` 出现在正文、标题非空、SEO 块完整、语言与内容大致匹配（启发式）。

### 3. publish-backlinks

读取已校验的 JSONL，通过 API 优先、浏览器后备的适配器发布。

```bash
# Medium（空运行）
cat validated.jsonl | publish-backlinks --platform medium --mode draft --dry-run

# Medium（真实发布——使用 API 令牌或浏览器后备）
cat validated.jsonl | publish-backlinks --platform medium --mode publish

# Blogger（草稿——使用 Blogger API v3）
cat validated.jsonl | publish-backlinks --platform blogger --mode draft

# 按行平台（省略 --platform）
cat validated.jsonl | publish-backlinks --mode draft
```

| 标志 | 默认 | 说明 |
|---|---|---|
| `--platform` | 按行 | 覆盖所有行的平台 |
| `--mode` | `draft` | `draft` 或 `publish` |
| `--dry-run` | 关 | 打印命令计划，不执行 |
| `--input`, `-i` | stdin | 输入文件路径 |

**Medium 限流：** 连续 Medium 发布之间会随机休眠 60–300 秒以避免限速。可用环境变量覆盖：

```bash
MEDIUM_THROTTLE_MIN=30 MEDIUM_THROTTLE_MAX=90 publish-backlinks ...
```

## SEO 锚文本关键词

生成的文章会放置指向每个目标站 `main_domain` 的两个反向链接。未配置时，两个锚文本默认使用裸域名（如 `your-site.com`）——SEO 信号几乎为零。要提升关键词相关性，在 `~/.config/backlink-publisher/config.toml` 中按目标配置关键词池：

```toml
[targets."https://your-site.com"]
anchor_keywords = [
  "your-site",                  # 品牌词
  "comprehensive content hub",  # 主词
  "in-depth resource guide",    # 长尾
  "curated reference library",
  "expert tutorials",
]
```

**选取策略。** 每篇文章用 `keywords[(position + offset) % len(keywords)]` 确定性地选两个不同关键词，`offset` 对 `url_mode` `A/B/C` 分别为 `0/1/2`。相同文章配置始终产出相同锚文本分布；跨文章变化 `url_mode` 会轮换关键词槽位，形成自然分布。推荐池大小 **5–10 个**，混合品牌词、主词与长尾。

**后备。** 若 `anchor_keywords` 缺失或为空列表，渲染器回退到裸域名标签，并对每篇文章发出一条 `WARN`，提醒操作者注意错失的 SEO 机会。文章仍正常发布。

**新标签页行为。** 渲染 HTML 中所有 `<a>` 标签都带 `target="_blank" rel="noopener"`，使反向链接在新标签打开且不暴露 opener 窗口。（注意：Medium 渲染器可能剥离这些属性——在 Medium 上属尽力而为。）

## 工作主题反向链接（三 URL 形式）

新项目推荐使用（Plan 2026-05-13-004）。每篇文章携带 **三个** 指向同一目标站的反向链接：

1. **`main_url`**——品牌权重锚（取自 `branded_pool`）。
2. **`list_url`**——发现面（锚 70% 取自 `partial_pool`，30% 取自 `exact_pool`）。
3. **`work_url`**——每篇一个 URL，锚由抓取的 `<title>` 经 `work_anchor_templates` 合成（默认模板：`{title}`、`{title} 详情`、`{title} 推荐`、`{title} 介绍`）。

三段落中的锚位置由每篇文章的种子置换（六种排序），避免形成稳定的「主链在前/工作链在后」指纹。所有锚都渲染为 `<a target="_blank" rel="noopener">`——**无 `nofollow`**，dofollow 权重完整传递。发布后校验器（`link_attr_verifier`）会标记任何被平台注入的 `rel="nofollow"`，使静默降权（Medium 等）在发布报告中暴露。

### 通过 WebUI 配置

打开 WebUI 的 `/sites` 页填写三 URL 表单。表单使用 CSRF 令牌，页面默认绑定 `127.0.0.1`——设 `BACKLINK_PUBLISHER_ALLOW_NETWORK=1` 可绑定非回环地址（仅在可信网络下使用）。保存通过与 `[blogger.oauth]` 相同的 `save_config` 持久化，因此既有凭据、旧版 `[sites.*]` 块、以及托管根下任何操作者添加的二级子段（如 `[medium.oauth]`、`[targets.X]`）都会原样保留。

### 通过 `config.toml` 配置

```toml
[targets."https://your-site.com"]
main_url = "https://your-site.com/"
list_url = "https://your-site.com/list"
work_urls = ["https://your-site.com/work/1", "https://your-site.com/work/2"]
branded_pool = ["Your Site", "Your Site Hub"]
partial_pool = ["site hub partial keyword"]
exact_pool = ["site keyword"]
# work_anchor_templates = ["{title}", "{title} 详情", "{title} 推荐", "{title} 介绍"]
# list_path_blocklist = ["/tag/", "/category/", "/page/"]
# insecure_tls = false
```

`work_urls` 为空时，规划器通过抓取 `/sitemap.xml`（向 `<sitemapindex>` 递归一层）发现候选，否则回退到抓取 `list_url` 上的 `<a href>` 元素（带默认导航路径黑名单：`/tag/`、`/category/`、`/page/`、`/author/`、`/about`、`/contact`、`/search`、`/feed`）。

### 双路径共存（无迁移压力）

已有 `[sites."<domain>"]` 块的站点继续走 zh-CN 短文调度器（下节）。为同一域名加上 `[targets."<domain>"]` 三 URL 块，只是将该域名改路由到工作主题规划器——两条路径都保留。一条 INFO 日志记录这种共存，便于决定何时（或是否）迁移。

## zh-CN 短文锚文本分布调度器

上述默认路径（`[targets."<domain>"].anchor_keywords`）驱动 en/ru 文章及任何未启用调度器的 zh-CN 目标。zh-CN 目标可启用更丰富的**锚文本分布调度器**：

- 生成 150–200 字短文（1 个主页链接 + 1–2 个非主页二级链接），而非长文的 6–8 链接布局；
- 在滑动窗口内对 Safe SEO 目标分布（品牌 55% / 部分匹配 25% / 精确 10% / LSI 10%）执行约束；
- 从 `{hot, animate, category, topic}` 中为每个二级链接选 URL 类别，使单篇文章不重复同一目标页。

### 默认模式：运行时无 LLM

`config.example.toml` 中的 51acgs.com 块按**运行时无 LLM** 预设尺寸：20 个 `(url_category, anchor_type)` 单元共 126 个手选候选，高频 `home/branded` 单元填充至 15 项以舒适地超出 20 项的文本去重窗口。500 篇 × 3 种子的模拟产出零 LLM 回退调用，每项目标比例误差在 1 个百分点内。

启用方法：取消注释 `config.example.toml` 中的 `[sites."https://51acgs.com".url_categories]` 与 `[sites."https://51acgs.com".anchor_pools.*]` 块（或复制到你的 config.toml）。调度器对任何 `main_domain` 配了这些块的 zh-CN 种子行自动启用；无 v2 配置的行回退到旧版长文路径，行为零变化。

扩展到其他站点时，照搬 51acgs.com 结构：列出站点 `url_categories`（须含 `home` 及至少一个非主页类别）→ 为每个想覆盖的 `(url_category, anchor_type)` 单元填 `anchor_pools`（每单元至少 3 个候选，`home/branded` **≥12** 以避开降级路径）→ 编辑后运行 `pytest tests/test_config_example_pool.py`（跑 500 篇模拟，若任何单元会触发降级则失败）。

### 可选模式：带 LLM 后备的混合

若想精简部分单元并让 LLM 按需生成候选，取消注释 `[llm.anchor_provider]` 块：

```toml
[llm.anchor_provider]
base_url = "https://api.openai.com/v1"
model = "gpt-4o-mini"
timeout_s = 30
```

API key 经 `BACKLINK_LLM_API_KEY` 环境变量（推荐）或同块内 `api_key = "sk-..."`（需 `chmod 600 config.toml`，否则发警告）提供。`base_url` 须为 `https://`。上线前用 `python scripts/llm_rejection_spike.py` 验证拒绝率（exit 0 = 拒绝率 < 20%）。成人内容站应预期主流提供商的高拒绝率——该脚本使此权衡可复现。

### 可观测性

至少 50 篇文章后，检视每站锚文本分布：

```bash
report-anchors --from-profile https://51acgs.com
```

报告展示滚动类型分布对比目标、`url_category × anchor_type` 交叉表、以及降级率（>10% 时标 ⚠️）。`--json` 提供 JSON 输出供脚本使用。

`report-anchors --from-profile` 还输出每个目标 URL 在滚动 30d/90d 窗口的三项分布指标：归一化锚文本分布的 **Shannon 熵**、**精确匹配比**、非品牌锚的 **Top-3 集中度**。任一目标的 90d 窗口越过配置阈值时，`report-anchors` 在 JSON 中发出 `alarm` 块、对每个越界目标在 stderr 打 `WARN [anchor_alarm]`，并以退出码 **6** 退出，使 cron 包装器可无歧义告警。

这是**检测而非预防**：发布路径不读取告警——操作者是决策者。某目标越界时：暂停对该 URL 发布、轮换其锚策略、再跑一批文章后重测。阈值经 `config.toml` 中 `[anchor_alarm]` 覆盖（详见 `config.example.toml`）。

## 发布适配器

发布以 API 优先，无 API 处用浏览器后备。**20+ 平台** 注册在 `publishing/adapters/__init__.py`，下表按链接权重分组。新增只需一行 `register(...)`——见 [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter)。

每个注册声明一个 `dofollow` 判定（`True` / `False` / `"uncertain"`）加 `referral_value`（`high` / `low`）。`"uncertain"` 表示第三方探测见到 dofollow，但**我们自己**的发布路径金丝雀尚未确认；`canary-targets` 会重新抓取线上文章逐步定论。

| 平台 | dofollow | 传输 | 认证 |
|---|---|---|---|
| **Blogger** | ✅ dofollow | Blogger API v3 | OAuth2 令牌 |
| **Medium** | ✅ dofollow | Medium API v1 + Playwright 后备 | 集成令牌 / 浏览器 |
| **Telegraph** | ✅ dofollow | telegra.ph API | 匿名 |
| **Velog** | ✅ dofollow | 内部 GraphQL `writePost` | cookie jar（30 天） |
| **GitHub Pages** | ✅ dofollow | GitHub Contents API（`*.github.io`） | Bearer PAT |
| **WordPress.com** | ❔ uncertain | WordPress.com REST v1.1 | OAuth2 令牌 |
| **Substack** | ❔ uncertain | 内部发布 API | cookie jar |
| **Hatena** | ❔ uncertain | AtomPub | API key |
| **HackMD** | ❔ uncertain | HackMD API | API 令牌 |
| **Mataroa** | ❔ uncertain | Mataroa API | API key |
| **GitLab Pages** | ❔ uncertain | GitLab Repository Files API（`*.gitlab.io`） | PAT (PRIVATE-TOKEN) |
| **Rentry / txt.fyi** | ❔ uncertain | 匿名 paste POST | 无 |
| **Hashnode / Write.as** | ❔ uncertain | API（逐步退役） | API 令牌 |
| **dev.to / Notion** | ⛔ nofollow | API | API 令牌 |
| **LinkedIn¹ / Tumblr / LiveJournal / Mastodon** | ⛔ nofollow | API / cookie jar | 各平台 |

¹ LinkedIn 注册为 `visibility="experimental"`。

> nofollow 平台保留是为了**引荐流量、主题相关性、收录速度**——`referral_value` 记录其理由。用 `equity-ledger` / `plan-gap` 把新链接导向 dofollow 层。

### Blogger 设置

1. 创建 Google Cloud 项目并启用 **Blogger API v3**。
2. 创建 OAuth2 凭据（桌面应用）并下载 client JSON。
3. 加入 `~/.config/backlink-publisher/config.toml`：

```toml
[blogger.oauth]
client_id     = "..."
client_secret = "..."

[blogger]
"https://your-site.com" = "your-blog-id"
```

4. 运行任意 Blogger 发布——浏览器窗口打开一次进行 OAuth 授权，令牌自动保存供后续使用。

### Velog 设置

velog.io 无官方 API；通过其内部 `v2.velog.io/graphql` GraphQL 端点配合社交登录得到的 cookie jar 发布。

```bash
pip install playwright && playwright install chromium   # 一次性
velog-login                                             # 打开有头 Chromium 完成社交登录
```

凭据保存到 `~/.config/backlink-publisher/velog-cookies.json`（0600）。发布：

```bash
cat seeds.jsonl | plan-backlinks | validate-backlinks \
  | publish-backlinks --platform velog --mode publish
```

**注意：** access_token 24h（自动刷新），refresh_token **30 天**——每 30 天重跑一次 `velog-login`。每日上限按机器计；详见 `docs/operations/velog-login.md`。

### Medium 设置

**方案 A——集成令牌（推荐）：** 在 `medium.com/me/settings/security → Integration tokens` 生成令牌，加入配置 `[medium] integration_token = "your-token"`。

**方案 B——浏览器后备（无需令牌）：** `playwright install chromium`，在 `~/.config/backlink-publisher/chrome-profile-default/` 配置中登录一次 Medium。未配置令牌时流水线自动使用浏览器。

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功 |
| `1` | 用法错误（CLI 标志错误） |
| `2` | 输入校验错误（schema、链接数、坏 URL） |
| `3` | 依赖错误（缺配置、OAuth 未设、Playwright 未装） |
| `4` | 外部服务错误（API 错误、登录过期、CAPTCHA） |
| `5` | 意外内部错误 |
| `6` | 锚文本分布告警——`report-anchors --from-profile` 检测到至少一个目标 90d 窗口越界。输出仍有效，视为需操作者行动的警告。 |

## 输出契约

- **stdout**——仅结构化 JSONL（成功时）
- **stderr**——仅诊断信息（失败时）
- **exit code**——成功 0，失败非 0
- 任何模式下都无人类可读的「完成 / 成功」消息

## 示例流水线

```bash
cat > seeds.jsonl <<'EOF'
{"target_url":"https://example.com/article","main_domain":"https://example.com","language":"en","platform":"medium","url_mode":"A","publish_mode":"draft","topic":"Web Development"}
{"target_url":"https://blog.example.org/posts/guide","main_domain":"https://blog.example.org","language":"zh-CN","platform":"blogger","url_mode":"C","publish_mode":"publish","topic":"Python最佳实践"}
EOF

# 完整流水线（空运行）
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --mode draft --dry-run

# 完整流水线（Blogger，发布）
cat seeds.jsonl | plan-backlinks | validate-backlinks | publish-backlinks --platform blogger --mode publish
```

## 故障排查

若发布失败提示 `channel 'X' credentials expired`，打开设置 `/settings` 点击受影响渠道卡上的 **重新绑定**；有头浏览器打开供登录，写入 storage_state 且 `mark_bound` 记录后徽章从 `绑定中…` → `已绑定 ✓`。CLI 替代：`bind-channel --channel <velog|medium|blogger>`。完整生命周期见 `AGENTS.md → Binding a channel`。

| 问题 | 解决 |
|---|---|
| `Blogger OAuth not configured` | 在 config.toml 加 `[blogger.oauth]` |
| `No Blogger blog_id configured for domain` | 在 `[blogger]` 下加域名映射 |
| `channel 'X' credentials expired`（exit 3） | `/settings` → 重新绑定，或 `bind-channel --channel X` |
| `medium integration token not configured` | 加 `[medium] integration_token`，或装 Playwright 作后备 |
| `Medium login expired` | 在受管 Chrome 配置中登录 Medium |
| `Medium CAPTCHA detected` | 在 medium.com 手动解 CAPTCHA 后重试 |
| `Playwright is not installed` | `playwright install chromium` |
| 发布失败保存了截图 | 查看 `~/.cache/backlink-publisher/screenshots/` |

## CLI 命令一览

`pyproject.toml` `[project.scripts]` 声明了 23 个控制台入口。核心为 `plan-backlinks → validate-backlinks → publish-backlinks`，其余为只读分析、渠道绑定、再校验辅助：

| 命令 | 角色 |
|---|---|
| `plan-backlinks` / `validate-backlinks` / `publish-backlinks` | 核心三段流水线 |
| `report-anchors` | 锚文本分布 + 告警 |
| `footprint` | 链接足迹分析 |
| `equity-ledger` | 每目标反链记分卡（只读） |
| `plan-gap` | 基于记分卡的缺口驱动重规划（只读） |
| `audit-state` | 双态分歧审计（只读） |
| `preflight-targets` | 发布前目标页健康检查 |
| `cull-channels` / `channel-scorecard` | 渠道质量建议（只读） |
| `canary-targets` | 重抓 dofollow 文章确认链接存活（建议性） |
| `recheck-backlinks` / `recheck-overlay` | 再校验已发链接 + 叠加增量 |
| `bind-channel` | 将目标绑定到发布渠道 |
| `velog-login` / `medium-login` / `frw-login` | 各平台交互登录辅助 |
| `generate-backlink-text` | LLM 辅助内容起草 |
| `comment` | 评论外联驱动 |
| `gate-probe` | 探测闸门/限流决策 |
| `plan-check` | 计划文档漂移校验 |
| `phase0-seal` | Phase 0 封存操作 |

## 项目结构

```
backlink-publisher/                 # 规范 git 仓库（本目录）
├── config.example.toml
├── pyproject.toml
├── README.md / README.zh.md
├── AGENTS.md                        # 权威贡献者指南
├── webui.py                         # Flask WebUI 启动器（:8888）
├── src/backlink_publisher/
│   ├── cli/                         # 23 个控制台入口
│   │   └── plan_backlinks/          # plan-backlinks 已拆为包
│   ├── publishing/
│   │   ├── adapters/__init__.py     # 适配器注册表 — register("x", …)
│   │   ├── registry.py              # 动态平台查找
│   │   ├── browser_publish/         # Playwright 后备
│   │   └── reliability/             # 限流 / 熔断
│   ├── anchor/                      # 锚关键词调度器 + 告警
│   ├── content/                     # 文章渲染
│   ├── linkcheck/                   # URL 可达性 + SSRF 防护
│   ├── canary/ · ledger/ · gap/     # equity-ledger / plan-gap 引擎
│   ├── audit/ · recheck/ · scorecard/
│   ├── config/ · schema.py · http.py · _util/
│   └── llm/                         # 可选 LLM 锚提供器
├── webui_app/                       # Flask 应用（routes + services/）
├── webui_store/                     # WebUI 状态单例
├── tests/                           # pytest 套件（PYTHONHASHSEED=0）
├── monolith_budget.toml             # radon SLOC 上限
├── complexity_budget.toml           # radon 圈复杂度上限
└── fixtures/
```

## 贡献者指南

新增发布平台（WordPress、Substack、Telegraph……）只需一行 `register("x", XAdapter)` 即可贯通 CLI 与 schema 层。五步配方（继承 / 实现 / 注册 / 配置 / 依赖 / 测试）见 [AGENTS.md → Adding a new publisher adapter](AGENTS.md#adding-a-new-publisher-adapter)。更广的项目约定（`docs/solutions/` 经验沉淀、SLOC 单体预算、工作树自动清理）以 [AGENTS.md](AGENTS.md) 为准。
