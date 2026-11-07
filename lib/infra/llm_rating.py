"""评分解析工具

将 TradingAgents 的 5-tier 评分解析迁移到 stock-scanner：
- Buy / Overweight / Hold / Underweight / Sell
- 支持从自然语言文本中提取评分

参考: TradingAgents/tradingagents/agents/utils/rating.py
"""
from __future__ import annotations

import re
from typing import Tuple


# 标准 5-tier 评分（从看涨到看跌）
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# 匹配 "Rating: X" / "rating - X" / "Rating: **X**" 等格式
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)


def parse_rating(text: str, default: str = "Hold") -> str:
    """从文本中启发式提取 5-tier 评分。

    两遍策略：
    1. 查找显式的 "Rating: X" 标签（容忍 markdown 粗体）
    2. 回退到文本中任意位置的第一个 5-tier 评分词

    Returns:
        Title-case 评分字符串，未找到时返回 default
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default
