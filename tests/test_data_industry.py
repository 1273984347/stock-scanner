import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch


class _FakeProFinancial:
    """只支持 balancesheet/income/cashflow 三个表的假 pro 对象"""

    def __init__(self, tables: dict):
        self._tables = tables

    def __getattr__(self, name):
        if name in ("balancesheet", "income", "cashflow"):
            def _call(**kwargs):
                return self._tables[name]
            return _call
        raise AttributeError(name)


def test_get_balancesheet_returns_empty_df_when_pro_unavailable():
    from lib.data_tushare import get_balancesheet
    with patch("lib.data_tushare._get_pro", return_value=None):
        df = get_balancesheet("600519.SH")
    assert isinstance(df, pd.DataFrame) and df.empty


def test_get_income_passes_dates_through():
    from lib.data_tushare import get_income
    fake = _FakeProFinancial({
        "income": pd.DataFrame(
            {"ts_code": ["600519.SH"], "end_date": ["20231231"], "revenue": [1500.0]}
        )
    })
    with patch("lib.data_tushare._get_pro", return_value=fake):
        df = get_income("600519.SH", start_date="20200101", end_date="20231231")
    assert len(df) == 1
    assert df.iloc[0]["revenue"] == 1500.0


def test_fetch_block_financial_series_median_aggregation_and_cache(tmp_path):
    import lib.data_industry as di
    # 两只股票：inventory_yoy 分别为 0.10 与 0.20 → 中位数 0.15
    fake_series = {
        "600000": pd.DataFrame({
            "date": ["20231231"], "inventory_yoy": [0.10],
            "revenue_yoy": [0.10], "capex_yoy": [0.10], "debt_yoy": [0.10],
        }),
        "600001": pd.DataFrame({
            "date": ["20231231"], "inventory_yoy": [0.20],
            "revenue_yoy": [0.20], "capex_yoy": [0.20], "debt_yoy": [0.20],
        }),
    }
    with patch.object(di, "CACHE_DIR", Path(tmp_path) / "industry"), \
         patch.object(di, "get_all_blocks",
                      return_value={"测试板块": ["600000", "600001"]}), \
         patch.object(di, "_fetch_stock_yoy_series",
                      side_effect=lambda code: fake_series.get(code)):
        df = di.fetch_block_financial_series("测试板块")
    assert df is not None and len(df) == 1
    assert df.iloc[0]["inventory_yoy"] == pytest.approx(0.15)
    assert (Path(tmp_path) / "industry" / "测试板块.parquet").exists()
