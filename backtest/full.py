"""
backtest_full.py - [DEPRECATED] 全量回测

本文件已废弃。请使用 backtest.engine.run_backtest(parallel=True) 替代。
保留辅助函数（_get_stock_local, _scan_local_stocks 等）供兼容使用。
"""

import os, sys
from pathlib import Path
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple

from lib.scoring.technical_signals import ema
from backtest.utils import compute_atr, add_indicators

# 本地文件直接读取（避免网络回退）
def _get_stock_local(code: str, days: int = 500) -> pd.DataFrame:
    """只从本地 TDX 文件读取，不触发网络"""
    from pytdx.reader import TdxDailyBarReader
    reader = TdxDailyBarReader()
    code = code.strip().zfill(6)
    if code.startswith(('6', '5', '9')):
        market_str, subdir = 'sh', 'D:/TDX/vipdoc/sh/lday'
    elif code.startswith(('0', '2', '3')):
        market_str, subdir = 'sz', 'D:/TDX/vipdoc/sz/lday'
    else:
        return pd.DataFrame()
    fp = Path(subdir) / f'{market_str}{code}.day'
    if not fp.exists():
        return pd.DataFrame()
    # 抑制 pytdx 的错误输出
    old_stderr = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    try:
        df = reader.get_df(str(fp))
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        df = df.rename(columns={'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'})
        for c in ['open', 'high', 'low', 'close', 'volume']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
        if len(df) > days:
            df = df.tail(days)
        return df[['date', 'open', 'high', 'low', 'close', 'volume']]
    except Exception:
        return pd.DataFrame()
    finally:
        os.dup2(old_stderr, 2)
        os.close(devnull)
        os.close(old_stderr)


def _get_index_local(code: str, days: int = 500) -> pd.DataFrame:
    """从本地 TDX 文件读取指数数据"""
    return _get_stock_local(code, days)


# P-118（2026-11-21，批次 C13）：模块级全局猴补丁已删除。
# 旧实现 `_dl.get_stock_hist = _patched_get_stock` 在 import 期永久替换
# data_layer.get_stock_hist 为本地 TDX 版——backtest/__init__ 导入 full 即触发，
# 任何 import backtest 的进程（API/CLI/测试）全局数据断供；ADR-0017 删除本地
# TDX 后补丁函数恒返回空帧，危害从「避免网络回退」变成「get_stock_hist 全局
# 返回空且永不还原」。full.py 自身数据路径走 get_stock_hist_multi_source，
# 不依赖该补丁（grep 证实零内部调用点）。

# 导入
from lib.data.data_layer import get_index_hist
from lib.data.data_source_registry import get_stock_hist_multi_source


# ============================================================
# 配置
# ============================================================

FULL_STOCKS: List[str] = []
SECTOR_MAP: Dict[str, str] = {}  # code → sector（用于板块轮动）

# 扫描本地 TDX 文件，建立股票列表 + 板块映射
def _scan_local_stocks():
    global FULL_STOCKS, SECTOR_MAP
    if FULL_STOCKS:
        return  # 已扫描

    sh_dir = Path('D:/TDX/vipdoc/sh/lday')
    sz_dir = Path('D:/TDX/vipdoc/sz/lday')

    # 加载真实行业分类（baostock 缓存）
    sector_cache_path = Path('cache/sector_map.json')
    cached_sectors = {}
    if sector_cache_path.exists():
        import json
        with open(sector_cache_path, 'r', encoding='utf-8') as f:
            cached_sectors = json.load(f)
        print(f"  加载行业缓存: {len(cached_sectors)} 只股票")

    sh_codes, sz_codes = [], []
    for f in sh_dir.iterdir():
        if not f.name.endswith('.day'): continue
        code = f.name[2:-4]
        if code.startswith(('600', '601', '603')):
            sh_codes.append(code)
            SECTOR_MAP[code] = cached_sectors.get(code, '未知')

    for f in sz_dir.iterdir():
        if not f.name.endswith('.day'): continue
        code = f.name[2:-4]
        if code.startswith(('000', '002')):
            sz_codes.append(code)
            SECTOR_MAP[code] = cached_sectors.get(code, '未知')

    FULL_STOCKS = sh_codes + sz_codes
    # 统计板块分布
    from collections import Counter
    sector_dist = Counter(SECTOR_MAP.values())
    unknown = sector_dist.get('未知', 0)
    print(f"扫描完成: {len(FULL_STOCKS)} 只股票, {len(SECTOR_MAP)} 只有板块映射, 未分类={unknown}")


