# CLAUDE.md — Stock Scanner (个人量化选股系统)

> 最后更新：2026-11-12 | 本次更新：RF-01 Phase 7 P7-3 —— 评分灰度收口（ADR-0014，用户拍板删 flag）：score_4d 唯一路径 = ScoringKernel（返回值恒等，L5 285 样本锁定）、L6 对拍退役、GRAY_CONSUMERS/死 API 删除、L1 基线 1293 passed；评分域过渡层清零

## 工作流

**开发流程**: `需求 → 验证 → 实现 → 回测 → 记录`

**调试流程**: `查 Skill → 诊断 → 修复 → 验证 → 沉淀`

**复杂调试**: `/systematic-debugging` 系统化排查

---

## 项目身份

- **类型**: Python 量化选股工具 + REST API 服务
- **定位**: A股个人量化投资辅助系统 (非机构级，注重实用性)
- **技术栈**: Python 3.12 / FastAPI / pandas / XGBoost / LightGBM
- **数据源**: Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富

---

## 技术栈

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

---

## 项目结构

```
stock-scanner/
├── api.py                 # FastAPI 装配层（仅 include_router；端点见 routers/）
├── routers/               # 域路由包（common.py + 17 域模块）
├── config.py              # 全局配置 (策略/风控/LLM/ML/组合)
├── dashboard.py           # Streamlit Dashboard
├── factors.py             # 因子库 (技术指标/市场体制/宏观门控)
├── notifier.py            # 飞书推送
├── paper_trading.py       # 纸带交易
├── run.py                 # CLI 入口
├── run_v2.py              # V2 策略实时扫描
├── scanner.py             # 选股策略主模块
├── trading_journal.py     # 交易日志
│
├── backtest/              # 回测模块（包）
│   ├── __init__.py        # 兼容旧 import（from backtest import ... 仍可用）
│   ├── core.py            # 主回测引擎 (run_factor_backtest, get_stock_data)
│   ├── engine.py          # 单股信号测试 (run_backtest, BacktestConfig)
│   ├── full.py            # 全市场并行回测 (run_full_backtest)
│   ├── rebalance.py       # 月频调仓回测 (run_rebalance_backtest)
│   ├── stress.py          # 压力测试 (run_stress_test)
│   ├── unified.py         # **唯一对外入口** (BacktestRunner/BacktestRequest, 6 mode)
│   └── legacy/            # 未接线的回测能力（benchmark/monte_carlo），保留不删
│
├── lib/                   # 核心业务模块 —— 按职责分 8 个子包 (2026-11-07 重构)
│   ├── data/              # 数据层：多源获取/缓存/限流/协议规范化 (37)
│   ├── scoring/           # 评分体系：多维打分/因子/评委团/估值 (30)
│   ├── strategy/          # 策略层：选股策略/编排管道/调仓 (21)
│   ├── risk/              # 风控执行：风控/仓位/约束/成本/滑点 (14)
│   ├── portfolio/         # 组合与因子分析：组合构建/优化/IC (10)
│   ├── ml/                # 机器学习：训练/推理/数据管道/监控 (5)
│   ├── monitoring/        # 监控告警：异常检测/告警/日志/体制 (9)
│   └── infra/             # 横切层：认证/通用工具/LLM工具/共享状态 (9)
│   # 归属表与依赖方向约束见 RULES.md §1.3
│
├── scripts/               # 工具脚本 (12 个)
│   ├── backtest_charts.py # matplotlib 回测图表 [NEW]
│   ├── daily_scan.py      # 每日扫描脚本 [NEW]
│   ├── update_tdx_data.py # TDX 数据更新
│   ├── backfill_ic.py     # IC 历史回填
│   └── ...                # 其他工具脚本
│
├── tests/                 # 测试套件 (38 个文件)
│   └── test_new_modules.py    # 新模块测试 (32 tests) [NEW]
│
├── reporter/              # 报告生成
├── static/                # 前端 Dashboard (HTML/JS/CSS)
├── config/                # 配置文件
├── docs/                  # 文档
├── cache/                 # 数据缓存
└── .env                   # 敏感配置 (禁止 commit)
```

---

## 核心功能

### 五大选股策略

