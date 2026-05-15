# stock-scanner 项目记忆

## 项目概述
A股量化交易系统 v3.0，FastAPI + vanilla HTML/JS 前端，akshare 数据源

## 架构
- `scanner.py` - 核心引擎（5策略 + 四维评分 + 组合优化 + 审计日志）
- `backtest.py` - 回测模块（ABroker + RiskSizedPosition + ATR止损 + 基准对比 + 数据校验）
- `api.py` - FastAPI REST API v3（12个端点）
- `factors.py` - 因子库（6大策略信号 + 技术指标 + 市场体制检测）
- `config.py` - 配置（balanced/aggressive/conservative 三预设）
- `lib/portfolio_optimizer.py` - 组合优化器（均值方差 + 行业约束 + 换手惩罚）
- `lib/audit_logger.py` - 审计日志（SQLite存储决策历史）
- `lib/judge_panel.py` - 裁判团（7派34评委）
- `lib/scorer.py` - 四维评分（基本面35%+资金面35%+消息面20%+技术面10%）
- `lib/risk_manager.py` - 风控（VolCorrRiskManager + 风险预算）
- `lib/` - 其他子模块（broker, evaluator, analyzer, walkforward, alpha 等）

## API 端点清单
- GET /api/scan - 选股+风控+组合优化
- GET /api/score/{code} - 单股四维评分
- GET /api/stock/{code} - 单股因子详情
- GET /api/market - 市场概况+风控
- GET /api/positions - 持仓概况
- GET /api/risk - 完整风控（宏观门控+风险预算+压力测试）
- GET /api/exposures - 因子暴露追踪
- GET /api/dashboard - 仪表盘汇总
- GET /api/audit - 审计日志列表
- GET /api/audit/{id} - 审计详情
- POST /api/backtest - 回测

## 前端结构（5面板仪表盘）
1. 组合优化 - 权重表 + 行业饼图 + 收益波动图 + 组合指标
2. 选股结果 - 策略Tab + 表格 + 信号/评分图
3. 风控中心 - 宏观门控 + 风险预算 + 压力测试3场景
4. 因子暴露 - 加权平均暴露柱图 + 方向标签
5. 决策审计 - 扫描历史时间线

## 仍需改进（需要外部资源或大重构）
- 实盘交易执行（需券商API如XtQuant）— 这是P0但需要外部账户
- 实时风控监控（需WebSocket推送）— 需要持续运行的服务
- 无生存偏差免费数据源 — 需Tushare Pro
- 因子权重硬编码（AlphaSynthesizer.optimize_weights_ic_ir未被调用）
- 因子无行业中性化（AlphaSynthesizer.neutralize_sector未被调用）
- 回测默认3只手选股（需扩展为自动构建股票池）
- 行业政策信号（industry_policy）无免费API
- ak.stock_news_em 有regex bug

## 编码规范
- PowerShell 用 `py` 不用 `python`
- 涨→红色，跌→绿色（A股惯例）
- 货币用 ¥
