"""LLM 工具模块

提供 LLM 调用相关的工具函数：
- structured: 结构化输出降级机制
"""
from lib.llm_utils.structured import bind_structured, invoke_structured_or_freetext

__all__ = [
    "bind_structured",
    "invoke_structured_or_freetext",
]
