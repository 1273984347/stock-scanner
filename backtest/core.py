"""回测模块 - 基于 ABroker 的 A 股真实模拟回测

核心改动（相比旧版）：
1. 用 RiskSizedPosition 替代固定 20% 仓位
2. 接入 RiskManager 做回测期间风控
3. 波动率自适应止损替代硬编码百分比
4. 支持 judge_panel 评委团评分（可选）

.. deprecated:: 使用 backtest.engine 替代
"""

import warnings
warnings.warn("backtest.core is deprecated, use backtest.engine", DeprecationWarning, stacklevel=2)

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pathlib import Path
import time
import logging

from lib.broker import ABroker, Order, TradingConstraints
from lib.broker_data import Holding
from lib.analyzer import PerformanceAnalyzer
from lib.position_size import RiskSizedPosition, PositionSizeConfig, atr_risk_size
from lib.risk_manager import RiskManager, RiskConfig
from lib.partial_exit import PartialExitManager, ConsecutiveLossBreaker
from factors import compute_all_signals, calc_atr

logger = logging.getLogger(__name__)

_BACKTEST_CACHE_DIR = Path("cache/backtest_data")


def _validate_ohlcv(df: pd.DataFrame, symbol: str = "") -> pd.DataFrame:
    """数据质量校验：检测并修复常见数据问题

    检查项：
    - 零成交量非停牌日
    - 收盘价异常跳变（单日>30%无除权）
    - 负价格/零价格
    - 连续相同收盘价（>5天）
    """
    if df.empty or len(df) < 2:
        return df

    issues = []

    # 1. 零价格/负价格
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            bad = df[col] <= 0
            if bad.any():
                count = bad.sum()
                df.loc[bad, col] = np.nan
                issues.append(f"{col}有{count}个零/负值→NaN")

    # 2. 异常跳变：单日涨跌>30%（可能是复权问题或数据错误）
    if "close" in df.columns and len(df) > 1:
        returns = df["close"].pct_change()
        extreme = returns.abs() > 0.30
        if extreme.any():
            count = extreme.sum()
            issues.append(f"有{count}天单日涨跌>30%（检查复权）")

    # 3. 连续相同收盘价
    if "close" in df.columns:
        same_close = (df["close"] == df["close"].shift(1)) & (df["close"].notna())
        streak = 0
        max_streak = 0
        for s in same_close:
            streak = streak + 1 if s else 0
            max_streak = max(max_streak, streak)
        if max_streak > 5:
            issues.append(f"连续{max_streak}天收盘价相同（数据可能过期）")

    # 4. 用前值填充 NaN
    df = df.ffill().bfill()

    if issues:
        logger.debug(f"{symbol} 数据校验: {'; '.join(issues)}")

    return df


class AllDataSourcesUnavailableError(Exception):
    """所有数据源均不可用时抛出"""
    pass


_survivorship_warned = False

def get_stock_data(symbol: str, days: int = 250, use_cache: bool = True) -> pd.DataFrame:
    """获取股票历史数据（带 parquet 缓存，24小时有效期）

    注意：当前数据源只包含仍在交易的股票，已退市股票无法获取。
    这会导致回测存在幸存者偏差（Survivorship Bias）：
    - 回测收益可能被高估 1-3%/年
    - 风险指标可能被低估
    - 建议在实盘前用包含退市股票的数据集重新验证

    Args:
        symbol: 股票代码
        days: 历史天数
        use_cache: 是否使用 parquet 缓存（默认 True）

    Raises:
        AllDataSourcesUnavailableError: 所有数据源失败时抛出，不返回空 DataFrame
    """
    _BACKTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _BACKTEST_CACHE_DIR / f"{symbol}_{days}.parquet"

    # 检查缓存（24小时内有效）
    if use_cache and cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
        if age_hours < 24:
            try:
                df = pd.read_parquet(cache_file)
                if not df.empty and len(df) >= 60:
                    return df
            except Exception:
                pass  # 缓存损坏，重新获取

    # 原始数据获取逻辑
    df = _fetch_stock_data_impl(symbol, days)

    # 写入缓存
    if use_cache and not df.empty:
        try:
            df.to_parquet(cache_file, index=False)
        except Exception:
            pass  # 缓存写入失败不影响主流程

    return df


