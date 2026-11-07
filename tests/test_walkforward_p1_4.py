"""P1-4: lib/walkforward.py warmup_bars 传递给 strategy_fn 测试

修复前: val_data 只到 validation_start, warmup 字段被计算后丢弃
修复后: val_data 包含 [warmup_start, val_end], 含 warmup 上下文
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect
import pandas as pd
import numpy as np

from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig


def _make_market_data(codes, n_dates, seed=42):
    """构造测试用市场数据"""
    np.random.seed(seed)
    market_data = {}
    for code in codes:
        records = []
        price = 100.0
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
        for d in dates:
            pct = np.random.uniform(-0.02, 0.02)
            price = price * (1 + pct)
            records.append({
                "trade_date": d,
                "open": price * 0.99,
                "close": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "volume": 1e6,
            })
        market_data[code] = pd.DataFrame(records)
    return market_data, [str(d.date()) for d in dates]


def test_p1_4_val_data_includes_warmup_context():
    """P1-4: val 阶段 strategy_fn 应收到含 warmup_bars 的数据

    验证: warmup_bars=10, val_days=5, 期望 val 调用收到 dates 长度 ≥ 15
    """
    strategy_calls = []

    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        for code, df in market_data.items():
            strategy_calls.append({
                "n_dates": len(dates),
                "df_len": len(df),
            })
        return {"trades": [], "total_return": 0, "total_trades": 0}

    config = WalkForwardConfig(
        train_days=20, validation_days=5, step_days=5,
        warmup_bars=10,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)
    market_data, dates = _make_market_data(["600519"], 60)
    wf.run(strategy_fn=strategy_fn, market_data=market_data, dates=dates, symbols=["600519"])

    # 至少有 2 次调用 (train + val), 第二次是 val
    assert len(strategy_calls) >= 2, (
        f"P1-4 修复未完成: strategy_fn 调用次数 {len(strategy_calls)} < 2。"
    )
    val_call = strategy_calls[1]
    assert val_call["n_dates"] >= 15, (
        f"P1-4 修复未完成: val 调用 dates 长度 {val_call['n_dates']} "
        f"< warmup_bars(10) + val_days(5) = 15。warmup 上下文未传递。"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
