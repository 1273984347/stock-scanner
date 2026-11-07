"""统一板块评分（0-100）

四维度各 0-25：
1. 涨幅 — 5日均收益百分位排名
2. 成交额放大 — 近5日/前5日量比
3. 涨停家数 — 板块内涨停占比
4. 龙头强度 — 板块内最高收益百分位

使用方式：
    from lib.strategy.sector_score import compute_sector_scores, get_sector_score_for_stock
    scores = compute_sector_scores(top_n=20)
    stock_score = get_sector_score_for_stock("600519")
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

TDX_DATA_PATH = "D:/TDX/vipdoc"


@dataclass
class SectorScoreDetail:
    """板块评分明细"""
    block: str
    stock_count: int
    score_return: float       # 0-25
    score_volume: float       # 0-25
    score_limit_up: float     # 0-25
    score_leader: float       # 0-25
    total_score: float        # 0-100
    avg_return_5d: float = 0.0
    volume_ratio: float = 0.0
    limit_up_count: int = 0
    leader_return: float = 0.0
    leader_code: str = ""


def _read_tdx_day(code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """读取 TDX 日线数据 — 委托给 lib.data.market_utils.read_tdx_day_ohlcv"""
    from lib.data.market_utils import read_tdx_day_ohlcv
    return read_tdx_day_ohlcv(code, days=days)


def _score_return(avg_ret: float, all_returns: List[float]) -> float:
    """涨幅评分（百分位排名 → 0-25）"""
    if not all_returns:
        return 12.5
    pct = sum(1 for r in all_returns if r <= avg_ret) / len(all_returns)
    return round(pct * 25, 1)


def _score_volume(volume_ratio: float) -> float:
    """成交额放大评分（量比 → 0-25）"""
    return round(max(0, min(25, (volume_ratio - 0.5) * 25 / 1.5)), 1)


def _score_limit_up(count: int, block_size: int) -> float:
    """涨停家数评分（占比 → 0-25）"""
    if block_size <= 0:
        return 0
    ratio = count / block_size
    return round(min(25, ratio * 250), 1)


def _score_leader(leader_ret: float, all_returns: List[float]) -> float:
    """龙头强度评分（百分位 → 0-25）"""
    if not all_returns:
        return 12.5
    pct = sum(1 for r in all_returns if r <= leader_ret) / len(all_returns)
    return round(pct * 25, 1)


# 缓存（10 分钟 TTL）
_sector_scores_cache: Dict = {}
_sector_scores_ts: float = 0


def compute_sector_scores(top_n: int = 20) -> List[SectorScoreDetail]:
    """计算所有板块的统一评分（0-100），缓存 10 分钟

    Returns:
        按 total_score 降序排列的前 top_n 个板块
    """
    import time as _time
    global _sector_scores_cache, _sector_scores_ts
    cache_key = f"top_{top_n}"
    if cache_key in _sector_scores_cache and _time.time() - _sector_scores_ts < 600:
        return _sector_scores_cache[cache_key]

    from lib.data.data_layer import get_all_blocks
    blocks = get_all_blocks()
    if not blocks:
        return []

    all_sector_data = []
    all_returns_5d = []

    for block_name, stock_codes in blocks.items():
        if len(stock_codes) < 5:
            continue

        returns_5d = []
        volume_ratios = []
        limit_up_count = 0
        best_return = -999
        best_code = ""

        for code in stock_codes[:30]:
            df = _read_tdx_day(code, days=15)
            if df is None or len(df) < 6:
                continue

            closes = df["close"].values
            volumes = df["volume"].values

            if closes[0] == 0:
                continue

            if len(closes) >= 6:
                ret = (closes[-1] / closes[-6] - 1) * 100
                returns_5d.append(ret)
                if ret > best_return:
                    best_return = ret
                    best_code = code

            if len(volumes) >= 10:
                recent = np.mean(volumes[-5:])
                prev = np.mean(volumes[-10:-5])
                if prev > 0:
                    volume_ratios.append(recent / prev)

            if len(closes) >= 2:
                daily_ret = (closes[-1] / closes[-2] - 1) * 100
                if daily_ret >= 9.5:
                    limit_up_count += 1

        if not returns_5d:
            continue

        avg_ret = float(np.mean(returns_5d))
        vol_ratio = float(np.mean(volume_ratios)) if volume_ratios else 1.0
        all_returns_5d.append(avg_ret)

        all_sector_data.append({
            "block": block_name,
            "stock_count": len(stock_codes),
            "avg_return_5d": avg_ret,
            "volume_ratio": vol_ratio,
            "limit_up_count": limit_up_count,
            "leader_return": best_return,
            "leader_code": best_code,
        })

    scored = []
    for d in all_sector_data:
        s_return = _score_return(d["avg_return_5d"], all_returns_5d)
        s_volume = _score_volume(d["volume_ratio"])
        s_limit = _score_limit_up(d["limit_up_count"], d["stock_count"])
        s_leader = _score_leader(d["leader_return"], all_returns_5d)
        total = s_return + s_volume + s_limit + s_leader

        scored.append(SectorScoreDetail(
            block=d["block"],
            stock_count=d["stock_count"],
            score_return=s_return,
            score_volume=s_volume,
            score_limit_up=s_limit,
            score_leader=s_leader,
            total_score=round(total, 1),
            avg_return_5d=round(d["avg_return_5d"], 2),
            volume_ratio=round(d["volume_ratio"], 2),
            limit_up_count=d["limit_up_count"],
            leader_return=round(d["leader_return"], 2),
            leader_code=d["leader_code"],
        ))

    scored.sort(key=lambda x: x.total_score, reverse=True)
    result = scored[:top_n]

    # 更新缓存
    _sector_scores_cache[cache_key] = result
    _sector_scores_ts = _time.time()

    return result


def get_sector_score_for_stock(code: str) -> float:
    """获取个股所属板块的最高评分（结果被 compute_sector_scores 缓存）

    Returns:
        float 0-100，未知时返回 50.0
    """
    try:
        from lib.data.data_layer import get_stock_blocks
        stock_blocks = get_stock_blocks(code)
        if not stock_blocks:
            return 50.0

        scores = compute_sector_scores(top_n=50)
        # 用 dict comprehension 一次构建，后续调用命中缓存
        score_map = {s.block: s.total_score for s in scores}

        block_scores = [score_map.get(b, 0) for b in stock_blocks if b in score_map]
        if block_scores:
            return max(block_scores)
        return 50.0
    except Exception as e:
        logger.debug(f"板块评分失败 {code}: {e}")
        return 50.0
