"""每日报告格式化 -- 生成富文本飞书推送内容"""

import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def format_daily_report(report, eval_result: dict = None,
                        market_state: dict = None,
                        sector_scores: dict = None) -> str:
    """格式化每日报告

    Args:
        report: PaperTradingRunner 的 DailyReport 对象
        eval_result: 信号评估结果（来自 SignalTracker.evaluate_signals）
        market_state: 市场状态（sentiment, macro_gate, exposure）
        sector_scores: 板块评分 {sector_name: score}

    Returns:
        Markdown 格式报告
    """
    lines = [
        f"## \U0001f4ca 量化日报 {report.date}",
        "",
        f"### \U0001f4b0 账户概况",
        f"| 指标 | 值 |",
        f"|------|-----|",
        f"| 总资产 | {report.total_value:,.0f} |",
        f"| 累计收益 | {report.cumulative_return:.2%} |",
        f"| 最大回撤 | {report.max_drawdown:.2%} |",
        f"| 胜率 | {report.win_rate:.1%} |",
        f"| 今日交易 | {report.trades_today} 笔 |",
        "",
    ]

    # 市场状态
    if market_state:
        sentiment = market_state.get("sentiment", 0)
        macro = market_state.get("macro_gate", {})
        exposure = market_state.get("exposure", {})

        emoji = "\U0001f7e2" if sentiment > 0.3 else "\U0001f534" if sentiment < -0.3 else "\U0001f7e1"
        lines.extend([
            f"### {emoji} 市场状态",
            f"- 情绪指数: **{sentiment:.2f}**",
            f"- 宏观门控: **{macro.get('state', '?')}** ({macro.get('regime', '?')})",
            f"- 建议仓位: **{exposure.get('exposure', 0):.0%}**",
            "",
        ])

    # 板块评分
    if sector_scores:
        sorted_sectors = sorted(sector_scores.items(), key=lambda x: x[1], reverse=True)
        lines.extend([
            f"### \U0001f4c8 板块评分（Top 5）",
            "| 板块 | 评分 |",
            "|------|------|",
        ])
        for name, score in sorted_sectors[:5]:
            bar = "█" * max(1, int(score * 10))
            lines.append(f"| {name} | {bar} {score:.2f} |")
        lines.append("")

    # 信号列表（按置信度排序）
    if report.signals:
        sorted_signals = sorted(report.signals, key=lambda s: s.get("confidence", 0), reverse=True)
        lines.extend([
            f"### \U0001f4e1 今日信号（{len(sorted_signals)} 条）",
            "| 代码 | 信号 | 置信度 | IC分数 |",
            "|------|------|--------|--------|",
        ])
        for s in sorted_signals[:10]:
            code = s.get("code", "?")
            signal = s.get("signal", "?")
            conf = s.get("confidence", 0)
            ic = s.get("ic_score", 0)
            signal_emoji = "\U0001f4c8" if signal == "bullish" else "\U0001f4c9" if signal == "bearish" else "➡️"
            lines.append(f"| {code} | {signal_emoji} {signal} | {conf:.2f} | {ic:.3f} |")
        if len(sorted_signals) > 10:
            lines.append(f"| ... | 共 {len(sorted_signals)} 条 | | |")
        lines.append("")

    # 信号评估
    if eval_result:
        lines.extend([
            f"### \U0001f4c8 信号质量（近30天）",
            f"- 总信号: {eval_result.get('total_signals', 0)}",
            f"- 准确率: **{eval_result.get('accuracy', 0):.1%}**",
            f"- 盈亏比: **{eval_result.get('profit_loss_ratio', 0):.2f}**",
            f"- 高置信度准确率: {eval_result.get('high_confidence_accuracy', 0):.1%}",
            "",
        ])

    # 持仓
    if report.positions:
        lines.extend([
            f"### \U0001f4e6 当前持仓（{len(report.positions)} 只）",
            "| 代码 | 股数 | 成本 | 盈亏 |",
            "|------|------|------|------|",
        ])
        for p in report.positions[:5]:
            code = p.get("code", "?")
            shares = p.get("shares", 0)
            cost = p.get("cost", 0)
            pnl = p.get("pnl_pct", 0)
            pnl_emoji = "\U0001f4c8" if pnl > 0 else "\U0001f4c9"
            lines.append(f"| {code} | {shares} | {cost:.2f} | {pnl_emoji} {pnl:.2%} |")
        if len(report.positions) > 5:
            lines.append(f"| ... | 共 {len(report.positions)} 只 | | |")
        lines.append("")

    lines.append(f"---")
    lines.append(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    return "\n".join(lines)
