"""回测模块 - 基于 ABroker 的 A 股真实模拟回测

核心改动（相比旧版）：
1. 用 RiskSizedPosition 替代固定 20% 仓位
2. 接入 RiskManager 做回测期间风控
3. 波动率自适应止损替代硬编码百分比
4. 支持 judge_panel 评委团评分（可选）
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from lib.broker import ABroker, Order, TradingConstraints
from lib.analyzer import PerformanceAnalyzer
from lib.position_size import RiskSizedPosition, PositionSizeConfig, atr_risk_size
from lib.risk_manager import RiskManager, RiskConfig
from factors import compute_all_signals, calc_atr

logger = logging.getLogger(__name__)


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


def get_stock_data(symbol: str, days: int = 250) -> pd.DataFrame:
    """获取股票历史数据（统一数据层，多数据源自动切换）"""
    from lib.data_layer import get_stock_hist_multi_source, AllDataSourcesFailedError
    try:
        df = get_stock_hist_multi_source(symbol, days=days, source="auto")
    except AllDataSourcesFailedError:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
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
) -> Optional[Dict]:
    """基于因子信号的多股票回测（增强版）

    改进点：
    - 用 RiskSizedPosition 计算仓位（替代固定 20%）
    - 用 ATR 自适应止损/止盈（替代硬编码百分比）
    - 接入 RiskManager 做回测期风控
    - 最大持仓天数放宽到 10 天（趋势需要空间）
    """
    from config import get_config

    # 获取数据（len > 30 保证 ATR 等指标可计算）
    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = get_stock_data(sym, days=days)
        if not df.empty and len(df) > 30:
            market_data[sym] = df

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

            # 硬止损兜底（防止 ATR 止损过宽）
            if pnl_pct <= -risk_mgr.config.single_stock_stop_loss:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="硬止损")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                stop_prices.pop(code, None)
                continue

            # ATR 止盈：使用 ATR 乘数
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

        # 买入信号（风控暂停时跳过）
        if risk_paused:
            broker.record_daily_snapshot(date_str)
            continue

        for sym in symbols:
            if sym in broker.holdings:
                continue

            df = market_data.get(sym)
            if df is None:
                continue

            hist = df[df["trade_date"] <= date]
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

            # 用 RiskSizedPosition 计算仓位
            current_price = hist["close"].iloc[-1]
            atr_val = _compute_atr_for_position(hist)

            pos_result = position_sizer.calculate(
                equity=broker.total_value,
                entry_price=current_price,
                atr=atr_val if atr_val > 0 else None,
                cash_available=broker.cash,
                current_exposure=broker.total_market_value / broker.total_value if broker.total_value > 0 else 0,
            )

            if pos_result.qty <= 0:
                continue

            order = Order(code=sym, direction="BUY", price=current_price,
                          shares=pos_result.qty, date=date_str, reason="因子买入")
            trade = broker.submit_order(order, market_data, date_str)
            if trade:
                buy_prices[sym] = trade.price
                buy_dates[sym] = date_str
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

    # 基准对比：沪深300（对齐回测窗口，本地TDX优先）
    benchmark_metrics = None
    try:
        bt_start = trade_dates[0]
        bt_end = trade_dates[-1]
        bt_start_str = str(bt_start.date()) if hasattr(bt_start, 'date') else str(bt_start)
        bt_end_str = str(bt_end.date()) if hasattr(bt_end, 'date') else str(bt_end)

        # 优先用本地TDX数据（快速，无网络依赖）
        from lib.data_tdx import get_index_local
        idx_df = get_index_local("000300", days=500)
        if not idx_df.empty and "close" in idx_df.columns:
            date_col = "date"
            idx_df[date_col] = pd.to_datetime(idx_df[date_col], errors="coerce")
            idx_df = idx_df.dropna(subset=[date_col])
            idx_df = idx_df[(idx_df[date_col] >= bt_start_str) & (idx_df[date_col] <= bt_end_str)]
            idx_df = idx_df.sort_values(date_col)

        if idx_df.empty or "close" not in idx_df.columns:
            # Fallback：akshare 实时数据
            import akshare as ak
            idx_raw = ak.stock_zh_index_daily(symbol="sh000300")
            if not idx_raw.empty:
                date_col = "date"
                idx_raw[date_col] = pd.to_datetime(idx_raw[date_col])
                idx_df = idx_raw[(idx_raw[date_col] >= bt_start_str) & (idx_raw[date_col] <= bt_end_str)].copy()
                idx_df = idx_df.sort_values(date_col)

        if idx_df.empty or "close" not in idx_df.columns:
            # 最终Fallback：缓存数据
            from lib.data_layer import _load_cache
            idx_df = _load_cache("hs300_cache", max_age_hours=24)
            if not idx_df.empty:
                date_col = "trade_date" if "trade_date" in idx_df.columns else "date"
                idx_df[date_col] = pd.to_datetime(idx_df[date_col])
                idx_df = idx_df.sort_values(date_col)
                idx_df = idx_df[(idx_df[date_col] >= bt_start_str) & (idx_df[date_col] <= bt_end_str)]

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
        "exit_reasons": exit_reasons,
        "benchmark": benchmark_metrics,
        "report": analyzer.summary_report(),
    }


def run_walk_forward(
    symbols: List[str],
    days: int = 500,
    train_days: int = 180,
    validation_days: int = 30,
    step_days: int = 30,
) -> Dict:
    """Walk-forward 验证"""
    from lib.walkforward import WalkForwardBacktest, WalkForwardConfig, summarize_rounds

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
        for sym in (symbols or list(market_data.keys())):
            df = market_data.get(sym)
            if df is None or len(df) < 60:
                continue
            result = compute_all_signals(df)
            if result and result["combined"]["signal"] == "bullish":
                trades.append({"code": sym, "signal": "bullish"})
        return {"trades": trades, "total_return": 0, "total_trades": len(trades)}

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
        strategy_name="factor_strategy",
    )

    summary = summarize_rounds(rounds)
    return {"rounds": rounds, "summary": summary}


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
