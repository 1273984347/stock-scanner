"""tests/test_llm_utils.py - structured.py 降级机制测试"""
import pytest
from unittest.mock import MagicMock, patch
from pydantic import BaseModel

from lib.infra.llm_structured import bind_structured, invoke_structured_or_freetext


class MockSchema(BaseModel):
    """测试用 Schema"""
    name: str
    score: float


class TestBindStructured:
    """bind_structured 测试"""

    def test_bind_structured_success(self):
        """正常绑定返回 with_structured_output 结果"""
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured

        result = bind_structured(mock_llm, MockSchema, "test_agent")

        assert result is mock_structured
        mock_llm.with_structured_output.assert_called_once_with(MockSchema)

    def test_bind_structured_unsupported(self):
        """Provider 不支持时返回 None"""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.side_effect = NotImplementedError("不支持")

        result = bind_structured(mock_llm, MockSchema, "test_agent")

        assert result is None

    def test_bind_structured_attribute_error(self):
        """Provider 不支持时返回 None（AttributeError）"""
        mock_llm = MagicMock()
        mock_llm.with_structured_output.side_effect = AttributeError("无此方法")

        result = bind_structured(mock_llm, MockSchema, "test_agent")

        assert result is None


class TestInvokeStructuredOrFreetext:
    """invoke_structured_or_freetext 测试"""

    def test_invoke_structured_success(self):
        """结构化调用成功时返回渲染结果"""
        mock_structured = MagicMock()
        mock_plain = MagicMock()
        mock_structured.invoke.return_value = MockSchema(name="test", score=0.85)
        render_fn = lambda x: f"{x.name}: {x.score}"

        result = invoke_structured_or_freetext(
            mock_structured, mock_plain, "prompt", render_fn, "test_agent"
        )

        assert result == "test: 0.85"
        mock_plain.invoke.assert_not_called()

    def test_invoke_structured_fallback_on_exception(self):
        """结构化调用失败时回退到自由文本"""
        mock_structured = MagicMock()
        mock_plain = MagicMock()
        mock_structured.invoke.side_effect = Exception("结构化失败")
        # mock_plain.invoke() 返回的 MagicMock 的 .content 需要设置
        mock_plain.invoke.return_value.content = "free text response"
        render_fn = lambda x: str(x)

        result = invoke_structured_or_freetext(
            mock_structured, mock_plain, "prompt", render_fn, "test_agent"
        )

        assert result == "free text response"
        mock_plain.invoke.assert_called_once_with("prompt")

    def test_invoke_no_structured(self):
        """structured_llm 为 None 时直接调用自由文本"""
        mock_plain = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "plain response"
        mock_plain.invoke.return_value = mock_response

        result = invoke_structured_or_freetext(
            None, mock_plain, "test prompt", lambda x: str(x), "test_agent"
        )

        assert result == "plain response"
        mock_plain.invoke.assert_called_once_with("test prompt")
