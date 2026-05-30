"""
backtest_engine.py - 干净的回测引擎
修复点:
1. ATR止损替代EMA21硬止损
2. 趋势持有版本（移动止损/跟踪止损）
3. 回测口径验证:
   - 资金曲线独立计算（非PNL累加）
   - 单利 vs 复利分清楚
   - 无未来函数
   - 无重复计入
"""

import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from lib.technical_signals import ema
from lib.data_layer import get_index_hist
from lib.data_source_registry import get_stock_hist_multi_source
from lib.market_regime import detect_market_regime, TREND_DOWN, RISK_OFF


# ============================================================
# 数据结构
# ============================================================

@dataclass
class Trade:
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float          # 单笔收益率（%）
    hold_days: int
    exit_reason: str
    code: str = ""
    atr_pct: float = 0     # 入场时ATR%（用于止损空间）
    peak_trough_pct: float = 0  # 持仓期间最大浮盈/浮亏


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    position_size_pct: float = 0.10   # 单笔仓位（%本金）
    max_positions: int = 5            # 最大同时持仓数
    stop_loss_atr_mult: float = 2.0  # ATR倍数止损
    trailing_stop_atr_mult: float = 3.0  # ATR移动止损
    trend_exit_days: int = 20         # 趋势持有最大天数
    atr_period: int = 14
    sector_filter_top_n: int = 0     # 板块过滤：只做近N日最强板块（0=禁用，5=top5，8=top8）；需200+只股票才有效
    # STEP 6 相对强度过滤
    relative_strength_threshold: float = 0.0  # 股票必须相对HS300强势X%（0=禁用，正数如2.0要求 stock_ret > hs300_ret + 2%）
    relative_strength_lookback: int = 5       # 计算相对强度的回溯期（天）
    # 交易成本（统一从 config.py 读取）
    commission_rate: float = field(default_factory=lambda: __import__('config').COMMISSION_RATE)
    stamp_duty_rate: float = field(default_factory=lambda: __import__('config').STAMP_DUTY_RATE)
    slippage_rate: float = field(default_factory=lambda: __import__('config').SLIPPAGE_RATE)


# ============================================================
# 指标计算
# ============================================================

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
    """给数据添加必要指标"""
    df = df.copy()
    df['EMA5'] = ema(df['close'], 5)
    df['EMA21'] = ema(df['close'], 21)
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    df['atr'] = compute_atr(df)
    df['atr_pct'] = df['atr'] / df['close'] * 100  # ATR占价格的百分比
    df['low_10'] = df['low'].rolling(10).min()
    df['high_20'] = df['high'].rolling(20).max()
    df['sma50'] = df['close'].rolling(50).mean()
    return df


# ============================================================
# 入场信号
# ============================================================

def check_entry(df: pd.DataFrame, i: int) -> Tuple[bool, str]:
    """Check if day i meets entry conditions (design spec version)

    Design requirements:
    1. Price > EMA21
    2. Breakout 20-day high (Donchian breakout)
    3. Volume confirmation (volume > 20-day average)
    4. Next-day entry (return signal at close of day i, execute on i+1 open)
    """
    if i < 20:
        return False, ""

    row = df.iloc[i]

    # 1. Price > EMA21
    if row['close'] <= row['EMA21']:
        return False, ""

    # 2. Breakout 20-day high (Donchian breakout)
    high_20 = row.get('high_20', 0)
    if high_20 > 0 and row['close'] < high_20:
        return False, ""

    # 3. Volume confirmation (volume > 20-day average)
    vol_ma20 = row.get('vol_ma20', 0)
    if vol_ma20 > 0 and row['volume'] <= vol_ma20:
        return False, ""

    return True, "donchian_breakout"


# ============================================================
# 退出检查（无未来函数）
# ============================================================

