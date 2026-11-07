"""基础设施层 —— API 横切层/缓存/通用工具/市场检测/LLM 工具。

LLM 工具子模块：
- structured: 结构化输出降级机制
- rating: 5-tier 评分解析
- memory: 交易决策日志 + 反思学习
"""
from lib.infra.llm_structured import bind_structured, invoke_structured_or_freetext
from lib.infra.llm_rating import parse_rating, RATINGS_5_TIER
from lib.infra.llm_memory import TradingMemoryLog

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
