"""数据层单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import numpy as np
import pandas as pd


class TestNormalizeColumns:
    def test_vol_renamed_to_volume(self):
        """vol 列统一为 volume"""
        from lib.data.data_layer import _normalize_columns
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=10),
            "open": [100] * 10, "high": [105] * 10,
            "low": [95] * 10, "close": [102] * 10,
            "vol": [1e6] * 10,
        })
        result = _normalize_columns(df)
        assert "volume" in result.columns
        assert "vol" not in result.columns or "volume" in result.columns

    def test_chinese_columns_renamed(self):
        """中文列名统一为英文"""
        from lib.data.data_layer import _normalize_columns
        df = pd.DataFrame({
            "日期": pd.date_range("2026-01-01", periods=10),
            "开盘": [100] * 10, "收盘": [102] * 10,
            "最高": [105] * 10, "最低": [95] * 10,
            "成交量": [1e6] * 10,
        })
        result = _normalize_columns(df)
        assert "date" in result.columns
        assert "open" in result.columns
        assert "close" in result.columns

    def test_empty_dataframe(self):
        """空 DataFrame 直接返回"""
        from lib.data.data_layer import _normalize_columns
        result = _normalize_columns(pd.DataFrame())
        assert result.empty


class TestValidateDataframe:
    def test_valid_dataframe(self):
        """标准列且行数足够 → True"""
        from lib.data.data_layer import _validate_dataframe
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=20),
            "open": [100] * 20, "high": [105] * 20,
            "low": [95] * 20, "close": [102] * 20,
            "volume": [1e6] * 20,
        })
        assert _validate_dataframe(df) is True

    def test_too_few_rows(self):
        """行数不足 → False"""
        from lib.data.data_layer import _validate_dataframe
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=5),
            "open": [100] * 5, "high": [105] * 5,
            "low": [95] * 5, "close": [102] * 5,
            "volume": [1e6] * 5,
        })
        assert _validate_dataframe(df, min_rows=10) is False

    def test_missing_required_column(self):
        """缺必需列 → False"""
        from lib.data.data_layer import _validate_dataframe
        df = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=20),
            "open": [100] * 20, "close": [102] * 20,
        })
        assert _validate_dataframe(df) is False

    def test_empty_dataframe(self):
        """空 DataFrame → False"""
        from lib.data.data_layer import _validate_dataframe
        assert _validate_dataframe(pd.DataFrame()) is False