def get_all_stocks() -> List[str]:
    _scan_local_stocks()
    return FULL_STOCKS


# ============================================================
# 入场/出场
# ============================================================

def check_entry(df: pd.DataFrame, i: int) -> bool:
    """入场信号：多信号 OR 口径（全市场并行回测用）

    ⚠️ **不是重复实现** —— `backtest/engine.py::check_entry` 是 **Donchian 突破** 口径，
    二者服务于不同回测引擎、对应不同策略假设，**不可合并**。
    详见 `docs/adr/0002-check-entry-duplicate-not-merge.md`。

    入场信号检查（多信号 OR 逻辑）

    信号1: EMA5/EMA21 金叉 + 价格>EMA5（原有）
    信号2: RSI 超卖反弹（RSI<30 后回升到 35）
    信号3: 成交量突破（5日均量/20日均量 > 1.5）
    信号4: MACD 金叉（MACD 线上穿信号线）
    """
    if i < 20:
        return False
    row = df.iloc[i]
    prev = df.iloc[i - 1]

    # 信号1: EMA 金叉
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    price_ok = row['close'] > row['EMA5']
    if ma5_cross and price_ok:
        return True

    # 信号2: RSI 超卖反弹
    if 'rsi' in row.index and 'rsi' in prev.index:
        if prev.get('rsi', 50) < 30 and row.get('rsi', 50) >= 35:
            return True

    # 信号3: 成交量突破
    if 'volume' in df.columns:
        vol5 = df['volume'].iloc[max(0, i-5):i].mean()
        vol20 = df['volume'].iloc[max(0, i-20):i].mean()
        if vol20 > 0 and vol5 / vol20 > 1.5:
            if row['close'] > row.get('EMA21', row['close']):
                return True

    # 信号4: MACD 金叉
    if 'macd' in row.index and 'macd_signal' in row.index:
        if (row.get('macd', 0) > row.get('macd_signal', 0) and
            prev.get('macd', 0) <= prev.get('macd_signal', 0)):
            return True

    return False


def check_exit_v2(df: pd.DataFrame, entry_i: int, hs300_vol_ratio: float = 1.0) -> Tuple[str, float, int]:
    """V2 趋势持有（day1-10 EMA21有效，day11-20禁用）

    D 情绪过滤：当 HS300 波动率升高时，禁用 EMA21 退出，
    改用 trailing_stop 或 timeout，避免在震荡中被洗出。
    hs300_vol_ratio: 当前 HS300 ATR / 20日均值（>1.5 表示高波动）

    Returns:
        (exit_reason, exit_price, exit_i)
    """
    entry_row = df.iloc[entry_i]
    entry_price = float(entry_row['open']) if 'open' in entry_row.index else float(entry_row['close'])
    atr_at_entry = float(entry_row.get('atr', 0))
    if atr_at_entry <= 0 or pd.isna(atr_at_entry):
        atr_at_entry = entry_price * 0.02  # fallback: 2% of price

    # ATR-based trailing stop（优化4: 更适应波动率）
    trailing_stop = entry_price - 2.0 * atr_at_entry  # 初始止损: 入场价 - 2ATR
    peak_price = entry_price
    max_exit_i = min(entry_i + 20, len(df) - 1)

    # D: 高波动时完全禁用 EMA21 退出
    use_ema21_exit = (hs300_vol_ratio <= 1.2) and (hs300_vol_ratio >= 0.5)

    for j in range(entry_i + 1, max_exit_i + 1):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]
        days_held = j - entry_i
        current_price = float(row['close'])
        current_atr = float(row.get('atr', atr_at_entry))
        if current_atr <= 0 or pd.isna(current_atr):
            current_atr = atr_at_entry

        # 更新峰值和移动止损
        if current_price > peak_price:
            peak_price = current_price
            # 移动止损: 峰值 - 2ATR（随价格上涨而上移）
            trailing_stop = max(trailing_stop, peak_price - 2.0 * current_atr)

        # 移动止损触发
        if current_price < trailing_stop:
            return "trailing_stop", float(trailing_stop), j

        # EMA21 辅助（仅前10天，且非高波动）
        if use_ema21_exit and days_held <= 10:
            if current_price < row['EMA21'] and float(prev_row['close']) < float(prev_row['EMA21']):
                return "trend_break", current_price, j

    last_i = max_exit_i
    return "timeout", float(df.iloc[last_i]['close']), last_i