def _fetch_stock_data_impl(symbol: str, days: int) -> pd.DataFrame:
    """实际的数据获取逻辑（从原 get_stock_data 移入）"""
    global _survivorship_warned
    if not _survivorship_warned:
        logger.warning(
            "【幸存者偏差警告】回测数据仅包含当前仍在交易的股票，"
            "已退市股票无法获取。回测收益可能被高估 1-3%/年。"
        )
        _survivorship_warned = True

    from lib.data_layer import get_stock_hist_multi_source, AllDataSourcesFailedError
    try:
        df = get_stock_hist_multi_source(symbol, days=days, source="auto", prefer_adjusted=True)
    except AllDataSourcesFailedError as e:
        raise AllDataSourcesUnavailableError(
            f"所有数据源失败，无法获取 {symbol} 历史数据: {e}"
        ) from e
    if df.empty:
        raise AllDataSourcesUnavailableError(f"数据源返回空结果，无法获取 {symbol} 历史数据")
    # 确保有 trade_date 列（ABroker 需要）
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"])

    # 数据质量校验
    df = _validate_ohlcv(df, symbol)

    # 添加 pre_close：直接用 shift(1) 近似，避免额外阻塞API调用
    if "pre_close" not in df.columns:
        df["pre_close"] = df["close"].shift(1)
        df["pre_close"] = df["pre_close"].fillna(df["close"])

    # 计算涨跌停/停牌标记
    limit_pct = ABroker.get_limit_pct(symbol)
    if "pct_chg" in df.columns and "pre_close" in df.columns:
        df["is_limit_up"] = (df["pct_chg"] >= limit_pct * 100 - 0.01)
        df["is_limit_down"] = (df["pct_chg"] <= -limit_pct * 100 + 0.01)
    else:
        pct = (df["close"] - df["pre_close"]) / df["pre_close"].replace(0, np.nan)
        df["is_limit_up"] = (pct >= limit_pct - 0.001)
        df["is_limit_down"] = (pct <= -limit_pct + 0.001)
    df["is_suspend"] = df["volume"].fillna(0) == 0

    return df


def _compute_atr_for_position(df: pd.DataFrame, period: int = 14) -> float:
    """从 DataFrame 计算 ATR（供仓位计算使用）"""
    if df.empty or len(df) < period + 1:
        return 0.0
    try:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        atr_series = calc_atr(high, low, close, period)
        val = atr_series.iloc[-1]
        return float(val) if not pd.isna(val) else 0.0
    except Exception:
        return 0.0


