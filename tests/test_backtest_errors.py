"""H5 backtest 数据获取异常传播测试

验证 get_stock_data() 在数据源全部失败时：
1. 抛出 AllDataSourcesUnavailableError（而非返回空 DataFrame）
2. 异常包含股票代码信息
3. 异常可被上层捕获处理
"""
import pytest
from unittest.mock import patch, MagicMock


def test_get_stock_data_raises_on_all_sources_failed():
    """数据源全部失败时抛出 AllDataSourcesUnavailableError"""
    from backtest.core import get_stock_data, AllDataSourcesUnavailableError
    from lib.data_layer import AllDataSourcesFailedError

    with patch("lib.data_layer.get_stock_hist_multi_source") as mock_get:
        mock_get.side_effect = AllDataSourcesFailedError("000001", ["数据源A失败", "数据源B失败"])

        with pytest.raises(AllDataSourcesUnavailableError) as exc_info:
            get_stock_data("000001", days=250, use_cache=False)

        assert "000001" in str(exc_info.value)
        assert "所有数据源失败" in str(exc_info.value)


def test_get_stock_data_raises_on_empty_dataframe():
    """数据源返回空 DataFrame 时抛出异常"""
    from backtest.core import get_stock_data, AllDataSourcesUnavailableError

    mock_df = MagicMock()
    mock_df.empty = True

    with patch("lib.data_layer.get_stock_hist_multi_source", return_value=mock_df):
        with pytest.raises(AllDataSourcesUnavailableError) as exc_info:
            get_stock_data("600519", days=250, use_cache=False)

        assert "600519" in str(exc_info.value)
        assert "空结果" in str(exc_info.value)


def test_all_data_sources_unavailable_error_inherits_exception():
    """异常类型合理继承"""
    from backtest.core import AllDataSourcesUnavailableError

    err = AllDataSourcesUnavailableError("test message")
    assert "test message" in str(err)
    assert isinstance(err, Exception)
