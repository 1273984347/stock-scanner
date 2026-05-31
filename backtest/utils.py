"""backtest/utils.py - 回测引擎共享工具函数"""

import pandas as pd
import numpy as np
from typing import List, Dict
from lib.technical_signals import ema


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算 ATR"""
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return atr


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """给数据添加技术指标"""
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['atr'] = compute_atr(df)
    df['atr_pct'] = df['atr'] / df['close'] * 100
    df['low_10'] = df['low'].rolling(10).min()
    df['high_20'] = df['high'].rolling(20).max()
    df['sma50'] = df['close'].rolling(50).mean()
    return df


def compute_metrics(trades: List[Dict], equity_curve: List[float] = None) -> Dict:
    """统一指标计算"""
    if not trades:
        return {
            "win_rate": 0, "avg_pnl": 0, "profit_factor": 0,
            "max_drawdown": 0, "sharpe": 0, "total_return": 0,
        }

    pnls = [t.get("pnl_pct", 0) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]

    win_rate = len(wins) / len(pnls) if pnls else 0
    avg_pnl = float(np.mean(pnls)) if pnls else 0
    avg_win = float(np.mean(wins)) if wins else 0
    avg_loss = float(np.mean(losses)) if losses else 1
    profit_factor = avg_win / avg_loss if avg_loss > 0 else 0

    max_drawdown = 0
    sharpe = 0
    total_return = 0
    if equity_curve and len(equity_curve) > 1:
        peak = equity_curve[0]
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak if peak > 0 else 0
            max_drawdown = max(max_drawdown, dd)
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0] if equity_curve[0] > 0 else 0
        returns = pd.Series(equity_curve).pct_change().dropna()
        if len(returns) > 1 and returns.std() > 0:
            sharpe = float(returns.mean() / returns.std() * np.sqrt(252))

    return {
        "win_rate": round(win_rate, 4),
        "avg_pnl": round(avg_pnl, 4),
        "profit_factor": round(profit_factor, 3),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe": round(sharpe, 3),
        "total_return": round(total_return, 4),
    }
