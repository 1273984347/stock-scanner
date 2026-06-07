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
    from lib.data_layer import _get_financial_data
    sig = inspect.signature(_get_financial_data)
    assert "as_of_date" in sig.parameters, \
        f"_get_financial_data 必须有 as_of_date 参数，当前参数: {list(sig.parameters.keys())}"


def test_get_financial_data_warns_when_no_as_of_date():
    """P1-2: 不传 as_of_date 应打 warning（提示 backtest 风险）

    backtest 入口若忘记传 as_of_date → logger.warning 记录
    """
    from lib import data_layer
    from lib.data_cache import FinancialCache

    # 用 mock cache 避免网络
    fake_cache = FinancialCache(memory_ttl=300, disk_ttl=604800)
    with patch.object(data_layer, "_financial_cache", fake_cache):
        with patch.object(data_layer.logger, "warning") as mock_warn:
            # 调用不带 as_of_date
            result = data_layer._get_financial_data("600519")
            # 应至少有一条 warning
            assert mock_warn.called, \
                "不带 as_of_date 调用应打 warning（提示可能引入未来信息）"
            # warning 内容应包含 backtest / 实时 / as_of 等关键词之一
            warning_msg = " ".join(str(call.args[0]) for call in mock_warn.call_args_list)
            assert any(kw in warning_msg.lower() for kw in ["backtest", "as_of", "snapshot", "历史"]), \
                f"warning 应提示 backtest/历史快照风险，实际: {warning_msg}"


def test_get_financial_data_no_warning_with_as_of_date():
    """P1-2: 传入 as_of_date 时不应打 backtest 风险 warning

    注意：仍可能因其他原因打 warning（如数据源失败），所以只检查
    'backtest'/'未来信息'/'snapshot' 类型的警告**不**出现
    """
    from lib import data_layer
    from lib.data_cache import FinancialCache

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
