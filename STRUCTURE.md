# 目录结构说明

> 本文件用于解释 `stock-scanner/` 项目各目录的用途、命名规范与协作约定。
> 当前版本：v1.3（2026-08-26）— W001 六文档治理体系落地 · 详见 [CHANGELOG.md](CHANGELOG.md)。
> **维护契约**：本文件是目录/命名/协作约定的静态快照——只在目录或约定变更时修改；禁止追加速度记/批次摘要（批次信息归 CHANGELOG）；版本行由人工维护，须与 `git rev-parse --short HEAD` 核对。

## 顶层结构

| 路径 | 用途 |
|------|------|
| `api.py` | FastAPI 端点（/api/scan、/api/score、/api/market、/api/backtest 等） |
| `run.py` / `run_v2.py` | CLI 入口 / V2 策略实时扫描 |
| `config.py` | 全局配置（策略/风控/LLM/ML/组合） |
| `factors.py` | 因子库（技术指标/市场体制/宏观门控） |
| `scanner.py` / `paper_trading.py` | 选股策略主模块 / 纸带交易 |
| `dashboard.py` / `notifier.py` | Streamlit Dashboard / 飞书推送 |
| `trading_journal.py` | 交易日志 |
| `backtest/` | 回测模块（包：core/engine/full/rebalance/stress） |
| `lib/` | 核心业务模块（110+ 个：数据层/策略/风控/ML/回测） |
| `scripts/` | 工具脚本（22 个：daily_scan / update_tdx_data / backtest_* 等） |
| `tests/` | 测试套件（60+ 测试，L1 验证层） |
| `reporter/` | 报告生成（report_generator / feishu_pusher / ic_report_generator） |
| `docs/` | 文档（superpowers 档案 / audit 审计 / 规划 / 模板） |
| `static/` | 前端 Dashboard（HTML/JS/CSS） |
| `config/` | 配置文件（judges.json 等） |
| `cache/` | 数据缓存（industry/tables 财报、models/ ML 模型、optimizer/ 等） |
| `src/`（若存在） | 前端源码（vite.config.js 相关） |
| `AGENTS.md` | Agent 协作指南 |
| `CHANGELOG.md` | 更新日志（时间线真源） |
| `交接文档.md` | 跨 session 状态快照 |
| `新Agent启动Prompt.md` | 新 session 启动包 |
| `README.md` | 项目说明 |
| `CLAUDE.md` | 平台配置桥接（指向 AGENTS.md） |
| `.env` | 敏感配置（禁止 commit） |

## 核心目录说明

### lib/ 核心业务模块（按职责分组）

