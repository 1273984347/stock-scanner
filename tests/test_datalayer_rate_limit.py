"""akshare 限流 HTML 返回检测 + 翻倍 backoff (2026-06-09)

修复前: 4 个 _retry_api_call 调用 (ak.stock_zh_a_spot 等) 在 akshare 限流
返 HTML 时, 与真实错误用相同 backoff, 限流恢复前 retry 撞死。

修复后: _is_rate_limit_error() 区分限流 (HTML/JSON decode 失败) vs 真实错误,
限流用 2x backoff (翻倍), 真实错误用 1x backoff (正常指数)。

覆盖:
- _is_rate_limit_error 5 个用例: JSONDecodeError / ValueError-with-decode /
  TimeoutError / KeyError / RuntimeError
- _retry_api_call 行为: rate limit 命中 → 翻倍 backoff, 真实错误 → 正常 backoff
- 端到端: mock func 第一次抛限流, 第二次返 success → 验证只 retry 1 次
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import json
from unittest.mock import patch, MagicMock

import pytest

from lib import data_layer


# ─────────────────────────────────────────────────────────────
# _is_rate_limit_error 单元测试 (5 用例)
# ─────────────────────────────────────────────────────────────

def test_rate_limit_json_decode_error_true():
    """json.JSONDecodeError → True (限流)"""
    try:
        json.loads("<html>rate limit warning</html>")
    except json.JSONDecodeError as e:
        assert data_layer._is_rate_limit_error(e) is True


def test_rate_limit_value_error_can_not_decode_true():
    """ValueError 含 'Can not decode' → True (pandas 包装)"""
    exc = ValueError("Can not decode value starting with character '<'")
    assert data_layer._is_rate_limit_error(exc) is True


def test_rate_limit_value_error_expecting_value_true():
    """ValueError 含 'Expecting value' → True"""
    exc = ValueError("Expecting value: line 1 column 1 (char 0)")
    assert data_layer._is_rate_limit_error(exc) is True


def test_real_error_timeout_false():
    """TimeoutError → False (真实错误, 不算限流)"""
    exc = TimeoutError("Connection timed out after 30s")
    assert data_layer._is_rate_limit_error(exc) is False


def test_real_error_runtime_no_decode_false():
    """RuntimeError 无 'Can not decode' 消息 → False"""
    exc = RuntimeError("akshare internal error: code=500")
    assert data_layer._is_rate_limit_error(exc) is False


# ─────────────────────────────────────────────────────────────
# _retry_api_call 行为测试 (3 用例)
# ─────────────────────────────────────────────────────────────

def test_retry_call_succeeds_first_try():
    """func 第一次成功 → 不 retry"""
    mock_func = MagicMock(return_value="ok")
    result = data_layer._retry_api_call(mock_func, "arg1")
    assert result == "ok"
    assert mock_func.call_count == 1


def test_retry_call_rate_limit_doubles_backoff():
    """func 抛 JSONDecodeError → 应 retry + 翻倍 backoff (打 info log)"""
    call_count = [0]

    def mock_func():
        call_count[0] += 1
        if call_count[0] == 1:
            # 第一次抛限流错误
            try:
                json.loads("<html>blocked</html>")
            except json.JSONDecodeError:
                raise
        return "ok_after_retry"

    with patch("lib.data_layer.time.sleep") as mock_sleep, \
         patch("lib.data_layer.API_RETRY_BASE_WAIT_S", 1), \
         patch("lib.data_layer.logger") as mock_logger:  # mock logger 直接验证 info 调用
        result = data_layer._retry_api_call(mock_func)

    assert result == "ok_after_retry"
    assert call_count[0] == 2  # 失败 1 次 + 成功 1 次
    # mock_sleep 收到多次调用: rate_limiter.acquire() + retry wait
    # 取最后一次 (retry wait): 限流 backoff = 1 * 2^(0+1) = 2
    all_sleeps = [call.args[0] for call in mock_sleep.call_args_list]
    assert 2 in all_sleeps, f"retry wait 应为 2 (rate limit 翻倍), 实际 sleeps: {all_sleeps}"
    # 验证 logger.info 被调用且消息含 "API 限流"
    info_calls = [call.args[0] for call in mock_logger.info.call_args_list]
    assert any("API 限流" in m for m in info_calls), \
        f"应打 'API 限流' info log, 实际 info 调用: {info_calls}"


def test_retry_call_real_error_normal_backoff():
    """func 抛 RuntimeError → 应 retry + 正常 backoff (打 debug log)"""
    call_count = [0]

    def mock_func():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("real akshare error, not rate limit")
        return "ok"

    with patch("lib.data_layer.time.sleep") as mock_sleep, \
         patch("lib.data_layer.API_RETRY_BASE_WAIT_S", 1):
        result = data_layer._retry_api_call(mock_func)

    assert result == "ok"
    assert call_count[0] == 2
    # 真实错误 backoff: 1 * (0+1) = 1 秒 (正常指数, 不是 2x)
    all_sleeps = [call.args[0] for call in mock_sleep.call_args_list]
    assert 1 in all_sleeps, f"retry wait 应为 1 (正常), 实际 sleeps: {all_sleeps}"


def test_retry_call_exhausted_max_retries():
    """func 持续失败 → 应 retry max_retries 次后 raise"""
    def mock_func():
        raise ValueError("Can not decode value starting with '<'")  # 限流

    with patch("lib.data_layer.time.sleep"), \
         patch("lib.data_layer.API_RETRY_MAX_ATTEMPTS", 2), \
         patch("lib.data_layer.API_RETRY_BASE_WAIT_S", 0.01):
        with pytest.raises(ValueError) as excinfo:
            data_layer._retry_api_call(mock_func)

    assert "Can not decode" in str(excinfo.value)


# ─────────────────────────────────────────────────────────────
# 端到端: get_realtime_quotes 在 akshare 限流时优雅降级
# ─────────────────────────────────────────────────────────────

def test_get_realtime_quotes_handles_akshare_rate_limit():
    """akshare 限流 → TDX 也失败 → 返空 DataFrame (不 raise)"""
    import pandas as pd
    # 跳过缓存
    with patch.object(data_layer, "_load_cache", return_value=pd.DataFrame()):
        # 强制 akshare 抛限流错误
        with patch("lib.data_layer.ak.stock_zh_a_spot", side_effect=ValueError("Can not decode value starting with '<'")):
            # TDX 走 except 分支, 让 _retry_api_call 也命中限流模式
            with patch("lib.data_layer.ak.stock_individual_info_em", side_effect=ValueError("Can not decode")):
                # 整个 TDX 流程 mock 掉, 让它走 fail 分支
                with patch.object(data_layer, "REALTIME_SAMPLE_CODES", []):
                    result = data_layer.get_realtime_quotes()
    # 关键: 不 raise, 返空 DataFrame
    assert isinstance(result, pd.DataFrame), f"应返 DataFrame, 实际 {type(result)}"
    assert result.empty, "限流时应返空 DataFrame (graceful degradation)"