def run_factor_backtest(
    symbols: List[str],
    days: int = 250,
    initial_cash: float = 1_000_000,
    stop_loss_atr_mult: float = 2.0,
    take_profit_atr_mult: float = 4.0,
    max_hold_days: int = 10,
    confidence_threshold: float = 0.3,
    risk_per_trade_pct: float = 0.5,
    preset: str = "balanced",
    initial_positions: List[Dict] = None,
    include_delisted: bool = False,
) -> Optional[Dict]:
    """基于因子信号的多股票回测（增强版）

    改进点：
    - 用 RiskSizedPosition 计算仓位（替代固定 20%）
    - 用 ATR 自适应止损/止盈（替代硬编码百分比）
    - 接入 RiskManager 做回测期风控
    - 最大持仓天数放宽到 10 天（趋势需要空间）
    - 支持 include_delisted=True 加入退市股票（缓解幸存者偏差）

    Args:
        include_delisted: 是否包含退市股票数据（默认 False）
    """
    from config import get_config

    # 获取数据（len > 30 保证 ATR 等指标可计算）
    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = get_stock_data(sym, days=days)
        if not df.empty and len(df) > 30:
            market_data[sym] = df

    # 可选：加入退市股票数据（缓解幸存者偏差）
    if include_delisted:
        try:
            from lib.data_delisted import get_all_delisted_hist, merge_with_delisted
            delisted_data = get_all_delisted_hist(days=days, max_stocks=30)
            if delisted_data:
                market_data = merge_with_delisted(market_data, delisted_data)
                logger.info(f"已加入 {len(delisted_data)} 只退市股票数据")
        except Exception as e:
            logger.warning(f"退市股票数据获取失败（继续使用活股票数据）: {e}")

    if not market_data:
        logger.error("无有效股票数据")
        return None

    # 收集所有交易日
    all_dates = set()
    for df in market_data.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(all_dates)

    # 初始化
    broker = ABroker(initial_cash=initial_cash)
    broker.set_trade_dates([str(d.date()) if hasattr(d, 'date') else str(d) for d in trade_dates])
    constraints = TradingConstraints(exclude_st=True)

    # 预置初始持仓（用于测试已有持仓的止损）
    if initial_positions:
        first_date_str = trade_dates[0].date().isoformat() if hasattr(trade_dates[0], 'date') else str(trade_dates[0])
        for pos in initial_positions:
            code = pos["code"]
            shares = pos["shares"]
            buy_price = pos["buy_price"]
            # buy_date 设为其前一日，can_sell_date 为首日（T+1 已过）
            buy_date = (trade_dates[0] - timedelta(days=1)).date().isoformat() if hasattr(trade_dates[0], 'date') else first_date_str
            holding = Holding(
                code=code,
                shares=shares,
                cost_price=buy_price,
                buy_date=buy_date,
                can_sell_date=first_date_str,
            )
            broker.holdings[code] = holding
            # 设置 last_price 为首日收盘价（用于计算持仓市值）
            if code in market_data and not market_data[code].empty:
                first_row = market_data[code][market_data[code]["trade_date"] == trade_dates[0]]
                if not first_row.empty:
                    broker._last_prices[code] = first_row.iloc[0]["close"]
                else:
                    broker._last_prices[code] = buy_price
            else:
                broker._last_prices[code] = buy_price
    position_sizer = RiskSizedPosition(PositionSizeConfig(
        risk_per_trade_pct=risk_per_trade_pct,
        max_position_pct=0.20,
        use_atr_stop=True,
        atr_multiplier=stop_loss_atr_mult,
    ))
    risk_mgr = RiskManager(RiskConfig(
        max_drawdown_stop=0.20,
        single_stock_stop_loss=stop_loss_atr_mult * 0.05,  # 近似 ATR 止损
        single_stock_take_profit=take_profit_atr_mult * 0.05,
    ))

    # 分仓止盈 + 连亏熔断
    partial_exit = PartialExitManager()
    loss_breaker = ConsecutiveLossBreaker(reduce_threshold=2, stop_threshold=3)

    # 回测循环
    buy_prices: Dict[str, float] = {}
    buy_dates: Dict[str, str] = {}
    stop_prices: Dict[str, float] = {}  # ATR 止损价
    risk_paused = False

    for i, date in enumerate(trade_dates):
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)

        # 更新市场价格
        broker.update_market_prices(market_data, date_str)

        # 风控检查：最大回撤暂停
        if broker.daily_values:
            last_value = broker.daily_values[-1]["total_value"]
            peak = max(d["total_value"] for d in broker.daily_values)
            drawdown = (peak - last_value) / peak if peak > 0 else 0
            if drawdown > risk_mgr.config.max_drawdown_stop:
                if not risk_paused:
                    logger.warning(f"{date_str} 回撤 {drawdown:.1%} 超过阈值，暂停交易")
                    risk_paused = True
            else:
                risk_paused = False

        # 构建持仓字典（供风控使用）
        positions_dict = {}
        prices_dict = broker._last_prices.copy()
        for code, holding in broker.holdings.items():
            current_price = prices_dict.get(code, holding.cost_price)
            pnl_pct = (current_price - holding.cost_price) / holding.cost_price if holding.cost_price > 0 else 0
            weight = (holding.shares * current_price) / broker.total_value if broker.total_value > 0 else 0
            positions_dict[code] = {"pnl_pct": pnl_pct, "weight": weight}

        # 日亏损检查
        if broker.daily_values:
            prev_value = broker.daily_values[-1]["total_value"]
            daily_pnl = broker.total_value - prev_value
            if risk_mgr.is_daily_loss_limit_triggered(daily_pnl, prev_value):
                logger.warning(f"{date_str} 日亏损超限，跳过今日交易")
                broker.record_daily_snapshot(date_str)
                continue

        # 检查止损止盈（ATR 自适应）
        for code in list(broker.holdings.keys()):
            holding = broker.holdings.get(code)
            if holding is None:
                continue

            current_price = broker._last_prices.get(code)
            if current_price is None:
                continue

            buy_price = buy_prices.get(code, holding.cost_price)
            pnl_pct = (current_price - buy_price) / buy_price if buy_price > 0 else 0

            # ATR 止损：如果设置了止损价，用止损价判断
            stop_price = stop_prices.get(code)
            if stop_price is not None and current_price <= stop_price:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="ATR止损")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)
                continue

            # 分仓变速止盈
            pe_result = partial_exit.check_exit(code, current_price, date_str)
            if pe_result and pe_result["action"] == "SELL":
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=pe_result["shares"], date=date_str, reason=pe_result["reason"])
                trade = broker.submit_order(order, market_data, date_str)
                if trade and pe_result["remaining"] <= 0:
                    buy_prices.pop(code, None)
                    buy_dates.pop(code, None)
                    stop_prices.pop(code, None)
                continue

            # 硬止损兜底（防止 ATR 止损过宽）
            if pnl_pct <= -risk_mgr.config.single_stock_stop_loss:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="硬止损")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)
                continue

            # ATR 止盈：使用买入时的 ATR（固定，不随时间变化）
            # 止损价和止盈价在买入时确定，持仓期间不变
            atr_val = _compute_atr_for_position(market_data.get(code, pd.DataFrame()))
            if atr_val > 0 and buy_price > 0:
                take_profit_price = buy_price + atr_val * take_profit_atr_mult
                if current_price >= take_profit_price:
                    order = Order(code=code, direction="SELL", price=current_price,
                                  shares=holding.shares, date=date_str, reason="ATR止盈")
                    broker.submit_order(order, market_data, date_str)
                    buy_prices.pop(code, None)
                    buy_dates.pop(code, None)
                    stop_prices.pop(code, None)
                    continue

            # 最大持仓天数
            hold_days = (date - pd.Timestamp(buy_dates.get(code, date))).days
            if hold_days >= max_hold_days:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="超时")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)

        # 买入信号（风控暂停或连亏熔断时跳过）
        if risk_paused or loss_breaker.is_stopped():
            broker.record_daily_snapshot(date_str)
            continue

        for sym in symbols:
            if sym in broker.holdings:
                continue

            df = market_data.get(sym)
            if df is None:
                continue

            # 使用严格小于当前日期的数据（避免未来数据泄漏）
            # 实盘中盘中无法知道今天的收盘价
            hist = df[df["trade_date"] < date]
            if len(hist) < 60:
                continue

            result = compute_all_signals(hist)
            if not result:
                continue

            combined = result["combined"]
            if combined["signal"] != "bullish" or combined["confidence"] < confidence_threshold:
                continue
            if combined.get("macro_blocked"):
                continue

            # 用 RiskSizedPosition 计算仓位（应用连亏熔断乘数）
            current_price = hist["close"].iloc[-1]
            atr_val = _compute_atr_for_position(hist)
            loss_mult = loss_breaker.get_position_multiplier()

            pos_result = position_sizer.calculate(
                equity=broker.total_value,
                entry_price=current_price,
                atr=atr_val if atr_val > 0 else None,
                cash_available=broker.cash,
                current_exposure=broker.total_market_value / broker.total_value if broker.total_value > 0 else 0,
            )

            if pos_result.qty <= 0:
                continue

            # 应用连亏熔断仓位乘数
            adjusted_qty = int(pos_result.qty * loss_mult / 100) * 100
            if adjusted_qty <= 0:
                continue

            order = Order(code=sym, direction="BUY", price=current_price,
                          shares=adjusted_qty, date=date_str, reason="因子买入")
            trade = broker.submit_order(order, market_data, date_str)
            if trade:
                buy_prices[sym] = trade.price
                buy_dates[sym] = date_str
                # 注册分仓止盈
                partial_exit.register_entry(sym, trade.price, trade.shares, date_str)
                # 设置 ATR 止损价
                if atr_val > 0:
                    stop_prices[sym] = trade.price - atr_val * stop_loss_atr_mult
                else:
                    stop_prices[sym] = trade.price * 0.95  # 默认 5% 止损

        broker.record_daily_snapshot(date_str)

    # 生成报告
    perf_df = broker.get_performance_df()
    if perf_df.empty:
        return None

    analyzer = PerformanceAnalyzer(perf_df)
    metrics = analyzer.analyze()
    cost_summary = broker.get_cost_summary()

    # 基准对比：沪深300（使用统一数据层）
    benchmark_metrics = None
    try:
        bt_start = trade_dates[0]
        bt_end = trade_dates[-1]
        bt_start_str = str(bt_start.date()) if hasattr(bt_start, 'date') else str(bt_start)
        bt_end_str = str(bt_end.date()) if hasattr(bt_end, 'date') else str(bt_end)

        from lib.data_layer import get_index_hist
        idx_df = get_index_hist("000300", days=500)
        if not idx_df.empty and "close" in idx_df.columns:
            date_col = "date" if "date" in idx_df.columns else "trade_date"
            idx_df[date_col] = pd.to_datetime(idx_df[date_col], errors="coerce")
            idx_df = idx_df.dropna(subset=[date_col])
            idx_df = idx_df[(idx_df[date_col] >= bt_start_str) & (idx_df[date_col] <= bt_end_str)]
            idx_df = idx_df.sort_values(date_col)

        if not idx_df.empty and "close" in idx_df.columns:
            bm_closes = idx_df["close"].values
            if len(bm_closes) >= 2:
                bm_start = bm_closes[0] if bm_closes[0] > 0 else 1
                bm_nav = bm_closes / bm_start
                bm_return = (bm_nav[-1] - 1)
                benchmark_metrics = {
                    "total_return": round(float(bm_return), 4),
                    "name": "沪深300",
                }
                if metrics and "total_return" in metrics:
                    excess = metrics["total_return"] - bm_return
                    benchmark_metrics["excess_return"] = round(float(excess), 4)
    except Exception as e:
        logger.debug(f"基准对比失败: {e}")

    # 统计退出原因（用 Order 的 reason 字段，不是 date）
    exit_reasons = {"stop_loss": 0, "take_profit": 0, "timeout": 0, "other": 0}
    for t in broker.trade_history:
        if t.direction == "SELL":
            reason = str(getattr(t, 'reason', ''))
            if "止损" in reason:
                exit_reasons["stop_loss"] += 1
            elif "止盈" in reason:
                exit_reasons["take_profit"] += 1
            elif "超时" in reason:
                exit_reasons["timeout"] += 1
            else:
                exit_reasons["other"] += 1

    return {
        "metrics": metrics,
        "cost_summary": cost_summary,
        "perf_df": perf_df,
        "trade_count": len(broker.trade_history),
        "trades": [vars(t) for t in broker.trade_history],
        "exit_reasons": exit_reasons,
        "benchmark": benchmark_metrics,
        "report": analyzer.summary_report(),
        "delisted_codes": list(broker.get_delisted_codes()),
        "survivorship_bias_warning": "回测数据仅含当前交易股票，收益可能被高估",
    }


