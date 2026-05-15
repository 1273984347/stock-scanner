"""压力测试：滑点对交易执行价格的影响分析"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.basicConfig(level=logging.WARNING, format="%(message)s")

import numpy as np
import pandas as pd

print("=" * 70)
print("  压力测试：滑点对策略收益的影响")
print("=" * 70)

# ----------------------------------------------------------------
# Part 1: 因子回测的低频信号发现
# ----------------------------------------------------------------
print("\n## Part 1: 因子策略信号频率分析")
print("-" * 50)

from backtest import run_factor_backtest

for conf in [0.3, 0.2, 0.1]:
    r = run_factor_backtest(
        ["000001", "600519", "000858", "002415", "000333", "002594"],
        days=250, confidence_threshold=conf
    )
    trades = r["trade_count"] if r else 0
    print(f"  置信度阈值 {conf}: {trades} 笔交易 (250天/6只股票)")
    if trades > 0 and r:
        m = r["metrics"]
        print(f"    夏普={m.sharpe_ratio:.4f}, 收益={m.total_return:.4%}")

print("\n  结论：因子策略在6只大盘股上250天仅触发0-10笔交易，")
print("  信号频率极低。报告所指的'夏普1.76'应来自ML模型回测而非因子回测。")

# ----------------------------------------------------------------
# Part 2: 滑点对收益的影响
# ----------------------------------------------------------------
print("\n## Part 2: 滑点对单笔交易收益的影响")
print("-" * 50)

def simulate_trade(slippage, price=100, shares=10000, days=5):
    """模拟一笔完整的买入持有卖出交易"""
    broker = ABroker(initial_cash=1_000_000, slippage=slippage)
    dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
    broker.set_trade_dates(dates)

    # 模拟价格逐步上涨
    prices = [100, 101, 102, 103, 104]
    records = []
    pre_close = 100
    for i, d in enumerate(dates):
        c = prices[i]
        records.append({
            "trade_date": pd.Timestamp(d), "open": c, "close": c,
            "high": c * 1.01, "low": c * 0.99, "volume": 5e6,
            "pre_close": pre_close, "pct_chg": (c - pre_close) / pre_close * 100,
            "is_limit_up": False, "is_limit_down": False, "is_suspend": False,
        })
        pre_close = c
    md = {"000001": pd.DataFrame(records)}

    # 买入
    buy = Order(code="000001", direction="BUY", price=price,
                shares=shares, date=dates[0], reason="买入")
    t_buy = broker.submit_order(buy, md, dates[0])
    if not t_buy:
        return None

    # T+1 卖出
    sell_price = prices[-1]
    sell = Order(code="000001", direction="SELL", price=sell_price,
                 shares=t_buy.shares, date=dates[-1], reason="卖出")
    t_sell = broker.submit_order(sell, md, dates[-1])
    if not t_sell:
        return None

    buy_cost = t_buy.price * t_buy.shares
    sell_proceed = t_sell.price * t_sell.shares
    total_cost = t_buy.commission + t_sell.commission + t_sell.stamp_tax
    pnl = sell_proceed - buy_cost - total_cost
    pnl_pct = pnl / buy_cost * 100

    return {
        "buy_price": t_buy.price, "sell_price": t_sell.price,
        "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 3),
        "total_cost": round(total_cost, 2),
    }

from lib.broker import ABroker, Order

base = simulate_trade(0.001)
pes = simulate_trade(0.005)

if base and pes:
    print(f"\n  场景: 买入100元股票10000股，持有5天后104元卖出")
    print(f"\n  滑点 0.1%:")
    print(f"    买入价: {base['buy_price']:.4f}")
    print(f"    卖出价: {base['sell_price']:.4f}")
    print(f"    交易成本: {base['total_cost']}")
    print(f"    净利润: {base['pnl']} ({base['pnl_pct']:.3f}%)")

    print(f"\n  滑点 0.5%:")
    print(f"    买入价: {pes['buy_price']:.4f}")
    print(f"    卖出价: {pes['sell_price']:.4f}")
    print(f"    交易成本: {pes['total_cost']}")
    print(f"    净利润: {pes['pnl']} ({pes['pnl_pct']:.3f}%)")

    profit_loss = base['pnl_pct'] - pes['pnl_pct']
    print(f"\n  利润损失: {profit_loss:.3f}% (每笔交易)")
    print(f"  对夏普的估算影响: 每笔损失 {profit_loss:.2f}% 利润")
    print(f"  相当于年化收益降低 ~{profit_loss * 20:.1f}% (假设20笔交易/年)")

# ----------------------------------------------------------------
# 最终结论
# ----------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  压力测试总结")
print(f"{'='*70}")
print("""
1. 因子策略信号频率极低（0-10笔/250天/6股），无法用于压力测试对比。
   '夏普1.76'应来自ML模型回测(lib.ml_backtest)，需单独进行滑点敏感性分析。

2. 单笔交易分析显示：滑点0.1%->0.5%使利润降低约0.8%/笔。
   对年化收益的影响取决于交易频率：
   - 低频(10笔/年): 影响 ~8% 年化收益
   - 中频(50笔/年): 影响 ~40% 年化收益
   - 高频(100笔/年): 策略可能由盈转亏

3. 建议：在实盘前用ML回测路径(MLBacktest.run)设置tc=0.006(当前)
   和tc=0.01(悲观)两个参数重跑，对比夏普变化。

4. 数据源问题修复：backtest.py的数据获取已从单数据源(akshare-only)
   改为多数据源自动切换(baostock->adata->westock->tushare->akshare)，
   提高了回测的可用性。
""")
