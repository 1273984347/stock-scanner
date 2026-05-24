"""LLM 工具模块

提供 LLM 调用相关的工具函数：
- structured: 结构化输出降级机制
- rating: 5-tier 评分解析
- memory: 交易决策日志 + 反思学习
"""
from lib.llm_utils.structured import bind_structured, invoke_structured_or_freetext
from lib.llm_utils.rating import parse_rating, RATINGS_5_TIER
from lib.llm_utils.memory import TradingMemoryLog

__all__ = [
    # 结构化输出
    "bind_structured",
    "invoke_structured_or_freetext",
    # 评分解析
    "parse_rating",
    "RATINGS_5_TIER",
    # 决策日志
    "TradingMemoryLog",
]
