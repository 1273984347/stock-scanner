"""
STEP 1: 历史扫描器 - 手动回溯验证 V4 MVP 策略

规则:
  - 市场过滤: HS300 收盘 > EMA21
  - 个股买入:
      1. 收盘价 > EMA21
      2. 收盘价 > 20日最高价（真突破）
      3. 成交量 > 20日均量
  - 止损: 跌破10日最低价
  - 止盈: 跟踪10日最低价（移动止损）

用法:
  python historical_scanner.py
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import json

from lib.data_layer import get_index_hist, get_stock_hist
from lib.technical_signals import ema


# ============================================================
# 核心策略逻辑
# ============================================================

def mvp_scan(df: pd.DataFrame, hs300_df: Optional[pd.DataFrame] = None) -> List[Dict]:
    """
    扫描一只股票的所有信号，返回信号列表

    Returns:
        List of {date, code, entry_price, hs300_regime, reason}
    """
    if df is None or len(df) < 60:
        return []

    # 计算指标
    df = df.copy()
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['high_20'] = df['high'].rolling(20).max()
    df['low_10'] = df['low'].rolling(10).min()

    signals = []

    for i in range(30, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        # 市场条件: HS300 > EMA21
        market_ok = True
        if hs300_df is not None:
            hs300_on_date = hs300_df[hs300_df['date'] <= row['date']]
            if len(hs300_on_date) >= 2:
                hs_latest = hs300_on_date.iloc[-1]
                market_ok = hs_latest['close'] > hs_latest['EMA21']

        # 个股条件
        price_above_ema = row['close'] > row['EMA21']
        volume_break = (row['vol_ma20'] > 0 and row['volume'] > row['vol_ma20'])
        prev_below_high = prev['close'] < prev['high_20'] if not pd.isna(prev['high_20']) else True
        curr_above_high = row['close'] > row['high_20'] if not pd.isna(row['high_20']) else False

        breakout = (prev_below_high and curr_above_high and price_above_ema and volume_break)

        if breakout and market_ok:
            signals.append({
                'date': row['date'],
                'close': row['close'],
                'volume_ratio': row['volume'] / row['vol_ma20'] if row['vol_ma20'] > 0 else 0,
                'hs300_bull': market_ok
            })

    return signals


def simulate_trades(df: pd.DataFrame, signals: List[Dict], stop_pct: float = 0.05) -> List[Dict]:
    """
    模拟交易: 按信号买入，止损/移动止盈卖出

    Args:
        df: 股票历史数据
        signals: 买入信号列表
        stop_pct: 固定止损百分比（暂用）

    Returns:
        List of {entry_date, entry_price, exit_date, exit_price, pnl_pct, hold_days, max_drawdown}
    """
    if not signals:
        return []

    trades = []
    used_indices = set()

    for sig in signals:
        sig_date = sig['date']
        sig_idx = df[df['date'] == sig_date].index

        if len(sig_idx) == 0:
            continue
        idx = sig_idx[0]
        if idx in used_indices:
            continue

        # 找这个信号之后的交易日
        df_after = df[df['date'] > sig_date]
        if len(df_after) < 1:
            continue

        entry_price = sig['close']
        stop_loss = entry_price * (1 - stop_pct)

        # 移动止损: 10日最低价
        trailing_stop = None
        exit_price = None
        exit_date = None
        hold_days = 0

        for j, (i, row) in enumerate(df_after.iterrows()):
            if j > 60:  # 最多持有60天
                exit_price = row['close']
                exit_date = row['date']
                break

            low_10 = row.get('low_10')
            if low_10 is None or pd.isna(low_10):
                low_10 = row['low']

            # 移动止损: 跟踪10日最低价
            if trailing_stop is None or low_10 < trailing_stop:
                trailing_stop = low_10

            # 止损
            if row['close'] < trailing_stop:
                exit_price = trailing_stop
                exit_date = row['date']
                break

            # 止盈: 跌破EMA21
            if row['close'] < row['EMA21']:
                exit_price = row['close']
                exit_date = row['date']
                break

        if exit_price is None:
            # 最后一根K线强制平仓
            last_row = df.iloc[-1]
            exit_price = last_row['close']
            exit_date = last_row['date']

        hold_days = (pd.Timestamp(exit_date) - pd.Timestamp(sig_date)).days
        pnl_pct = (exit_price - entry_price) / entry_price * 100

        trades.append({
            'entry_date': sig_date,
            'entry_price': entry_price,
            'exit_date': exit_date,
            'exit_price': exit_price,
            'pnl_pct': round(pnl_pct, 2),
            'hold_days': hold_days,
        })

        used_indices.add(idx)

    return trades


# ============================================================
# 主扫描
# ============================================================

TEST_STOCKS = [
    '000858', '600519', '000333', '002475', '300750',
    '600036', '000001', '601318', '002594', '300059',
    '600276', '000568', '603259', '002415', '601888',
]


def run_historical_scan(stock_codes: List[str], days: int = 180) -> pd.DataFrame:
    """
    扫描多只股票的历史信号
    """
    # HS300 数据
    hs300 = get_index_hist('000300', days=days + 60)
    hs300['EMA21'] = ema(hs300['close'], 21)

    all_trades = []

    for code in stock_codes:
        try:
            df = get_stock_hist(code, days=days + 60)
            if df is None or len(df) < 60:
                continue

            signals = mvp_scan(df, hs300)
            trades = simulate_trades(df, signals)

            for t in trades:
                t['code'] = code
                all_trades.append(t)

        except Exception as e:
            print(f"Error scanning {code}: {e}")
            continue

    return pd.DataFrame(all_trades)


def print_summary(trades_df: pd.DataFrame):
    """打印交易摘要"""
    if trades_df.empty:
        print("No trades found!")
        return

    total = len(trades_df)
    wins = trades_df[trades_df['pnl_pct'] > 0]
    losses = trades_df[trades_df['pnl_pct'] <= 0]

    print(f"\n{'='*60}")
    print(f"HISTORICAL SCAN RESULTS")
    print(f"{'='*60}")
    print(f"Total trades: {total}")
    print(f"Wins: {len(wins)} ({len(wins)/total*100:.1f}%)")
    print(f"Losses: {len(losses)} ({len(losses)/total*100:.1f}%)")

    if len(wins) > 0:
        avg_win = wins['pnl_pct'].mean()
        print(f"Average win: {avg_win:.2f}%")

    if len(losses) > 0:
        avg_loss = losses['pnl_pct'].mean()
        print(f"Average loss: {avg_loss:.2f}%")

    pnl_list = trades_df['pnl_pct'].tolist()
    profit_factor = sum(wins['pnl_pct']) / abs(sum(losses['pnl_pct'])) if len(losses) > 0 and sum(losses['pnl_pct']) != 0 else float('inf')
    print(f"Profit factor: {profit_factor:.2f}")

    # Max drawdown (simplified)
    cumulative = np.cumsum(pnl_list)
    peak = np.maximum.accumulate(cumulative)
    drawdown = cumulative - peak
    max_dd = abs(drawdown.min()) if len(drawdown) > 0 else 0
    print(f"Max drawdown: {max_dd:.2f}%")

    print(f"\nTotal return: {sum(pnl_list):.2f}%")
    print(f"Avg hold days: {trades_df['hold_days'].mean():.1f}")

    # Show recent trades
    print(f"\n{'='*60}")
    print(f"TRADE LOG (sorted by date)")
    print(f"{'='*60}")
    trades_sorted = trades_df.sort_values('entry_date')
    print(trades_sorted[['entry_date', 'code', 'entry_price', 'exit_price', 'pnl_pct', 'hold_days']].to_string(index=False))

    return trades_df


if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 180

    print(f"Scanning {len(TEST_STOCKS)} stocks, last {days} days...")
    trades = run_historical_scan(TEST_STOCKS, days=days)
    print_summary(trades)

    # Save to file
    if not trades.empty:
        output_file = "cache/reports/historical_scan.csv"
        import os
        os.makedirs("cache/reports", exist_ok=True)
        trades.to_csv(output_file, index=False)
        print(f"\nSaved to {output_file}")
