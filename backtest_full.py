"""
backtest_full.py - 全量回测：3206只A股 × V2策略 × 多配置对比

用法：
    python backtest_full.py                    # 全量（3206只）
    python backtest_full.py --limit 500       # 快速测试（500只）

覆盖：
    A: 全量回测基准（3206只）
    C: 板块轮动过滤（TOP5/TOP8 板块强度，注：需真实行业分类数据）
    D: 情绪过滤（高波动时禁用 EMA21 退出）
"""

import os, sys, time
from pathlib import Path
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple

from lib.technical_signals import ema

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


# 覆盖 get_stock_hist 为本地版本（避免网络回退）
from lib import data_layer as _dl

def _patched_get_stock(code, days=500):
    df = _get_stock_local(code, days)
    if not df.empty:
        return df
    return pd.DataFrame()

# 指数用原始接口（000300有网络数据，不影响速度）
_dl.get_stock_hist = _patched_get_stock

# 导入
from lib.data_layer import get_index_hist
from lib.data_source_registry import get_stock_hist_multi_source


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

    # 板块映射（简化版：按前3位分配，与真实行业分类存在较大偏差）
    # 例如：600519(茅台)→金融(实际食品饮料)、600036(招行)→金融(实际银行)
    # 如需精确行业分类：使用 baostock query_stock_industry() 并缓存，
    # 但会增加网络依赖，与"纯本地数据回测"目标冲突。
    # 当前板块映射仅用于回测参考，不应用于实盘决策。
    SH_SECTORS = {
        '600': '金融', '601': '金融', '603': '消费',
    }
    SZ_SECTORS = {
        '000': '制造', '002': '制造',
    }

    sh_codes, sz_codes = [], []
    for f in sh_dir.iterdir():
        if not f.name.endswith('.day'): continue
        code = f.name[2:-4]
        if code.startswith(('600', '601', '603')):
            sh_codes.append(code)
            # 简化板块：取首三位的映射
            prefix3 = code[:3]
            SECTOR_MAP[code] = SH_SECTORS.get(prefix3, '工业')

    for f in sz_dir.iterdir():
        if not f.name.endswith('.day'): continue
        code = f.name[2:-4]
        if code.startswith(('000', '002')):
            sz_codes.append(code)
            prefix3 = code[:3]
            SECTOR_MAP[code] = SZ_SECTORS.get(prefix3, '制造')

    FULL_STOCKS = sh_codes + sz_codes
    print(f"扫描完成: {len(FULL_STOCKS)} 只股票, {len(SECTOR_MAP)} 只有板块映射")


def get_all_stocks() -> List[str]:
    _scan_local_stocks()
    return FULL_STOCKS


# ============================================================
# 指标计算
# ============================================================

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    prev_close = df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['atr'] = compute_atr(df)
    df['atr_pct'] = df['atr'] / df['close'] * 100
    df['low_10'] = df['low'].rolling(10).min()
    return df


# ============================================================
# 入场/出场
# ============================================================

def check_entry(df: pd.DataFrame, i: int) -> bool:
    if i < 20:
        return False
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    ma5_cross = (row['EMA5'] > row['EMA21']) and (prev['EMA5'] <= prev['EMA21'])
    price_ok = row['close'] > row['EMA5']
    return bool(ma5_cross and price_ok)


def check_exit_v2(df: pd.DataFrame, entry_i: int, hs300_vol_ratio: float = 1.0) -> Tuple[str, float, int]:
    """V2 趋势持有（day1-10 EMA21有效，day11-20禁用）

    D 情绪过滤：当 HS300 波动率升高时，禁用 EMA21 退出，
    改用 trailing_stop 或 timeout，避免在震荡中被洗出。
    hs300_vol_ratio: 当前 HS300 ATR / 20日均值（>1.5 表示高波动）

    Returns:
        (exit_reason, exit_price, exit_i)
    """
    entry_row = df.iloc[entry_i]
    trailing_stop = entry_row['low_10'] if not pd.isna(entry_row['low_10']) else entry_row['close'] * 0.95
    max_exit_i = min(entry_i + 20, len(df) - 1)

    # D: 高波动时完全禁用 EMA21 退出
    use_ema21_exit = (hs300_vol_ratio <= 1.5) and (hs300_vol_ratio >= 0.5)

    for j in range(entry_i + 1, max_exit_i + 1):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]
        days_held = j - entry_i

        # 移动止损（全程）
        if not pd.isna(row['low_10']):
            trailing_stop = max(trailing_stop, row['low_10'])
        if row['close'] < trailing_stop:
            return "trailing_stop", float(trailing_stop), j

        # EMA21 辅助（仅前10天，且非高波动）
        if use_ema21_exit and days_held <= 10:
            if row['close'] < row['EMA21'] and prev_row['close'] < prev_row['EMA21']:
                return "trend_break", float(row['close']), j

    last_i = max_exit_i
    return "timeout", float(df.iloc[last_i]['close']), last_i


