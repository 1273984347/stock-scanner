"""Pipeline 模块化拆分

- orchestrator.py  - 核心编排逻辑
- risk_checker.py  - 风控检查
- factor_monitor.py - 因子健康检查
- optimizer.py     - 组合优化
"""
from .orchestrator import run_all_strategies
from .risk_checker import check_portfolio_risk, check_factor_health
from .optimizer import optimize_portfolio
