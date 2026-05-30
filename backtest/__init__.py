"""backtest 包 —— 回测模块统一入口

子模块：
- core: 主回测引擎 (run_factor_backtest, get_stock_data)
- engine: 单股信号测试 (run_backtest)
- full: 全市场并行回测 (run_full_backtest)
- rebalance: 月频调仓回测 (run_rebalance_backtest)
- stress: 压力测试 (run_stress_test)

兼容旧 import：
    from backtest import run_factor_backtest  # 仍然可用
    from backtest.core import run_factor_backtest  # 推荐
"""

from backtest.core import (
    get_stock_data,
    run_factor_backtest,
    run_walk_forward,
    run_mvp_backtest,
    AllDataSourcesUnavailableError,
    _compute_atr_for_position,
)

from backtest.engine import (
    run_backtest,
    BacktestConfig,
    Trade,
)

from backtest.full import run_full_backtest
from backtest.rebalance import run_rebalance_backtest
from backtest.stress import run_stress_test, format_stress_report
