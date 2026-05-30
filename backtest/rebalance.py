"""月频调仓回测 —— 用历史数据验证调仓器+退出规则

在历史数据上模拟完整的调仓流程：
- 每 20 个交易日执行一次扫描
- 集成退出规则（夏普<1.0 或回撤>25% 停止）
- 集成连亏熔断（连亏 3 笔停止）
- 集成分仓止盈（+60%/+30%/成本价梯度退出）

使用方式：
    python backtest_rebalance.py
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from lib.broker import ABroker, Order
from lib.broker_data import Holding
from config import get_config
from lib.position_size import RiskSizedPosition, PositionSizeConfig
from lib.risk_manager import RiskManager, RiskConfig
from lib.partial_exit import PartialExitManager, ConsecutiveLossBreaker
from lib.rebalance_scheduler import RebalanceScheduler, RebalanceConfig
from factors import compute_all_signals, calc_atr, compute_ic_weighted_score

logger = logging.getLogger(__name__)


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
) -> Optional[Dict]:
    """月频调仓回测

    Args:
        symbols: 股票代码列表
        days: 回测天数
        initial_cash: 初始资金
        rebalance_period: 调仓周期（交易日）
        disable_loss_breaker: 禁用连亏熔断（用于因子对比测试）
        factor_config: FactorConfig 实例，控制因子开关
    """
    from backtest.core import get_stock_data, _compute_atr_for_position

    if factor_config is None:
        factor_config = get_config("balanced").factor

    # 获取数据（并行批量读取）
    market_data: Dict[str, pd.DataFrame] = {}
    try:
        from lib.tdx_batch import batch_read_day_files
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
    from lib.dynamic_exit import DynamicExitManager
    dynamic_exit = DynamicExitManager()
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
                from lib.market_regime import detect_market_regime_from_hs300
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

    from lib.analyzer import PerformanceAnalyzer
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


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

    symbols = ["000001", "600519", "000858", "002415", "601318"]
    print(f"月频调仓回测 - 标的: {symbols}")
    print("=" * 60)

    result = run_rebalance_backtest(symbols, days=500, rebalance_period=20)
    if result:
        print(f"调仓次数: {result['rebalance_count']}")
        print(f"总交易: {result['trade_count']} 笔")
        print(f"卖出: {result['sell_count']} 笔")
        print(f"胜率: {result['win_rate']:.1%}")
        print(f"平均盈利: {result['avg_win']:.2%}")
        print(f"平均亏损: {result['avg_loss']:.2%}")
        print(f"盈亏比: {result['avg_win']/result['avg_loss']:.2f}" if result['avg_loss'] > 0 else "盈亏比: N/A")
        print(f"最终夏普: {result['final_sharpe']}")
        print(f"最终回撤: {result['final_drawdown']:.2%}")
        print(f"退出规则触发: {'是' if result['is_stopped'] else '否'}")
        if result['stop_reason']:
            print(f"退出原因: {result['stop_reason']}")
        print(f"连亏状态: {result['loss_breaker_status']}")
        print()
        print(result['report'])

        # 保存结果
        from lib.backtest_saver import save_backtest_result
        perf_df = result.get('perf_df')
        nav = perf_df['nav'].values if perf_df is not None and 'nav' in perf_df.columns else np.array([1.0])
        trades_list = result.get('trades', [])
        if hasattr(trades_list, 'to_dict'):
            trades_list = trades_list.to_dict('records')
        save_backtest_result("rebalance", nav,
                             trades=trades_list,
                             args={"symbols": symbols, "days": 500, "rebalance_period": 20},
                             extra={"sharpe": result.get('final_sharpe'),
                                    "drawdown": result.get('final_drawdown'),
                                    "stop_reason": result.get('stop_reason')})
    else:
        print("回测无结果")
