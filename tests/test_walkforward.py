"""Walk-Forward 验证测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import numpy as np
from lib.walkforward import WalkForwardBacktest, WalkForwardConfig, WalkForwardRound, ValidationStatus, summarize_rounds


def _make_mock_market_data(codes, dates, base_price=100.0):
    """构造模拟市场数据"""
    market_data = {}
    for code in codes:
        records = []
        price = base_price
        for d in dates:
            pct = np.random.uniform(-0.05, 0.05)
            price = price * (1 + pct)
            records.append({
                "trade_date": pd.Timestamp(d),
                "open": price * 0.99,
                "close": price,
                "high": price * 1.02,
                "low": price * 0.98,
                "volume": 1e6,
                "pre_close": price / (1 + pct) if pct != 0 else price,
                "pct_chg": pct * 100,
            })
        market_data[code] = pd.DataFrame(records)
    return market_data


def test_walkforward_config():
    """测试 WalkForwardConfig"""
    config = WalkForwardConfig(
        train_days=60,
        validation_days=20,
        step_days=20,
    )
    assert config.train_days == 60
    assert config.validation_days == 20
    assert config.step_days == 20


def test_walkforward_backtest_basic():
    """测试 WalkForwardBacktest 基本功能"""
    from factors import compute_all_signals

    codes = ["000001", "600519", "000858"]
    dates = pd.date_range("2025-01-01", periods=200, freq="D").strftime("%Y-%m-%d").tolist()

    market_data = _make_mock_market_data(codes, dates)

    # 定义简单策略函数
    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        trades = []
        for sym in (symbols or list(market_data.keys())):
            df = market_data.get(sym)
            if df is None or len(df) < 60:
                continue
            result = compute_all_signals(df)
            if result and result.get("combined", {}).get("signal") == "bullish":
                trades.append({"code": sym, "signal": "bullish"})
        return {"trades": trades, "total_return": 0, "total_trades": len(trades)}

    config = WalkForwardConfig(
        train_days=60,
        validation_days=20,
        step_days=20,
    )

    wf = WalkForwardBacktest(config)
    rounds = wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=codes,
        strategy_name="test_strategy",
    )

    assert len(rounds) > 0, "应有至少一轮 walk-forward"
    # WalkForwardRound 是 dataclass，有 window_index 和 validation_status 属性
    assert all(hasattr(r, "window_index") for r in rounds)
    assert all(hasattr(r, "validation_status") for r in rounds)


def test_summarize_rounds():
    """测试轮次汇总"""
    rounds = [
        WalkForwardRound(
            window_index=0,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            train_metrics={},
            validation_metrics={"total_return": 0.05, "sharpe_ratio": 1.2},
        ),
        WalkForwardRound(
            window_index=1,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            train_metrics={},
            validation_metrics={"total_return": -0.02, "sharpe_ratio": -0.3},
        ),
        WalkForwardRound(
            window_index=2,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            train_metrics={},
            validation_metrics={"total_return": 0.08, "sharpe_ratio": 1.5},
        ),
    ]

    summary = summarize_rounds(rounds)

    assert summary["rounds"] == 3
    assert "median_return" in summary
    assert "worst_return" in summary
    assert "median_sharpe" in summary
    assert "stable_rounds" in summary
    assert "fragile_rounds" in summary


def test_summarize_rounds_empty():
    """测试空轮次汇总"""
    summary = summarize_rounds([])
    assert summary["rounds"] == 0
    assert summary["stable_rounds"] == 0


def test_walkforward_with_limit_up():
    """测试涨跌停限制"""
    from factors import compute_all_signals

    codes = ["000001"]
    dates = pd.date_range("2025-01-01", periods=100, freq="D").strftime("%Y-%m-%d").tolist()

    market_data = _make_mock_market_data(codes, dates)

    # 添加涨跌停标记
    for code, df in market_data.items():
        df["is_limit_up"] = False
        df["is_limit_down"] = False
        df["is_suspend"] = False

    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        return {"trades": [], "total_return": 0, "total_trades": 0}

    config = WalkForwardConfig(train_days=30, validation_days=10, step_days=10)
    wf = WalkForwardBacktest(config)
    rounds = wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=codes,
        strategy_name="limit_test",
    )

    assert len(rounds) > 0


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
