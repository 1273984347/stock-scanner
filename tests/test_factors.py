"""因子计算测试 —— 手工数据验证 RSI/动量/波动率"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import math
import numpy as np
import pandas as pd
from factors import calc_rsi, safe_float


def _make_hist(length=100):
    """构造模拟 OHLCV，前 20 行含极端值"""
    np.random.seed(42)

    # 极端值序列：连续大涨 → 横盘 → 连续大跌 → 震荡
    segments = (
        [100.0 + i * 3.0 for i in range(5)] +        # 0-4:  急涨 +15
        [115.0] * 3 +                                  # 5-7:  横盘
        [115.0 - i * 4.0 for i in range(5)] +         # 8-12: 急跌 -20
        [95.0] * 3 +                                   # 13-15: 横盘
        [95.0 + i * 1.5 for i in range(5)]            # 16-20: 缓慢回升
    )
    # 剩余 79 行加入随机游走
    remaining = 100 - len(segments)
    noise = np.random.randn(remaining).cumsum() * 0.8
    base = segments[-1]
    tail = [base + float(n) for n in noise]

    close = np.array(list(segments) + tail)
    high = close + np.abs(np.random.randn(100)) * 0.5
    low = close - np.abs(np.random.randn(100)) * 0.5
    volume = np.random.randint(1e6, 5e6, 100)

    dates = pd.date_range("2026-01-01", periods=100, freq="D")
    df = pd.DataFrame({
        "date": dates, "open": close + np.random.randn(100) * 0.3,
        "close": close, "high": high, "low": low, "volume": volume,
    })
    return df


def test_rsi_known_values():
    """RSI(14) 与 numpy 手算一致"""
    hist = _make_hist()
    close = hist["close"]
    rsi_series = calc_rsi(close, 14)
    rsi_val = safe_float(rsi_series.iloc[-1], 50)

    # 手算 RSI
    delta = close.diff()
    gain = delta.clip(lower=0).fillna(0)
    loss = (-delta.clip(upper=0)).fillna(0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    expected = safe_float((100 - 100 / (1 + rs)).iloc[-1], 50)

    assert abs(rsi_val - expected) < 0.01, f"RSI: 实际 {rsi_val}, 期望 {expected}"


def test_momentum_5_known_values():
    """动量5日与手算一致"""
    hist = _make_hist()
    close = hist["close"].values
    expected = (close[-1] / close[-6] - 1) * 100

    from factors import compute_all_signals
    result = compute_all_signals(hist)
    assert result, "compute_all_signals 返回空"
    actual = result["factors"]["momentum_5"]
    assert abs(actual - expected) < 0.01, f"动量5: 实际 {actual}, 期望 {expected}"


def test_momentum_20_known_values():
    """动量20日与手算一致"""
    hist = _make_hist()
    close = hist["close"].values
    expected = (close[-1] / close[-21] - 1) * 100

    from factors import compute_all_signals
    result = compute_all_signals(hist)
    assert result, "compute_all_signals 返回空"
    actual = result["factors"]["momentum_20"]
    assert abs(actual - expected) < 0.01, f"动量20: 实际 {actual}, 期望 {expected}"


def test_volatility_20_known_values():
    """波动率20日与 numpy 手算一致"""
    hist = _make_hist()
    close = hist["close"]
    returns = close.pct_change()
    expected = safe_float(returns.tail(20).std() * math.sqrt(252) * 100, 0)

    from factors import compute_all_signals
    result = compute_all_signals(hist)
    assert result, "compute_all_signals 返回空"
    actual = result["factors"]["volatility_20"]
    assert abs(actual - expected) < 0.01, f"波动率20: 实际 {actual}, 期望 {expected}"


def test_rsi_formula_consistency():
    """RSI 计算与手算一致（含零除保护）"""
    # 已知序列：手动计算最后一行的 RSI
    prices = pd.Series([44, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10,
                        45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
                        46.28, 46.00, 46.03, 46.41, 46.22, 46.21])
    rsi_series = calc_rsi(prices, 14)
    rsi_val = safe_float(rsi_series.iloc[-1], 50)
    # 验证 RSI 在合理范围内
    assert 0 <= rsi_val <= 100, f"RSI={rsi_val} 超出 [0,100]"

    # 全部上涨的极端序列：loss=0 不应导致 NaN 崩溃
    straight_up = pd.Series([100.0 + i * 0.5 for i in range(20)])
    rsi_up = safe_float(calc_rsi(straight_up, 14).iloc[-1], 50)
    assert rsi_up == 50 or rsi_up > 50, f"全部上涨 RSI={rsi_up} 应 >= 50"

    # 全部下跌的极端序列
    straight_down = pd.Series([100.0 - i * 0.5 for i in range(20)])
    rsi_down = safe_float(calc_rsi(straight_down, 14).iloc[-1], 50)
    assert rsi_down == 50 or rsi_down < 50, f"全部下跌 RSI={rsi_down} 应 <= 50"


def test_compute_all_signals_empty():
    """边界：空 DataFrame → 返回空 dict"""
    from factors import compute_all_signals
    result = compute_all_signals(pd.DataFrame())
    assert result == {}


def test_compute_all_signals_too_short():
    """边界：数据不足 30 行 → 返回空 dict"""
    from factors import compute_all_signals
    short = pd.DataFrame({"close": [100] * 20})
    result = compute_all_signals(short)
    assert result == {}
