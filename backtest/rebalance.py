"""月频调仓回测 —— 用历史数据验证调仓器+退出规则

在历史数据上模拟完整的调仓流程：
- 每 20 个交易日执行一次扫描
- 集成退出规则（夏普<1.0 或回撤>25% 停止）
- 集成连亏熔断（连亏 3 笔停止）
- 集成分仓止盈（+60%/+30%/成本价梯度退出）

使用方式：
    python backtest_rebalance.py
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from lib.data.broker import ABroker, Order
from lib.risk import Holding
from config import get_config
from lib.risk.position_size import RiskSizedPosition, PositionSizeConfig
from lib.risk.risk_manager import RiskManager, RiskConfig
from lib.risk.partial_exit import PartialExitManager, ConsecutiveLossBreaker
from lib.strategy.rebalance_scheduler import RebalanceScheduler, RebalanceConfig
from factors import compute_all_signals, calc_atr, compute_ic_weighted_score

logger = logging.getLogger(__name__)


def run_rebalance_backtest(symbols=None, **kwargs):
    """[DEPRECATED] 请使用 backtest.engine.run_rebalance_backtest()"""
    import warnings
    warnings.warn(
        "rebalance.run_rebalance_backtest is deprecated, use backtest.engine.run_rebalance_backtest()",
        DeprecationWarning,
        stacklevel=2,
    )
    from backtest.engine import run_rebalance_backtest as _run
    return _run(symbols=symbols, **kwargs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    symbols = ["000001", "600519", "000858", "002415", "601318"]
    print(f"月频调仓回测 - 标的: {symbols}")
    print("=" * 60)

    result = run_rebalance_backtest(symbols, days=500, rebalance_period=20)
    if result:
        print(f"调仓次数: {result['rebalance_count']}")
        print(f"总交易: {result['trade_count']} 笔")
        print(f"卖出: {result['sell_count']} 笔")
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"平均盈利: {result['avg_win']:.2%}")
        print(f"平均亏损: {result['avg_loss']:.2%}")
        print(f"盈亏比: {result['avg_win']/result['avg_loss']:.2f}" if result['avg_loss'] > 0 else "盈亏比: N/A")
        print(f"最终夏普: {result['final_sharpe']}")
        print(f"最终回撤: {result['final_drawdown']:.2%}")
        print(f"退出规则触发: {'是' if result['is_stopped'] else '否'}")
        if result['stop_reason']:
            print(f"退出原因: {result['stop_reason']}")
        print(f"连亏状态: {result['loss_breaker_status']}")
        print()
        print(result['report'])

        # 保存结果
        from lib.monitoring.backtest_saver import save_backtest_result
        perf_df = result.get('perf_df')
        nav = perf_df['nav'].values if perf_df is not None and 'nav' in perf_df.columns else np.array([1.0])
        trades_list = result.get('trades', [])
        if hasattr(trades_list, 'to_dict'):
            trades_list = trades_list.to_dict('records')
        save_backtest_result("rebalance", nav,
                             trades=trades_list,
                             args={"symbols": symbols, "days": 500, "rebalance_period": 20},
                             extra={"sharpe": result.get('final_sharpe'),
                                    "drawdown": result.get('final_drawdown'),
                                    "stop_reason": result.get('stop_reason')})
    else:
        print("回测无结果")
