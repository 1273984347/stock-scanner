# 更新日志

本项目所有重要变更均记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

> **W### 编号规则**：每个版本段标注唯一 W### ID（W001 起，当前 max 为 W002）。
> 每段附四件套（来源/文件/验证/状态）。新批编号先 Grep 现役段取 max+1 再写（防撞号）。
> 版本号 vX.Y.Z 为发布批次编号，不适用 SemVer；判断「改了什么」看本文件。
>
> **维护契约**：① 已发布版本段（历史）只增不删、禁改；② 版本段插入/重排时保持锚点唯一；
> ③ 每段保持四件套（来源/文件/验证/状态），单段 ≤ 25 行；④ 每批完成后须同步根目录
> 交接文档.md 与 README.md 相关口径。

## [Unreleased]

### v1.3（2026-08-26）：W001 六文档治理体系建立 + W002 A 股周期顶部预警（进行中）

> **来源**：用户学习 xiyouji 六文档体系后批准移植（docs/superpowers/specs/2026-08-26-six-docs-governance-design.md）
> - **执行（W001 已完成）**：根目录新增 AGENTS.md / CHANGELOG.md / STRUCTURE.md / 交接文档.md / 新Agent启动Prompt.md；README.md 增补维护契约 + 现役版本行；CLAUDE.md 加桥接；docs/NEXT_SESSION_PLAN.md 归档
> - **执行（W002 进行中）**：A 股周期顶部预警优化（feature/cycle-top-warning 分支）——Phase 0 数据地基 G0 修复 / Phase 1 cycle_heat 三指标历史分位 / Phase 2 top_warning + DynamicExitManager 集成 / Phase 3 run_rebalance_backtest 集成 + 财报数据源东财 datacenter 批量提速（lib/data_industry.py，1180 只 24min 零失败预热）
> - **文件**：根目录六文档、docs/superpowers/specs+plans、lib/data_industry.py、lib/cycle_heat.py、lib/top_warning.py、scripts/warm_fin_tables.py 等
> - **验证**：六文档维护契约齐全、版本行/W 编号交叉一致；W002 代码 pytest 全绿（60 tests PASS）
> - **状态**：W001 已落地；W002 进行中（A/B 回测与 G2 闸门待跑）

### v1.2（2026-08-24~08-25）：财报数据链工程强化

> **来源**：A 股周期预警数据层需求（W002 前置工程）
> - **执行**：Tushare→akshare 财报双源降级（e2a2be4）→ 行业财务序列聚合 + parquet 缓存（ec771f3）→ 逐股财报表磁盘缓存 + 板块进程 memo（b3750c0）→ 新浪批量接口（9d4fc0f）→ 东财 datacenter 批量报表接口（557069c，1180 只 24min 零失败）
> - **文件**：lib/data_industry.py、lib/data_tushare.py、lib/industry.py、scripts/warm_fin_tables.py
> - **验证**：端到端单股 6.4-7.2s 零降级；预热 1180/1180 ok fail=0
> - **状态**：已完成

### v1.1（2026-05-30）：21 新模块 + V6 自适应交易系统

> **来源**：CLAUDE.md 记录（2026-05-30 更新）
> - **执行**：新增 21 个模块（multi_period_kdj / sector_score / dynamic_weight_manager / strategy_regime_mapper / factor_optimizer / dynamic_exit / sector_rotation / investor_agents / signal_tracker / backtest_unified / paper_trading_runner / multi_market_validator / data_margin / data_fund_enhanced / data_level2 / data_northbound_rt / sentiment / tdx_batch / stock_pool 等，完整清单见 lib/）；V6 自适应交易系统（多周期 KDJ / 统一板块评分 / 动态权重 / 策略-状态映射）
> - **文件**：lib/ 下 21 个新模块、scripts/（backtest_charts / daily_scan 等）
> - **验证**：60 个测试全部通过
> - **状态**：已完成

### v0.6-v1.0（2026-06-01~06-29）：安全与数据化加固

> **来源**：git 历史 + CLAUDE.md 记录
> - **执行**：V7-V9（Alpha 引擎 / 资本配置 / 数据修正 / 归因）→ V6 后端重构与 TradingView 风格前端 → 652 测试 → quant-auditor 五维审计体系 → gitleaks + pre-commit 安全扫描 → TDX 数据更新脚本 + ML 线程安全清零（133 tests PASS）
> - **文件**：lib/alpha_*.py、lib/capital_allocator.py、lib/data_correction.py、.gitleaks.toml、.pre-commit-config.yaml、scripts/update_tdx_data.py
> - **验证**：pytest 全绿；gitleaks pre-commit Passed；ML Rank IC 0.074 / 融合后夏普 3.80
> - **状态**：已完成

### v0.2-v0.5（2026-05 中下旬）：体系成型（回测/ML/前端/评分）

> **来源**：git 历史 + CLAUDE.md 记录
> - **执行**：回测引擎（backtest/core + rebalance + stress）→ ML 集成（XGBoost 20 日预测）→ 前端 TradingView 风格 Dashboard → 四维评分体系（四层 9 评委 ABSTAIN）
> - **文件**：backtest/、lib/ml_predictor.py、lib/ml_trainer.py、lib/scorecard.py、static/、dashboard.py
> - **验证**：回测与 walk-forward 验证通过
> - **状态**：已完成

### v0.1（2026-05 上旬）：项目立项

> **来源**：git 首批 commit + README 历史
> - **执行**：整合 quant-system + ai-hedge-fund + cryptoquant-ai 为 A 股适用版；五大选股策略（涨停板 / 量价突破 / 热门板块 / 龙虎榜 / 多因子）；市场体制检测 + 宏观风险门控；多源数据层（TDX 本地 / Baostock / AkShare / Tushare）
> - **文件**：scanner.py、factors.py、config.py、lib/data_*.py
> - **验证**：基础选股 + 回测跑通
> - **状态**：已完成