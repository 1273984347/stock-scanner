"""
backtest_regime_analysis.py - V2 系统问题诊断

STEP 4 任务：
1. EMA21退出分析：为何11%胜率还触发这么多次？
2. 市场状态分层：TREND_UP vs MEAN_REVERSION 时期表现差异
3. 入场质量分析：信号质量是否与市场状态相关
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple
from dataclasses import dataclass

from lib.data.data_layer import get_index_hist
from lib.data.data_source_registry import get_stock_hist_multi_source
from lib.scoring.technical_signals import ema
from lib.monitoring.market_regime import detect_market_regime, TREND_UP, MEAN_REVERSION, COMPRESSION, TREND_DOWN


@dataclass
class RegimeTrade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    hold_days: int
    exit_reason: str
    code: str
    regime_at_entry: str        # 入场时的市场状态
    regime_conf_at_entry: float
    hs300_price_vs_ema21: float  # 入场时HS300价格 vs EMA21（趋势强度代理）


def compute_hs300_regime_series(hs300: pd.DataFrame) -> pd.DataFrame:
    """计算HS300每日市场状态序列"""
    hs300 = hs300.copy()
    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['EMA5'] = ema(hs300['close'], 5)

    # 简化版V4状态：基于HS300自身趋势
    # TREND_UP: price > EMA21 and EMA21向上
    # MEAN_REVERSION: price 在 EMA21 附近震荡
    # TREND_DOWN: price < EMA21 and EMA21向下
    # COMPRESSION: 价格在窄幅震荡，ATR收缩

    ema_slope = hs300['EMA21'].pct_change(5)
    hs300['trend_strength'] = (hs300['close'] - hs300['EMA21']) / hs300['EMA21']
    hs300['ema_slope_pct'] = ema_slope * 100

    def classify_regime(row):
        t = row['trend_strength']
        s = row['ema_slope_pct']
        if pd.isna(t) or pd.isna(s):
            return MEAN_REVERSION
        if t > 0.01 and s > 0.05:
            return TREND_UP
        elif t < -0.01 and s < -0.05:
            return TREND_DOWN
        elif abs(t) < 0.015 and abs(s) < 0.1:
            return MEAN_REVERSION
        else:
            return MEAN_REVERSION  # 边界情况归为均值回归

    hs300['regime'] = hs300.apply(classify_regime, axis=1)
    hs300['price_vs_ema21'] = hs300['trend_strength']
    return hs300[['date', 'close', 'EMA21', 'EMA5', 'regime', 'price_vs_ema21']]


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['low_10'] = df['low'].rolling(10).min()
    return df


def check_entry(df: pd.DataFrame, i: int) -> bool:
    if i < 20:
        return False
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    price_ok = row['close'] > row['EMA5']
    return bool(ma5_cross and price_ok)


def check_exit(df: pd.DataFrame, entry_i: int) -> Tuple[str, float]:
    """V2 趋势持有退出"""
    for j in range(entry_i + 1, min(entry_i + 20, len(df))):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]

        if not pd.isna(row['low_10']):
            trailing_stop = max(
                row['low_10'] if not pd.isna(df.iloc[entry_i]['low_10']) else df.iloc[entry_i]['close'] * 0.95,
                row['low_10']
            )
        else:
            trailing_stop = df.iloc[entry_i]['close'] * 0.95

        if row['close'] < trailing_stop:
            return "trailing_stop", float(trailing_stop)

        if row['close'] < row['EMA21'] and prev_row['close'] < prev_row['EMA21']:
            return "trend_break", float(row['close'])

    last_i = min(entry_i + 20, len(df) - 1)
    return "timeout", float(df.iloc[last_i]['close'])


def run_analysis(
    stock_codes: List[str],
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    """运行带市场状态标记的回测"""

    # HS300
    hs300 = get_index_hist('000300', days=500)
    hs300 = compute_hs300_regime_series(hs300)
    hs300_dict = dict(zip(hs300['date'].astype(str), hs300['regime']))
    hs300_pvem_dict = dict(zip(hs300['date'].astype(str), hs300['price_vs_ema21']))

    all_trades: List[RegimeTrade] = []

    for code in stock_codes:
        try:
            df = get_stock_hist_multi_source(code, days=500, source="auto", prefer_adjusted=True)
            if df is None or len(df) < 60:
                continue
            df = add_indicators(df)
            df = df[df['date'] >= start_date]
            if len(df) < 30:
                continue

            for i in range(30, len(df) - 1):
                row = df.iloc[i]
                entry_date_str = str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date'])

                # 市场过滤：HS300 > EMA21（与原始回测一致）
                hs_match = hs300[hs300['date'] <= row['date']]
                if len(hs_match) == 0:
                    continue
                if not (hs_match.iloc[-1]['close'] > hs_match.iloc[-1]['EMA21']):
                    continue

                if not check_entry(df, i):
                    continue

                exit_reason, exit_price = check_exit(df, i)
                entry_price = float(row['close'])
                pnl_pct = round((exit_price / entry_price - 1) * 100, 2)
                hold_days = min(20, len(df) - 1 - i)

                # 获取入场时的市场状态
                regime = hs300_dict.get(entry_date_str, MEAN_REVERSION)
                pvem = hs300_pvem_dict.get(entry_date_str, 0.0)

                # 找实际退出日期
                exit_i = min(i + hold_days, len(df) - 1)
                exit_date_str = str(df.iloc[exit_i]['date'].date()) if hasattr(df.iloc[exit_i]['date'], 'date') else str(df.iloc[exit_i]['date'])

                all_trades.append(RegimeTrade(
                    entry_date=entry_date_str,
                    entry_price=entry_price,
                    exit_date=exit_date_str,
                    exit_price=exit_price,
                    pnl_pct=pnl_pct,
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                    code=code,
                    regime_at_entry=regime,
                    regime_conf_at_entry=0.5,  # 简化版不用confidence
                    hs300_price_vs_ema21=pvem,
                ))
        except Exception:
            continue

    if not all_trades:
        return pd.DataFrame()

    df_t = pd.DataFrame([{
        'entry_date': t.entry_date,
        'exit_date': t.exit_date,
        'code': t.code,
        'pnl_pct': t.pnl_pct,
        'hold_days': t.hold_days,
        'exit_reason': t.exit_reason,
        'regime_at_entry': t.regime_at_entry,
        'regime_conf_at_entry': t.regime_conf_at_entry,
        'hs300_pvem': t.hs300_price_vs_ema21,
    } for t in all_trades])

    return df_t.sort_values('exit_date')


def print_diagnostic_report(df: pd.DataFrame):
    """打印诊断报告"""
    if df.empty:
        print("无交易数据")
        return

    print(f"\n{'='*70}")
    print("STEP 4 诊断报告：V2 系统问题分析")
    print(f"{'='*70}")
    print(f"总交易数: {len(df)}")

    # ========== 1. 按市场状态分层 ==========
    print(f"\n{'='*70}")
    print("【1】按市场状态分层")
    print(f"{'='*70}")

    regimes = df.groupby('regime_at_entry')

    regime_stats = []
    for regime, group in regimes:
        pnls = group['pnl_pct'].tolist()
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = len(wins) / len(pnls) * 100 if pnls else 0
        avg = np.mean(pnls) if pnls else 0
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

        # 各退出方式分布
        exit_dist = group.groupby('exit_reason')['pnl_pct'].agg(['count', 'mean'])

        regime_stats.append({
            'regime': regime,
            'count': len(pnls),
            'win_rate': wr,
            'avg_pnl': avg,
            'profit_factor': pf,
            'wins': len(wins),
            'losses': len(losses),
            'exit_dist': exit_dist,
        })

        print(f"\n【{regime}】{len(pnls)}笔交易, 胜率{wr:.1f}%, 均值{avg:+.2f}%, 盈亏比{pf:.2f}")
        print(f"  盈利: {len(wins)}笔, 亏损: {len(losses)}笔")
        for reason, row in exit_dist.iterrows():
            pct = row['count'] / len(pnls) * 100
            print(f"    {reason}: {int(row['count'])}次({pct:.0f}%), 均值={row['mean']:+.2f}%")

    # ========== 2. EMA21退出深度分析 ==========
    print(f"\n{'='*70}")
    print("【2】EMA21退出（trend_break）深度分析")
    print(f"{'='*70}")

    ema21_exits = df[df['exit_reason'] == 'trend_break']
    if len(ema21_exits) > 0:
        print(f"触发次数: {len(ema21_exits)} ({len(ema21_exits)/len(df)*100:.1f}%)")
        print(f"胜率: {(ema21_exits['pnl_pct'] > 0).mean()*100:.1f}%")
        print(f"均值: {ema21_exits['pnl_pct'].mean():+.2f}%")

        # 按市场状态分解
        for regime, group in ema21_exits.groupby('regime_at_entry'):
            if len(group) > 0:
                wr = (group['pnl_pct'] > 0).mean() * 100
                avg = group['pnl_pct'].mean()
                pct = len(group) / len(ema21_exits) * 100
                print(f"  {regime}: {len(group)}次({pct:.0f}%), 胜率{wr:.1f}%, 均值{avg:+.2f}%")

        # 按持仓天数分解
        print(f"\n  按持仓天数:")
        for days in [1, 2, 3, 4, 5, 6, 7, 10, 15, 20]:
            subset = ema21_exits[ema21_exits['hold_days'] == days]
            if len(subset) > 0:
                wr = (subset['pnl_pct'] > 0).mean() * 100
                avg = subset['pnl_pct'].mean()
                print(f"    {days}天: {len(subset)}次, 胜率{wr:.1f}%, 均值{avg:+.2f}%")

    # ========== 3. 持有20天 vs 其他 ==========
    print(f"\n{'='*70}")
    print("【3】20天到期 vs 主动退出")
    print(f"{'='*70}")

    timeout_trades = df[df['exit_reason'] == 'timeout']
    active_trades = df[df['exit_reason'] != 'timeout']

    print(f"持有到期(timeout): {len(timeout_trades)}笔, 胜率{(timeout_trades['pnl_pct']>0).mean()*100:.1f}%, 均值{timeout_trades['pnl_pct'].mean():+.2f}%")
    print(f"主动退出(active): {len(active_trades)}笔, 胜率{(active_trades['pnl_pct']>0).mean()*100:.1f}%, 均值{active_trades['pnl_pct'].mean():+.2f}%")

    # 主动退出内部对比
    for reason in df['exit_reason'].unique():
        subset = df[df['exit_reason'] == reason]
        if len(subset) > 5:
            wr = (subset['pnl_pct'] > 0).mean() * 100
            avg = subset['pnl_pct'].mean()
            print(f"  {reason}: {len(subset)}笔, 胜率{wr:.1f}%, 均值{avg:+.2f}%")

    # ========== 4. HS300趋势强度分析 ==========
    print(f"\n{'='*70}")
    print("【4】HS300趋势强度（入场时 price vs EMA21）")
    print(f"{'='*70}")

    df['pvem_bucket'] = pd.cut(df['hs300_pvem'], bins=[-np.inf, -0.02, 0, 0.02, 0.05, np.inf],
                                labels=['deep_down', 'below_ema', 'near_ema', 'above_ema', 'strong_up'])

    for bucket, group in df.groupby('pvem_bucket', observed=True):
        if len(group) > 0:
            wr = (group['pnl_pct'] > 0).mean() * 100
            avg = group['pnl_pct'].mean()
            print(f"  {bucket}: {len(group)}笔, 胜率{wr:.1f}%, 均值{avg:+.2f}%, EMA21偏离{bucket}≈{group['hs300_pvem'].mean()*100:.1f}%")

    # ========== 5. 问题总结 ==========
    print(f"\n{'='*70}")
    print("【5】V2 系统核心问题")
    print(f"{'='*70}")

    # 找最差退出方式
    exit_perf = df.groupby('exit_reason')['pnl_pct'].agg(['mean', 'count'])
    exit_perf = exit_perf[exit_perf['count'] >= 5].sort_values('mean')
    worst = exit_perf.iloc[0] if len(exit_perf) > 0 else None
    if worst is not None:
        print(f"⚠️  最差退出方式: {worst.name}, {int(worst['count'])}次, 均值{worst['mean']:+.2f}%")

    # 找最佳市场状态
    if regime_stats:
        best_regime = max(regime_stats, key=lambda x: x['avg_pnl'])
        worst_regime = min(regime_stats, key=lambda x: x['avg_pnl'])
        print(f"✅  最佳市场状态: {best_regime['regime']}, {best_regime['count']}笔, 均值{best_regime['avg_pnl']:+.2f}%")
        print(f"❌  最差市场状态: {worst_regime['regime']}, {worst_regime['count']}笔, 均值{worst_regime['avg_pnl']:+.2f}%")


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

    print("运行 V2 系统诊断...")
    df = run_analysis(TEST_STOCKS, '2024-01-01', '2026-05-22')
    print_diagnostic_report(df)

    # 保存结果
    from lib.monitoring.backtest_saver import save_backtest_result, RESULTS_DIR
    import json as _json
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"regime_{ts}.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_json.dumps({
        "timestamp": ts, "script": "regime_analysis",
        "stocks": len(TEST_STOCKS),
        "data": df.to_dict('records') if df is not None and not df.empty else []
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n结果已保存: {out_path}")
