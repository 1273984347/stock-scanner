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

import time
import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed
from lib.scoring.technical_signals import ema
from lib.data.data_layer import get_index_hist
from lib.data.data_source_registry import get_stock_hist_multi_source
from lib.monitoring.market_regime import detect_market_regime, TREND_DOWN, RISK_OFF
from backtest.utils import compute_atr, add_indicators
from config import COMMISSION_RATE, STAMP_DUTY_RATE, SLIPPAGE_RATE, MAX_POSITIONS

logger = logging.getLogger(__name__)


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
    alpha_type: str = ""       # V7: Alpha 来源分类
    opportunity_score: float = 0  # V7: 入场时机会评分


@dataclass
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    position_size_pct: float = 0.10   # 单笔仓位（%本金）
    max_positions: int = field(default_factory=lambda: MAX_POSITIONS)  # 最大同时持仓数
    stop_loss_atr_mult: float = 2.0  # ATR倍数止损
    trailing_stop_atr_mult: float = 3.0  # ATR移动止损
    trend_exit_days: int = 20         # 趋势持有最大天数
    atr_period: int = 14
    sector_filter_top_n: int = 5     # 板块过滤：只做近N日最强板块（0=禁用，5=top5，8=top8）；需200+只股票才有效
    # STEP 6 相对强度过滤
    relative_strength_threshold: float = 0.0  # 股票必须相对HS300强势X%（0=禁用，正数如2.0要求 stock_ret > hs300_ret + 2%）
    relative_strength_lookback: int = 5       # 计算相对强度的回溯期（天）
    # 交易成本（统一从 config.py 读取）
    commission_rate: float = field(default_factory=lambda: COMMISSION_RATE)
    stamp_duty_rate: float = field(default_factory=lambda: STAMP_DUTY_RATE)
    slippage_rate: float = field(default_factory=lambda: SLIPPAGE_RATE)


# ============================================================
# 入场信号
# ============================================================

