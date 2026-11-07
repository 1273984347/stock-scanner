"""多市场周期验证（改进版）— 扩大股票池 + 调整熔断阈值

改进点：
1. 股票池从 5 只扩大到 50+ 只
2. 连亏熔断阈值从 5 次放宽到 8 次
3. 冷却期从 20 天缩短到 10 天
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger(__name__)


def get_large_stock_pool(n: int = 50) -> list:
    """获取大规模股票池"""
    try:
        from lib.strategy.stock_pool import StockPoolBuilder, PoolConfig
        config = PoolConfig(
            max_candidates_per_strategy=20,
            enable_hs300=True,
            enable_zz500=True,
            enable_zz1000=False,
            enable_value_quality=True,
        )
        builder = StockPoolBuilder(config)
        pool = builder.build_combined_pool()
        codes = list(pool.keys())[:n] if isinstance(pool, dict) else list(pool)[:n]
        if len(codes) >= 10:
            logger.info(f"Stock pool: {len(codes)} stocks")
            return codes
    except Exception as e:
        logger.warning(f"Stock pool builder failed: {e}")

    # 回退：使用 HS300 成分股
    try:
        from lib.data.data_layer import get_index_stocks
        codes = get_index_stocks("000300")[:n]
        if codes:
            logger.info(f"HS300 fallback: {len(codes)} stocks")
            return codes
    except Exception:
        pass

    # 最终回退：手动列表
    default = [
        "600519", "000858", "000001", "601318", "000725",
        "600036", "601166", "600276", "000333", "002415",
        "600887", "601888", "000568", "002304", "600309",
        "601012", "600900", "000002", "600030", "601398",
        "601939", "601288", "600016", "600000", "601601",
        "601668", "600585", "000776", "002142", "600015",
        "601988", "601857", "600028", "601088", "600048",
        "000063", "002714", "600570", "601211", "600837",
        "601688", "600332", "000895", "002352", "600104",
        "601628", "600518", "601229", "601818", "600809",
    ]
    logger.info(f"Using default pool: {len(default)} stocks")
    return default[:n]


def run_validation():
    """运行多市场周期验证"""
    from lib.strategy.multi_market_validator import MultiMarketValidator, MARKET_PERIODS
    from backtest.rebalance import run_rebalance_backtest

    # 获取大规模股票池
    symbols = get_large_stock_pool(50)
    logger.info(f"Stock pool: {len(symbols)} stocks")

    # 自定义回测函数（调整熔断阈值）
    def backtest_with_relaxed_breaker(symbols, days, **kwargs):
        """放宽熔断阈值的回测"""
        # 临时修改 partial_exit 的默认参数
        import lib.risk.partial_exit as pe
        orig_init = pe.ConsecutiveLossBreaker.__init__

        def patched_init(self, reduce_threshold=3, stop_threshold=8, cooldown_days=10):
            orig_init(self, reduce_threshold=reduce_threshold,
                     stop_threshold=stop_threshold, cooldown_days=cooldown_days)

        pe.ConsecutiveLossBreaker.__init__ = patched_init

        try:
            result = run_rebalance_backtest(
                symbols=symbols,
                days=days,
                disable_loss_breaker=False,
            )
            return result
        finally:
            pe.ConsecutiveLossBreaker.__init__ = orig_init

    # 运行验证
    validator = MultiMarketValidator()
    report = validator.validate(symbols=symbols, backtest_func=backtest_with_relaxed_breaker)

    # 打印结果
    print("\n" + "=" * 60)
    print("Multi-Market Validation (Improved)")
    print("=" * 60)
    print(f"Stock pool: {len(symbols)} stocks")
    print(f"Loss breaker: stop_threshold=8, cooldown=10 days")
    print()

    for r in report['periods']:
        if 'error' in r:
            print(f"{r['period']}: ERROR - {r['error']}")
        else:
            print(f"{r['period']}: Sharpe={r.get('sharpe',0):.3f} | "
                  f"MaxDD={r.get('max_drawdown',0):.1%} | "
                  f"WR={r.get('win_rate',0):.1%} | "
                  f"Trades={r.get('trade_count',0)}")

    print()
    s = report['summary']
    print(f"Valid periods: {s['valid_periods']}/{s['total_periods']}")
    print(f"Avg Sharpe: {s['avg_sharpe']:.3f}")
    print(f"Avg Win Rate: {s['avg_win_rate']:.1%}")
    print(f"Is Robust: {s['is_robust']}")
    if s['issues']:
        print("Issues:")
        for issue in s['issues']:
            print(f"  - {issue}")

    # 保存报告
    output_path = Path("cache/multi_market_report_v2.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\nReport saved to {output_path}")

    # 生成 markdown 报告
    md_path = Path("docs/multi_market_report_v2.md")
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write("# V6 多市场周期验证报告（改进版）\n\n")
        f.write(f"> 运行时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} | ")
        f.write(f"股票池：{len(symbols)} 只\n\n")
        f.write("## 改进点\n\n")
        f.write("1. 股票池从 5 只扩大到 50+ 只\n")
        f.write("2. 连亏熔断阈值从 5 次放宽到 8 次\n")
        f.write("3. 冷却期从 20 天缩短到 10 天\n\n")
        f.write("## 结果总览\n\n")
        f.write("| 市场周期 | 时间段 | Sharpe | 最大回撤 | 胜率 | 交易数 |\n")
        f.write("|:---------|:-------|:------:|:--------:|:----:|:------:|\n")
        for r in report['periods']:
            if 'error' not in r:
                f.write(f"| {r['period']} | - | {r.get('sharpe',0):.3f} | "
                        f"{r.get('max_drawdown',0):.1%} | {r.get('win_rate',0):.1%} | "
                        f"{r.get('trade_count',0)} |\n")
        f.write("\n## 综合评估\n\n")
        f.write(f"- **平均 Sharpe**: {s['avg_sharpe']:.3f}\n")
        f.write(f"- **平均胜率**: {s['avg_win_rate']:.1%}\n")
        f.write(f"- **鲁棒性**: {'通过' if s['is_robust'] else '不通过'}\n")
        if s['issues']:
            f.write("- **问题**:\n")
            for issue in s['issues']:
                f.write(f"  - {issue}\n")
    print(f"Markdown report saved to {md_path}")

    return report


if __name__ == "__main__":
    run_validation()
