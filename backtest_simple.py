"""
backtest_simple.py - 最简纯趋势持有版本
退出条件：
1. 跌破 EMA21（确认）
2. 20天到期
无 ATR 止损
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from dataclasses import dataclass

from lib.data_layer import get_index_hist
from lib.data_source_registry import get_stock_hist_multi_source
from lib.technical_signals import ema


@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    code: str


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['low_10'] = df['low'].rolling(10).min()
    return df


def check_entry(df: pd.DataFrame, i: int) -> Tuple[bool, str]:
    """MA5/MA21金叉入场"""
    if i < 20:
        return False, ""
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    price_ok = row['close'] > row['EMA5']
    if ma5_cross and price_ok:
        return True, "ma5_ma21_gc"
    return False, ""


def check_exit(df: pd.DataFrame, entry_i: int) -> Tuple[str, float]:
    """
    纯趋势退出：无 ATR 止损
    - 跌破 EMA21（昨天也破 = 确认）
    - 20天到期
    """
    entry_price = float(df.iloc[entry_i]['close'])

    for j in range(entry_i + 1, min(entry_i + 20, len(df))):
        row = df.iloc[j]
        prev = df.iloc[j - 1]

        # EMA21 跌破确认（昨天也破）
        if row['close'] < row['EMA21'] and prev['close'] < prev['EMA21']:
            return "ema21_exit", float(row['close'])

    # 20天强制平
    last_i = min(entry_i + 20, len(df) - 1)
    return "timeout", float(df.iloc[last_i]['close'])


def run_backtest(
    stock_codes: List[str],
    start_date: str,
    end_date: str,
    initial_capital: float = 1_000_000.0,
    position_pct: float = 0.10,
) -> Dict:
    """纯趋势持有回测"""

    # HS300
    hs300 = get_index_hist('000300', days=500)
    hs300['EMA21'] = ema(hs300['close'], 21)

    all_trades: List[Trade] = []

    for code in stock_codes:
        try:
            df = get_stock_hist_multi_source(code, days=500, source="tdx_local_qfq")
            if df is None or len(df) < 60:
                continue
            df = add_indicators(df)
            df = df[df['date'] >= start_date]
            if len(df) < 30:
                continue

            for i in range(30, len(df) - 1):
                row = df.iloc[i]

                # 市场过滤
                hs_match = hs300[hs300['date'] <= row['date']]
                if len(hs_match) == 0:
                    continue
                if not (hs_match.iloc[-1]['close'] > hs_match.iloc[-1]['EMA21']):
                    continue

                entered, _ = check_entry(df, i)
                if not entered:
                    continue

                exit_reason, exit_price = check_exit(df, i)
                entry_price = float(row['close'])
                pnl_pct = round((exit_price / entry_price - 1) * 100, 2)
                hold_days = min(20, len(df) - 1 - i)

                all_trades.append(Trade(
                    entry_date=str(row['date'].date()),
                    entry_price=entry_price,
                    exit_date=str(df.iloc[min(i + hold_days, len(df) - 1)]['date'].date()),
                    exit_price=exit_price,
                    pnl_pct=pnl_pct,
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                    code=code,
                ))
        except Exception:
            continue

    if not all_trades:
        return {"metrics": {}, "trades": []}

    # 转 DataFrame
    df_t = pd.DataFrame([{
        'entry_date': t.entry_date, 'exit_date': t.exit_date,
        'code': t.code, 'pnl_pct': t.pnl_pct,
        'hold_days': t.hold_days, 'exit_reason': t.exit_reason
    } for t in all_trades])
    df_t = df_t.sort_values('exit_date')

    # 指标
    pnl_list = df_t['pnl_pct'].tolist()
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    win_rate = len(wins) / len(pnl_list) * 100
    avg_pnl = np.mean(pnl_list)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

    # 资金曲线（单利）
    trade_amount = initial_capital * position_pct
    equity = initial_capital
    equity_curve = [initial_capital]
    for _, row in df_t.iterrows():
        equity += trade_amount * row['pnl_pct'] / 100
        equity_curve.append(equity)

    eq_series = pd.Series(equity_curve)
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max * 100
    max_dd = drawdown.min()
    total_ret = (equity - initial_capital) / initial_capital * 100

    # 按月
    df_t['period'] = pd.to_datetime(df_t['exit_date']).dt.to_period('M')
    monthly = df_t.groupby('period').agg(
        signals=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
        total_ret=('pnl_pct', 'sum'),
    ).round(1)

    # 按退出
    by_reason = df_t.groupby('exit_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)

    return {
        "metrics": {
            "total_trades": len(df_t),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(avg_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(pf, 2),
            "max_drawdown": round(max_dd, 2),
            "total_return_pct": round(total_ret, 2),
            "final_capital": round(equity, 0),
        },
        "trades": df_t,
        "monthly": monthly,
        "by_reason": by_reason,
    }


if __name__ == "__main__":
    import sys
    sys.path.insert(0, '.')

    TEST_STOCKS = [
        '000858', '600519', '000333', '002475', '300750',
        '600036', '000001', '601318', '002594', '300059',
        '600276', '000568', '603259', '002415', '601888',
        '300033', '300124', '300274', '300496',
        '002049', '002371', '002456', '002460', '002466',
        '002236', '300014', '300751',
    ]

    print("=" * 60)
    print("纯趋势持有回测 (无 ATR 止损)")
    print("=" * 60)

    r = run_backtest(TEST_STOCKS, '2024-01-01', '2026-05-22')
    m = r['metrics']

    print(f"\n总交易: {m['total_trades']}")
    print(f"胜率: {m['win_rate']}%")
    print(f"均值: {m['avg_pnl']}%")
    print(f"盈亏比: {m['profit_factor']}")
    print(f"最大回撤: {m['max_drawdown']}%")
    print(f"总收益: {m['total_return_pct']}%")

    print(f"\n按退出方式:")
    for reason, row in r['by_reason'].iterrows():
        print(f"  {reason}: {int(row['count'])}次, 均={row['avg_pnl']:.1f}%, 胜率={row['win_rate']:.0f}%")

    print(f"\n月度统计:")
    print(r['monthly'].to_string())
