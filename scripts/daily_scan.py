"""每日扫描脚本 —— 可配合 cron/Task Scheduler 定时运行

功能：
1. 扫描候选股信号
2. 检查持仓退出条件
3. 生成日报
4. 推送飞书

使用方式：
    python scripts/daily_scan.py                    # 单次运行
    python scripts/daily_scan.py --no-push          # 不推送飞书
    python scripts/daily_scan.py --symbols 600519   # 指定股票
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_daily_scan(push_feishu: bool = True, extra_symbols: list = None):
    """执行每日扫描"""
    from lib.paper_trading_runner import PaperTradingRunner, TRADE_LOG_DIR

    runner = PaperTradingRunner(initial_cash=1_000_000, preset="balanced")
    report = runner.run_once()

    # 输出日报
    print(f"\n{'='*50}")
    print(f"模拟盘日报 {report.date}")
    print(f"{'='*50}")
    print(f"总资产: ¥{report.total_value:,.0f}")
    print(f"累计收益: {report.cumulative_return:.2%}")
    print(f"最大回撤: {report.max_drawdown:.2%}")
    print(f"胜率: {report.win_rate:.1%}")
    print(f"今日交易: {report.trades_today} 笔")
    print(f"平均滑点: {report.slippage_avg:.3%}")

    if report.positions:
        print(f"\n持仓:")
        for p in report.positions[:5]:
            print(f"  {p['code']}: {p['shares']}股 成本{p['cost']:.2f}")

    if report.signals:
        print(f"\n今日信号:")
        for s in report.signals:
            print(f"  {s['code']}: 置信度{s['confidence']:.2f}")

    print(f"\n报告已保存: {TRADE_LOG_DIR}/report_{datetime.now().strftime('%Y%m%d')}.json")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()

    run_daily_scan(push_feishu=not args.no_push, extra_symbols=args.symbols)