| 策略 | 函数 | 说明 |
|------|------|------|
| 涨停板 | `get_zt_candidates()` | 追踪涨停后连板股 |
| 量价突破 | `get_volume_breakout_candidates()` | 价格+成交量突破 |
| 热门板块 | `get_hot_board_candidates()` | 板块资金流向 |
| 龙虎榜 | `get_lhb_candidates()` | 机构席位跟踪 |
| 多因子 | `get_multi_factor_candidates()` | Alpha多因子模型 |

### 四维评分体系

| 层级 | 维度 | 评委数 |
|------|------|--------|
| L1 | 基本面 | 9评委 ABSTAIN |
| L2 | 资金流 | 9评委 ABSTAIN |
| L3 | 新闻/情绪 | 9评委 ABSTAIN |
| L4 | 技术面 | 9评委 ABSTAIN |

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

### API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/scan` | GET | 选股 + 风控 + 组合优化 |
| `/api/score/{code}` | GET | 单股四维评分 |
| `/api/stock/{code}` | GET | 个股因子详情 |
| `/api/market` | GET | 市场概况 + 风控 |
| `/api/positions` | GET | 持仓概况 |
| `/api/risk` | GET | 完整风控评估 |
| `/api/exposures` | GET | 因子暴露追踪 |
| `/api/dashboard` | GET | 仪表盘汇总 |
| `/api/backtest` | POST | 回测 |

---

## 工作流规则

### Python 代码验证

```bash
# 语法检查
python -m py_compile <file.py>

# 运行测试
pytest tests/ -v

# API 启动
uvicorn api:app --reload --port 8000
```

### 数据源优先级

Tushare (主) → AkShare → WeStock → 通达信 → Baostock → 东方财富

自动降级，更高优先级失败时切换。

### 环境变量

`.env` 文件包含敏感信息，**禁止 commit**:
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `TUSHARE_TOKEN`

---

## ML 模型状态

- **模型**: XGBoost 20日预测
- **Rank IC**: 0.074
- **融合后夏普比率**: 3.80
- **线程安全**: 已清零 (133 tests PASS)

---

## 可用 Skill 与插件映射

### 项目专用

| Skill | 用途 | 触发场景 |
|:---|:---|:---|
| `stock-analysis-helper` | A 股量化分析速查 | TDX 数据/股票扫描/因子计算 |
| `quant-auditor` | 量化系统五维审计 | 代码质量/安全/性能审查 |
| `python-design-patterns` | Python 设计模式 | 代码重构/架构优化 |
| `dignified-python` | 生产级 Python 标准 | 代码规范/类型标注 |

### Skill 路由速查（stock-scanner 专属）

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

### 流程工具

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

### 通用工具

| Skill | 用途 | 触发场景 |
|:---|:---|:---|
| `audit` | 多项目并行审计 | 安全/质量/性能扫描 |
| `health` | 项目健康检查 | DEEP 任务完成后 |
| `parallel-scan` | 多 Agent 并行扫描 | 多维度快速检查 |
| `session-report` | session 用量报告 | token 消耗分析 |

### 完整目录

所有可用 Skill 详见：`~/.claude/projects/C--Users-12739/memory/skills/skills-catalog.md`

## Task 驱动工作流

### Bug 修复
```
1. TaskCreate: "修复 <问题> - <简述>"
2. 诊断问题根源 (`/systematic-debugging`)
3. 修复 + pytest 验证
4. experience-capture 沉淀
5. git commit
```

### 策略/因子修改
```
1. TaskCreate: "修改 <策略/因子>"
2. 修改 config.py / factors.py
3. pytest tests/ 验证
4. 回测验证 (api.py 或 run.py)
5. git commit
```

## 验证标准

> **权威口径是 `RULES.md §4.1` 的 L0–L8 分层门禁**（提交前必跑，不可跳级）。
> 下表仅为 CLI / 服务的快速手工验证，编号与门禁层级无关，避免两套 L2/L3 口径混淆。

