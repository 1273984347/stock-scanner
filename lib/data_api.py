"""数据源统一适配器 —— 单一入口 + 契约规范化（RF-01 Phase 2）

为什么需要：项目里 `get_kline` / `get_realtime_quotes` / `get_stock_financial` /
`clear_cache` 各有多份同名实现，**签名与返回列都不同**（详见 RF-01 §七 第 2 批复盘）。
直接合并这些实现 = 改变数据语义 ⇒ 本模块把"统一"落在**入口层**：

- 新代码只需 `from lib.data_api import get_kline` 一个入口
- 返回值统一按 `lib.data_protocol` 的 K 线契约规范化
- **不改任何数据源的内部实现** ⇒ 零破坏

各源实现保持原样、仍可单独调用；本适配器只是"推荐入口"。
"""

import logging
from typing import Dict, List, Optional

import pandas as pd

from lib.data_protocol import KLINE_COLUMNS, normalize_kline

logger = logging.getLogger(__name__)


def get_kline(code: str, days: int = 180, period: str = "daily",
              as_of_date: Optional[str] = None) -> pd.DataFrame:
    """统一 K 线入口 —— 返回符合 K 线契约的 DataFrame。

    Args:
        code: 6 位股票代码
        days: 取多少天
        period: daily/weekly/monthly
        as_of_date: 时间截断 (YYYY-MM-DD)。**回测必传**，否则会取到 T 日数据（前视偏差）。

    Returns:
        列 = KLINE_COLUMNS 的 DataFrame（空时列也齐全，便于下游统一处理）。
    """
    from lib.data_layer import get_stock_hist_multi_source

    raw = get_stock_hist_multi_source(code, days, "auto", as_of_date)
    if raw is None or getattr(raw, "empty", True):
        return pd.DataFrame(columns=KLINE_COLUMNS)
    return normalize_kline(raw)


def get_realtime_quotes(force: bool = False) -> pd.DataFrame:
    """统一实时行情入口（列名已由 data_layer 归一）。"""
    from lib.data_layer import get_realtime_quotes as _impl

    df = _impl(force=force)
    if df is None:
        return pd.DataFrame()
    return df


def get_stock_financial(code: str) -> pd.DataFrame:
    """统一财务摘要入口（列名由 data_layer 归一）。"""
    from lib.data_layer import get_stock_financial as _impl

    df = _impl(code)
    if df is None:
        return pd.DataFrame()
    return df


def clear_caches() -> List[str]:
    """清理各数据源自身的缓存，返回实际清理的源名列表。

    各源的 `clear_cache` **各清各的**（data_margin / tdx_batch / sentiment …），
    同名不同语义 ⇒ 不能合并成一个实现，这里只做汇总调用。
    """
    cleared: List[str] = []
    for module_name, attr in (
        ("lib.data_margin", "clear_cache"),
        ("lib.tdx_batch", "clear_cache"),
        ("lib.sentiment", "clear_cache"),
    ):
        try:
            import importlib

            mod = importlib.import_module(module_name)
            fn = getattr(mod, attr, None)
            if callable(fn):
                fn()
                cleared.append(module_name)
        except Exception as e:
            logger.debug(f"清理 {module_name}.{attr} 失败: {e}")
    return cleared
