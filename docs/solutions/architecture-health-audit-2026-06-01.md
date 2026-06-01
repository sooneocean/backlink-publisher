---
title: "架构健康审计 — 模块化 / 前后端分离现状核查"
date: 2026-06-01
type: audit
status: reference
verdict: healthy
triggers: ["全面优化", "拆分模组", "前后端分离", "comprehensive optimization", "split modules", "refactor architecture"]
---

# 架构健康审计 (2026-06-01)

## TL;DR

**触发**:一次「帮我全面优化代码库 / 拆分模组 / 让前后端分离更好」的请求。

**结论**:代码库**已经充分模块化、前后端已分离、所有对应的优化计划均已 ship**。
实测发现的 4 处「疑似瑕疵」中——**3 处是刻意的架构设计,1 处是未提交的孤儿死代码**。
**无需重做此类工作。** 真正的瓶颈是执行/收敛,不是再拆模组或再开计划。

> 下次再出现「全面优化 / 拆分模组」类请求,先读本文件,再决定是否真有新增价值。

---

## 核查方法

| 维度 | 手段 |
|---|---|
| 模块结构 | `find src -maxdepth 2 -type d` + `webui_app/` 盘点 |
| 前后端依赖方向 | `grep "import webui" src/`(后端是否反向依赖前端) |
| 每处疑点爆炸半径 | `grep -rl` 量化 import 点数量 |
| 计划完成度 | 逐个读 `docs/plans/*.md` frontmatter `status` |
| 死活判定 | `grep` 引用计数 + 读源文件确认意图 |

---

## 现状:健康指标

- **`src/backlink_publisher/` = 24 个领域子包**:`anchor / audit / canary / cli / comment_outreach / config / content / events / gap / gates / geo / idempotency / ledger / linkcheck / llm / persistence / phase0 / publishing / recheck / scorecard / validate / _util` 等。**不是 monolith。**
- **`webui_app/` 已分层**:`routes(25)` / `services(10)` / `store` / `api` / `helpers` / `templates` / `static`。
- **两道自动闸门防膨胀**:`monolith_budget.toml`(radon SLOC per-file)+ `complexity_budget.toml`(CC per-function),CI 强制。
- **前后端分离健康**:后端 `src/` **0 处** import 前端 Flask app(`webui_app`)✅。

---

## 计划状态核对:字面对应请求的计划全部已 ship

| 计划 | 主题 | 状态 |
|---|---|---|
| `2026-05-28-008-...-comprehensive-optimization` | 4 波质量+类型安全+可观测 | ✅ complete |
| `2026-05-27-004-...-thin-webui-in-process-pipeline` | 前后端分离(in-process pipeline) | ✅ completed |
| `2026-06-01-007-...-webui-frontend-maintainability` | 前端地基(base/lib/tokens) | ✅ completed |
| `2026-05-18-001-...-architecture-health-roadmap` | webui/config/领域分包路线图 | ✅ completed |
| `2026-06-01-009-...-active-plan-convergence-closeout` | 计划状态收敛收尾 | ✅ completed |

---

## 4 处疑点的裁定

| # | 疑点 | 爆炸半径 | 裁定 |
|---|---|---|---|
| 1 | `webui_store` 被后端 import | **8 个后端文件** | 🟡 刻意:共享持久层,放 repo 根是既定决策 |
| 2 | `webui_app/` 5 个顶层散文件 | 各 1–3 引用 | 🟢 活跃 webui 层;`medium_login` 是刻意 shim |
| 3 | webui import `cli._report_engine` | **仅 1 处** | 🟢 刻意:thin-WebUI in-process 调用 seam |
| 4 | `cli/lease_management.py` | **0 引用** | 🔴 真孤儿:未提交、未接线的死代码 |

### #1 — `webui_store` 耦合(刻意,非缺陷)
`canary/store.py`、`ledger/sources.py`、`publishing/{adapters,reliability,browser_publish}/*`、`cli/_publish_helpers.py`、`cli/_resume.py`、`cli/_bind/_driver_impl.py` 共 8 个后端模块 `from webui_store import …`。
`webui_store` 名义上带 `webui_` 前缀,实为**跨层共享的 JSON 持久层**(`history_store` / `channel_status` 等单例)。这是团队已知并接受的定位(见 repo 约定:store 放 repo 根而非 `src/`)。
**裁定**:重命名为 `shared_store` 之类会触及 8+ 后端 + 全部 webui import 点 + 测试 mock 目标字符串,**高 churn、低 ROI,不建议动。**

### #2 — 5 个 webui 顶层文件(活跃,可选归并)
`binding_status.py`(195)、`health_metrics.py`(250)、`scheduler.py`(217) 是正当的 webui 层模块;`medium_liveness.py`(172) 活跃;`medium_login.py`(**16 行**)是 Wave 1 留下的 **re-export shim**,转发到 canonical `publishing.adapters.medium_auth`。
**裁定**:全部活跃,无死代码。归并进 `webui_app/services/` 纯属整洁度偏好,低 ROI,**可选不必做。**

### #3 — `_report_engine` import(刻意 seam,非泄漏)
`webui_app/api/pipeline_api.py:452` `from backlink_publisher.cli._report_engine import report_from_profile`——这正是 thin-WebUI Phase 2 Unit 7 的设计:用**进程内直调纯核心**替代 subprocess。`_report_engine` 被特意抽成 CLI 与 webui 共享的纯函数核心。下划线只是命名,不是真泄漏。
**裁定**:去下划线会触及 CLI 侧 + 测试 mock.patch 字符串目标,**低 ROI,不建议。**

### #4 — `lease_management.py` 孤儿(唯一真问题)
`src/backlink_publisher/cli/lease_management.py`(`LeaseManager`,防并发发布)**全仓 0 处 import**,且处于 **untracked 未提交**状态(挂在 `feat/copilot-qna` 工作树)。是某次未完成的 WIP 死代码。
**裁定**:**唯一行动项**——决定「接线+测试+提交」或「删除」。属于收敛/卫生,不属于 main 分支重构。

---

## 可选 backlog(若将来确有需要)

| 项 | Blast radius | 风险 | ROI | 建议 |
|---|---|---|---|---|
| `webui_store` → `shared_store` 重命名 | 8 后端 + 全 webui + mock 串 | 中 | 低 | ❌ 不做(已接受现状) |
| 5 文件归并 `services/` | 各 1–3 import 点 | 中 | 低 | ⚪ 可选 |
| `_report_engine` 去下划线 | CLI + mock 串 | 中 | 低 | ❌ 不做 |
| 孤儿 `lease_management.py` 去留 | 0 | 低 | — | ✅ 做(决定 wire/删) |

---

## 结论

**这是一个成熟、被主动治理的代码库**(24 子包 + 分层 webui + 双闸门 + 已 ship 的 5 份相关计划)。
笼统的「全面优化 / 拆分模组 / 前后端分离」**已无可做之事**;少数疑点是刻意设计或低 ROI 整洁度偏好。
按既有判断:**瓶颈是执行不是规划**——价值在收敛在飞改动、清理孤儿、ship 零阻塞件,而非新一轮重构。