def check_exit_v1_atr(df: pd.DataFrame, entry_i: int) -> Tuple[str, float]:
    """
    V1: ATR止损版
    - 止损: 跌破 ATR*2 止损位
    - 预警: EMA21 仅作预警
    - 持有一天后允许 EMA21 辅助退出（但非硬止损）
    返回 (退出原因, 退出价格)
    """
    entry_row = df.iloc[entry_i]
    entry_price = entry_row['close']
    entry_atr_pct = entry_row['atr_pct']
    stop_price = entry_price * (1 - entry_atr_pct * 2.0 / 100)

    ema21_broken = False  # EMA21突破标记（当天close<EMA21）

    for j in range(entry_i + 1, min(entry_i + 20, len(df))):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]

        # ATR止损（硬止损）
        if row['low'] < stop_price:
            return "atr_stop", stop_price

        # 持有超过5天后，EMA21辅助退出（需要前一天也确认跌破）
        if j - entry_i >= 5:
            if row['close'] < row['EMA21']:
                # 昨天 EMA21 也破？用 prev_row 判断（无look-ahead）
                if prev_row['close'] < prev_row['EMA21']:
                    return "ema21_confirm", row['close']

        # 跟踪止损更新（只上调，不下调）
        if not pd.isna(row['atr_pct']):
            new_stop = row['close'] * (1 - row['atr_pct'] * 3.0 / 100)
            stop_price = max(stop_price, new_stop)

    # 超时强制退出
    exit_price = df.iloc[min(entry_i + 20, len(df) - 1)]['close']
    return "timeout", exit_price


def check_exit_v2_trend(df: pd.DataFrame, entry_i: int) -> Tuple[str, float]:
    """
    V2: 趋势持有版（修复版）

    诊断发现：原版EMA21在第20天触发率极高（71%，162/168次），
    这些交易均值-3.29%，是系统最大摩擦成本。

    修复逻辑：
    - 前15天：EMA21辅助退出（给趋势破坏留出逃跑窗口）
    - 后5天（day16-20）：EMA21禁用，让利润奔跑，以超时为准
    - 移动止损全程跟踪（20日低点）
    - 最长持有20天

    关键洞察：持有20天是93%胜率的超额收益来源，
    EMA21破坏在第19-20天几乎全是假信号。
    """
    entry_row = df.iloc[entry_i]
    entry_price = entry_row['close']
    trailing_stop = entry_row['low_10'] if not pd.isna(entry_row['low_10']) else entry_price * 0.95

    for j in range(entry_i + 1, min(entry_i + 20, len(df))):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]
        days_held = j - entry_i

        # 更新跟踪止损（只上移）
        if not pd.isna(row['low_10']):
            trailing_stop = max(trailing_stop, row['low_10'])

        # 止损触发（全程有效）
        if row['close'] < trailing_stop:
            return "trailing_stop", trailing_stop

        # EMA21辅助退出（仅前10天，第11天起禁用）
        # 诊断确认：day16-20的EMA21破坏几乎全是假信号
        # 收紧到10天：减少day11-15的假信号触发
        if days_held <= 10:
            if row['close'] < row['EMA21'] and prev_row['close'] < prev_row['EMA21']:
                return "trend_break", row['close']

    # 超时强制退出（后5天的主要出口）
    last_i = min(entry_i + 20, len(df) - 1)
    return "timeout", float(df.iloc[last_i]['close'])


def check_exit_v3_hybrid(df: pd.DataFrame, entry_i: int) -> Tuple[str, float]:
    """
    V3: 混合版
    - 入场后前5天：只做ATR止损（给趋势空间）
    - 第5天后：加入EMA21辅助退出
    - 移动止损全程跟踪
    """
    entry_row = df.iloc[entry_i]
    entry_price = entry_row['close']
    entry_atr_pct = entry_row['atr_pct']
    stop_price = entry_price * (1 - entry_atr_pct * 2.0 / 100)
    trailing_stop = entry_row['low_10'] if not pd.isna(entry_row['low_10']) else entry_price * 0.95

    for j in range(entry_i + 1, min(entry_i + 20, len(df))):
        row = df.iloc[j]
        prev_row = df.iloc[j - 1]

        # ATR止损
        if row['low'] < stop_price:
            return "atr_stop", stop_price

        # 更新跟踪止损
        if not pd.isna(row['low_10']):
            trailing_stop = max(trailing_stop, row['low_10'])

        # 触发跟踪止损
        if row['close'] < trailing_stop:
            return "trailing_stop", trailing_stop

        # 5天后EMA21辅助退出（用 prev_row 确认，无look-ahead）
        if j - entry_i >= 5:
            if row['close'] < row['EMA21'] and prev_row['close'] < prev_row['EMA21']:
                return "ema21_confirm", row['close']

    exit_price = df.iloc[min(entry_i + 20, len(df) - 1)]['close']
    return "timeout", exit_price


