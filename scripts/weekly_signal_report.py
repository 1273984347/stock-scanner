"""每周信号准确率报告 —— 推送飞书

使用方式：
    python scripts/weekly_signal_report.py           # 最近 7 天
    python scripts/weekly_signal_report.py --days 14  # 最近 14 天
    python scripts/weekly_signal_report.py --no-push  # 只打印不推送
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def format_report(eval_result: dict, days: int) -> str:
    """格式化信号评估结果为 Markdown"""
    lines = [
        f"## 信号准确率报告（最近 {days} 天）",
        "",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总信号数 | {eval_result['total_signals']} |",
        f"| 已验证 | {eval_result.get('evaluated', 0)} |",
        f"| 准确率 | {eval_result['accuracy']:.1%} |",
        f"| 平均 5 日收益 | {eval_result['avg_return_5d']:.2%} |",
        f"| 盈亏比 | {eval_result['profit_loss_ratio']:.2f} |",
        f"| 高置信度准确率 | {eval_result.get('high_confidence_accuracy', 0):.1%} |",
        f"| 看多信号 | {eval_result.get('bullish_count', 0)} |",
        f"| 看空信号 | {eval_result.get('bearish_count', 0)} |",
        "",
        f"报告生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="每周信号准确率报告")
    parser.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    parser.add_argument("--no-push", action="store_true", help="只打印不推送飞书")
    args = parser.parse_args()

    from lib.signal_tracker import SignalTracker

    tracker = SignalTracker()
    tracker.update_actual_returns()
    eval_result = tracker.evaluate_signals(days=args.days)

    report_text = format_report(eval_result, args.days)
    print(report_text)

    if not args.no_push:
        try:
            from reporter.feishu_pusher import push_report
            success = push_report(report_text, [])
            if success:
                logger.info("飞书推送成功")
            else:
                logger.warning("飞书推送失败")
        except Exception as e:
            logger.error(f"飞书推送异常: {e}")


if __name__ == "__main__":
    main()
