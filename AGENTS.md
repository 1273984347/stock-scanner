# AGENTS.md — Stock Scanner 项目 Agent 指南

> 本文件面向进入本仓库工作的 AI Agent（及新接手的人类维护者），提供项目用途、技术栈、目录结构、工作流、验证分层与必须遵守的铁律。动手前请通读；更细的规则以文末「权威文档」为准。
>
> **维护契约（防乱写）**：本文档是 Agent 协作指南，只允许结构性编辑——① 保持 §1-§7 骨架不变；② 新增铁律/规则必须**就地并入对应章节**并全篇去重（禁止追加「速记 / 补充」尾部段落）；③ 版本脚注更新须与当前 HEAD（`git rev-parse --short HEAD`）核对一致；④ 与 交接文档 / CHANGELOG / STRUCTURE 冲突时以后三者为准。

---

## 1. 项目用途与定位

**Stock Scanner**（A 股盘后选股工具）是一个 **A 股个人量化投资辅助系统**（非机构级，注重实用性）：

- 整合 quant-system + ai-hedge-fund + cryptoquant-ai 的 A 股适用版选股系统
- 五大选股策略（涨停板 / 量价突破 / 热门板块 / 龙虎榜 / 多因子）
- 四维评分体系（L1 基本面 / L2 资金流 / L3 新闻情绪 / L4 技术面，各 9 评委 ABSTAIN）
- 市场体制检测（TREND_UP / TREND_DOWN / RANGE / RISK_OFF）+ 宏观风险门控（仓位乘数 100%/50%/0%）
- 回测 / ML 预测 / 纸带交易 / 飞书推送 / REST API 服务

**版本号语义**：`vX.Y.Z` 是内容/功能发布批次编号，不适用 SemVer。每个发布批次有唯一 W### ID；判断「改了什么」看 CHANGELOG.md，不要从版本号推断兼容性。

---

## 2. 技术栈

| 类别 | 技术 |
|------|------|
| 核心语言 | Python 3.12 |
| 数据处理 | pandas, numpy, scipy |
| 量化框架 | backtrader, adata |
| ML 模型 | XGBoost, LightGBM |
| Web 框架 | FastAPI + uvicorn |
| 缓存 | Parquet 文件 + SQLite (audit.db) |
| 外部 API | 飞书 Webhook、DeepSeek/Kimi/Mimo LLM |
| 部署 | uvicorn (API 服务) |
| 数据源优先级 | Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富 |

---

## 3. 目录结构

```
stock-scanner/
├── api.py / run.py / run_v2.py     # FastAPI 端点 / CLI 入口 / V2 实时扫描
├── config.py / factors.py           # 全局配置 / 因子库
├── scanner.py / paper_trading.py    # 选股策略主模块 / 纸带交易
├── dashboard.py / notifier.py       # Streamlit Dashboard / 飞书推送
├── trading_journal.py               # 交易日志
├── backtest/                        # 回测模块（core/engine/full/rebalance/stress）
├── lib/                             # 核心业务模块（87+ 个：数据层/策略/风控/ML/回测）
├── scripts/                         # 工具脚本（daily_scan/update_tdx_data/backtest_charts 等）
├── tests/                           # 测试套件（60+ 测试）
├── reporter/                        # 报告生成（图表/飞书推送/IC 报告）
├── docs/                            # 文档（superpowers 档案/audit/规划）
├── static/                          # 前端 Dashboard (HTML/JS/CSS)
├── config/                          # 配置文件
├── cache/                           # 数据缓存（含 industry/tables 财报缓存、models/）
└── .env                             # 敏感配置（禁止 commit）
```

完整目录与子模块说明见 STRUCTURE.md。

---

## 4. 工作流

### 开发流程
`需求 → 验证 → 实现 → 回测 → 记录`

### 调试流程
`查 Skill → 诊断 → 修复 → 验证 → 沉淀`（复杂调试用 `/systematic-debugging`）

### 验证分层（L0-L3）