| 分组 | 代表模块 | 职责 |
|------|----------|------|
| 数据层 | `data_layer.py` / `data_source_registry.py` / `data_tdx.py` / `data_tushare.py` / `data_baostock.py` / `data_akshare.py`（经 data_ashare）/ `data_sina_fin.py` / `data_industry.py` / `data_margin.py` / `data_fund_enhanced.py` / `data_level2.py` / `data_northbound_rt.py` / `data_eastmoney_fin.py` / `data_westock.py` / `data_scjy.py` / `data_tencent.py` / `data_sector.py` / `data_earnings.py` / `data_macro.py` / `data_delisted.py` / `data_cache.py` / `tdx_batch.py` | 多源数据获取/缓存/限流/降级；财报链三级源（datacenter→新浪→东财逐期） |
| 策略与因子 | `strategies.py` / `factor_strategy.py` / `factors.py`（顶层）/ `technical_signals.py` / `cycle_heat.py` / `top_warning.py` / `multi_period_kdj.py` / `sector_rotation.py` / `market_regime.py` / `pump_dump_detector.py` / `anomaly_detector.py` | 5 大选股策略 + 周期/轮动/预警因子 |
| 编排与评分 | `pipeline.py` / `scorecard.py` / `scorer.py` / `judge_panel.py` / `panel_decision.py` / `judge_strategies.py` / `investor_agents.py` / `sector_score.py` / `dynamic_weight_manager.py` / `strategy_regime_mapper.py` / `strategy_ranking.py` / `opportunity_scorer.py` / `fundamental.py` / `dcf.py` / `value_checklist.py` | 策略编排/组合优化/四维评分/评委团 |
| 风控与仓位 | `risk_manager.py` / `position_size.py` / `position_manager.py` / `capital_allocator.py` / `risk_parity.py` / `constraints.py` / `slippage_model.py` / `cost_filter.py` / `dynamic_exit.py` / `partial_exit.py` / `fill_rate.py` / `exrights_adjuster.py` / `suspension_monitor.py` / `alert_monitor.py` | 止损/回撤/仓位/冲击成本/动态退出 |
| 交易执行 | `broker.py` / `broker_data/` / `twap.py` / `live_broker.py`（含）/ `portfolio.py` / `portfolio_manager.py` / `portfolio_optimizer.py` / `rebalance_scheduler.py` / `paper_trading_runner.py` / `signal_tracker.py` / `trade_logger.py` | 券商核心/整手/T+1/纸带交易/信号追踪 |
| ML | `ml_predictor.py` / `ml_trainer.py` / `ml_data.py` / `ml_backtest.py` / `ml_ab_test.py` / `ml_monitor.py` / `model_registry.py` / `walkforward.py` / `factor_optimizer.py` / `ab_testing.py` / `bayesian_updater.py` | XGBoost/LightGBM 训练预测/回测/AB 实验 |
| Alpha 引擎 | `alpha.py` / `alpha_factory.py` / `alpha_classifier.py` / `alpha_feedback.py` / `attribution.py` | Alpha 生成/分类/反馈/归因 |
| 分析 | `analyzer.py` / `evaluator.py` / `backtest_unified.py` / `backtest_saver.py` / `multi_market_validator.py` / `indicators.py` / `technical_signals.py` / `utils.py` / `llm_analysis.py` / `kg_store.py` / `kg_neo4j.py` | 性能分析/统一回测/多市场验证 |
| 监控与通知 | `notification_logger.py` / `audit_logger.py` / `news_signal.py` / `sentiment.py` / `northbound_sector.py` / `lhb_analyzer.py` / `fundamental_fetcher.py` / `factor_monitor.py` / `strategy_snapshot.py` | 情绪/龙虎榜/北向/因子监控 |

### scripts/ 工具脚本

- `daily_scan.py` 每日扫描（--no-push 不推送飞书）
- `update_tdx_data.py` TDX 数据更新（增量/--force/--package/--list）
- `backtest_*.py` 系列：charts / simple / multi / channel / regime_analysis
- `warm_fin_tables.py` 财报缓存预热（--workers/--delay/--dry-run）
- `backtest_top_warning_ab.py` 周期顶部预警 A/B 回测（G2 闸门）
- `probe_industry_cycle.py` 行业周期数据探针（G0 闸门）
- `backfill_ic.py` IC 历史回填；`run_optimizer.py` / `compare_optimizers.py` 因子优化

### backtest/ 回测模块

| 文件 | 职责 |
|------|------|
| `core.py` | 主回测引擎（run_factor_backtest, get_stock_data） |
| `engine.py` | 单股信号测试（run_backtest, BacktestConfig） |
| `full.py` | 全市场并行回测（run_full_backtest） |
| `rebalance.py` | 月频调仓回测（run_rebalance_backtest，V1.3 起支持顶部预警 opt-in） |
| `stress.py` | 压力测试（run_stress_test） |

### docs/ 文档

| 路径 | 用途 |
|------|------|
| `superpowers/` | spec / plan / retrospective / templates（开发档案） |
| `audit/` | quant-auditor 五维审计报告 |
| `adr/` | 架构决策记录 |
| `archive/` | 归档（NEXT_SESSION_PLAN-2026-06-06 等） |
| `templates/` | 报告/文档模板 |
| `*.md` | architecture / strategies / factor_library / backtest-log 等 |

## 命名规范

- 测试文件：`tests/test_*.py`
- 工具脚本：`scripts/*.py`（`_` 前缀 = 一次性/诊断脚本，不入库门禁）
- 设计/计划文档：`docs/superpowers/specs|plans/YYYY-MM-DD-<topic>.md`
- 数据缓存：`cache/*.parquet`、`cache/industry/tables/{code}.pkl`
- 敏感配置：`.env`（禁止 commit，`.env.example` 提供模板）