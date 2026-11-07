"""P1-2: _get_financial_data 防止 backtest 误用实时快照

修复前：_get_financial_data() 实时拉腾讯 PE/PB/市值（当前快照）
       → backtest 训练时调用会引入未来信息（look-ahead bias）

修复后：增加 as_of_date 参数
- 传入 as_of_date（历史日期）→ 数据源应回溯 T-1 日快照
- 不传 as_of_date → 警告，提示仅用于实时扫描，禁止 backtest 使用
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

import inspect
from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest


def test_get_financial_data_has_as_of_date_param():
    """P1-2: _get_financial_data 必须接受 as_of_date 参数"""
    from lib.data.data_layer import _get_financial_data
    sig = inspect.signature(_get_financial_data)
    assert "as_of_date" in sig.parameters, \
        f"_get_financial_data 必须有 as_of_date 参数，当前参数: {list(sig.parameters.keys())}"


def test_get_financial_data_raises_when_no_as_of_date():
    """P0 #1 修复 (2026-06-08): 不传 as_of_date 应 raise NotImplementedError

    旧 P1-2 行为是 warning (但被忽略导致 backtest 误用)
    新 P0 #1 行为: 硬 raise, 防止误用, 强制 caller 显式传 as_of_date 或改用 get_stock_fundamentals_lite()
    """
    import lib.data.data_layer as data_layer
    from lib.data.data_cache import FinancialCache

    # 用 mock cache 避免网络
    fake_cache = FinancialCache(memory_ttl=300, disk_ttl=604800)
    with patch.object(data_layer, "_financial_cache", fake_cache):
        # 不传 as_of_date → 应 raise
        with pytest.raises(NotImplementedError) as excinfo:
            data_layer._get_financial_data("600519")
        # error msg 应说明 as_of_date 要求
        assert "as_of_date" in str(excinfo.value), \
            f"raise msg 应说明 as_of_date 要求，实际: {excinfo.value}"
        assert "backtest" in str(excinfo.value).lower() or "前视" in str(excinfo.value) or \
               "实时" in str(excinfo.value), \
            f"raise msg 应说明回测/前视偏差风险，实际: {excinfo.value}"


def test_get_financial_data_no_warning_with_as_of_date():
    """P1-2: 传入 as_of_date 时不应打 backtest 风险 warning

    注意：仍可能因其他原因打 warning（如数据源失败），所以只检查
    'backtest'/'未来信息'/'snapshot' 类型的警告**不**出现
    """
    import lib.data.data_layer as data_layer
    from lib.data.data_cache import FinancialCache

    fake_cache = FinancialCache(memory_ttl=300, disk_ttl=604800)
    with patch.object(data_layer, "_financial_cache", fake_cache):
        with patch.object(data_layer.logger, "warning") as mock_warn:
            # 调用带 as_of_date
            data_layer._get_financial_data("600519", as_of_date="2024-06-15")

            # 检查是否有"backtest"或"look-ahead"相关 warning
            for call in mock_warn.call_args_list:
                msg = str(call.args[0])
                assert "backtest" not in msg.lower() or "as_of" in msg.lower(), \
                    f"传 as_of_date 后不应再打 backtest 风险 warning: {msg}"