| 动作 | 命令 | 用途 |
|:-----|:-----|:------|
| 语法 | `python -m py_compile <file>.py` | 单文件语法检查 |
| 单测 | `pytest tests/ -v` | 单元测试 |
| CLI | `python run.py --help` | 命令行入口验证 |
| 服务 | `uvicorn api:app --reload` | API 手工验证 |
| **L8** | `python scripts/blocking_io_check.py --check` | 阻塞 IO 基线：blocking 只准降 / other 冻结；B1 起覆盖 `/ws`、非 `/api/` 路由、中间件与 async helper（独立 extended 集合） |
| **L8 自证** | `python scripts/blocking_io_check.py --selftest` | 6 类注入回归 + 3 条 Tier B 语义断言，证明门禁会因回归判红 |
| **L8 取证重建** | `python scripts/blocking_io_check.py --recapture --mode scope --allow-endpoints "..."` | 判据范围扩展专用模式：既有 HTTP 端点必须逐字段不变，新增扩展端点须显式列出；证据写 G4 历史 |
| **L9** | `python scripts/offload_switch_diff.py --check` | 迁移 AST 等价 + off/on 响应 diff + 线程池自证；RF-02/ADR-0015 后：35 严格冻结 + 2 有意分歧（notifications/strategy_recommend 行为修复，参见 `MIGRATION_DIVERGENCES`） |
| **L10** | `python scripts/concurrency_probe.py --check` | 20 并发 P95 + 前端路径「源码重算 vs 登记清单」双校验（数量不再硬编码）；`--tier-b` 清单由 L8 `--check` 自动校验 |

## 强制触发规则

| 条件 | 必须执行 |
|:-----|:---------|
| 工作开始前 | `/reflection-retriever` 查经验 (代码/CPU 密集型项目) |
| 数据源修改 | 测试多数据源降级 |
| 策略逻辑修改 | 回测验证 + Walk-forward |
| Bug 调试 >10min | `/experience-capture` |
| P0/P1 Bug | `/fix-verify-loop` |
| 新增 API 端点 | 过 API 安全 Checklist（输入校验→认证→鉴权→错误消息→资源限制）|

## 常见错误

| 症状 | 原因 | 解决 |
|:-----|:-----|:------|
| Tushare Token 过期 | 数据获取失败 | 更新 `.env` 中的 Token |
| 数据降级触发 | 高优先级数据源失败 | 检查网络/账户状态 |
| 回测结果异常 | 涨停/停牌处理逻辑 | 检查 `broker.py` |
| API 启动失败 | Port 被占用 | `netstat -ano \| grep :8000` |

---

## Session 结束检查

```
1. [ ] pytest tests/ 通过
2. [ ] 回测结果无异常
3. [ ] git status 确认
```

---

## Task 状态追踪

| 状态 | 含义 |
|:-----|:------|
| `pending` | 等待开始 |
| `in_progress` | 执行中 |
| `completed` | 已完成 |

---

## API 服务管理

### 启动服务
```bash
uvicorn api:app --reload --port 8000
```

### 停止服务
```bash
# 查 port
netstat -ano | grep :8000 | grep LISTEN
# 杀进程
taskkill //F //PID <PID>
```

### 测试 API
```bash
curl http://localhost:8000/api/market
curl http://localhost:8000/api/scan
```

---

## 数据源调试

| 症状 | 排查方式 |
|:-----|:----------|
| 数据获取失败 | 检查 `.env` Token 是否有效 |
| 频繁降级 | 检查高优先级数据源网络 |
| 缓存过期 | 删除 `cache/*.parquet` 重新获取 |

---

## 关键文件

