---
date: 2026-05-12
topic: anchor-text-and-blank-target
---

# 外链锚文本 SEO 化与新窗打开

## Problem Frame

当前生成的外链文章里，所有指向 target 站点的反链都用裸域名（如 `xhssex.com`）做锚文本，且渲染后的 HTML `<a>` 标签没有 `target="_blank"`。两个直接后果：

1. **SEO 价值损失**：裸域名锚文本对 target 站的关键词权重传递几乎为零；搜索引擎无法把锚文本当作话题信号。
2. **跳出率风险**：用户点击反链时直接离开承载文章的站点，宿主站滞留时间下降，反链来源页的"质量信号"被弱化。

需要把锚文本改为有实际语义、对 target 站 SEO 有效的关键词，并让所有外链在新标签页打开以保留宿主页停留。

## Requirements

**锚文本生成（关键词池）**

- R1. 每个 target 站在 config 中维护一份 `anchor_keywords` 列表（品牌词 + 行业词 + 长尾词，至少 3 个，建议 5–10 个），由人工填写。
- R2. 模板渲染时，所有原本输出 `[{domain}]({main_domain})` 的位置改为从该 target 的 `anchor_keywords` 中选词，输出 `[<keyword>]({main_domain})`。
- R3. 锚文本选取策略为**确定性选取**：基于文章的 `url_mode (A/B/C)` 与该锚在文中的位置序号（1st/2nd/3rd 反链）映射到 keyword pool 的不同槽位；同一篇文章内多次反链使用不同关键词，跨文章产生可预测的多样化分布。
- R4. 当 `anchor_keywords` 缺失或为空时，回退到当前的裸域名行为，并在生成日志里 WARN 一次（不阻断流水线）。

**HTML 渲染（新窗打开）**

- R5. `render_to_html` 渲染产出的所有 `<a href>` 标签必须带 `target="_blank"` 与 `rel="noopener"`，无论指向 target 站、supporting 站还是 citation 链接。
- R6. 不引入 `nofollow`，保持现有"反链必须 dofollow"约定。

## Success Criteria

- 任意一篇生成文章的所有 `<a>` 锚文本可读、为关键词短语而非裸域名（人工抽检 5 篇 100% 通过）。
- 渲染后的 HTML 中 `target="_blank"` 与 `rel="noopener"` 出现在每个 `<a>` 上（单元测试断言）。
- 同一 target 站连续生成 10 篇，锚文本分布覆盖至少 3 个不同关键词（测试断言）。
- 现有 43/43 测试在适配后仍全部通过（无回归）。

## Scope Boundaries

- **不**做锚文本自动抓取/AI 生成（R1 决定走人工配置池）。
- **不**做锚文本去重/反垃圾算法（如禁止连续相同锚），由 R3 的确定性映射隐式分散即可。
- **不**修改适配器 publish 逻辑本身；仅修改文章生成与 HTML 渲染。
- **不**为 supporting/citation 链接构造关键词池（这些链接锚文本由模板自然语言提供，已经有意义）。

## Key Decisions

- **锚文本来源 = 目标站配置关键词池**：可控、易复现、符合 SEO 最佳实践，避免被算法判定为单一锚文本过度优化。
- **\_blank 范围 = 所有外链（含 supporting/citation）**：实现最简，且符合外链文章的整体目的（保留宿主页停留）。
- **选取策略 = 确定性映射 (url_mode + 位置)**：可复现、易测试，又能跨文章自然轮换。
- **rel="noopener" 一并加入**：浏览器安全最佳实践，与 SEO 无冲突；不加 `noreferrer`（会丢失 referrer 流量信号）。

## Dependencies / Assumptions

- 假设 target 站 config schema 可扩展新增 `anchor_keywords: list[str]` 字段（参考现有 config.example.toml 的 target 段结构）。
- 假设 `markdown-it-py` 支持自定义 renderer 规则覆盖 `link_open`（标准能力，无新依赖）。
- 假设上线前会为现有所有 target 站补齐 `anchor_keywords`，否则将走 R4 的回退路径。

## Outstanding Questions

### Resolve Before Planning

（无）

### Deferred to Planning

- [Affects R5][Technical] Medium 平台已知会剥离大部分 HTML 属性（包括 `target`/`rel`）。需要 plan 阶段确认：是否在 Medium 适配器层做特殊提示/告警，或接受"Medium 上 \_blank 是 best-effort"。
- [Affects R3][Technical] 确定性映射的具体公式（如 `keywords[(position_index + url_mode_offset) % len(keywords)]`）由 plan 阶段定稿。
- [Affects R1][Needs research] 每个 target 站推荐的关键词数量上限是否需要硬约束（避免 config 膨胀）。

## Next Steps

→ `/ce:plan` for structured implementation planning
