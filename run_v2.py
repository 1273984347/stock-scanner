"""
run_v2.py - V2 策略实时扫描入口

用法：
    python run_v2.py                    # 全量扫描（3206只）
    python run_v2.py --limit 50         # 限制扫描数量（快速测试）
    python run_v2.py --stocks 000858,600519  # 指定股票

V2 入场条件：
    1. MA5/MA21 金叉（当日EMA5上穿EMA21）
    2. 价格站上 EMA5
    3. HS300 > EMA21（市场过滤）
    4. MEAN_REVERSION 市场：HS300趋势强度 >3%
    5. 可选：相对强度阈值（只做相对HS300强势的票）
"""

import argparse
import os
import sys
from datetime import datetime, date
from typing import List, Dict, Tuple

sys.path.insert(0, '.')

# 强制使用本地 TDX 数据（不触发网络回退）
os.environ['NO_VERIFY_TDX'] = '1'

from lib.data_layer import get_stock_hist, get_index_hist
from lib.technical_signals import ema
from lib.market_regime import detect_market_regime_from_hs300, TREND_UP, MEAN_REVERSION


def compute_indicators(df):
    """给数据添加 V2 所需指标"""
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    return df


def check_v2_entry(df, i: int) -> Tuple[bool, str]:
    """检查第i天是否满足V2入场条件"""
    if i < 20:
        return False, ""
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    # MA5/MA21 金叉
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    # 价格站上 EMA5
    price_ok = row['close'] > row['EMA5']
    if ma5_cross and price_ok:
        return True, "MA5_MA21_GC"
    return False, ""


def scan_v2_candidates(
    stock_codes: List[str],
    relative_strength_threshold: float = 0.0,
    lookback: int = 5,
) -> List[Dict]:
    """扫描 V2 策略的候选股票

    Returns:
        List[Dict], 每只股票包含:
        - code, name: 代码和名称（如果有）
        - entry_date: 入场日期
        - signal_type: 信号类型
        - price: 当前价格
        - ema5, ema21: 均线值
        - hs300_status: HS300市场状态
        - hs300_pvem: HS300偏离EMA21百分比
        - stock_ret: 股票近N日收益率
        - relative_strength: 相对HS300的强度
        - atr_pct: ATR占价格百分比
        - stop_price: 建议止损价
    """
    # 获取 HS300 市场状态
    hs300 = get_index_hist('000300', days=60)
    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['pvem'] = (hs300['close'] / hs300['EMA21'] - 1) * 100
    hs300['ema_slope_pct'] = hs300['EMA21'].pct_change(5) * 100

    def classify_regime(row):
        t = row['pvem'] / 100
        s = row['ema_slope_pct']
        if pd.isna(t) or pd.isna(s):
            return MEAN_REVERSION
        if t > 0.01 and s > 0.05:
            return TREND_UP
        elif t < -0.01 and s < -0.05:
            return "TREND_DOWN"
        else:
            return MEAN_REVERSION

    hs300['regime'] = hs300.apply(classify_regime, axis=1)

    latest_hs300 = hs300.iloc[-1]
    hs300_regime = latest_hs300['regime']
    hs300_pvem = latest_hs300['pvem']
    hs300_ret = hs300['close'].pct_change(lookback).iloc[-1] * 100

    candidates = []

    for code in stock_codes:
        try:
            df = get_stock_hist(code, days=60)
            if df is None or len(df) < 30:
                continue
            df = compute_indicators(df)

            # 取最后一行（今日或最近交易日）
            row = df.iloc[-1]
            prev_row = df.iloc[-2] if len(df) >= 2 else row

            # 市场过滤: HS300 > EMA21
            if not (latest_hs300['close'] > latest_hs300['EMA21']):
                continue

            # MEAN_REVERSION 市场过滤
            if hs300_regime == MEAN_REVERSION and hs300_pvem <= 3.0:
                continue  # 趋势不够强，跳过

            # V2 入场信号
            entered, sig_type = check_v2_entry(df, len(df) - 1)
            if not entered:
                continue

            # 计算股票收益率
            stock_ret = df['close'].pct_change(lookback).iloc[-1] * 100
            rel_strength = stock_ret - hs300_ret

            # 相对强度过滤
            if relative_strength_threshold > 0 and rel_strength < relative_strength_threshold:
                continue

            # 计算 ATR
            high = df['high']
            low = df['low']
            prev_close = df['close'].shift(1)
            tr1 = high - low
            tr2 = (high - prev_close).abs()
            tr3 = (low - prev_close).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            atr = tr.rolling(14).mean()
            atr_pct = atr.iloc[-1] / row['close'] * 100 if not pd.isna(atr.iloc[-1]) else 0
            stop_price = row['close'] * (1 - atr_pct * 2 / 100)

            candidates.append({
                'code': code,
                'entry_date': str(row['date'].date()) if hasattr(row['date'], 'date') else str(row['date']),
                'signal_type': sig_type,
                'price': round(float(row['close']), 2),
                'ema5': round(float(row['EMA5']), 2),
                'ema21': round(float(row['EMA21']), 2),
                'hs300_regime': hs300_regime,
                'hs300_pvem': round(hs300_pvem, 2),
                'hs300_ret': round(hs300_ret, 2),
                'stock_ret': round(stock_ret, 2),
                'rel_strength': round(rel_strength, 2),
                'atr_pct': round(atr_pct, 2),
                'stop_price': round(stop_price, 2),
                'volume_ratio': round(float(row['volume'] / row['vol_ma20']), 2) if not pd.isna(row['vol_ma20']) and row['vol_ma20'] > 0 else 0,
            })
        except Exception:
            continue

    # 按相对强度排序
    candidates.sort(key=lambda x: x['rel_strength'], reverse=True)
    return candidates