| 文件 | 说明 |
|:-----|:-----|
| `config.py` | 全局配置 (策略/风控/LLM/ML；env 单一真相源 + 调用时 accessor) |
| `docs/refactor/MIGRATION-GUIDE.md` | 迁移指南：RF-01 全部破坏点（BC-01~08b）before/after + 回滚方式 |
| `reporter/scan_notify.py` | 盘后选股信号飞书推送/控制台输出（原 notifier.py，P7-2 平迁） |
| `scanner.py` | 选股策略入口 |
| `lib/strategy/pipeline.py` | 策略编排核心 |
| `lib/data/data_layer.py` | 多源数据层 |
| `.env` | 敏感配置 (禁止 commit) |
| `cache/models/` | ML 模型存储 |
| `scripts/update_tdx_data.py` | TDX 日K线数据更新脚本 |
| `RULES.md` | **项目规约** (目录结构/import/路径基准/**回测调用规约**/验证分层硬约束)。改代码前先读 |
| `docs/refactor/pitfalls.md` | **踩坑集** (真实坑：现象→根因→修复→推广；条数以该文件头部「规模」行为唯一真源，此处不硬编码)。动 `lib/`、`tests/`、CI 前先读 |
| `docs/refactor/README.md` | 重构度量基线说明 (契约/覆盖率/对拍三把尺子的口径与自证记录) |
| `docs/plans/2026-11-07-refactor-plan.md` | RF-01 分阶段重构计划 (§七 为逐批执行记录/验收/复盘) |
| `lib/scoring/scoring_contract.py` | **统一评分契约**：`ScoringResult` / `ScoringKernel` / 四维真源声明（RF-01 Phase 4） |
| `lib/scoring/scoring_adapters.py` | 7 模块 / 9 入口的纯转发适配器 + `build_kernel()` |
| `lib/scoring/scoring_weights.py` | 评分权重唯一定义点 + 外部权重源登记 |
| `lib/scoring/scoring_gateway.py` | **灰度判定唯一入口**：`score_4d(consumer, ...)`，off 直调旧实现 / on 经内核（阶段 B） |
| `scripts/scoring_baseline.py` | 第 5 把尺子：评分对拍基线（9 入口 × 285 样本，`--capture` / `--check`）。**注意 `--check` 的 diff 只打印前 30 条**，排查须自行导出全量。`constraints` 登记 `judge_groups_source` / `judge_groups_weights`（G5 机器判据，配置源一变即判红） |
| `docs/audit/l5-scoring-baseline-drift-2026-11-13.md` | **L5 34-diff 定因 + 重建记录**：根因 = `4b9c2d3` 删 `config/judges.json` 后 judge_panel 权重 0.20→1.00，仅展示字段 `group_scores` ×5、决策字段零变化 ⇒ 基线过期非代码回归。2026-11-13 已按 G4 用 `--capture` 重建，L5 恢复 PASS；见坑表 P-78 |
| `scripts/scoring_switch_diff.py` | 第 6 把尺子：三分支开关前后整份响应 diff + 自证内核被调用 + 逐个消费者自证（`--check` / `--branch endpoint`） |
| `docs/refactor/scoring-baseline.json` | 评分对拍基线读数（改动评分前后必比对） |
| `docs/adr/0003-*.md` | 基线 pin 修复 + Phase 4 归一范围 / 覆盖率判据修正 |
| `docs/adr/0004-*.md` | Phase 5 端点归属表基线 + 前端路径读数 18→36 修正 |
| `scripts/route_ownership_check.py` | 第 7 把尺子：17 域 / 68 端点归属 + 鉴权/async + 契约五重校验（`--capture` / `--check`） |
| `docs/refactor/contracts/route-ownership.json` | 68 端点归属表（Phase 5 拆分前后比对的权威读数） |
| `docs/refactor/contracts/frontend-api-paths.json` | 前端 36 条 API 路径清单（Phase 5 实跑验收依据） |
| `scripts/blocking_io_check.py` | **第 8 把尺子 / L8 门禁**：阻塞 IO 基线采集、`--check`（blocking 只准降 / offloaded 只准升 / other 冻结 / 端点集合与 L7 一致）、`--selftest`（6 类注入回归 + 3 条 Tier B 语义断言）；B1 起分 `endpoints`(68 HTTP) 与 `extended_endpoints`(6：/ws、非 /api/ 路由、中间件) 两个集合，Tier B 按符号组合语义分类，`--recapture --mode scope` 做范围扩展取证；B2 起 `--recapture` 的 G4 字段级取证同时覆盖两个集合 |
| `docs/refactor/contracts/blocking-io-baseline.json` | Phase 6 起点读数：68 端点 / 1052 调用 / 242 阻塞调用 / 63 端点含阻塞点；含 `plan_stated_target` 对照 |
| `docs/adr/0006-*.md` | 第 6 次判据修正：绝对阈值「≤1」→ 单调下降 + Tier A/B 分层退出判据 |
| `tests/test_blocking_io_check.py` | L8 的 11 例机器判据（含 helper 展开、表外符号判红、尺子自证） |
| `lib/infra/async_offload.py` | **Phase 6 唯一 offload 入口**：`run_blocking(consumer, fn, ...)`，off 同步直调 / on 走线程池；`OFFLOAD_CONSUMERS` 显式登记 |
| `scripts/offload_switch_diff.py` | **L9 门禁**：固定 commit `523b353`（backtest 系）+ `77bc366`（第 4 批）源码 AST 逐语句等价 + off/on 整份响应 + 线程池调用自证 + 消费者登记双向对齐；RF-02/ADR-0015 演化：37 迁移中 35 严格冻结 + 2 有意分歧（`MIGRATION_DIVERGENCES` 清单——notifications/strategy_recommend 因 D1/D2 行为修复豁免逐字等价，仅校验接线完整性，行为正确性由 `tests/features/test_rf02_bad_endpoints.py` 覆盖） |
| `docs/refactor/contracts/blocking-io-recapture-evidence.json` | L8 基线历次重建的 G4 字段级取证（历史数组，不覆盖） |
| `docs/adr/0007-*.md` | 第 2 批决策：灰度入口 / Tier A 切换 / 共享状态加锁 / 分层重建基线 |
| `tests/features/test_async_offload.py` / `tests/test_shared_state_locks.py` / `tests/test_phase6_batch2.py` | 19 + 5 + 5 例：开关两条路径、并发状态流转、收口 AST 守卫与取证一致性 |
| `scripts/concurrency_probe.py` | **L10**：20 并发 × 3 轮 P95（批次发起计时，坑表 P-70）+ 前端路径由 L7 `capture_frontend_paths()` 重算后与登记清单比对（批次 B 去掉硬编码 36） |
| `docs/refactor/contracts/blocking-io-tier-b.json` | Tier B 168 条逐条理由 + **30 个 Tier A 候选端点**显式遗留（收官判据依据） |
| `docs/adr/0008-*.md` / `docs/adr/0009-*.md` | 缓存范围裁剪；压测口径修订与收官前核验结论 |
| `tests/test_phase6_batch3.py` | 7 例：既有缓存证据、禁止偷偷加缓存开关、压测口径、Tier B 机械分类与遗留不可隐藏 |
| `docs/audit/security-performance-review-2026-11-14.md` | **全仓安全/性能审查（2026-11-14）**：2 条 P0（配置注入永久污染 PRESETS 单例 / DOM XSS→sessionStorage 密钥接管）+ 12 条 P1 + 15 条 P2；含 L8 门禁盲区根因分析（只扫 `/api/` 装饰器端点、Tier B 按 tail 名机械分类）与三批次修复顺序建议。改 `routers/`、`lib/`、`static/` 前先查此表 |
| `docs/adr/0020-batch-a-security-hardening.md` | **批次 A 安全加固 ADR**：5 项根因修 + L8 G4 recapture 取证（5 变 / 63 未动逐字段一致 / blocking 107→106）+ L9 第 3 项有意分歧的依据；含 L3/L10 既有判红的对照实验结论 |
| `tests/features/test_batch_a_security.py` | 批次 A 的 22 例机器判据：schema 自动生成零遗漏、恶意配置拒绝、export→import 往返、get_config 隔离、错误上报 401/413/400、缓存键隔离、esc 单一实现、innerHTML 静态守卫、routers 禁裸路径基准 |
| `tests/features/test_batch_b_gates.py` | 批次 B 的 18 例机器判据：L3 情绪 pin 抗污染、L4 `start_date` 透传、L10 清单漂移；B1 L8 视野/语义分类；B2 单后台广播、`broadcast` 推全部并摘除失效连接、backup 白名单排除 `cache/` 与原子落地、限流单一真源+分桶 |

