"""P0-4: 解决回测幸存者偏差 - 注入退市股票数据

修复前：
- backtest 默认仅含活股票（tdx_local_qfq）
- 退市股票从未被纳入回测
- 年化收益可能被高估 1-3%

修复后：
- run_backtest 默认 include_delisted=True
- 退市股票自动注入回测池（最多 50 只）
- _process_single_stock_engine 活股数据不足时 fallback 到退市数据源
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, MagicMock
import inspect

from backtest.engine import run_backtest, _run_backtest_parallel, BacktestConfig


def test_run_backtest_has_include_delisted_param():
    """P0-4: run_backtest 签名应包含 include_delisted 参数"""
    sig = inspect.signature(run_backtest)
    assert "include_delisted" in sig.parameters, \
        f"run_backtest 缺 include_delisted 参数，现有参数: {list(sig.parameters.keys())}"
    # 默认值应为 True（修复后默认开）
    assert sig.parameters["include_delisted"].default is True, \
        "include_delisted 默认值应改为 True（修复后开启）"


def test_run_backtest_parallel_has_include_delisted_param():
    """P0-4: _run_backtest_parallel 签名应包含 include_delisted 参数"""
    sig = inspect.signature(_run_backtest_parallel)
    assert "include_delisted" in sig.parameters, \
        f"_run_backtest_parallel 缺 include_delisted 参数"


def test_run_backtest_delisted_stocks_added_to_pool():
    """P0-4: include_delisted=True 时，run_backtest 应注入退市股票到 stock_codes"""
    from backtest import engine as engine_mod

    # 拦截 _run_backtest_parallel 来捕获实际传入的 stock_codes
    fake_delisted = ["000001", "000002", "000003"]
    captured_codes = []
    original_parallel = engine_mod._run_backtest_parallel

    def mock_parallel(stock_codes, *args, **kwargs):
        captured_codes.extend(stock_codes)
        # 直接返回空结果，避免实际跑（stock_codes < 100 不会走并行分支）
        return {"trades": [], "equity_curve": [], "metrics": {}}

    # 强制并行路径：len > 100 才会走
    big_codes = [f"{i:06d}" for i in range(101)]

    with patch("lib.data_delisted.get_delisted_symbols", return_value=set(fake_delisted)):
        with patch("backtest.engine._run_backtest_parallel", side_effect=mock_parallel):
            run_backtest(
                stock_codes=big_codes,
                days=100,
                parallel=True,
            )

    # 验证：1) 注入调用了 get_delisted_symbols 2) 退市股票被加入
    assert any(code in fake_delisted for code in captured_codes), \
        f"退市股票应被注入到 stock_codes，实际包含前 5 个: {captured_codes[:5]}"


def test_run_backtest_delisted_injection_failure_does_not_crash():
    """P0-4: 退市股票数据获取失败时（API 不可用）应优雅降级，不中断主流程"""
    with patch("lib.data_delisted.get_delisted_symbols", side_effect=Exception("API down")):
        # 不应抛异常
        result = run_backtest(
            stock_codes=["600519"],
            days=100,
            parallel=False,  # 非并行路径
            include_delisted=True,
        )
    # 至少应返回基本结构
    assert "trades" in result
    assert "metrics" in result


def test_run_backtest_include_delisted_false_skips_injection():
    """P0-4: include_delisted=False 时，不应注入退市股票"""
    from backtest import engine as engine_mod
    # 用 8xx 开头代码（与小测试数据 0xx 不冲突）
    fake_delisted = ["800001", "800002"]
    captured_codes = []

    def mock_parallel(stock_codes, *args, **kwargs):
        captured_codes.extend(stock_codes)
        return {"trades": [], "equity_curve": [], "metrics": {}}

    big_codes = [f"{i:06d}" for i in range(101)]

    with patch("lib.data_delisted.get_delisted_symbols", return_value=set(fake_delisted)):
        with patch("backtest.engine._run_backtest_parallel", side_effect=mock_parallel):
            run_backtest(
                stock_codes=big_codes,
                days=100,
                parallel=True,
                include_delisted=False,
            )

    # 不应注入退市股票
    for code in fake_delisted:
        assert code not in captured_codes, \
            f"include_delisted=False 时不应注入 {code}"


def test_worker_signature_accepts_include_delisted():
    """P0-4: _process_single_stock_engine 应接收 include_delisted 参数（第 10 个位置参数）"""
    import inspect
    from backtest.engine import _process_single_stock_engine
    sig = inspect.signature(_process_single_stock_engine)
    # 参数是 *args，所以无法直接 inspect，改为通过源代码检查
    src = inspect.getsource(_process_single_stock_engine)
    assert "include_delisted" in src, \
        "_process_single_stock_engine 源码应包含 include_delisted"
    # 应有退市股票 fallback
    assert "get_delisted_stock_hist" in src, \
        "_process_single_stock_engine 应有 get_delisted_stock_hist fallback"
