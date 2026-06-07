"""P1-3: lib/strategies.py _batch_get_stock_hist cutoff date 修复测试

修复前: 始终用实时数据, 回测会泄漏"今天盘中"数据
修复后: 接受 as_of_date, 过滤掉 > as_of_date 的数据
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect
import pandas as pd
from unittest.mock import patch


def test_p1_3_batch_get_stock_hist_accepts_as_of_date():
    """P1-3: _batch_get_stock_hist 应接受 as_of_date 参数"""
    from lib.strategies import _batch_get_stock_hist
    sig = inspect.signature(_batch_get_stock_hist)
    assert "as_of_date" in sig.parameters, (
        f"P1-3 修复未完成: _batch_get_stock_hist 必须接受 as_of_date 参数，"
        f"实际签名: {sig}"
    )


def test_p1_3_batch_get_stock_hist_filters_future_data():
    """P1-3: 传 as_of_date 后, 返回 DataFrame 不含 > as_of_date 的数据"""
    from lib import strategies

    def fake_hist(code, days, source):
        # 2024-09-01 ~ 2025-01-15: 含 2024-12-01 之后的"未来"数据
        dates = pd.date_range("2024-09-01", "2025-01-15", freq="D")
        return pd.DataFrame({
            "date": dates,
            "open": 100.0, "high": 101.0, "low": 99.0,
            "close": 100.0, "volume": 1e6,
        })

    with patch.object(strategies, "get_stock_hist_multi_source", fake_hist):
        result = strategies._batch_get_stock_hist(
            ["000001"], days=180, as_of_date="2024-12-01"
        )

    assert "000001" in result
    df = result["000001"]
    cutoff = pd.Timestamp("2024-12-01")
    date_col = next((c for c in ("trade_date", "date", "日期") if c in df.columns), None)
    assert date_col is not None, f"DataFrame 缺少日期列: {df.columns}"
    future_data = df[pd.to_datetime(df[date_col]) > cutoff]
    assert len(future_data) == 0, (
        f"P1-3 修复未完成: as_of_date 未生效，仍有 {len(future_data)} 行 > 2024-12-01。"
    )


def test_p1_3_get_volume_breakout_candidates_accepts_as_of_date():
    """P1-3: get_volume_breakout_candidates 签名应接受 as_of_date"""
    from lib.strategies import get_volume_breakout_candidates
    sig = inspect.signature(get_volume_breakout_candidates)
    assert "as_of_date" in sig.parameters, (
        f"P1-3 修复未完成: get_volume_breakout_candidates 必须接受 as_of_date 参数，"
        f"实际签名: {sig}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