---

## TDX 数据更新

**数据路径**: `D:/TDX/vipdoc`（通过 `TDX_DATA_PATH` 环境变量配置）

**下载页面**: `https://www.tdx.com.cn/article/alldata.html`

**更新脚本**:
```bash
python3 scripts/update_tdx_data.py              # 增量更新（跳过无变化的包）
python3 scripts/update_tdx_data.py --force     # 强制重新下载全部 16 个包
python3 scripts/update_tdx_data.py --package shlday  # 只下载单个包
python3 scripts/update_tdx_data.py --list       # 列出所有包
```

**增量更新机制**:
- 用 HTTP HEAD 请求检查 TDX 服务器 ZIP 文件的 `Last-Modified` 时间
- 元数据存储在 `D:/TDX/update_meta.json`
- 服务器文件未更新时跳过下载，节省时间
- 首次运行后生效；需要时可用 `--force` 强制全量更新

**定时任务**: 每个工作日 18:30 自动运行 (收盘后 ~3.5 小时)

**环境变量**:
```bash
export TDX_DATA_PATH="D:/TDX/vipdoc"
```

### 数据包清单（共 16 个）

| 包名 | 内容 | 路径 | 文件数 |
|:-----|:-----|:-----|-------:|
| `shlday` | 上证所有证券日线(TCKV4=0) | `sh/lday/` | 20,686 |
| `szlday` | 深证所有证券日线(TCKV4=0) | `sz/lday/` | 19,383 |
| `bjlday` | 北证所有证券日线 | `bj/lday/` | 582 |
| `shzsday` | 上证指数日线 | `sh/zs/` | 339 |
| `szzsday` | 深证指数日线 | `sz/zs/` | 463 |
| `tdxzs_day` | 通达信板块指数日线 | `tdx/zs/` | 1,317 |
| `shlday_4` | 上证所有证券日线(TCKV4=1 前复权) | `sh/lday_4/` | 20,686 |
| `szlday_4` | 深证所有证券日线(TCKV4=1 前复权) | `sz/lday_4/` | 19,736 |
| `shqqday` | 上海股票期权日线 | `sh/option/` | 11,680 |
| `szqqday` | 深圳股票期权日线 | `sz/option/` | 7,544 |
| `xsbday` | 股转系统日线 | `xsb/lday/` | 11,163 |
| `62day` | 中证指数日线 | `csi/zs/` | 3,010 |
| `ggtday` | 港股通日线 | `ggt/lday/` | 3,467 |
| `33day` | 开放式基金日线 | `fund/open/` | 29,773 |
| `34day` | 货币型基金日线 | `fund/money/` | 1,207 |
| `38day` | 宏观指标日线 | `macro/lday/` | 219 |