# ============================================================
# 预计算：HS300 序列
# ============================================================

def build_hs300_series(days=500):
    hs300 = get_index_hist('000300', days=days)
    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['pvem'] = (hs300['close'] / hs300['EMA21'] - 1) * 100
    hs300['ema_slope_pct'] = hs300['EMA21'].pct_change(5) * 100

    def classify_regime(row):
        t = row['pvem'] / 100
        s = row['ema_slope_pct']
        if pd.isna(t) or pd.isna(s):
            return "MEAN_REVERSION"
        if t > 0.01 and s > 0.05:
            return "TREND_UP"
        elif t < -0.01 and s < -0.05:
            return "TREND_DOWN"
        return "MEAN_REVERSION"

    hs300['regime'] = hs300.apply(classify_regime, axis=1)
    hs300['date_str'] = hs300['date'].apply(lambda d: str(d.date()) if hasattr(d, 'date') else str(d))
    hs300['ret5'] = hs300['close'].pct_change(5) * 100

    # 趋势强度评分（用于入场过滤）
    # trend_score = abs(ema_slope) / atr_ratio，值越大趋势越强
    hs300['trend_score'] = hs300['ema_slope_pct'].abs()

    # HS300 波动率（用于 D 情绪过滤）
    high = hs300['high']
    low = hs300['low']
    prev_close = hs300['close'].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    hs300['atr'] = tr.rolling(14).mean()
    hs300['atr_ma20'] = hs300['atr'].rolling(20).mean()
    hs300['vol_ratio'] = hs300['atr'] / hs300['atr_ma20']
    return hs300


# ============================================================
# 预计算：每只股票每天的 5 日收益率（用于板块强度）
# ============================================================

def build_stock_returns(stock_codes: List[str], days: int = 500):
    """预计算所有股票的 5 日收益率（避免重复计算）"""
    stock_rets = {}
    for code in stock_codes:
        try:
            df = get_stock_hist_multi_source(code, days=days, source="auto", prefer_adjusted=True)
            if df is None or len(df) < 20:
                continue
            df = df.copy()
            df['ret5'] = df['close'].pct_change(5) * 100
            df['date_str'] = df['date'].apply(lambda d: str(d.date()) if hasattr(d, 'date') else str(d))
            stock_rets[code] = df.set_index('date_str')['ret5']
        except:
            continue
    return stock_rets


# ============================================================
# 板块强度
# ============================================================

def get_top_sectors(date_str: str, stock_rets: Dict, top_n: int = 5) -> set:
    """返回指定日期最强的 top_n 板块名称集合"""
    sector_returns = {}
    for code, sret in stock_rets.items():
        sector = SECTOR_MAP.get(code)
        if not sector:
            continue
        ret = sret.get(date_str)
        if ret is None or pd.isna(ret):
            continue
        if sector not in sector_returns:
            sector_returns[sector] = []
        sector_returns[sector].append(ret)

    if not sector_returns:
        return set()

    sector_avg = {s: np.mean(r) for s, r in sector_returns.items()}
    sorted_sectors = sorted(sector_avg, key=sector_avg.get, reverse=True)
    return set(sorted_sectors[:top_n])


