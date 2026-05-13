"""回测模块 - 基于 ABroker 的 A 股真实模拟回测

替代 backtrader，支持：
- T+1 制度
- 涨跌停限制
- 滑点 + 冲击成本
- 佣金 + 印花税
- 多股票组合回测
- Walk-forward 验证
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from lib.broker import ABroker, Order, TradingConstraints
from lib.analyzer import PerformanceAnalyzer
from lib.walkforward import WalkForwardBacktest, WalkForwardConfig, summarize_rounds
from factors import compute_all_signals

logger = logging.getLogger(__name__)


def get_stock_data(symbol: str, days: int = 250) -> pd.DataFrame:
    """获取股票历史数据（统一数据层，调用 scanner.get_stock_hist）"""
    from scanner import get_stock_hist
    df = get_stock_hist(symbol, days=days)
    if df.empty:
        return pd.DataFrame()
    # 确保有 trade_date 列（ABroker 需要）
    if "date" in df.columns and "trade_date" not in df.columns:
        df = df.rename(columns={"date": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    # 添加 pre_close 用于涨跌停判断
    if "pre_close" not in df.columns:
        df["pre_close"] = df["close"].shift(1)
        df["pre_close"] = df["pre_close"].fillna(df["close"])
    return df


def run_factor_backtest(
    symbols: List[str],
    days: int = 250,
    initial_cash: float = 1_000_000,
    stop_loss_pct: float = 0.05,
    take_profit_pct: float = 0.10,
    max_hold_days: int = 5,
    confidence_threshold: float = 0.3,
    preset: str = "balanced",
) -> Optional[Dict]:
    """基于因子信号的多股票回测

    策略逻辑：
    - 每日计算因子信号，bullish 且置信度 > 阈值时买入
    - 止损/止盈/最大持仓天数卖出
    - 使用 ABroker 模拟真实交易（T+1、滑点、冲击成本）
    """
    from config import get_config

    # 获取数据
    market_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = get_stock_data(sym, days=days)
        if not df.empty and len(df) > 60:
            market_data[sym] = df

    if not market_data:
        logger.error("无有效股票数据")
        return None

    # 收集所有交易日
    all_dates = set()
    for df in market_data.values():
        all_dates.update(df["trade_date"].tolist())
    trade_dates = sorted(all_dates)

    # 初始化券商
    broker = ABroker(initial_cash=initial_cash)
    broker.set_trade_dates([str(d.date()) if hasattr(d, 'date') else str(d) for d in trade_dates])

    constraints = TradingConstraints(exclude_st=True)

    # 回测循环
    buy_prices: Dict[str, float] = {}
    buy_dates: Dict[str, str] = {}

    for i, date in enumerate(trade_dates):
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)

        # 更新市场价格
        broker.update_market_prices(market_data, date_str)

        # 检查止损止盈
        for code in list(broker.holdings.keys()):
            holding = broker.holdings.get(code)
            if holding is None:
                continue

            current_price = broker._last_prices.get(code)
            if current_price is None:
                continue

            buy_price = buy_prices.get(code, holding.cost_price)
            pnl_pct = (current_price - buy_price) / buy_price

            # 止损
            if pnl_pct <= -stop_loss_pct:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="止损")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                continue

            # 止盈
            if pnl_pct >= take_profit_pct:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="止盈")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)
                continue

            # 最大持仓天数
            hold_days = (date - pd.Timestamp(buy_dates.get(code, date))).days
            if hold_days >= max_hold_days:
                order = Order(code=code, direction="SELL", price=current_price,
                              shares=holding.shares, date=date_str, reason="超时")
                broker.submit_order(order, market_data, date_str)
                buy_prices.pop(code, None)
                buy_dates.pop(code, None)

        # 寻找买入信号
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

            # 计算买入股数
            current_price = hist["close"].iloc[-1]
            available_cash = broker.cash * 0.2  # 单票最多 20% 仓位
            shares = constraints.compute_buy_shares(available_cash, current_price)
            if shares <= 0:
                continue

            order = Order(code=sym, direction="BUY", price=current_price,
                          shares=shares, date=date_str, reason="因子买入")
            trade = broker.submit_order(order, market_data, date_str)
            if trade:
                buy_prices[sym] = trade.price
                buy_dates[sym] = date_str

        broker.record_daily_snapshot(date_str)

    # 生成报告
    perf_df = broker.get_performance_df()
    if perf_df.empty:
        return None

    analyzer = PerformanceAnalyzer(perf_df)
    metrics = analyzer.analyze()
    cost_summary = broker.get_cost_summary()

    return {
        "metrics": metrics,
        "cost_summary": cost_summary,
        "perf_df": perf_df,
        "trade_count": len(broker.trade_history),
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
    print("  因子策略回测")
    print("=" * 60)

    # 测试股票
    test_symbols = ["000001", "600519", "000858"]

    print(f"\n回测标的: {', '.join(test_symbols)}")
    print(f"回测周期: 250 天")
    print("-" * 60)

    result = run_factor_backtest(test_symbols, days=250)
    if result:
        print(result["report"])
        print(f"\n交易次数: {result['trade_count']}")
        cost = result["cost_summary"]
        print(f"交易成本: 佣金={cost['total_commission']:.2f}  "
              f"印花税={cost['total_stamp_tax']:.2f}  "
              f"冲击成本={cost['total_impact_cost']:.2f}")
    else:
        print("回测无结果")

    print("\n" + "=" * 60)
