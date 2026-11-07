"""多风格投票决策 + past_context 注入 (Borrow 1 production caller)

封装 AgentPanel.vote() + SignalTracker past_context, 供 daily_scan.py / api.py 调用.
"""
import logging
from typing import Dict, Optional

import pandas as pd

from lib.scoring.investor_agents import AgentPanel
from lib.scoring.signal_tracker import get_cached_tracker, build_past_context

logger = logging.getLogger(__name__)

_panel = AgentPanel()


def get_panel_decision(
    code: str,
    hist_df: pd.DataFrame,
    data_factors: Dict = None,
    as_of_date: str = None,
) -> Dict:
    """对单股做多风格投票 + past_context 注入.

    Returns:
        dict with keys: signal, confidence, votes, consensus, bullish_pct, bearish_pct, past_context (optional)
    """
    result = _panel.vote(hist_df, code=code, data_factors=data_factors, as_of_date=as_of_date)
    # past_context 已由 AgentPanel._inject_past_context 注入 (如果 code 不为 None)
    return result


def format_panel_decision(result: Dict, code: str) -> str:
    """格式化 panel 决策为可读文本 (日报 / 飞书推送用)."""
    lines = [f"## {code} 多风格投票"]
    lines.append(f"- 信号: {result['signal']}")
    lines.append(f"- 置信度: {result['confidence']:.1%}")
    lines.append(f"- 共识: {result['consensus']}")
    lines.append(f"- 看多: {result['bullish_pct']:.1%} / 看空: {result['bearish_pct']:.1%}")
    if "past_context" in result:
        lines.append(f"\n### 历史反思\n{result['past_context']}")
    return "\n".join(lines)