| 层级 | 命令 | 用途 |
|:-----|:-----|:-----|
| **L0** | `python -m py_compile <file>.py` | 语法检查 |
| **L1** | `pytest tests/ -v` | 单元测试 |
| **L2** | `python run.py --help` | CLI 验证 |
| **L3** | `uvicorn api:app --reload` | API 服务验证 |

### 提交规范
Conventional Commits：`feat:` / `fix:` / `perf:` / `docs:` / `chore:`；gitleaks pre-commit 自动扫描（Passed 后才允许提交）。

---

## 5. 核心功能速览

### 五大选股策略

| 策略 | 函数 | 说明 |
|------|------|------|
| 涨停板 | `get_zt_candidates()` | 追踪涨停后连板股 |
| 量价突破 | `get_volume_breakout_candidates()` | 价格+成交量突破 |
| 热门板块 | `get_hot_board_candidates()` | 板块资金流向 |
| 龙虎榜 | `get_lhb_candidates()` | 机构席位跟踪 |
| 多因子 | `get_multi_factor_candidates()` | Alpha多因子模型 |

### 市场体制检测

| 体制 | 条件 | 行为 |
|------|------|------|
| TREND_UP | 综合 > 0.35 | 允许趋势突破买入 |
| TREND_DOWN | 综合 < -0.35 | 允许趋势突破卖出 |
| RANGE | -0.35 ~ 0.35 | 只允许均值回归 |
| RISK_OFF | 波动率过高 | 禁止所有开仓 |

### 宏观风险门控

| 状态 | 情绪阈值 | 仓位乘数 |
|------|----------|----------|
| ALLOW_FULL | > -0.10 | 100% |
| ALLOW_REDUCED | -0.30 ~ -0.10 | 50% |
| BLOCK_NEW_RISK | < -0.30 | 0% |

### API 端点（api.py）

`/api/scan` 选股+风控+组合优化 · `/api/score/{code}` 单股四维评分 · `/api/stock/{code}` 个股因子详情 · `/api/market` 市场概况+风控 · `/api/positions` 持仓概况 · `/api/risk` 完整风控评估 · `/api/exposures` 因子暴露追踪 · `/api/dashboard` 仪表盘汇总 · `/api/backtest` 回测

---

## 6. 铁律（强制触发规则）

| 条件 | 必须执行 |
|:-----|:---------|
| 工作开始前 | 通读 交接文档.md（零/一/三）+ 本文档 §5 |
| 数据源修改 | 测试多数据源降级 |
| 策略逻辑修改 | 回测验证 + Walk-forward |
| Bug 调试 > 10min | 沉淀经验（三 skill 闭环） |
| P0/P1 Bug | 修复后验证循环（fix-verify-loop） |
| 新增 API 端点 | 过 API 安全 Checklist（输入校验→认证→鉴权→错误消息→资源限制） |
| 任何文件修改后 | Grep spot-check 验证落地（E1 铁律：声明 ≠ 落地） |
| `.env` / 密钥 | 禁止 commit；gitleaks 扫描红线 |

### 常见错误速查

| 症状 | 原因 | 解决 |
|:-----|:-----|:-----|
| Tushare Token 过期 | 数据获取失败 | 更新 `.env` 中的 Token |
| 数据降级触发 | 高优先级数据源失败 | 检查网络/账户状态 |
| 回测结果异常 | 涨停/停牌处理逻辑 | 检查 `broker.py` |
| API 启动失败 | Port 被占用 | `netstat -ano \| grep :8000` |

---

## 7. 权威文档（冲突时以此优先级为准）

1. [交接文档.md](交接文档.md) — 当前状态 / 阻塞 / 下一步 / 方法论（状态快照，与 CHANGELOG 冲突时以 CHANGELOG 为准）
2. [CHANGELOG.md](CHANGELOG.md) — 所有变更的时间线真源（W### 批次 + 四件套）
3. [STRUCTURE.md](STRUCTURE.md) — 目录结构快照
4. [README.md](README.md) — 项目说明
5. [新Agent启动Prompt.md](新Agent启动Prompt.md) — 新 session 启动包
6. [docs/superpowers/](docs/superpowers/) — spec / plan / retrospective 开发档案