def precompute_top_sectors(stock_rets: Dict, top_n: int = 5) -> Dict[str, set]:
    """预计算所有日期的板块排名（避免内层循环重复计算）

    Returns:
        {date_str: set_of_top_sector_names}
    """
    # 收集所有日期
    all_dates = set()
    for sret in stock_rets.values():
        all_dates.update(sret.keys())

    result = {}
    for date_str in sorted(all_dates):
        result[date_str] = get_top_sectors(date_str, stock_rets, top_n)
    return result


# ============================================================
# 全量回测核心
# ============================================================

def _process_single_stock(args):
    """处理单只股票的回测（用于多进程）

    Args:
        args: tuple(code, start_date, data_days, hs300_dict, sector_map, sector_filter_top_n,
                    rel_strength_threshold, use_sentiment_filter, stock_rets_code_ret)
    Returns:
        list of trade dicts
    """
    code, start_date, data_days, hs300_dates, hs300_closes, hs300_emas, hs300_regime_dict, \
        hs300_pvem_dict, hs300_ret5_dict, hs300_vol_dict, hs300_trend_dict, \
        sector_map, sector_filter_top_n, rel_strength_threshold, \
        use_sentiment_filter, precomputed_sectors = args

    trades = []
    try:
        df = get_stock_hist_multi_source(code, days=data_days, source="auto", prefer_adjusted=True)
        if df is None or len(df) < 60:
            return trades
        df = add_indicators(df)
        df = df[df['date'] >= start_date]
        if len(df) < 30:
            return trades

        df['date_str'] = df['date'].apply(lambda d: str(d.date()) if hasattr(d, 'date') else str(d))
        df['stock_ret5'] = df['close'].pct_change(5) * 100

        for i in range(30, len(df) - 1):
            row = df.iloc[i]
            entry_date_str = row['date_str']

            # HS300 市场过滤（用 dict 查找替代 DataFrame 过滤）
            if entry_date_str not in hs300_dates:
                continue
            if not (hs300_closes.get(entry_date_str, 0) > hs300_emas.get(entry_date_str, 0)):
                continue

            # 市场状态过滤（优化1: 跳过COMPRESSION，收紧MEAN_REVERSION）
            regime = hs300_regime_dict.get(entry_date_str, "MEAN_REVERSION")
            pvem = hs300_pvem_dict.get(entry_date_str, 0.0)

            if regime == "COMPRESSION":
                continue  # 压缩状态不做多

            if regime == "MEAN_REVERSION" and pvem <= 5.0:
                continue  # 均值回归时需要更强的趋势偏离（pvem>5%）

            if regime == "TREND_DOWN":
                continue  # 下跌趋势不做多

            # 趋势强度过滤（优化3: 只在趋势明确时交易）
            trend_score = hs300_trend_dict.get(entry_date_str, 0)
            if trend_score < 0.3:
                continue  # 趋势太弱，不做

            # 情绪过滤（入场级）：高波动时跳过入场
            if use_sentiment_filter:
                vol_ratio = hs300_vol_dict.get(entry_date_str, 1.0)
                if vol_ratio > 1.2:  # ATR > 1.2倍均值 = 高波动
                    continue

            # 相对强度过滤
            if rel_strength_threshold > 0:
                stock_ret = row.get('stock_ret5')
                hs_ret = hs300_ret5_dict.get(entry_date_str)
                if stock_ret is None or hs_ret is None:
                    continue
                if (stock_ret - hs_ret) < rel_strength_threshold:
                    continue

            # 板块过滤（使用预计算的板块排名）
            if sector_filter_top_n > 0 and precomputed_sectors:
                top_sectors = precomputed_sectors.get(entry_date_str, set())
                stock_sector = sector_map.get(code)
                if top_sectors and stock_sector not in top_sectors:
                    continue

            # 入场信号
            if not check_entry(df, i):
                continue

            # 入场（次日开盘价）
            if i + 1 >= len(df):
                continue
            entry_row = df.iloc[i + 1]
            entry_price = float(entry_row['open'])
            entry_i = i + 1

            # 退出（使用 backtest_full 的 check_exit_v2）
            hs300_vol_ratio = hs300_vol_dict.get(entry_date_str, 1.0) if use_sentiment_filter else 1.0
            exit_reason, exit_price, exit_i = check_exit_v2(df, entry_i, hs300_vol_ratio=hs300_vol_ratio)

            # 入场信号
            if not check_entry(df, i):
                continue

            # 入场（次日开盘价）
            if i + 1 >= len(df):
                continue
            entry_row = df.iloc[i + 1]
            entry_price = float(entry_row['open'])
            entry_i = i + 1

            # 退出（使用 backtest_full 的 check_exit_v2）
            hs300_vol_ratio = hs300_vol_dict.get(entry_date_str, 1.0) if use_sentiment_filter else 1.0
            exit_reason, exit_price, exit_i = check_exit_v2(df, entry_i, hs300_vol_ratio=hs300_vol_ratio)

            pnl_pct = (exit_price / entry_price - 1) * 100
            exit_row = df.iloc[exit_i]
            exit_date_str = exit_row['date_str'] if 'date_str' in exit_row.index else entry_date_str

            trades.append({
                'code': code,
                'entry_date': entry_date_str,
                'exit_date': exit_date_str,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'pnl_pct': round(pnl_pct, 2),
                'exit_reason': exit_reason,
                'regime': regime,
                'sector': sector_map.get(code, '未知'),
            })
    except Exception:
        pass

    return trades


