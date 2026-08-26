"""行业周期过热分位 —— 库存/产能/债务三指标历史分位

分位口径（W003 校准）：个股级聚合——对板块成分股各自计算三指标历史分位，
再对每个指标取成分股分位均值作为板块分位（保留个股异议，避免板块 median 稀释）。
个股分位：窗口 window_quarters=40 个季度内 (w <= 最新值).mean()；
有效观测 < min_obs=32 → 该指标中性 None（不入均值与 hot 统计）。
过热判定：分位 ≥ pctile_high=0.60（W003 校准 0.85→0.60 放宽）。
as_of 过滤：只用 date <= as_of 的序列计算（回测点内，无未来函数）；
as_of 为 YYYYMMDD 字符串。板块分位结果按 (block, as_of 年月) 进程 memo，TTL 10 分钟。

使用方式：
    from lib.cycle_heat import get_block_cycle_heat
    hot = get_block_cycle_heat("白酒", as_of="20231231")
"""
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config import CycleWarningConfig
from lib.data_industry import _block_codes, _fetch_stock_yoy_series

logger = logging.getLogger(__name__)

_CONFIG = CycleWarningConfig()

# {block: {"_ts": float, "inventory": float, "capex": float, "debt": float}}
_BLOCK_PCT_MEMO: Dict[str, dict] = {}
_BLOCK_PCT_TTL_SEC = 600  # 10 分钟


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


def _stock_level_pct_map(block: str, as_of: Optional[str],
                         cfg: CycleWarningConfig) -> Optional[Dict[str, float]]:
    """板块成分股各自三指标分位 → 每指标取成分股分位均值。

    pct 为 None（观测不足）的个股不计入该指标均值；三指标全无有效样本返回 None。
    """
    memo_key = f"{block}_{(as_of or 'latest')[:6]}"  # 年月粒度：同月共享（财务数据季度级准静态）
    now = time.time()
    hit = _BLOCK_PCT_MEMO.get(memo_key)
    if hit is not None and now - hit["_ts"] < _BLOCK_PCT_TTL_SEC:
        return hit

    codes = _block_codes(block)
    if not codes:
        return None

    per_stock: List[Dict[str, float]] = []
    for code in codes[:cfg.max_stocks_per_block]:
        try:
            s = _fetch_stock_yoy_series(code)
            if s is None or s.empty:
                continue
            s = s.sort_values("date")
            if as_of is not None:
                s = s[s["date"] <= str(as_of)]
            if s.empty:
                continue
            p = {}
            for col, label in (("inventory_yoy", "inventory"),
                               ("capex_yoy", "capex"),
                               ("debt_yoy", "debt")):
                p[label] = _percentile(s[col], cfg)
            if any(v is not None for v in p.values()):
                per_stock.append(p)
        except Exception as e:
            logger.debug(f"行业过热个股分位跳过 {block}/{code}: {e}")
            continue

    if not per_stock:
        return None

    out: Dict[str, float] = {"_ts": now}
    for label in ("inventory", "capex", "debt"):
        vals = [p[label] for p in per_stock if p[label] is not None]
        if vals:
            out[label] = max(vals)  # 保留"任一成分股过热"的个股异议信号
    if not any(k in out for k in ("inventory", "capex", "debt")):
        return None
    _BLOCK_PCT_MEMO[memo_key] = out
    # 防 memo 无限膨胀：超 512 条清空（周期扫描会话内块数量有限，兜底即可）
    if len(_BLOCK_PCT_MEMO) > 512:
        _BLOCK_PCT_MEMO.clear()
    return out


def get_block_cycle_heat(block: str, as_of: Optional[str] = None) -> Optional[HotResult]:
    """计算板块三指标过热分位（个股级聚合口径）。

    Args:
        block: TDX 板块名（如 "白酒"）
        as_of: 数据截止日 YYYYMMDD 字符串；None = 全序列最新

    Returns:
        HotResult；板块无成分股或全部个股样本不足返回 None
    """
    cfg = _CONFIG
    pct_map = _stock_level_pct_map(block, as_of, cfg)
    if pct_map is None:
        return None

    pcts = {
        label: round(pct_map.get(label, 0.5) or 0.5, 4)
        for label in ("inventory", "capex", "debt")
    }
    hot = [
        col for col, label in (
            ("inventory_yoy", "inventory"), ("capex_yoy", "capex"), ("debt_yoy", "debt"))
        if pcts[label] >= cfg.pctile_high
    ]

    # n_obs: 三指标中有有效（非 None）分位覆盖的指标数（0-3）
    n_obs = sum(
        pct_map.get(label) is not None
        for label in ("inventory", "capex", "debt")
    )
    return HotResult(
        block=block,
        hot_count=len(hot),
        hot_indicators=sorted(hot),
        inventory_pct=pcts["inventory"],
        capex_pct=pcts["capex"],
        debt_pct=pcts["debt"],
        n_obs=n_obs,
        as_of=str(as_of or "latest"),
    )