### 其他可用数据包（不在日常更新范围，需要时单独下载）

| 包名 | 内容 |
|:-----|:-----|
| `tdxfin` | 财务报表数据（季度/年度） |
| `tdxgp` | 高频数据（分时/Tick） |
| `hsjday` | 沪股通日线 |
| `ScJyData_zbca` | 特定市场数据 |

---

## 维护提示

- `config.py` 是全局配置核心，修改前确认影响范围
- `lib/strategy/pipeline.py` 是策略编排核心，修改后运行回测验证
- 数据源适配器在 `lib/data_*.py`，新增数据源时参考现有模式
- ML 模型存储在 `cache/models/`，更新后重新验证 Walk-forward

---

## 新模块工作流（2026-05-30）

### 因子权重优化
```bash
# 评估当前权重
python -m lib.scoring.factor_optimizer --eval-only --symbols 600519 000858

# 运行 GA 优化（50代×30种群）
python -m lib.scoring.factor_optimizer --symbols 600519 000858 --generations 50 --population 30

# 结果保存到 cache/optimizer/optimal_weights_latest.json
# 应用：替换 factors.py IC_FACTOR_WEIGHTS
```

### 统一回测
```python
from lib.monitoring.backtest_unified import BacktestRunner, BacktestRequest

runner = BacktestRunner()
result = runner.run(mode="rebalance", symbols=["600519", "000858"], days=300)
# mode: simple(单股) / full(全市场) / rebalance(调仓) / factor(因子)

# 直接调用（推荐）
from backtest.core import run_factor_backtest
from backtest.rebalance import run_rebalance_backtest

# 兼容旧写法（仍可用）
from backtest import run_factor_backtest
```

### 投资者风格评委团
```python
from lib.scoring.investor_agents import AgentPanel
panel = AgentPanel()
votes = panel.vote(hist_df, code="600519", data_factors={...})
# votes["signal"]: bullish/bearish/neutral
# votes["consensus"]: 全票看多/多数看多/分歧/...
```

### 每日扫描
```bash
python scripts/daily_scan.py           # 单次运行
python scripts/daily_scan.py --no-push # 不推送飞书
```

### 回测图表
```bash
python scripts/backtest_charts.py --symbols 600519,000858 --days 500
# 输出到 cache/charts/equity_curve.png, trade_heatmap.png, factor_contribution.png
```

### 信号追踪
```python
from lib.scoring.signal_tracker import SignalTracker
tracker = SignalTracker()
tracker.record_signal("600519", "bullish", 0.8, source="ic_score")
tracker.update_actual_returns()  # 事后更新实际收益
report = tracker.evaluate_signals(days=30)
# report["accuracy"], report["profit_loss_ratio"], ...
```

### 数据源优先级
```
本地 TDX 文件（D:/TDX/vipdoc/）→ akshare API → 其他数据源
融资融券：SSE/SZSE 接口（非交易日自动回退到上一个交易日）
情绪分析：akshare 新闻接口
```