def get_available_stocks(limit: int = None) -> List[str]:
    """扫描本地 TDX 文件，生成可用股票列表"""
    from pathlib import Path

    sh_dir = Path('D:/TDX/vipdoc/sh/lday')
    sz_dir = Path('D:/TDX/vipdoc/sz/lday')

    stocks = []
    for f in sh_dir.iterdir():
        if not f.name.endswith('.day'):
            continue
        code = f.name[2:-4]
        if code.startswith(('600', '601', '603')):
            stocks.append(code)
    for f in sz_dir.iterdir():
        if not f.name.endswith('.day'):
            continue
        code = f.name[2:-4]
        if code.startswith(('000', '002')):
            stocks.append(code)

    if limit:
        stocks = stocks[:limit]
    return stocks


if __name__ == "__main__":
    import pandas as pd

    parser = argparse.ArgumentParser(description='V2 策略实时扫描')
    parser.add_argument('--limit', type=int, default=None, help='限制扫描股票数量（用于快速测试）')
    parser.add_argument('--stocks', type=str, default=None, help='指定股票代码，逗号分隔，如 000858,600519')
    parser.add_argument('--min-rel-strength', type=float, default=0.0, help='相对强度阈值（%%）')
    args = parser.parse_args()

    # 获取股票列表
    if args.stocks:
        stock_codes = [s.strip() for s in args.stocks.split(',')]
    else:
        stock_codes = get_available_stocks(limit=args.limit)

    print(f"V2 策略扫描")
    print(f"=" * 60)
    print(f"扫描股票数: {len(stock_codes)}")
    print(f"相对强度阈值: {args.min_rel_strength}%")
    print(f"扫描时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # 市场状态
    regime = detect_market_regime_from_hs300(days=60)
    print(f"【市场状态】{regime['regime']}")
    print(f"  置信度: {regime['confidence']:.0%}")
    print(f"  仓位范围: {regime['position_scalar'][0]:.0%}-{regime['position_scalar'][1]:.0%}")
    if regime.get('indicators'):
        ind = regime['indicators']
        print(f"  EMA斜率: {ind.get('ema_slope', 'N/A'):.3f}%")
        print(f"  趋势得分: {ind.get('trend_score', 'N/A'):.3f}")
        print(f"  ATR比率: {ind.get('atr_ratio', 'N/A'):.3f}")
    print()

    # 执行扫描
    candidates = scan_v2_candidates(
        stock_codes,
        relative_strength_threshold=args.min_rel_strength,
    )

    print(f"【扫描结果】")
    print(f"  符合条件股票: {len(candidates)} 只")
    print()

    if candidates:
        df = pd.DataFrame(candidates)
        print(df[['code', 'price', 'stock_ret', 'rel_strength', 'atr_pct', 'stop_price']].to_string(index=False))
    else:
        print("  当前无符合条件的股票（可能市场状态不适合）")

    print()
    print(f"备注: V2策略在 {regime['regime']} 市场下，", end="")
    if regime['regime'] == 'TREND_DOWN':
        print("禁止开仓。")
    elif regime['regime'] == 'MEAN_REVERSION':
        print("建议轻仓(25%-50%)，等待金叉信号。")
    else:
        print("可正常操作，积极做多。")
