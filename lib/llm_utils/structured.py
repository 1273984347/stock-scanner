"""LLM 结构化输出降级工具

将 TradingAgents 的结构化输出降级机制迁移到 stock-scanner：
1. Provider 不支持 with_structured_output 时自动降级
2. 调用失败时自动回退到自由文本生成

参考: TradingAgents/tradingagents/agents/utils/structured.py
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def bind_structured(llm: Any, schema: type[T], agent_name: str) -> Optional[Any]:
    """返回 ``llm.with_structured_output(schema)``，不支持时返回 None。

    当绑定失败时记录警告，让用户理解 agent 将使用自由文本生成。
    """
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s: provider 不支持 with_structured_output (%s); "
            "将使用自由文本生成",
            agent_name, exc,
        )
        return None


def invoke_structured_or_freetext(
    structured_llm: Optional[Any],
    plain_llm: Any,
    prompt: Any,
    render: Callable[[T], str],
    agent_name: str,
) -> str:
    """执行结构化调用并渲染为 markdown；任何失败时回退到自由文本。

    ``prompt`` 可以是字符串或消息字典列表，取决于底层 LLM。
    回退时使用相同的输入，确保结构化和自由文本路径行为一致。
    """
    if structured_llm is not None:
        try:
            result = structured_llm.invoke(prompt)
            return render(result)
        except Exception as exc:
            logger.warning(
                "%s: 结构化输出调用失败 (%s); 将重试为自由文本",
                agent_name, exc,
            )

    response = plain_llm.invoke(prompt)
    return response.content