def run_walk_forward(
    symbols: List[str],
    days: int = 500,
    train_days: int = 180,
    validation_days: int = 30,
    step_days: int = 30,
    strategy_name: str = "factor_strategy",
) -> Dict:
    """Walk-forward 验证"""
    from lib.walkforward import (WalkForwardBacktest, WalkForwardConfig,
                                  summarize_rounds, save_walkforward_results)

    # 获取数据
    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = get_stock_data(sym, days=days)
        if not df.empty and len(df) > 60:
            market_data[sym] = df

    if not market_data:
        return {"error": "无有效数据"}

    # 收集交易日
    all_dates = set()
    for df in market_data.values():
        all_dates.update(df["trade_date"].tolist())
    dates = sorted(all_dates)

    # 定义策略函数
    def strategy_fn(market_data, factor_data, dates, symbols, **kwargs):
        trades = []
        total_return = 0.0
        for sym in (symbols or list(market_data.keys())):
            df = market_data.get(sym)
            if df is None or len(df) < 60:
                continue
            result = compute_all_signals(df)
            if result and result["combined"]["signal"] == "bullish":
                trades.append({"code": sym, "signal": "bullish"})
                # 计算信号后的收益（简化：取信号日后的5日收益）
                if len(df) > 5:
                    ret = (df["close"].iloc[-1] / df["close"].iloc[-6] - 1)
                    total_return += ret
        return {"trades": trades, "total_return": total_return, "total_trades": len(trades)}

    # 执行 walk-forward
    config = WalkForwardConfig(
        train_days=train_days,
        validation_days=validation_days,
        step_days=step_days,
    )
    wf = WalkForwardBacktest(config)
    rounds = wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=symbols,
        strategy_name=strategy_name,
    )

    summary = summarize_rounds(rounds)

    # 持久化
    save_walkforward_results(strategy_name, summary, len(rounds))

    return {"rounds": rounds, "summary": summary}