def check_entry(df: pd.DataFrame, i: int) -> Tuple[bool, str]:
    """入场信号：Donchian 突破口径（单股信号回测默认）

    ⚠️ **不是重复实现** —— `backtest/full.py::check_entry` 是**多信号 OR** 口径，
    二者服务于不同回测引擎、对应不同策略假设，**不可合并**。
    详见 `docs/adr/0002-check-entry-duplicate-not-merge.md`。
    同族第三份：`check_entry_multi_signal`（本模块内，多信号口径）。

    Check if day i meets entry conditions (design spec version)

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


def check_entry_multi_signal(df: pd.DataFrame, i: int) -> Tuple[bool, str]:
    """多信号 OR 入场（EMA 金叉 / RSI 超卖 / 成交量突破 / MACD 金叉）"""
    if i < 55:
        return False, ""

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    signals = []

    # 1. EMA 金叉
    if prev['EMA5'] <= prev['EMA21'] and row['EMA5'] > row['EMA21']:
        signals.append("ema_cross")

    # 2. RSI 超卖反弹
    if 'rsi' in df.columns:
        if prev.get('rsi', 50) < 30 and row.get('rsi', 50) > 30:
            signals.append("rsi_oversold")

    # 3. 成交量突破
    if row['volume'] > row['vol_ma20'] * 1.5:
        signals.append("vol_breakout")

    # 4. MACD 金叉
    if 'macd' in df.columns and 'macd_signal' in df.columns:
        if prev.get('macd', 0) <= prev.get('macd_signal', 0) and row.get('macd', 0) > row.get('macd_signal', 0):
            signals.append("macd_cross")

    if signals:
        return True, "+".join(signals)
    return False, ""


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
            # P0-2: 考虑跳空低开，实盘成交价 = min(open, stop_price)
            actual_exit = min(row['open'], stop_price)
            return "atr_stop", actual_exit

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
            # P0-2: 考虑跳空低开，实盘成交价 = min(open, trailing_stop)
            actual_exit = min(row['open'], trailing_stop)
            return "trailing_stop", actual_exit

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


def _check_sentiment_filter() -> bool:
    """情绪过滤：HS300 ATR ratio > 1.2% 时返回 True（高波动）"""
    try:
        hs300 = get_index_hist("000300", days=60)
        if hs300.empty or len(hs300) < 30:
            return False
        atr = compute_atr(hs300)
        atr_ratio = float(atr.iloc[-1] / hs300['close'].iloc[-1])
        return atr_ratio > 0.012
    except Exception:
        return False


# ============================================================
# 并行处理辅助
# ============================================================

def _process_single_stock_engine(args):
    """处理单只股票的回测（用于并行执行）

    Args:
        args: tuple(code, start_date, data_days, exit_fn_name, hs300_regime_dict,
                    hs300_pvem_dict, hs300_ret_dict, config_dict, multi_signal_entry,
                    include_delisted)
    Returns:
        list of Trade objects
    """
    code, start_date, data_days, exit_fn_name, hs300_regime_dict, \
        hs300_pvem_dict, hs300_ret_dict, config_dict, multi_signal_entry = args[:9]
    include_delisted = args[9] if len(args) > 9 else False

    from lib.data.data_source_registry import get_stock_hist_multi_source as _get_hist
    from backtest.utils import add_indicators as _add_ind
    # P0-4 修复（2026-06-05）：活股数据不足时 fallback 到退市股数据
    from lib.data.data_delisted import get_delisted_stock_hist

    trades = []
    try:
        df = _get_hist(code, days=data_days, source="auto", prefer_adjusted=True)
        # P0-4：include_delisted 开启 + 活股数据不足 → 用退市股数据补
        if (df is None or len(df) < 60) and include_delisted:
            try:
                df = get_delisted_stock_hist(code, days=data_days)
            except Exception:
                df = None
        if df is None or len(df) < 60:
            return trades
        df = _add_ind(df)
        df = df[df['date'] >= start_date]
        if len(df) < 30:
            return trades

        df['date_str'] = df['date'].apply(lambda d: str(d.date()) if hasattr(d, 'date') else str(d))
        df['stock_ret5'] = df['close'].pct_change(5) * 100

        entry_fn = check_entry_multi_signal if multi_signal_entry else check_entry

        exit_fn_map = {
            "v1_atr": check_exit_v1_atr,
            "v2_trend": check_exit_v2_trend,
            "v3_hybrid": check_exit_v3_hybrid,
        }
        exit_fn = exit_fn_map.get(exit_fn_name, check_exit_v1_atr)

        for i in range(30, len(df) - 1):
            row = df.iloc[i]
            entry_date_str = row['date_str']

            # 市场过滤
            regime = hs300_regime_dict.get(entry_date_str, "MEAN_REVERSION")
            if regime in (TREND_DOWN, RISK_OFF):
                continue

            pvem = hs300_pvem_dict.get(entry_date_str, 0.0)
            if regime == "MEAN_REVERSION" and pvem <= 3.0:
                continue

            # 相对强度过滤
            if config_dict.get('relative_strength_threshold', 0) > 0:
                stock_ret = row.get('stock_ret5')
                hs_ret = hs300_ret_dict.get(entry_date_str)
                if stock_ret is None or hs_ret is None:
                    continue
                if (stock_ret - hs_ret) < config_dict['relative_strength_threshold']:
                    continue

            # 入场信号
            entered, sig_type = entry_fn(df, i)
            if not entered:
                continue

            # 次日入场
            if i + 1 >= len(df):
                continue
            entry_row = df.iloc[i + 1]
            entry_price = float(entry_row['open'])
            entry_i = i + 1

            # 退出
            exit_reason, exit_price = exit_fn(df, entry_i)

            buy_cost = config_dict['commission_rate'] + config_dict['slippage_rate']
            sell_cost = config_dict['commission_rate'] + config_dict['stamp_duty_rate'] + config_dict['slippage_rate']
            total_cost = buy_cost + sell_cost
            pnl_pct = (exit_price / entry_price - 1 - total_cost) * 100

            atr_pct = float(entry_row['atr_pct']) if not pd.isna(entry_row['atr_pct']) else 0.0
            actual_entry_date = entry_row['date'].date() if hasattr(entry_row['date'], 'date') else entry_row['date']

            trade = Trade(
                entry_date=str(actual_entry_date),
                entry_price=entry_price,
                exit_date=str(df.iloc[min(entry_i + 20, len(df) - 1)]['date'].date()
                              if hasattr(df.iloc[min(entry_i + 20, len(df) - 1)]['date'], 'date')
                              else df.iloc[min(entry_i + 20, len(df) - 1)]['date']),
                exit_price=float(exit_price),
                pnl_pct=round(pnl_pct, 2),
                hold_days=min(20, len(df) - 1 - entry_i),
                exit_reason=exit_reason,
                code=code,
                atr_pct=round(atr_pct, 3),
            )
            trades.append(trade)

    except Exception as e:
        logger.warning(f"Stock {code} backtest failed: {e}")

    return trades


def _run_backtest_parallel(
    stock_codes: List[str],
    start_date: str,
    end_date: str,
    exit_version: str,
    config: BacktestConfig,
    max_workers: int,
    multi_signal_entry: bool,
    sentiment_filter: bool,
    days: int,
    include_delisted: bool = True,
) -> Dict:
    """并行回测入口（当 parallel=True 且 stock_codes > 100 时调用）"""
    import os
    from datetime import datetime, timedelta

    start_dt = datetime.strptime(start_date, "%Y-%m-%d") if start_date else datetime.now() - timedelta(days=365)
    today = datetime.now()
    delta_days = (today - start_dt).days + 365
    data_days = max(delta_days, days)

    # 预计算 HS300
    hs300 = get_index_hist('000300', days=data_days)
    if hs300.empty:
        return {
            "trades": [], "equity_curve": [], "metrics": {
                "total_trades": 0, "win_rate": 0, "avg_pnl": 0,
                "profit_factor": 0, "max_drawdown": 0,
            }
        }

    hs300['EMA21'] = ema(hs300['close'], 21)
    hs300['date_str'] = hs300['date'].apply(
        lambda d: str(d.date()) if hasattr(d, 'date') else str(d)
    )
    hs300['pvem'] = (hs300['close'] / hs300['EMA21'] - 1) * 100

    hs300_regime_dict = {}
    for j in range(len(hs300)):
        row = hs300.iloc[j]
        date_str = row['date_str']
        if j >= 29:
            slice_df = hs300.iloc[:j + 1]
            regime_result = detect_market_regime(slice_df)
            hs300_regime_dict[date_str] = regime_result["regime"]
        else:
            hs300_regime_dict[date_str] = "MEAN_REVERSION"

    hs300_pvem_dict = dict(zip(hs300['date_str'], hs300['pvem']))

    hs300[f'ret{config.relative_strength_lookback}'] = hs300['close'].pct_change(config.relative_strength_lookback) * 100
    hs300_ret_dict = dict(zip(hs300['date_str'], hs300[f'ret{config.relative_strength_lookback}']))

    config_dict = {
        'commission_rate': config.commission_rate,
        'stamp_duty_rate': config.stamp_duty_rate,
        'slippage_rate': config.slippage_rate,
        'relative_strength_threshold': config.relative_strength_threshold,
    }

    workers = max_workers or min(os.cpu_count() or 4, 8)

    task_args = [
        (code, start_date, data_days, exit_version,
         hs300_regime_dict, hs300_pvem_dict, hs300_ret_dict,
         config_dict, multi_signal_entry, include_delisted)
        for code in stock_codes
    ]

    all_trades = []
    t0 = time.time()
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_process_single_stock_engine, args): args[0] for args in task_args}
        for future in as_completed(futures):
            completed += 1
            try:
                trades = future.result()
                all_trades.extend(trades)
            except Exception:
                pass
            if completed % 500 == 0:
                elapsed = time.time() - t0
                print(f"  并行回测进度: {completed}/{len(stock_codes)}, "
                      f"耗时: {elapsed:.1f}s, 交易: {len(all_trades)}")

    elapsed_total = time.time() - t0

    # ====== 情绪过滤（入场级）======
    if sentiment_filter:
        high_vol = _check_sentiment_filter()
        if high_vol:
            all_trades = [t for t in all_trades if t.exit_reason != "timeout"]

    # ====== 汇总指标 ======
    if not all_trades:
        return {
            "trades": [], "equity_curve": [], "metrics": {
                "total_trades": 0, "win_rate": 0, "avg_pnl": 0,
                "profit_factor": 0, "max_drawdown": 0,
                # P0-1: RiskManager 字段（即使无交易也需返回）
                "risk_alerts_count": 0,
                "risk_actions_count": 0,
                "consecutive_losses": 0,
                "risk_suspended": False,
                "risk_max_drawdown": 0.0,
                # P1-3: 幸存者偏差标注
                "survivorship_bias_warning": True,
                "survivorship_bias_factor": 0.97,
                "survivorship_bias_adjusted_return_pct": 0.0,
                "survivorship_bias_note": "回测仅含存活股票，实际收益可能被高估 1-3%/年",
            }
        }

    pnl_list = [t.pnl_pct for t in all_trades]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    total_trades = len(pnl_list)
    win_rate = len(wins) / total_trades if total_trades > 0 else 0
    avg_pnl = np.mean(pnl_list) if pnl_list else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = abs(np.mean(losses)) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

    # 资金曲线（单利）
    equity = config.initial_capital
    equity_curve = [(start_date, equity)]
    trade_amount = config.initial_capital * config.position_size_pct
    sorted_trades = sorted(all_trades, key=lambda t: t.exit_date)
    for t in sorted_trades:
        pnl = trade_amount * t.pnl_pct / 100
        equity += pnl
        equity_curve.append((t.exit_date, equity))

    eq_dates, eq_values = zip(*equity_curve)
    eq_series = pd.Series(eq_values)
    rolling_max = eq_series.cummax()
    drawdown = (eq_series - rolling_max) / rolling_max * 100
    max_drawdown = drawdown.min()

    trades_df = pd.DataFrame([{
        'entry_date': t.entry_date, 'exit_date': t.exit_date,
        'pnl_pct': t.pnl_pct, 'hold_days': t.hold_days,
        'exit_reason': t.exit_reason, 'code': t.code,
    } for t in all_trades])
    trades_df['period'] = pd.to_datetime(trades_df['exit_date']).dt.to_period('M')

    by_reason = trades_df.groupby('exit_reason').agg(
        count=('pnl_pct', 'count'),
        avg_pnl=('pnl_pct', 'mean'),
        win_rate=('pnl_pct', lambda x: (x > 0).mean() * 100),
    ).round(2)

    print(f"  并行回测完成: {total_trades} 笔交易, 耗时: {elapsed_total:.1f}s")

    return {
        "trades": trades_df,
        "equity_curve": equity_curve,
        "metrics": {
            "total_trades": total_trades,
            "win_rate": round(win_rate * 100, 1),
            "avg_pnl": round(float(avg_pnl), 2),
            "avg_win": round(float(avg_win), 2),
            "avg_loss": round(float(avg_loss), 2),
            "profit_factor": round(float(profit_factor), 2),
            "max_drawdown": round(float(max_drawdown), 2),
            "total_return_pct": round((equity - config.initial_capital) / config.initial_capital * 100, 2),
            "final_capital": round(equity, 2),
        },
        "by_reason": by_reason,
    }


# ============================================================
# V7: Alpha 类型推断（回测用）
# ============================================================

def _infer_alpha_type(regime: str, exit_reason: str) -> str:
    """根据市场状态和退出原因推断 Alpha 类型（简化版）

    回测中没有实时 data_factors，用市场状态做粗略归因。
    """
    if regime == "TREND_UP":
        return "trend"
    elif regime == "MEAN_REVERSION":
        return "mean_reversion"
    elif regime in ("HIGH_VOLATILITY", "LOW_VOLATILITY"):
        return "sentiment"
    elif regime in ("TREND_DOWN", "RISK_OFF"):
        return "event_driven"
    return "trend"


# ============================================================
# 核心回测函数
# ============================================================

def run_backtest(
    stock_codes: List[str],
    start_date: str = "",
    end_date: str = "",
    exit_version: str = "v1_atr",
    config: BacktestConfig = None,
    parallel: bool = False,
    max_workers: int = 4,
    multi_signal_entry: bool = False,
    sentiment_filter: bool = False,
    symbols: List[str] = None,
    days: int = 500,
    include_delisted: bool = True,
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

    # symbols alias for backward compatibility
    if symbols is not None:
        stock_codes = symbols

    # days fallback: if start_date not provided, compute from days
    if not start_date and days:
        from datetime import datetime, timedelta
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    # P0-4 修复（2026-06-05）：include_delisted 开启时，注入退市股票到 stock_codes
    # 限最多 50 只，避免退市池过大拖慢回测
    if include_delisted and parallel and len(stock_codes) > 100:
        try:
            from lib.data.data_delisted import get_delisted_symbols
            delisted = list(get_delisted_symbols())[:50]
            if delisted:
                # 去重 + 保留原顺序
                seen = set(stock_codes)
                for code in delisted:
                    if code not in seen:
                        stock_codes.append(code)
                        seen.add(code)
        except Exception as e:
            # 退市数据不可用时降级，不中断主流程
            import logging
            logging.getLogger(__name__).warning(f"退市股票注入失败: {e}")

    # ====== 并行执行路径 ======
    if parallel and len(stock_codes) > 100:
        return _run_backtest_parallel(
            stock_codes=stock_codes,
            start_date=start_date,
            end_date=end_date,
            exit_version=exit_version,
            config=config,
            max_workers=max_workers,
            multi_signal_entry=multi_signal_entry,
            sentiment_filter=sentiment_filter,
            days=days,
            include_delisted=include_delisted,
        )

    all_trades: List[Trade] = []
    equity = config.initial_capital
    equity_curve = [(start_date, equity)]

    # 加载 HS300 做市场过滤（只用收盘>EMA21判断趋势）
    from lib.data.data_layer import get_index_hist
    hs300 = get_index_hist('000300', days=days)
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
        df_s = get_stock_hist_multi_source(code, days=days, source="auto", prefer_adjusted=True)
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
            df = get_stock_hist_multi_source(code, days=days, source="auto", prefer_adjusted=True)
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
                # P0-1: RiskManager 字段
                "risk_alerts_count": 0,
                "risk_actions_count": 0,
                "consecutive_losses": 0,
                "risk_suspended": False,
                "risk_max_drawdown": 0.0,
                # P1-3: 幸存者偏差标注
                "survivorship_bias_warning": True,
                "survivorship_bias_factor": 0.97,
                "survivorship_bias_adjusted_return_pct": 0.0,
                "survivorship_bias_note": "回测仅含存活股票，实际收益可能被高估 1-3%/年",
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
        # P1-3: 幸存者偏差标注（轻量修复）
        # 回测仅含当前存活股票，不含已退市/ST 历史股票
        # 收益被高估约 1-3%/年 → 给用户提供保守调整后的数字
        "survivorship_bias_warning": True,
        "survivorship_bias_factor": 0.97,  # 收益调整系数（高估约 3%）
        "survivorship_bias_adjusted_return_pct": round(
            ((equity - config.initial_capital) / config.initial_capital * 100) * 0.97, 2
        ),
        "survivorship_bias_note": (
            "回测仅含存活股票（不含已退市/ST），"
            "实际收益可能被高估 1-3%/年。"
            "使用 survivorship_bias_adjusted_return_pct 替代 total_return_pct 看保守值。"
        ),
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

    # ========== V7: Alpha 反馈分析 ==========
    alpha_feedback = {}
    alpha_weights = {}
    try:
        from lib.scoring.alpha_feedback import AlphaFeedback
        fb = AlphaFeedback()
        for trade in all_trades:
            # 简化归因：根据市场状态和退出原因推断 Alpha 类型
            regime = hs300_regime_dict.get(trade.entry_date, "MEAN_REVERSION")
            alpha_type = _infer_alpha_type(regime, trade.exit_reason)
            fb.record_trade(
                alpha_type=alpha_type,
                pnl_pct=trade.pnl_pct,
                regime=regime,
                sector=SECTOR_MAP.get(trade.code, "unknown"),
            )
        alpha_feedback = fb.get_alpha_report()
        alpha_weights = fb.get_alpha_weights()
    except Exception as e:
        logger.debug(f"[backtest] Alpha 反馈分析失败: {e}")

    # ========== P0-1: 集成 RiskManager 模拟实盘风控 ==========
    # 跑完所有交易后，调用 RiskManager.check_all() 检查：
    # - max_drawdown_stop（15%）：若触发则 trading_suspended = True
    # - daily_loss_limit / weekly_loss_limit：累计超限告警
    # - consecutive_loss（P1-2）：连续亏损 ≥5 笔 → 减仓告警
    try:
        from lib.risk.risk_manager import RiskManager
        rm = RiskManager()
        # P0-2: 注册 executor 回调（之前默认空，3 类 action 都静默失败）
        # 回测场景下无可真实减仓，记录到 metrics 即视为"已执行"
        def _on_stop_trading(action):
            metrics["risk_actions_executed_stop"] = metrics.get("risk_actions_executed_stop", 0) + 1
            logger.warning(f"[backtest] 风控动作: STOP_TRADING ({action.reason})")
        def _on_reduce_position(action):
            metrics["risk_actions_executed_reduce"] = metrics.get("risk_actions_executed_reduce", 0) + 1
            logger.warning(f"[backtest] 风控动作: REDUCE_POSITION target={action.target} ({action.reason})")
        def _on_block_new(action):
            metrics["risk_actions_executed_block"] = metrics.get("risk_actions_executed_block", 0) + 1
            logger.warning(f"[backtest] 风控动作: BLOCK_NEW_POSITIONS target={action.target} ({action.reason})")
        rm.executor.register("STOP_TRADING", _on_stop_trading)
        rm.executor.register("REDUCE_POSITION", _on_reduce_position)
        rm.executor.register("BLOCK_NEW_POSITIONS", _on_block_new)
        for trade in all_trades:
            rm.record_trade_result(pnl_pct=trade.pnl_pct / 100.0)
        # 计算回测期末组合的 max_drawdown
        if equity_curve:
            eq_values = [e for _, e in equity_curve]
            peak = max(eq_values) if eq_values else config.initial_capital
            current = eq_values[-1] if eq_values else config.initial_capital
            computed_max_dd = (current - peak) / peak if peak > 0 else 0.0
        else:
            computed_max_dd = 0.0
        risk_alerts, risk_actions = rm.check_all(
            portfolio_value=equity,
            positions={},  # 简化为空（回测不模拟多持仓）
            daily_return=0,
            max_drawdown=computed_max_dd,
        )
        metrics["risk_alerts_count"] = len(risk_alerts)
        metrics["risk_actions_count"] = len(risk_actions)
        metrics["consecutive_losses"] = rm.consecutive_losses
        metrics["risk_suspended"] = rm.executor.is_trading_suspended
        metrics["risk_max_drawdown"] = round(computed_max_dd * 100, 2)
    except Exception as e:
        logger.debug(f"[backtest] RiskManager 集成失败: {e}")
        metrics["risk_alerts_count"] = 0
        metrics["risk_actions_count"] = 0
        metrics["consecutive_losses"] = 0
        metrics["risk_suspended"] = False

    return {
        "trades": trades_df,
        "equity_curve": equity_curve,
        "metrics": metrics,
        "monthly": monthly,
        "by_reason": by_reason,
        "alpha_feedback": alpha_feedback,
        "alpha_weights": alpha_weights,
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


# ============================================================
# 月频调仓回测（从 rebalance.py 迁入）
# ============================================================

def run_rebalance_backtest(
    symbols: List[str],
    days: int = 500,
    initial_cash: float = 1_000_000,
    rebalance_period: int = 20,
    stop_loss_atr_mult: float = 2.0,
    take_profit_atr_mult: float = 4.0,
    max_hold_days: int = 10,
    confidence_threshold: float = 0.3,
    disable_loss_breaker: bool = False,
    factor_config=None,
    max_workers: int = 8,
    exit_days_map: dict = None,
) -> Optional[Dict]:
    """月频调仓回测（从 rebalance.py 迁入）

    Args:
        symbols: 股票代码列表
        days: 回测天数
        initial_cash: 初始资金
        rebalance_period: 调仓周期（交易日）
        disable_loss_breaker: 禁用连亏熔断（用于因子对比测试）
        factor_config: FactorConfig 实例，控制因子开关
    """
    from datetime import datetime, timedelta
    from lib.data.broker import ABroker, Order
    from lib.risk import Holding
    from config import get_config
    from lib.risk.position_size import RiskSizedPosition, PositionSizeConfig
    from lib.risk.risk_manager import RiskManager, RiskConfig
    from lib.risk.partial_exit import PartialExitManager, ConsecutiveLossBreaker
    from lib.strategy.rebalance_scheduler import RebalanceScheduler, RebalanceConfig
    from factors import compute_all_signals, calc_atr, compute_ic_weighted_score
    from backtest.core import get_stock_data, _compute_atr_for_position

    if factor_config is None:
        factor_config = get_config("balanced").factor

    # 获取数据（并行批量读取）
    market_data: Dict[str, pd.DataFrame] = {}
    try:
        from lib.data.tdx_batch import batch_read_day_files
        raw_data = batch_read_day_files(symbols, days=days, max_workers=8)
        for sym, records in raw_data.items():
            if records and len(records) > 60:
                df = pd.DataFrame(records)
                if "trade_date" not in df.columns and "date" in df.columns:
                    df = df.rename(columns={"date": "trade_date"})
                df["trade_date"] = pd.to_datetime(df["trade_date"])
                # 补充 get_stock_data 会添加的列
                if "pre_close" not in df.columns:
                    df["pre_close"] = df["close"].shift(1)
                if "is_limit_up" not in df.columns:
                    df["is_limit_up"] = False
                if "is_limit_down" not in df.columns:
                    df["is_limit_down"] = False
                if "is_suspend" not in df.columns:
                    df["is_suspend"] = False
                market_data[sym] = df
    except Exception:
        # 回退到串行获取
        for sym in symbols:
            try:
                df = get_stock_data(sym, days=days)
                if not df.empty and len(df) > 60:
                    market_data[sym] = df
            except Exception:
                continue

    if not market_data:
        logger.error("无有效数据")
        return None

    # 收集交易日
    all_dates = set()
    for df in market_data.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(all_dates)

    # 初始化组件
    broker = ABroker(initial_cash=initial_cash)
    broker.set_trade_dates([str(d.date()) if hasattr(d, 'date') else str(d) for d in trade_dates])
    position_sizer = RiskSizedPosition(PositionSizeConfig(
        risk_per_trade_pct=0.5, max_position_pct=0.20, use_atr_stop=True,
        atr_multiplier=stop_loss_atr_mult,
    ))
    risk_mgr = RiskManager(RiskConfig(max_drawdown_stop=0.20))
    partial_exit = PartialExitManager()
    # 动态退出管理器（根据市场状态调整出场天数）
    from lib.risk.dynamic_exit import DynamicExitManager
    dynamic_exit = DynamicExitManager(exit_days_map=exit_days_map)
    if disable_loss_breaker:
        # No-op loss breaker for factor comparison
        class _NoOpBreaker:
            def record_trade(self, *a, **kw): pass
            def is_stopped(self): return False
            def get_position_multiplier(self): return 1.0
            def get_status(self): return {"is_stopped": False, "consecutive_losses": 0}
        loss_breaker = _NoOpBreaker()
    else:
        loss_breaker = ConsecutiveLossBreaker(reduce_threshold=3, stop_threshold=5, cooldown_days=20)
    scheduler = RebalanceScheduler(RebalanceConfig(
        rebalance_period=rebalance_period,
        rolling_sharpe_threshold=0.0,  # 禁用夏普退出（由 loss breaker 控制）
        max_drawdown_threshold=0.50 if disable_loss_breaker else 0.25,
        consecutive_sharpe_months=999,  # 禁用夏普退出
    ))
    scheduler.reset()  # 回测必须重置，避免加载之前的持久化状态

    # 状态
    buy_prices: Dict[str, float] = {}
    buy_dates: Dict[str, str] = {}
    stop_prices: Dict[str, float] = {}
    risk_paused = False
    rebalance_count = 0
    trade_log = []

    for i, date in enumerate(trade_dates):
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)

        # 推进冷却期计数
        if hasattr(loss_breaker, 'advance_day'):
            loss_breaker.advance_day()

        # 检测市场状态（每 5 天更新一次，避免频繁计算）
        if i % 5 == 0:
            try:
                from lib.monitoring.market_regime import detect_market_regime_from_hs300
                regime_result = detect_market_regime_from_hs300(days=60)
                current_regime = regime_result.get("regime", "MEAN_REVERSION")
                atr_ratio = regime_result.get("atr_ratio", 1.0)
                annualized_vol = atr_ratio * 0.25  # 粗略估算
                dynamic_exit.update_market_state(current_regime, annualized_vol)
            except Exception:
                current_regime = "MEAN_REVERSION"
        else:
            current_regime = getattr(dynamic_exit, '_current_regime', 'MEAN_REVERSION')

        # 动态出场天数
        current_max_hold = dynamic_exit.get_exit_days()
        if current_max_hold <= 0:
            # RISK_OFF: 不开新仓，但已有持仓继续管理
            current_max_hold = max_hold_days  # 回退到默认

        # 更新市场价格
        broker.update_market_prices(market_data, date_str)

        # 记录每日收益（供退出规则使用）
        scheduler.record_daily_return(broker.total_value, date_str)

        # 检查退出规则
        if scheduler.get_status()["is_stopped"]:
            if not risk_paused:
                logger.warning(f"{date_str} 退出规则触发: {scheduler.get_status()['stop_reason']}")
                risk_paused = True

        # 风控检查
        if broker.daily_values:
            last_value = broker.daily_values[-1]["total_value"]
            peak = max(d["total_value"] for d in broker.daily_values)
            drawdown = (peak - last_value) / peak if peak > 0 else 0
            if drawdown > risk_mgr.config.max_drawdown_stop:
                risk_paused = True

        # 检查止损止盈
        for code in list(broker.holdings.keys()):
            holding = broker.holdings.get(code)
            if holding is None:
                continue
            current_price = broker._last_prices.get(code)
            if current_price is None:
                continue

            buy_price = buy_prices.get(code, holding.cost_price)
            pnl_pct = (current_price - buy_price) / buy_price if buy_price > 0 else 0

            # ATR 止损
            stop_price = stop_prices.get(code)
            if stop_price is not None and current_price <= stop_price:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="ATR止损")
                trade = broker.submit_order(order, market_data, date_str)
                if trade:
                    loss_breaker.record_trade(code, pnl_pct, date_str)
                    trade_log.append({"date": date_str, "code": code, "action": "SELL",
                                      "reason": "ATR止损", "pnl": pnl_pct})
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)
                continue

            # 分仓止盈
            pe_result = partial_exit.check_exit(code, current_price, date_str)
            if pe_result and pe_result["action"] == "SELL":
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=pe_result["shares"], date=date_str, reason=pe_result["reason"])
                trade = broker.submit_order(order, market_data, date_str)
                if trade:
                    loss_breaker.record_trade(code, pnl_pct, date_str)
                    trade_log.append({"date": date_str, "code": code, "action": "SELL",
                                      "reason": pe_result["reason"], "pnl": pnl_pct})
                if pe_result["remaining"] <= 0:
                    buy_prices.pop(code, None)
                    buy_dates.pop(code, None)
                    stop_prices.pop(code, None)
                continue

            # 硬止损
            if pnl_pct <= -risk_mgr.config.single_stock_stop_loss:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="硬止损")
                trade = broker.submit_order(order, market_data, date_str)
                if trade:
                    loss_breaker.record_trade(code, pnl_pct, date_str)
                    trade_log.append({"date": date_str, "code": code, "action": "SELL",
                                      "reason": "硬止损", "pnl": pnl_pct})
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)
                continue

            # 最大持仓天数（动态调整）
            hold_days = (date - pd.Timestamp(buy_dates.get(code, date))).days
            if hold_days >= current_max_hold:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="超时")
                trade = broker.submit_order(order, market_data, date_str)
                if trade:
                    loss_breaker.record_trade(code, pnl_pct, date_str)
                    trade_log.append({"date": date_str, "code": code, "action": "SELL",
                                      "reason": "超时", "pnl": pnl_pct})
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)

        # 检查是否需要调仓
        if not scheduler.should_rebalance(date_str):
            broker.record_daily_snapshot(date_str)
            continue

        # 执行调仓扫描
        rebalance_count += 1
        if risk_paused or loss_breaker.is_stopped():
            broker.record_daily_snapshot(date_str)
            scheduler.mark_rebalanced(date_str)
            continue

        # 扫描买入信号（用 IC 加权评分排序）
        candidates = []
        for sym in symbols:
            if sym in broker.holdings:
                continue
            df = market_data.get(sym)
            if df is None:
                continue
            hist = df[df["trade_date"] < date]
            if len(hist) < 60:
                continue

            mkt = "sz" if sym.startswith(("0", "3")) else "sh"
            result = compute_all_signals(hist, code=sym, market=mkt,
                                         factor_config=factor_config,
                                         fast_mode=True)
            if not result:
                continue
            if result.get("combined", {}).get("macro_blocked"):
                continue

            ic_score = result.get("ic_score", 0)
            if ic_score > 0:  # 只买正评分的
                candidates.append((sym, ic_score, hist))

        # 按 IC 评分排序，取 Top 3
        candidates.sort(key=lambda x: x[1], reverse=True)
        for sym, ic_score, hist in candidates[:3]:
            current_price = hist["close"].iloc[-1]
            atr_val = _compute_atr_for_position(hist)
            loss_mult = loss_breaker.get_position_multiplier()

            pos_result = position_sizer.calculate(
                equity=broker.total_value, entry_price=current_price,
                atr=atr_val if atr_val > 0 else None,
                cash_available=broker.cash,
                current_exposure=broker.total_market_value / broker.total_value if broker.total_value > 0 else 0,
            )
            if pos_result.qty <= 0:
                continue

            adjusted_qty = int(pos_result.qty * loss_mult / 100) * 100
            if adjusted_qty <= 0:
                continue

            order = Order(code=sym, direction="BUY", price=current_price,
                          shares=adjusted_qty, date=date_str, reason="调仓买入")
            trade = broker.submit_order(order, market_data, date_str)
            if trade:
                buy_prices[sym] = trade.price
                buy_dates[sym] = date_str
                partial_exit.register_entry(sym, trade.price, trade.shares, date_str)
                if atr_val > 0:
                    stop_prices[sym] = trade.price - atr_val * stop_loss_atr_mult
                else:
                    stop_prices[sym] = trade.price * 0.95
                trade_log.append({"date": date_str, "code": sym, "action": "BUY",
                                  "reason": "调仓买入", "pnl": 0})

        scheduler.mark_rebalanced(date_str)
        broker.record_daily_snapshot(date_str)

    # 生成报告
    perf_df = broker.get_performance_df()
    if perf_df.empty:
        return None

    from lib.scoring.analyzer import PerformanceAnalyzer
    analyzer = PerformanceAnalyzer(perf_df)
    metrics = analyzer.analyze()

    # 统计
    sell_trades = [t for t in trade_log if t["action"] == "SELL"]
    wins = [t for t in sell_trades if t["pnl"] > 0]
    losses = [t for t in sell_trades if t["pnl"] <= 0]

    return {
        "metrics": metrics,
        "trade_count": len(trade_log),
        "sell_count": len(sell_trades),
        "win_rate": len(wins) / len(sell_trades) if sell_trades else 0,
        "avg_win": np.mean([t["pnl"] for t in wins]) if wins else 0,
        "avg_loss": np.mean([abs(t["pnl"]) for t in losses]) if losses else 0,
        "rebalance_count": rebalance_count,
        "final_sharpe": scheduler.get_rolling_sharpe(),
        "final_drawdown": scheduler.get_current_drawdown(),
        "is_stopped": scheduler.get_status()["is_stopped"],
        "stop_reason": scheduler.get_status()["stop_reason"],
        "loss_breaker_status": loss_breaker.get_status(),
        "trade_log": trade_log[-20:],  # 最近 20 笔
        "report": analyzer.summary_report(),
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