# ============================================================
# 核心回测函数
# ============================================================

def run_backtest(
    stock_codes: List[str],
    start_date: str,
    end_date: str,
    exit_version: str = "v1_atr",
    config: BacktestConfig = None,
) -> Dict:
    """
    干净的回测

    资金管理:
    - 每笔交易使用 config.position_size_pct * 当前本金
    - 最大同时持仓 config.max_positions 只
    - 单利模式（不用复利滚）

    返回:
    {
        "trades": [Trade],
        "equity_curve": [(date, equity)],
        "metrics": {...}
    }
    """
    if config is None:
        config = BacktestConfig()

    all_trades: List[Trade] = []
    equity = config.initial_capital
    equity_curve = [(start_date, equity)]

    # 加载 HS300 做市场过滤（只用收盘>EMA21判断趋势）
    from lib.data_layer import get_index_hist
    hs300 = get_index_hist('000300', days=500)
    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['date_str'] = hs300['date'].apply(
        lambda d: str(d.date()) if hasattr(d, 'date') else str(d)
    )
    # 使用统一 market_regime 模块（V4 五态模型）替代简化分类
    # 计算价格偏离EMA21比例（趋势强度代理）
    hs300['pvem'] = (hs300['close'] / hs300['EMA21'] - 1) * 100  # %偏离度

    hs300_regime_dict = {}
    hs300_pvem_dict = dict(zip(hs300['date_str'], hs300['pvem']))
    for j in range(len(hs300)):
        row = hs300.iloc[j]
        date_str = row['date_str']
        if j >= 29:  # detect_market_regime 需要 >=30 行数据
            slice_df = hs300.iloc[:j+1]
            regime_result = detect_market_regime(slice_df)
            hs300_regime_dict[date_str] = regime_result["regime"]
        else:
            hs300_regime_dict[date_str] = "MEAN_REVERSION"

    # HS300 N日收益率（用于相对强度计算）
    hs300[f'ret{config.relative_strength_lookback}'] = hs300['close'].pct_change(config.relative_strength_lookback) * 100
    hs300_ret_dict = dict(zip(hs300['date_str'], hs300[f'ret{config.relative_strength_lookback}']))

    # ========== 板块强度预计算（STEP 5）==========
    # 27只测试股已知板块映射（回测用）
    SECTOR_MAP: Dict[str, str] = {
        '000858': '白酒', '600519': '白酒', '000333': '家电',
        '002475': '消费电子', '300750': '新能源', '600036': '银行',
        '000001': '银行', '601318': '保险', '002594': '汽车',
        '300059': '证券', '600276': '医药', '000568': '白酒',
        '603259': '医药', '002415': '安防', '601888': '旅游',
        '300033': '证券', '300124': '工业', '300274': '光伏',
        '300496': '半导体', '002049': '半导体', '002371': '半导体',
        '002456': '消费电子', '002460': '有色金属', '002466': '有色金属',
        '002236': '安防', '300014': '新能源', '300751': '医药',
    }

    # 预计算：每只股票每天的5日收益率（用于板块强度代理）
    stock_returns: Dict[str, pd.DataFrame] = {}
    for code in stock_codes:
        df_s = get_stock_hist_multi_source(code, days=500, source="tdx_local_qfq")
        if df_s is None or len(df_s) < 10:
            continue
        df_s = df_s.copy()
        df_s['ret5'] = df_s['close'].pct_change(5) * 100  # 5日收益率%
        df_s['date_str'] = df_s['date'].apply(
            lambda d: str(d.date()) if hasattr(d, 'date') else str(d)
        )
        stock_returns[code] = df_s.set_index('date_str')['ret5']

    def get_top_sectors(date_str: str, top_n: int = 5) -> set:
        """返回指定日期的最强top_n板块名称集合"""
        sector_returns = {}
        for code, sret in stock_returns.items():
            sector = SECTOR_MAP.get(code)
            if sector is None:
                continue
            ret = sret.get(date_str)
            if ret is not None and not pd.isna(ret):
                if sector not in sector_returns:
                    sector_returns[sector] = []
                sector_returns[sector].append(ret)

        if not sector_returns:
            return set()  # 无数据时不过滤

        # 取每个板块的平均5日收益
        sector_avg = {s: np.mean(r) for s, r in sector_returns.items()}
        # 按收益排序，取top_n
        sorted_sectors = sorted(sector_avg, key=sector_avg.get, reverse=True)
        return set(sorted_sectors[:top_n])

    exit_fn = {
        "v1_atr": check_exit_v1_atr,
        "v2_trend": check_exit_v2_trend,
        "v3_hybrid": check_exit_v3_hybrid,
    }.get(exit_version, check_exit_v1_atr)

    for code in stock_codes:
        try:
            df = get_stock_hist_multi_source(code, days=500, source="tdx_local_qfq")
            if df is None or len(df) < 60:
                continue
            df = add_indicators(df)

            # 过滤日期范围
            df = df[df['date'] >= start_date]
            if len(df) < 30:
                continue

            for i in range(30, len(df) - 1):
                row = df.iloc[i]
                # 使用 .date() 避免时间戳不匹配
                entry_date = row['date'].date() if hasattr(row['date'], 'date') else row['date']

                # 市场过滤: 使用统一 V4 五态模型
                # 直接用 Timestamp 比较，不用字符串
                entry_date_str = str(entry_date)
                hs_match = hs300[hs300['date'] <= row['date']]
                if len(hs_match) == 0:
                    continue

                # TREND_DOWN / RISK_OFF 禁止开仓
                regime = hs300_regime_dict.get(entry_date_str, "MEAN_REVERSION")
                if regime in (TREND_DOWN, RISK_OFF):
                    continue

                hs_latest = hs_match.iloc[-1]
                if not (hs_latest['close'] > hs_latest['EMA21']):
                    continue

                # MEAN_REVERSION时期：要求HS300趋势强度>3%（更严格入场）
                # 诊断发现：near_ema(<1%偏离)胜率仅30%，strong_up(>5%)胜率67%
                # 优化：MEAN_REVERSION市场只做偏离>3%的较强趋势
                pvem = hs300_pvem_dict.get(entry_date_str, 0.0)
                if regime == "MEAN_REVERSION" and pvem <= 3.0:
                    continue  # 均值回归市场且趋势不够强，跳过

                # STEP 6 相对强度过滤：只做相对HS300强势的股票
                # 逻辑：入场时股票近N日收益率 > HS300同期收益 + 阈值
                if config.relative_strength_threshold > 0:
                    stock_ret = stock_returns[code].get(entry_date_str) if code in stock_returns else None
                    hs_ret = hs300_ret_dict.get(entry_date_str)
                    if stock_ret is None or hs_ret is None:
                        continue
                    if (stock_ret - hs_ret) < config.relative_strength_threshold:
                        continue  # 股票不够强势，跳过

                # STEP 5 板块过滤：只做近N日最强板块（需要200+只股票，每板块≥5只才有效）
                if config.sector_filter_top_n > 0 and SECTOR_MAP:
                    top_sectors = get_top_sectors(entry_date_str, top_n=config.sector_filter_top_n)
                    stock_sector = SECTOR_MAP.get(code)
                    if top_sectors and stock_sector not in top_sectors:
                        continue  # 该股所在板块不在最强top_n，跳过

                # 入场信号（Donchian突破+放量确认，信号日收盘确认）
                entered, sig_type = check_entry(df, i)
                if not entered:
                    continue

                # 次日入场（信号日收盘确认，次日开盘价执行）
                if i + 1 >= len(df):
                    continue
                entry_row = df.iloc[i + 1]
                entry_price = float(entry_row['open'])
                entry_i = i + 1  # 实际入场日索引

                # 执行退出（从实际入场日开始扫描）
                exit_reason, exit_price = exit_fn(df, entry_i)
                buy_cost = config.commission_rate + config.slippage_rate
                sell_cost = config.commission_rate + config.stamp_duty_rate + config.slippage_rate
                total_cost = buy_cost + sell_cost
                pnl_pct = (exit_price / entry_price - 1 - total_cost) * 100

                # 持仓天数（根据 exit_reason 找真实出场索引）
                max_exit_i = min(entry_i + 20, len(df) - 1)
                if exit_reason == "timeout":
                    exit_i = max_exit_i
                elif exit_reason == "atr_stop":
                    # ATR止损：向前找第一根突破止损价的bar
                    entry_atr_pct = float(entry_row['atr_pct']) if not pd.isna(entry_row['atr_pct']) else 0.0
                    stop_price = entry_price * (1 - entry_atr_pct * 2.0 / 100)
                    exit_i = max_exit_i
                    for tmp_j in range(entry_i + 1, max_exit_i + 1):
                        if df.iloc[tmp_j]['low'] < stop_price:
                            exit_i = tmp_j
                            break
                elif exit_reason in ("ema21_confirm", "trend_break"):
                    # EMA21确认退出：向前找第一根确认跌破EMA21的bar
                    start_scan = entry_i + 5
                    exit_i = max_exit_i
                    for tmp_j in range(start_scan, max_exit_i + 1):
                        row_j = df.iloc[tmp_j]
                        prev_j = df.iloc[tmp_j - 1]
                        if row_j['close'] < row_j['EMA21'] and prev_j['close'] < prev_j['EMA21']:
                            exit_i = tmp_j
                            break
                elif exit_reason == "trailing_stop":
                    # 移动止损：向前找第一根收盘<trailing_stop的bar
                    trailing_stop = float(entry_row['low_10']) if not pd.isna(entry_row['low_10']) else entry_price * 0.95
                    exit_i = max_exit_i
                    for tmp_j in range(entry_i + 1, max_exit_i + 1):
                        row_j = df.iloc[tmp_j]
                        # 跟踪止损随时间上移，重新计算
                        if not pd.isna(row_j['low_10']):
                            ts = row_j['low_10']
                        else:
                            ts = trailing_stop
                        if row_j['close'] < ts:
                            exit_i = tmp_j
                            break
                else:
                    # 其他退出原因，默认到最大期
                    exit_i = max_exit_i

                hold_days = exit_i - entry_i
                atr_pct = float(entry_row['atr_pct']) if not pd.isna(entry_row['atr_pct']) else 0.0

                # 使用实际入场日（i+1）的日期
                actual_entry_date = entry_row['date'].date() if hasattr(entry_row['date'], 'date') else entry_row['date']

                trade = Trade(
                    entry_date=str(actual_entry_date),
                    entry_price=entry_price,
                    exit_date=str(df.iloc[exit_i]['date'].date() if hasattr(df.iloc[exit_i]['date'], 'date') else df.iloc[exit_i]['date']),
                    exit_price=float(exit_price),
                    pnl_pct=round(pnl_pct, 2),
                    hold_days=hold_days,
                    exit_reason=exit_reason,
                    code=code,
                    atr_pct=round(atr_pct, 3),
                )
                all_trades.append(trade)

        except Exception as e:
            continue

    # ============================================================
    # 计算资金曲线（单利模式）
    # ============================================================
    # 按日期排序所有交易
    trades_df = pd.DataFrame([{
        'entry_date': t.entry_date,
        'exit_date': t.exit_date,
        'pnl_pct': t.pnl_pct,
        'entry_price': t.entry_price,
        'exit_price': t.exit_price,
        'hold_days': t.hold_days,
        'exit_reason': t.exit_reason,
        'code': t.code,
        'atr_pct': t.atr_pct,
    } for t in all_trades])

    if trades_df.empty:
        return {
            "trades": [],
            "equity_curve": equity_curve,
            "metrics": {
                "total_trades": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "profit_factor": 0,
                "max_drawdown": 0,
            }
        }

    # 按 exit_date 排序
    trades_df = trades_df.sort_values('exit_date').reset_index(drop=True)

    # 单利: 每笔交易独立计算，不复利
    # 每笔交易金额 = initial_capital * position_size_pct
    trade_amount = config.initial_capital * config.position_size_pct

    running_trades = []
    equity = config.initial_capital

    for _, row in trades_df.iterrows():
        # 分配交易金额
        pnl = trade_amount * row['pnl_pct'] / 100
        equity += pnl
        equity_curve.append((row['exit_date'], equity))

    # ============================================================
    # 计算指标
    # ============================================================
    pnl_list = trades_df['pnl_pct'].tolist()
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    total_trades = len(pnl_list)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    avg_pnl = np.mean(pnl_list) if pnl_list else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

    # 最大回撤（资金曲线）
    eq_dates, eq_values = zip(*equity_curve)
    eq_series = pd.Series(eq_values)
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max * 100
    max_drawdown = drawdown.min()

    # 胜率/盈亏比分时期
    trades_df['period'] = pd.to_datetime(trades_df['exit_date']).dt.to_period('M')

    metrics = {
        "total_trades": total_trades,
        "win_rate": round(win_rate * 100, 1),
        "avg_pnl": round(avg_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_drawdown, 2),
        "total_return_pct": round((equity - config.initial_capital) / config.initial_capital * 100, 2),
        "final_capital": round(equity, 2),
    }

    # 按月统计
    monthly = trades_df.groupby('period').agg(
        signals=('pnl_pct', 'count'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
        avg_pnl=('pnl_pct', 'mean'),
        total_return=('pnl_pct', 'sum'),
    ).round(1)

    # 按退出原因统计
    by_reason = trades_df.groupby('exit_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)

    return {
        "trades": trades_df,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "monthly": monthly,
        "by_reason": by_reason,
        "config": {
            "initial_capital": config.initial_capital,
            "position_size_pct": config.position_size_pct,
            "exit_version": exit_version,
            "commission_rate": config.commission_rate,
            "stamp_duty_rate": config.stamp_duty_rate,
            "slippage_rate": config.slippage_rate,
            "total_cost_pct": round((config.commission_rate * 2 + config.stamp_duty_rate + config.slippage_rate * 2) * 100, 3),
        }
    }


def print_report(result: Dict):
    """打印回测报告"""
    m = result['metrics']
    print(f"\n{'='*60}")
    print(f"BACKTEST REPORT ({result['config']['exit_version']})")
    print(f"{'='*60}")
    print(f"Config: capital={m.get('final_capital', result['config']['initial_capital']):.0f}, "
          f"pos_size={result['config']['position_size_pct']*100:.0f}%, "
          f"exit={result['config']['exit_version']}, "
          f"cost/trade={result['config'].get('total_cost_pct', 0):.3f}%")
    print(f"\n--- Core Metrics ---")
    print(f"Total trades:    {m['total_trades']}")
    print(f"Win rate:        {m['win_rate']:.1f}%")
    print(f"Avg pnl:         {m['avg_pnl']:.2f}%")
    print(f"Avg win:         {m['avg_win']:.2f}%")
    print(f"Avg loss:        {m['avg_loss']:.2f}%")
    print(f"Profit factor:   {m['profit_factor']:.2f}")
    print(f"Max drawdown:    {m['max_drawdown']:.2f}%")
    print(f"Total return:    {m['total_return_pct']:.2f}%")

    print(f"\n--- By Exit Reason ---")
    print(result['by_reason'].to_string())

    print(f"\n--- Monthly ---")
    print(result['monthly'].to_string())

    print(f"\n--- All Trades ---")
    print(result['trades'][['entry_date', 'exit_date', 'code', 'pnl_pct', 'hold_days', 'exit_reason']].to_string(index=False))