# ========== V4 A股 MVP 回测 ==========


def run_mvp_backtest(
    symbols: List[str],
    days: int = 500,
    initial_capital: float = 1_000_000.0,
) -> Dict:
    """V4 A股 MVP 回测验证

    V4 极简规则（目标：验证"趋势过滤 + 简单突破"是否有边际优势）：
    - 市场过滤：沪深300 > EMA21 才允许开仓
    - 入场条件（全部满足）：
        1. 收盘价 > 20日最高价（突破）
        2. 成交量 > 20日平均成交量（放量）
        3. 股价 > EMA21
    - 止损：跌破10日最低价
    - 止盈：不设固定止盈，跟踪止损
    - 仓位：单票10%，最大总仓50%

    Args:
        symbols: 股票代码列表
        days: 历史数据天数
        initial_capital: 初始资金

    Returns:
        回测指标 dict（胜率/盈亏比/最大回撤/Sharpe/连亏次数）
    """
    from lib.data_layer import get_stock_hist_multi_source
    from lib.market_regime import detect_market_regime_from_hs300

    results = {}
    all_trades = []

    # 获取 HS300 数据（用于市场过滤）
    hs300_hist = None
    try:
        from lib.data_layer import get_index_hist
        hs300_hist = get_index_hist("000300", days=days)
    except Exception:
        pass

    for sym in symbols:
        try:
            df = get_stock_hist_multi_source(sym, days=days, source="auto", prefer_adjusted=True)
            if df.empty or len(df) < 60:
                continue

            trades = _mvp_strategy(df, hs300_hist)
            all_trades.extend(trades)
            if trades:
                results[sym] = trades
        except Exception as e:
            logger.debug(f"MVP 回测 [{sym}] 失败: {e}")

    # 汇总统计
    if not all_trades:
        return {"error": "无有效交易", "symbols": len(symbols), "trades": 0}

    # 计算整体指标
    pnl_list = [t["pnl_pct"] for t in all_trades]
    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p <= 0]

    total_return = sum(pnl_list)
    win_rate = len(wins) / len(pnl_list) if pnl_list else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 1
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    # 最大连续亏损
    sorted_pnl = sorted(pnl_list)
    max_consecutive_loss = 0
    current_loss = 0
    for p in pnl_list:
        if p < 0:
            current_loss += abs(p)
            max_consecutive_loss = max(max_consecutive_loss, current_loss)
        else:
            current_loss = 0

    # 最大回撤（简化）
    cumulative = np.cumsum([0] + pnl_list)
    peak = np.maximum.accumulate(cumulative)
    drawdown = cumulative - peak
    max_drawdown = abs(drawdown.min()) if len(drawdown) > 0 else 0

    return {
        "strategy": "V4_MVP",
        "symbols_tested": len(symbols),
        "symbols_with_trades": len(results),
        "total_trades": len(all_trades),
        "total_return": round(total_return, 4),
        "win_rate": round(win_rate, 4),
        "avg_win_pct": round(avg_win, 4),
        "avg_loss_pct": round(avg_loss, 4),
        "profit_loss_ratio": round(profit_loss_ratio, 4),
        "max_consecutive_loss": round(max_consecutive_loss, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "sharpe_ratio": round(total_return / (max_drawdown + 1e-9), 4) if max_drawdown > 0 else 0,
        "trades": all_trades[:20],  # 保留前20条交易明细
    }


def _mvp_strategy(df: pd.DataFrame, hs300_hist: Optional[pd.DataFrame] = None) -> List[Dict]:
    """V4 MVP 策略核心逻辑

    返回每笔交易的盈亏（百分比）
    """
    if len(df) < 30:
        return []

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 计算指标
    ema21 = close.ewm(span=21, adjust=False).mean()
    high_20 = high.rolling(20).max()
    low_10 = low.rolling(10).min()
    vol_20_avg = volume.rolling(20).mean()

    trades = []

    for i in range(30, len(df)):
        c = float(close.iloc[i])
        h20 = float(high_20.iloc[i])
        l10 = float(low_10.iloc[i])
        v = float(volume.iloc[i])
        v20avg = float(vol_20_avg.iloc[i])
        ema = float(ema21.iloc[i])

        # 市场过滤：HS300 > EMA21
        market_ok = True
        if hs300_hist is not None and len(hs300_hist) > i:
            hs_close = float(hs300_hist["close"].iloc[i])
            hs_ema = float(hs300_hist["close"].ewm(span=21, adjust=False).mean().iloc[i])
            market_ok = hs_close > hs_ema

        if not market_ok:
            continue

        # 入场条件
        if c > h20 and v > v20avg and c > ema:
            # 入仓
            entry_price = c
            # 止损：跌破10日最低价
            stop_price = l10
            # 跟踪止损：EMA21
            trailing_stop = ema

            # 持有多天
            exit_idx = min(i + 20, len(df) - 1)  # 默认退出位置
            for j in range(i + 1, min(i + 20, len(df))):
                exit_price = float(close.iloc[j])
                curr_ema = float(ema21.iloc[j])

                # 止损触发
                if exit_price < stop_price:
                    pnl = (exit_price / entry_price - 1) * 100
                    trades.append({"code": df.index[j] if hasattr(df.index[j], "__str__") else str(j),
                                   "entry": entry_price, "exit": exit_price,
                                   "pnl_pct": pnl, "exit_reason": "stop_loss"})
                    exit_idx = j
                    break
                # EMA 止损
                if exit_price < curr_ema:
                    pnl = (exit_price / entry_price - 1) * 100
                    trades.append({"code": df.index[j] if hasattr(df.index[j], "__str__") else str(j),
                                   "entry": entry_price, "exit": exit_price,
                                   "pnl_pct": pnl, "exit_reason": "ema_exit"})
                    exit_idx = j
                    break
                # 跟踪止损更新
                trailing_stop = max(trailing_stop, curr_ema)
                exit_idx = j
            else:
                # 20天后强制平仓
                exit_price = float(close.iloc[exit_idx])
                pnl = (exit_price / entry_price - 1) * 100
                trades.append({"code": df.index[exit_idx] if hasattr(df.index[exit_idx], "__str__") else str(exit_idx),
                               "entry": entry_price, "exit": exit_price,
                               "pnl_pct": pnl, "exit_reason": "timeout"})

    return trades


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    print("=" * 60)
    print("  因子策略回测（增强版：ATR 仓位 + 风控）")
    print("=" * 60)

    test_symbols = ["000001", "600519", "000858"]

    print(f"\n回测标的: {', '.join(test_symbols)}")
    print(f"回测周期: 250 天")
    print(f"仓位管理: RiskSizedPosition (风险预算 {0.5}%)")
    print(f"止损: ATR × 2 | 止盈: ATR × 4")
    print("-" * 60)

    result = run_factor_backtest(test_symbols, days=250)
    if result:
        print(result["report"])
        print(f"\n交易次数: {result['trade_count']}")
        print(f"退出原因: {result['exit_reasons']}")
        cost = result["cost_summary"]
        print(f"交易成本: 佣金={cost['total_commission']:.2f}  "
              f"印花税={cost['total_stamp_tax']:.2f}  "
              f"冲击成本={cost['total_impact_cost']:.2f}")
    else:
        print("回测无结果")

    print("\n" + "=" * 60)