# ============================================================
# 预计算：HS300 序列
# ============================================================

def build_hs300_series():
    hs300 = get_index_hist('000300', days=500)
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

def build_stock_returns(stock_codes: List[str]):
    """预计算所有股票的 5 日收益率（避免重复计算）"""
    stock_rets = {}
    for code in stock_codes:
        try:
            df = get_stock_hist_multi_source(code, days=500, source="tdx_local_qfq")
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


# ============================================================
# 全量回测核心
# ============================================================

def run_full_backtest(
    stock_codes: List[str],
    start_date: str,
    end_date: str,
    sector_filter_top_n: int = 0,
    rel_strength_threshold: float = 0.0,
    use_sentiment_filter: bool = False,
) -> Dict:
    """全量回测"""

    print(f"  预计算 HS300 序列...")
    hs300 = build_hs300_series()
    hs300_regime = dict(zip(hs300['date_str'], hs300['regime']))
    hs300_pvem = dict(zip(hs300['date_str'], hs300['pvem']))
    hs300_ret5 = dict(zip(hs300['date_str'], hs300['ret5']))
    hs300_vol = dict(zip(hs300['date_str'], hs300['vol_ratio']))

    # 板块过滤和相对强度过滤需要预计算
    stock_rets = {}
    if sector_filter_top_n > 0 or rel_strength_threshold > 0:
        print(f"  预计算股票收益率 ({len(stock_codes)} 只)...")
        stock_rets = build_stock_returns(stock_codes)

    all_trades = []

    print(f"  开始回测 ({len(stock_codes)} 只股票)...")
    t0 = time.time()
    processed = 0

    for code_idx, code in enumerate(stock_codes):
        try:
            df = get_stock_hist_multi_source(code, days=500, source="tdx_local_qfq")
            if df is None or len(df) < 60:
                continue
            df = add_indicators(df)
            df = df[df['date'] >= start_date]
            if len(df) < 30:
                continue

            df['date_str'] = df['date'].apply(lambda d: str(d.date()) if hasattr(d, 'date') else str(d))
            df['stock_ret5'] = df['close'].pct_change(5) * 100

            for i in range(30, len(df) - 1):
                row = df.iloc[i]
                entry_date_str = row['date_str']

                # HS300 市场过滤
                hs_match = hs300[hs300['date_str'] <= entry_date_str]
                if hs_match.empty:
                    continue
                hs_latest = hs_match.iloc[-1]
                if not (hs_latest['close'] > hs_latest['EMA21']):
                    continue

                # MEAN_REVERSION 过滤
                regime = hs300_regime.get(entry_date_str, "MEAN_REVERSION")
                pvem = hs300_pvem.get(entry_date_str, 0.0)
                if regime == "MEAN_REVERSION" and pvem <= 3.0:
                    continue

                # 相对强度过滤（仅当阈值 > 0 时）
                if rel_strength_threshold > 0:
                    stock_ret = row.get('stock_ret5')
                    hs_ret = hs300_ret5.get(entry_date_str)
                    if stock_ret is None or hs_ret is None:
                        continue
                    if (stock_ret - hs_ret) < rel_strength_threshold:
                        continue

                # 板块过滤（仅当 top_n > 0 时）
                if sector_filter_top_n > 0 and stock_rets:
                    top_sectors = get_top_sectors(entry_date_str, stock_rets, top_n=sector_filter_top_n)
                    stock_sector = SECTOR_MAP.get(code)
                    if top_sectors and stock_sector not in top_sectors:
                        continue

                # 入场信号
                if not check_entry(df, i):
                    continue

                # D 情绪过滤：获取 HS300 波动率
                hs300_vol_ratio = hs300_vol.get(entry_date_str, 1.0) if use_sentiment_filter else 1.0

                # 出场
                exit_reason, exit_price, exit_i = check_exit_v2(df, i, hs300_vol_ratio=hs300_vol_ratio)

                entry_price = float(row['close'])
                pnl_pct = round((exit_price / entry_price - 1) * 100, 2)
                hold_days = exit_i - i
                exit_row = df.iloc[exit_i]

                all_trades.append({
                    'entry_date': entry_date_str,
                    'exit_date': exit_row['date_str'],
                    'code': code,
                    'pnl_pct': pnl_pct,
                    'hold_days': hold_days,
                    'exit_reason': exit_reason,
                    'sector': SECTOR_MAP.get(code, '未知'),
                })

        except Exception:
            continue

        processed += 1
        if processed % 500 == 0:
            elapsed = time.time() - t0
            print(f"    进度: {processed}/{len(stock_codes)}, 耗时: {elapsed:.1f}s, 当前交易: {len(all_trades)}")

    elapsed_total = time.time() - t0
    print(f"  回测完成: {len(all_trades)} 笔交易, 耗时: {elapsed_total:.1f}s")

    if not all_trades:
        return _empty_metrics()

    # 计算指标
    df_t = pd.DataFrame(all_trades)
    df_t = df_t.sort_values('exit_date')

    pnl_list = df_t['pnl_pct'].tolist()
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    total_trades = len(pnl_list)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    avg_pnl = np.mean(pnl_list) if pnl_list else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

    # 最大回撤（简化版：按 exit_date 累加）
    initial_capital = 1_000_000
    trade_amount = initial_capital * 0.10
    equity = initial_capital
    equity_curve = []
    for _, r in df_t.iterrows():
        equity += trade_amount * r['pnl_pct'] / 100
        equity_curve.append(equity)
    eq_series = pd.Series(equity_curve)
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max * 100
    max_drawdown = drawdown.min()

    # 按退出原因
    by_reason = df_t.groupby('exit_reason')['pnl_pct'].agg(['count', 'mean'])
    by_reason.columns = ['count', 'avg_pnl']

    # 按板块（仅 C 配置）
    by_sector = None
    if sector_filter_top_n > 0:
        by_sector = df_t.groupby('sector')['pnl_pct'].agg(['count', 'mean', 'std']).round(2)
        by_sector.columns = ['count', 'avg_pnl', 'std']

    return {
        'total_trades': total_trades,
        'win_rate': round(win_rate * 100, 1),
        'avg_pnl': round(avg_pnl, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_factor': round(pf, 2),
        'max_drawdown': round(max_drawdown, 2),
        'total_return': round((equity - initial_capital) / initial_capital * 100, 1),
        'final_capital': round(equity, 0),
        'by_reason': by_reason,
        'by_sector': by_sector,
        'elapsed_seconds': round(elapsed_total, 1),
    }


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
    r = run_full_backtest(stocks, start_date, end_date, sector_filter_top_n=0)
    m = r
    print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
          f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return']}%, "
          f"耗时={m['elapsed_seconds']}s")
    if not m['by_reason'].empty:
        for reason, row in m['by_reason'].iterrows():
            print(f"    {reason}: {int(row['count'])}笔, 均={row['avg_pnl']:+.2f}%")

    # === C: 板块轮动 ===
    for top_n in [5, 8]:
        print(f"\n【C-{top_n}】板块轮动 TOP{top_n}")
        r = run_full_backtest(stocks, start_date, end_date, sector_filter_top_n=top_n)
        m = r
        print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
              f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return']}%")
        if not m['by_reason'].empty:
            for reason, row in m['by_reason'].iterrows():
                print(f"    {reason}: {int(row['count'])}笔, 均={row['avg_pnl']:+.2f}%")

    # === A+C: 全量 + 相对强度 ===
    print(f"\n【A+RS1%】全量 + 相对强度阈值 1%")
    r = run_full_backtest(stocks, start_date, end_date, rel_strength_threshold=1.0)
    m = r
    print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
          f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return']}%")
    if not m['by_reason'].empty:
        for reason, row in m['by_reason'].iterrows():
            print(f"    {reason}: {int(row['count'])}笔, 均={row['avg_pnl']:+.2f}%")

    # === D: 情绪过滤（高波动时禁用 EMA21 退出）===
    print(f"\n【D】情绪过滤（高波动禁用 EMA21）")
    r = run_full_backtest(stocks, start_date, end_date, use_sentiment_filter=True)
    m = r
    print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
          f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return']}%")
    if not m['by_reason'].empty:
        for reason, row in m['by_reason'].iterrows():
            print(f"    {reason}: {int(row['count'])}笔, 均={row['avg_pnl']:+.2f}%")

    # === A+C+D: 全量 + 板块轮动 TOP5 + 情绪过滤 ===
    print(f"\n【A+C+D】全量 + TOP5板块 + 情绪过滤")
    r = run_full_backtest(stocks, start_date, end_date, sector_filter_top_n=5, use_sentiment_filter=True)
    m = r
    print(f"  交易={m['total_trades']}, 胜率={m['win_rate']}%, 均值={m['avg_pnl']}%, "
          f"PF={m['profit_factor']}, DD={m['max_drawdown']}%, 收益={m['total_return']}%")
    if not m['by_reason'].empty:
        for reason, row in m['by_reason'].iterrows():
            print(f"    {reason}: {int(row['count'])}笔, 均={row['avg_pnl']:+.2f}%")

    print(f"\n{'='*60}")
    print("全部配置回测完成")