def run_full_backtest(symbols=None, days=500, max_workers=4, **kwargs):
    """[DEPRECATED] 请使用 backtest.engine.run_backtest(parallel=True)"""
    import warnings
    warnings.warn(
        "run_full_backtest is deprecated, use backtest.engine.run_backtest(parallel=True)",
        DeprecationWarning,
        stacklevel=2,
    )
    from backtest.engine import run_backtest
    return run_backtest(
        # ⚠️ stock_codes 是 run_backtest 的**必填位置参数**，symbols 只是别名
        #    （engine.py:635-637）⇒ 只传 symbols 会抛 missing 1 required positional argument
        stock_codes=symbols,
        symbols=symbols, days=days,
        parallel=True, max_workers=max_workers,
        multi_signal_entry=True, sentiment_filter=True,
        **kwargs,
    )


def _empty_metrics():
    return {
        'total_trades': 0, 'win_rate': 0, 'avg_pnl': 0,
        'avg_win': 0, 'avg_loss': 0, 'profit_factor': 0,
        'max_drawdown': 0, 'total_return': 0,
        'final_capital': 1_000_000, 'elapsed_seconds': 0,
    }


# ============================================================
# 主程序
# ============================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=None, help='限制股票数量（测试用）')
    args = parser.parse_args()

    stocks = get_all_stocks()
    if args.limit:
        stocks = stocks[:args.limit]

    print(f"{'='*60}")
    print(f"全量回测: {len(stocks)} 只股票")
    print(f"{'='*60}")

    start_date = '2024-01-01'
    end_date = '2026-05-22'

    # === A: 全量基准 ===
    print("\n【A】全量基准（无过滤）")
    from backtest.engine import run_backtest as _run_bt
    r = _run_bt(stocks, start_date, end_date, parallel=True, multi_signal_entry=True, sentiment_filter=True)
    m = r['metrics']
    print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
          f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return_pct']}%")

    print(f"\n{'='*60}")
    print("全部配置回测完成")
