import pandas as pd
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
