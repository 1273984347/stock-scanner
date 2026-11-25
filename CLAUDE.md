# CLAUDE.md — Claude Code 专属入口（stock-scanner）

@AGENTS.md

> **本文件只回答一个问题：Claude Code 有哪些工具专属行为？**
> AI 行为约束（硬约束、禁区、门禁、验收标准、提交纪律）的**唯一真源是
> [AGENTS.md](AGENTS.md)** —— 本文件不复制它们，只在需要时链接。
> Claude Code 支持 `@AGENTS.md` 导入语法，上面一行会把 AI 行为规约直接拉进上下文；
> **若当前工具不支持 `@` 导入，请直接读取 `AGENTS.md`**（它与本文件同级，路径见下）。

---

## 1. 项目一句话

A股个人量化选股工具 + REST API 服务（Python 3.12 / FastAPI / pandas / XGBoost），
数据源走新浪/腾讯网页 K 线网关 + westock。**项目是什么、怎么用**见 [README.md](README.md)。

---

## 2. Claude Code 专属：Skill 与插件路由

> 通用工作流（开发/调试流程、强制触发规则、Task 驱动、常见错误）见 `AGENTS.md` §5 / §6。

### 2.1 项目专用 Skill

| Skill | 用途 | 触发场景 |
|:---|:---|:---|
| `stock-analysis-helper` | A 股量化分析速查 | TDX 数据/股票扫描/因子计算 |
| `quant-auditor` | 量化系统五维审计 | 代码质量/安全/性能审查 |
| `python-design-patterns` | Python 设计模式 | 代码重构/架构优化 |
| `dignified-python` | 生产级 Python 标准 | 代码规范/类型标注 |

### 2.2 Skill 路由速查（stock-scanner 专属）

| 场景 | 首选 Skill | 备选 | 不用 |
|:-----|:----------|:-----|:-----|
| **新增 API 端点** | `fastapi` | `api-fault-handling` | — |
| **修改策略/因子** | `brainstorming` → `writing-plans` | — | — |
| **修 bug（Python）** | `systematic-debugging` | `dignified-python` | — |
| **修 bug（前端）** | `systematic-debugging` → L3 验证 | — | — |
| **代码审查** | `quant-auditor` | `code-review` | — |
| **前端重构** | `frontend-dev` | — | `design-shotgun` |
| **新增数据源** | `api-fault-handling` | — | — |
| **安全审计** | `cso` | — | — |
| **性能优化** | `performance-engineer` | — | — |
| **测试补全** | `verification-before-completion` | — | — |
| **多市场回测** | 直接执行 | — | — |
| **E2E 测试** | Playwright test runner | — | MCP 浏览器 |
| **代码部署** | `ship` | — | — |
| **健康检查** | `health` | — | — |
| **经验沉淀** | `experience-capture` | — | — |
| **修复批量问题** | `fix-verify-loop` | — | — |
| **任务前检查** | `reflection-retriever` | `skill-usage-checklist` | — |
| `fastapi` | FastAPI 最佳实践 | API 端点开发 |
| `database-admin` | 数据库管理 | SQLite/Parquet 优化 |
| `performance-engineer` | 性能工程 | CPU 密集型计算优化 |

### 2.3 流程工具

| Skill | 用途 | 触发场景 |
|:---|:---|:---|
| `brainstorming` | 需求→设计方案 | 新策略/因子设计 |
| `writing-plans` | 任务拆分 | 多步骤实施 |
| `code-review` | 代码审查 | 完成功能后 |
| `verification-before-completion` | 完成前验证 | 声称"完成"前 |
| `systematic-debugging` | 根因排查 | 调试>10min |
| `fix-verify-loop` | 批量修复验证 | P0/P1≥3个 |
| `experience-capture` | 经验捕获 | 新发现/重复踩坑 |
| `reflection-retriever` | 经验检索 | 工作开始前 |
| `codex` | OpenAI Codex 第二意见 | 策略逻辑审查 |
| `ship` | 部署+PR | GitHub 部署 |
| `cso` | 安全审计 | .env/API 安全检查 |

### 2.4 通用工具

| Skill | 用途 | 触发场景 |
|:---|:---|:---|
| `audit` | 多项目并行审计 | 安全/质量/性能扫描 |
| `health` | 项目健康检查 | DEEP 任务完成后 |
| `parallel-scan` | 多 Agent 并行扫描 | 多维度快速检查 |
| `session-report` | session 用量报告 | token 消耗分析 |

### 2.5 完整目录

所有可用 Skill 详见：`~/.claude/projects/C--Users-12739/memory/skills/skills-catalog.md`

---

## 3. 导航

| 我要…… | 去看 |
|:--|:--|
| 改代码前确认硬约束 / 跑门禁 / 提交纪律 | **AGENTS.md**（§1 硬约束 · §4 门禁 · §7 提交） |
| 知道哪个文件是真源、关键文件在哪 | **AGENTS.md** §8 关键文件索引 |
| 想违反某条全局规约 | **RULES.md** §1 例外登记（先查是否已有例外） |
| 排查「为什么会这样」 | **docs/refactor/pitfalls.md**（按症状查） |
| 装依赖、跑服务、知道项目是什么 | **README.md** |
| 排期、看还差什么 | **TODOS.md** |
| 了解重构全貌 | **docs/plans/2026-11-07-refactor-plan.md** |

> 最后更新：2026-11-20｜本次更新：**文档分层重构**（新增 `AGENTS.md` 作为 AI 行为唯一真源；
> 本文件改为 `@AGENTS.md` 导入 + 仅保留 Claude 专属；`RULES.md` 收窄为例外登记；
> 规约 ID 消歧：回测调用 B1–B5 → RB1–RB5）。
