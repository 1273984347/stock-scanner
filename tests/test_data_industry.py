import os
import sys
import types

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
    import lib.data_tushare as dt
    with patch.object(dt, "_FIN_ACCESS_DENIED", False), \
         patch("lib.data_tushare._get_pro", return_value=None):
        df = get_balancesheet("600519.SH")
    assert isinstance(df, pd.DataFrame) and df.empty


def test_get_income_passes_dates_through():
    from lib.data_tushare import get_income
    import lib.data_tushare as dt
    fake = _FakeProFinancial({
        "income": pd.DataFrame(
            {"ts_code": ["600519.SH"], "end_date": ["20231231"], "revenue": [1500.0]}
        )
    })
    with patch.object(dt, "_FIN_ACCESS_DENIED", False), \
         patch("lib.data_tushare._get_pro", return_value=fake):
        df = get_income("600519.SH", start_date="20200101", end_date="20231231")
    assert len(df) == 1
    assert df.iloc[0]["revenue"] == 1500.0


def test_get_pro_survives_token_cache_write_failure():
    """tk.csv 写入被拒（沙箱/只读环境）时 _get_pro 仍应返回可用 pro 实例"""
    import lib.data_tushare as dt

    calls = []

    class _TokenWriteDeniedTs:
        def set_token(self, token):
            calls.append(("set_token", token))
            raise PermissionError(13, "Permission denied: 'C:\\Users\\12739\\tk.csv'")

        def pro_api(self, token=""):
            calls.append(("pro_api", token))
            return _FakeProFinancial({})

    with patch.dict(sys.modules, {"tushare": _TokenWriteDeniedTs()}), \
         patch.object(dt, "_pro", None), \
         patch.dict(os.environ, {"TUSHARE_TOKEN": "test-token"}):
        pro = dt._get_pro()
        assert isinstance(pro, _FakeProFinancial)
        assert ("set_token", "test-token") in calls
        assert ("pro_api", "test-token") in calls   # set_token 失败后仍显式传 token 调用


def test_data_tushare_sets_denied_flag_on_permission_error():
    from lib.data_tushare import get_balancesheet
    import lib.data_tushare as dt

    class _DeniedPro:
        def balancesheet(self, **kwargs):
            raise RuntimeError("抱歉，您没有接口(balancesheet)访问权限，"
                               "权限的具体详情访问：https://tushare.pro/document/1?doc_id=108")

    with patch.object(dt, "_FIN_ACCESS_DENIED", False), \
         patch.object(dt, "_get_pro", return_value=_DeniedPro()):
        df = get_balancesheet("600519.SH")
        assert df.empty
        assert dt._FIN_ACCESS_DENIED is True


def test_akshare_fallback_produces_normalized_series():
    """Tushare 财报权限被拒 + datacenter 不可用时降级 akshare 东财逐期接口，输出归一化 YYYYMMDD 序列"""
    import tempfile
    from pathlib import Path
    import lib.data_industry as di
    import lib.data_tushare as dt

    # shift(4) 按行位置错位，要求连续季度序列 → 给 6 个连续报告期
    dates = pd.to_datetime(["2023-12-31", "2024-03-31", "2024-06-30",
                            "2024-09-30", "2024-12-31", "2025-03-31"])
    ak_bs = pd.DataFrame({"REPORT_DATE": dates, "INVENTORY": [90.0, 100.0, 105.0, 110.0, 120.0, 130.0],
                          "SHORT_LOAN": [10.0] * 6, "LONG_LOAN": [20.0] * 6,
                          "BOND_PAYABLE": [0.0] * 6})
    ak_inc = pd.DataFrame({"REPORT_DATE": dates,
                           "TOTAL_OPERATE_INCOME": [180.0, 200.0, 210.0, 220.0, 240.0, 260.0]})
    ak_cf = pd.DataFrame({"REPORT_DATE": dates,
                          "CONSTRUCT_LONG_ASSET": [30.0, 30.0, 30.0, 33.0, 33.0, 33.0]})
    seen_symbols = []

    def _rec(table):
        def _fn(symbol):
            seen_symbols.append(symbol)
            return table
        return _fn

    fake_ak = types.SimpleNamespace(
        stock_balance_sheet_by_report_em=_rec(ak_bs),
        stock_profit_sheet_by_report_em=_rec(ak_inc),
        stock_cash_flow_sheet_by_report_em=_rec(ak_cf))
    with patch.object(dt, "_FIN_ACCESS_DENIED", True), \
         patch.dict(sys.modules, {"akshare": fake_ak}), \
         patch.object(di, "_fetch_from_em_datacenter",
                      side_effect=RuntimeError("mock datacenter down")), \
         patch.object(di, "_TABLES_CACHE_DIR", Path(tempfile.mkdtemp())):
        s = di._fetch_stock_yoy_series("600000")
    assert seen_symbols == ["SH600000", "SH600000", "SH600000"]  # 东财 symbol 格式：市场前缀+6位代码
    assert not s.empty
    for col in ("inventory_yoy", "revenue_yoy", "capex_yoy", "debt_yoy"):
        assert col in s.columns
    latest = s.dropna(subset=["inventory_yoy"]).iloc[-1]
    assert latest["date"] == "20250331"          # 跨源聚合前提：与 Tushare 同款日期格式
    assert latest["inventory_yoy"] == pytest.approx(0.30)
    assert latest["revenue_yoy"] == pytest.approx(0.30)
    assert latest["capex_yoy"] == pytest.approx(0.10)
    assert latest["debt_yoy"] == pytest.approx(0.0)


def test_tushare_preferred_when_permission_available():
    """Tushare 权限正常且有数据时不触发 akshare"""
    import lib.data_industry as di
    import lib.data_tushare as dt

    ts_bs = pd.DataFrame({"ts_code": ["600000.SH"], "end_date": ["20231231"],
                          "inventory": [100.0], "st_borr": [10.0],
                          "lt_borr": [20.0], "bond_payable": [0.0]})
    ts_inc = pd.DataFrame({"ts_code": ["600000.SH"], "end_date": ["20231231"], "revenue": [200.0]})
    ts_cf = pd.DataFrame({"ts_code": ["600000.SH"], "end_date": ["20231231"],
                          "c_pay_acq_const_fiolta": [30.0]})

    def _boom(symbol):
        raise AssertionError("akshare 不应被调用")

    fake_ak = types.SimpleNamespace(
        stock_balance_sheet_by_report_em=_boom,
        stock_profit_sheet_by_report_em=_boom,
        stock_cash_flow_sheet_by_report_em=_boom,
    )
    fake_pro = _FakeProFinancial({
        "balancesheet": ts_bs, "income": ts_inc, "cashflow": ts_cf,
    })
    with patch.object(dt, "_FIN_ACCESS_DENIED", False), \
         patch.object(dt, "_get_pro", return_value=fake_pro), \
         patch.dict(sys.modules, {"akshare": fake_ak}):
        s = di._fetch_stock_yoy_series("600000")
    assert isinstance(s, pd.DataFrame)


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
