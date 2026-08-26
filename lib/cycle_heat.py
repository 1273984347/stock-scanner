"""行业周期过热分位 —— 库存/产能/债务三指标历史分位

分位口径：窗口 window_quarters=40 个季度内 (w <= 最新值).mean()；
有效观测 < min_obs=32 → 该指标中性 0.5
过热判定：分位 ≥ pctile_high=0.85
已知特性：ECDF 口径下，处于历史低位平台上的最新低值也会得高分位
（如 [5.0]*4+[0.0]*28 当前 0.0 → 28/32 = 0.875 ≥ 0.85 会被判过热）；
真实财报 yoy 为连续浮点值，精确相等罕见，v1 接受该特性，
且 TopWarning 层另有价格破位条件二次过滤。
as_of 过滤：只用 date <= as_of 的序列计算（回测点内，无未来函数）；
as_of 为 YYYYMMDD 字符串

使用方式：
    from lib.cycle_heat import get_block_cycle_heat
    hot = get_block_cycle_heat("白酒", as_of="20231231")
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

import pandas as pd

from config import CycleWarningConfig
from lib.data_industry import fetch_block_financial_series

logger = logging.getLogger(__name__)

_CONFIG = CycleWarningConfig()


@dataclass
class HotResult:
    """行业周期过热结果（分位含义见模块 docstring）"""
    block: str
    hot_count: int
    hot_indicators: List[str]
    inventory_pct: float
    capex_pct: float
    debt_pct: float
    n_obs: int
    as_of: str


def _percentile(series: pd.Series, cfg: CycleWarningConfig) -> Optional[float]:
    """序列最新值的窗口内历史分位；有效观测不足返回 None"""
    s = series.dropna()
    if len(s) < cfg.min_obs:
        return None
    w = s.tail(cfg.window_quarters)
    return float((w <= s.iloc[-1]).mean())


def get_block_cycle_heat(block: str, as_of: Optional[str] = None) -> Optional[HotResult]:
    """计算板块三指标过热分位。

    Args:
        block: TDX 板块名（如 "白酒"）
        as_of: 数据截止日 YYYYMMDD 字符串；None = 全序列最新

    Returns:
        HotResult；板块无数据或截断后无数据返回 None
    """
    df = fetch_block_financial_series(block)
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    if as_of is not None:
        df = df[df["date"] <= str(as_of)]
    if df.empty:
        return None

    cfg = _CONFIG
    pcts = {}
    hot = []
    for col, label in (("inventory_yoy", "inventory"),
                       ("capex_yoy", "capex"),
                       ("debt_yoy", "debt")):
        p = _percentile(df[col], cfg)
        pcts[label] = 0.5 if p is None else round(p, 4)
        if p is not None and p >= cfg.pctile_high:
            hot.append(col)

    return HotResult(
        block=block,
        hot_count=len(hot),
        hot_indicators=sorted(hot),
        inventory_pct=pcts["inventory"],
        capex_pct=pcts["capex"],
        debt_pct=pcts["debt"],
        n_obs=int(df[["inventory_yoy", "capex_yoy", "debt_yoy"]].notna().all(axis=1).sum()),
        as_of=str(df["date"].iloc[-1]),
    )
