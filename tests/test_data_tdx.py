"""H2 TDX fallback 场景测试

验证 lib/data_tdx.py 的三级 fallback 链：
1. 网络直连 (pytdx) → 2. 本地文件 → 3. akshare
"""
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd


def _make_df(n: int = 30) -> pd.DataFrame:
    """构造有效 K 线 DataFrame"""
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "date": dates,
        "open": [10.0] * n,
        "high": [10.5] * n,
        "low": [9.5] * n,
        "close": [10.2] * n,
        "volume": [1e6] * n,
    })


def test_fallback_to_akshare_when_both_network_and_local_fail():
    """网络+本地均失败时，fallback 到 akshare"""
    from lib.data.data_tdx import get_kline

    df_akshare = _make_df()

    with patch("lib.data.data_tdx._fetch_via_network", return_value=pd.DataFrame()):
        with patch("lib.data.data_tdx._fetch_from_local_file", return_value=pd.DataFrame()):
            with patch("akshare.stock_zh_a_hist", return_value=df_akshare) as mock_ak:
                result = get_kline("600519", days=30)

                mock_ak.assert_called_once()
                assert not result.empty
                assert len(result) == 30


def test_network_success_no_local_no_akshare():
    """网络成功时，不调用本地文件和 akshare"""
    from lib.data.data_tdx import get_kline

    df_network = _make_df()

    with patch("lib.data.data_tdx._fetch_via_network", return_value=df_network) as mock_net:
        with patch("lib.data.data_tdx._fetch_from_local_file") as mock_local:
            with patch("akshare.stock_zh_a_hist") as mock_ak:
                result = get_kline("600519", days=30)

                mock_net.assert_called_once()
                mock_local.assert_not_called()
                mock_ak.assert_not_called()
                assert not result.empty


def test_local_success_no_akshare():
    """本地文件成功时，不调用 akshare"""
    from lib.data.data_tdx import get_kline

    df_local = _make_df()

    with patch("lib.data.data_tdx._fetch_via_network", return_value=pd.DataFrame()):
        with patch("lib.data.data_tdx._fetch_from_local_file", return_value=df_local) as mock_local:
            with patch("akshare.stock_zh_a_hist") as mock_ak:
                result = get_kline("600519", days=30)

                mock_local.assert_called_once()
                mock_ak.assert_not_called()
                assert not result.empty


def test_all_sources_fail_returns_empty_dataframe():
    """所有源都失败时，返回空 DataFrame"""
    from lib.data.data_tdx import get_kline

    with patch("lib.data.data_tdx._fetch_via_network", return_value=pd.DataFrame()):
        with patch("lib.data.data_tdx._fetch_from_local_file", return_value=pd.DataFrame()):
            with patch("akshare.stock_zh_a_hist", side_effect=Exception("akshare 失败")):
                result = get_kline("600519", days=30)

                assert result.empty


def test_akshare_returns_insufficient_data_uses_partial():
    """akshare 返回数据不足 30 天时，使用全部数据"""
    from lib.data.data_tdx import get_kline

    df_partial = _make_df(n=15)

    with patch("lib.data.data_tdx._fetch_via_network", return_value=pd.DataFrame()):
        with patch("lib.data.data_tdx._fetch_from_local_file", return_value=pd.DataFrame()):
            with patch("akshare.stock_zh_a_hist", return_value=df_partial):
                result = get_kline("600519", days=30)

                assert not result.empty
                assert len(result) == 15
