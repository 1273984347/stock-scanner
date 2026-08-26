"""顶部预警 —— 行业基本面过热 + 价格破位 → 逃顶信号

来源：视频《美股 vs A股的区别》A 股部分观点：
  基本面过热（库存/产能/债务三指标历史分位 ≥ 0.85）叠加价格破位 = 逃顶时机。
action 映射（权威见模块 docstring + 本文件 _resolve_action）：
  hot_count >= 3 且破位 → exit_all；hot_count == 2 且破位 → reduce_half；其余 → hold

使用方式：
    from lib.top_warning import TopWarningService, detect_top_warning
    svc = TopWarningService()
    warn = svc.get_warning("600519", as_of="20240131", price_df=hist_df)
"""
import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from config import CycleWarningConfig

logger = logging.getLogger(__name__)

_CONFIG = CycleWarningConfig()
_WARNING_CACHE: Dict[str, dict] = {}
_CACHE_TTL_SEC = 600          # 10 分钟
_PRICE_CACHE: Dict[str, pd.DataFrame] = {}


@dataclass
class TopWarningResult:
    """顶部预警结果"""
    block: str
    code: str
    triggered: bool
    hot_count: int
    hot_indicators: List[str]
    breakdown: bool
    action: str            # exit_all / reduce_half / hold
    reasoning: str
    as_of: str


def _price_df(code: str) -> Optional[pd.DataFrame]:
    """TDX 本地 250 日线（模块级单帧缓存，会话内只读一次）"""
    from lib.market_utils import read_tdx_day_ohlcv
    if code in _PRICE_CACHE:
        return _PRICE_CACHE[code]
    df = read_tdx_day_ohlcv(code, days=250)
    if df is not None:
        _PRICE_CACHE[code] = df
    return df


def check_price_breakdown(df: pd.DataFrame, mode: str = None) -> bool:
    """价格破位检测。

    "ma20_60": close[-1] < MA20(close) 且 close[-1] < MA60(close)
    "ma60_200": close[-1] < MA60(close) 且 close[-1] < MA200(close)
    数据行数不足长周期 → False；未知 mode → raise ValueError
    """
    mode = mode or _CONFIG.breakdown_mode
    if mode not in ("ma20_60", "ma60_200"):
        raise ValueError(f"未知破位模式: {mode}")
    close = df["close"].astype(float)
    need = 60 if mode == "ma20_60" else 200
    if len(close) < need:
        return False
    last = close.iloc[-1]
    if mode == "ma20_60":
        return bool(last < close.rolling(20).mean().iloc[-1]
                    and last < close.rolling(60).mean().iloc[-1])
    return bool(last < close.rolling(60).mean().iloc[-1]
                and last < close.rolling(200).mean().iloc[-1])


def _resolve_action(hot_count: int, breakdown: bool) -> str:
    """action 映射（唯一权威）：≥3 且破位 exit_all；==2 且破位 reduce_half；其余 hold"""
    if breakdown and hot_count >= _CONFIG.hot_count_exit:
        return "exit_all"
    if breakdown and hot_count >= _CONFIG.hot_count_reduce:
        return "reduce_half"
    return "hold"


def detect_top_warning(code: str, as_of: Optional[str] = None,
                       price_df: Optional[pd.DataFrame] = None) -> Optional[TopWarningResult]:
    """板块过热 + 价格破位合成顶部预警。

    Args:
        code: 股票代码
        as_of: 截面日 YYYYMMDD（传给 cycle_heat 做点内过滤；None = 最新）
        price_df: 价格 DataFrame（列含 close，≥60 行）；None 时读 TDX 本地 250 日线

    Returns:
        TopWarningResult；无板块数据或无有效价格数据返回 None
    """
    from lib.data_layer import get_stock_blocks
    from lib.cycle_heat import get_block_cycle_heat

    blocks = get_stock_blocks(code) or []
    df = price_df if price_df is not None else _price_df(code)
    if not blocks or df is None or len(df) < 60:
        return None

    for block in blocks:
        hot = get_block_cycle_heat(block, as_of=as_of)
        if hot is None:
            continue
        breakdown = check_price_breakdown(df, mode=_CONFIG.breakdown_mode)
        action = _resolve_action(hot.hot_count, breakdown)
        return TopWarningResult(
            block=block, code=code,
            triggered=action != "hold",
            hot_count=hot.hot_count, hot_indicators=hot.hot_indicators,
            breakdown=breakdown, action=action,
            reasoning=(f"过热{hot.hot_count}项[{','.join(hot.hot_indicators)}]"
                       f"{'+破位' if breakdown else ''}"),
            as_of=hot.as_of,
        )
    return None


class TopWarningService:
    """顶部预警服务（缓存 + 行业冷却注册）"""

    def __init__(self):
        self._cooldown: Dict[str, str] = {}   # block -> cooldown_end(YYYY-MM-DD)

    def get_warning(self, code: str, as_of: Optional[str] = None,
                    price_df: Optional[pd.DataFrame] = None) -> Optional[TopWarningResult]:
        """获取预警；price_df 非 None 时直接计算不缓存（回测点内路径）"""
        if price_df is None:
            key = f"{code}_{as_of or 'latest'}"
            if key in _WARNING_CACHE:
                hit = _WARNING_CACHE[key]
                if time.time() - hit["_ts"] < _CACHE_TTL_SEC:
                    return hit["result"]
            result = detect_top_warning(code, as_of=as_of)
            _WARNING_CACHE[key] = {"result": result, "_ts": time.time()}
            return result
        return detect_top_warning(code, as_of=as_of, price_df=price_df)

    def is_block_cooling(self, block: str, date_str: str) -> bool:
        """date_str(YYYY-MM-DD) < 冷却截止日 → True"""
        end = self._cooldown.get(block)
        return bool(end and str(date_str) < end)

    def mark_exit(self, block: str, cooldown_end: str) -> None:
        """记录行业逃顶，设置冷却截止日；已存在时取更晚者"""
        old = self._cooldown.get(block)
        if old is None or str(cooldown_end) > old:
            self._cooldown[block] = str(cooldown_end)
