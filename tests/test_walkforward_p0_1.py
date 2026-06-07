"""P0-1: lib/walkforward.py run() factor_data 切片防未来函数

修复前: train/val strategy_fn 都收到完整 factor_data
        → val 阶段可读取训练期/未来期因子值 (严重未来函数)
修复后: 按 train_end/val_end 切 factor_data, 只保留 ≤ 窗口 end 的行
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd

from lib.walkforward import WalkForwardBacktest, WalkForwardConfig


def test_p0_1_factor_data_sliced_per_window_no_future_data():
    """P0-1: strategy_fn 收到的 factor_data 行数应 ≤ 窗口 dates 行数

    关键不变式: factor_data 最大日期 ≤ 该次 strategy_fn 调用 dates 列表的最大日期
    """
    calls = []

    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        # 记录 factor_data 的最大 trade_date vs 本次调用 dates 最大日期
        factor_max = None
        if factor_data is not None and not factor_data.empty:
            if "trade_date" in factor_data.columns:
                factor_max = pd.Timestamp(factor_data["trade_date"].max())
        call_max = pd.Timestamp(dates[-1]) if dates else None
        calls.append({
            "factor_max_date": factor_max,
            "call_max_date": call_max,
            "n_factor_rows": len(factor_data) if factor_data is not None else 0,
            "n_call_dates": len(dates) if dates else 0,
        })
        return {"trades": [], "total_return": 0, "total_trades": 0}

    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    factor_data = pd.DataFrame({
        "trade_date": dates,
        "factor_value": range(len(dates)),
    })
    market_data = {"600519": pd.DataFrame({
        "trade_date": dates,
        "close": 100.0, "high": 101.0, "low": 99.0,
        "open": 100.0, "volume": 1e6,
    })}

    config = WalkForwardConfig(
        train_days=20, validation_days=5, step_days=5,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)

    wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=[str(d.date()) for d in dates],
        symbols=["600519"],
        factor_data=factor_data,
    )

    # 至少应有调用
    assert len(calls) >= 2, f"应至少有 2 次 strategy_fn 调用, 实际 {len(calls)}"

    # 关键不变式: factor_data 最大日期 ≤ 该次调用 dates 列表的最大日期
    for i, call in enumerate(calls):
        if call["factor_max_date"] is not None and call["call_max_date"] is not None:
            assert call["factor_max_date"] <= call["call_max_date"], (
                f"P0-1 修复未完成: 第 {i} 次 strategy_fn 调用中 "
                f"factor_data 最大日期 {call['factor_max_date']} > "
                f"调用 dates 最大日期 {call['call_max_date']}，存在未来函数"
            )

    # 更强断言: 第 1 次调用 (train_0) 的 factor_data 行数应 < 总行数 (被切片了)
    first_train_call = calls[0]
    assert first_train_call["n_factor_rows"] < len(factor_data), (
        f"P0-1 修复未完成: 第 1 次 train 调用收到 {first_train_call['n_factor_rows']} 行 "
        f"factor_data = 完整 {len(factor_data)} 行，未做切片"
    )


def test_p0_1_factor_data_indexed_by_datetime():
    """P0-1: 因子数据用 DatetimeIndex 时也应被切片"""
    calls = []

    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        if factor_data is not None and not factor_data.empty:
            max_idx = factor_data.index.max() if hasattr(factor_data.index, "max") else None
            call_max = pd.Timestamp(dates[-1]) if dates else None
            calls.append({
                "max_index": max_idx,
                "call_max": call_max,
                "n_rows": len(factor_data),
            })
        return {"trades": [], "total_return": 0, "total_trades": 0}

    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    # 用 DatetimeIndex 而非 trade_date 列
    factor_data = pd.DataFrame(
        {"value": range(len(dates))},
        index=pd.DatetimeIndex(dates, name="trade_date"),
    )
    market_data = {"600519": pd.DataFrame({
        "trade_date": dates,
        "close": 100.0, "high": 101.0, "low": 99.0,
        "open": 100.0, "volume": 1e6,
    })}

    config = WalkForwardConfig(
        train_days=20, validation_days=5, step_days=5,
        min_train_trades=0, min_validation_trades=0,
    )
    wf = WalkForwardBacktest(config=config)

    wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=[str(d.date()) for d in dates],
        symbols=["600519"],
        factor_data=factor_data,
    )

    assert len(calls) >= 2, f"应至少有 2 次调用"
    for i, call in enumerate(calls):
        if call["max_index"] is not None and call["call_max"] is not None:
            assert call["max_index"] <= call["call_max"], (
                f"P0-1 修复未完成: 第 {i} 次调用 factor_data index 最大 "
                f"{call['max_index']} > 调用 dates 最大 {call['call_max']}, "
                f"存在未来函数"
            )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
