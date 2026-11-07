"""板块轮动策略增强 —— 动量因子 + 资金流向 + 龙头识别

在 factors.py 的 get_sector_performance() 基础上增加：
1. 板块动量因子（哪个板块在加速上涨）
2. 板块资金流向（从冷门→热门板块）
3. 龙头股自动识别（板块内涨幅前 10%）

使用方式：
    from lib.strategy.sector_rotation import SectorRotationAnalyzer
    analyzer = SectorRotationAnalyzer()
    result = analyzer.analyze()
"""

import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

TDX_DATA_PATH = "D:/TDX/vipdoc"


@dataclass
class SectorMomentum:
    """板块动量指标"""
    block: str
    stock_count: int
    # 收益率（不同窗口）
    return_5d: float = 0.0
    return_10d: float = 0.0
    return_20d: float = 0.0
    # 动量加速度（短期收益 > 长期 = 加速）
    momentum_accel: float = 0.0
    # 资金流入强度（成交量变化）
    volume_change: float = 0.0
    # 涨跌家数
    up_count: int = 0
    down_count: int = 0
    up_ratio: float = 0.0
    # 龙头股
    leader_code: str = ""
    leader_name: str = ""
    leader_return: float = 0.0


@dataclass
class FundFlow:
    """板块资金流向"""
    block: str
    net_inflow_score: float = 0.0  # 净流入评分 [-1, 1]
    volume_surge: float = 0.0      # 成交量放大倍数
    turnover_change: float = 0.0   # 换手率变化


def _read_tdx_day(code: str, days: int = 60) -> Optional[pd.DataFrame]:
    """读取 TDX 日线数据 — 委托给 lib.data.market_utils.read_tdx_day_ohlcv"""
    from lib.data.market_utils import read_tdx_day_ohlcv
    return read_tdx_day_ohlcv(code, days=days)


class SectorRotationAnalyzer:
    """板块轮动分析器"""

    def __init__(self, tdx_path: str = TDX_DATA_PATH):
        self.tdx_path = tdx_path

    def analyze(self, top_n: int = 10) -> Dict:
        """完整板块轮动分析

        Returns:
            dict with keys:
            - momentum_ranking: 板块动量排名
            - fund_flow: 板块资金流向
            - leaders: 各板块龙头股
            - rotation_signal: 轮动信号（推荐板块）
        """
        from lib.data.data_layer import get_all_blocks

        blocks = get_all_blocks()
        if not blocks:
            return {"momentum_ranking": [], "fund_flow": [], "leaders": [], "rotation_signal": {}}

        # 分析每个板块
        sector_data = []
        for block_name, stock_codes in blocks.items():
            if len(stock_codes) < 5:
                continue

            momentum = self._analyze_sector_momentum(block_name, stock_codes)
            if momentum:
                sector_data.append(momentum)

        if not sector_data:
            return {"momentum_ranking": [], "fund_flow": [], "leaders": [], "rotation_signal": {}}

        # 排名
        momentum_ranking = sorted(sector_data, key=lambda x: x.momentum_accel, reverse=True)
        return_ranking = sorted(sector_data, key=lambda x: x.return_5d, reverse=True)

        # 资金流向
        fund_flow = [
            FundFlow(
                block=s.block,
                net_inflow_score=s.volume_change * 0.5 + s.up_ratio * 0.5,
                volume_surge=s.volume_change,
                turnover_change=s.momentum_accel,
            )
            for s in sector_data
        ]
        fund_flow.sort(key=lambda x: x.net_inflow_score, reverse=True)

        # 龙头股
        leaders = []
        for s in sector_data[:top_n]:
            if s.leader_code:
                leaders.append({
                    "block": s.block,
                    "code": s.leader_code,
                    "return": s.leader_return,
                })

        # 轮动信号
        rotation_signal = self._generate_rotation_signal(momentum_ranking, return_ranking)

        return {
            "momentum_ranking": [
                {
                    "block": s.block,
                    "return_5d": round(s.return_5d, 2),
                    "return_10d": round(s.return_10d, 2),
                    "return_20d": round(s.return_20d, 2),
                    "momentum_accel": round(s.momentum_accel, 4),
                    "up_ratio": round(s.up_ratio, 2),
                    "volume_change": round(s.volume_change, 2),
                }
                for s in momentum_ranking[:top_n]
            ],
            "fund_flow": [
                {"block": f.block, "score": round(f.net_inflow_score, 3)}
                for f in fund_flow[:top_n]
            ],
            "leaders": leaders[:top_n],
            "rotation_signal": rotation_signal,
        }

    def _analyze_sector_momentum(self, block_name: str, stock_codes: List[str]) -> Optional[SectorMomentum]:
        """分析单个板块的动量"""
        returns_5d = []
        returns_10d = []
        returns_20d = []
        volume_ratios = []
        up_count = 0
        down_count = 0
        best_return = -999
        best_code = ""

        for code in stock_codes[:30]:  # 采样 30 只
            df = _read_tdx_day(code, days=30)
            if df is None or len(df) < 20:
                continue

            closes = df["close"].values
            volumes = df["volume"].values

            if closes[0] == 0:
                continue

            # 收益率
            if len(closes) >= 5:
                ret_5 = (closes[-1] / closes[-5] - 1) * 100
                returns_5d.append(ret_5)
            if len(closes) >= 10:
                ret_10 = (closes[-1] / closes[-10] - 1) * 100
                returns_10d.append(ret_10)
            if len(closes) >= 20:
                ret_20 = (closes[-1] / closes[-20] - 1) * 100
                returns_20d.append(ret_20)

            # 涨跌
            if len(closes) >= 2:
                if closes[-1] > closes[-2]:
                    up_count += 1
                else:
                    down_count += 1

            # 成交量变化
            if len(volumes) >= 10:
                recent_vol = np.mean(volumes[-5:])
                prev_vol = np.mean(volumes[-10:-5])
                if prev_vol > 0:
                    volume_ratios.append(recent_vol / prev_vol)

            # 龙头
            if returns_5d and returns_5d[-1] > best_return:
                best_return = returns_5d[-1]
                best_code = code

        if not returns_5d:
            return None

        total = up_count + down_count
        avg_5 = float(np.mean(returns_5d))
        avg_10 = float(np.mean(returns_10d)) if returns_10d else 0
        avg_20 = float(np.mean(returns_20d)) if returns_20d else 0

        # 动量加速度 = 短期收益 - 长期收益（>0 表示加速）
        accel = avg_5 - avg_20 if avg_20 != 0 else 0

        return SectorMomentum(
            block=block_name,
            stock_count=len(stock_codes),
            return_5d=avg_5,
            return_10d=avg_10,
            return_20d=avg_20,
            momentum_accel=accel,
            volume_change=float(np.mean(volume_ratios)) if volume_ratios else 1.0,
            up_count=up_count,
            down_count=down_count,
            up_ratio=up_count / total if total > 0 else 0.5,
            leader_code=best_code,
            leader_return=best_return,
        )

    def _generate_rotation_signal(self, momentum_ranking: List, return_ranking: List) -> Dict:
        """生成轮动信号"""
        if not momentum_ranking:
            return {}

        # 动量加速板块（短期 > 长期）
        accelerating = [s for s in momentum_ranking[:5] if s.momentum_accel > 0]

        # 高收益 + 高动量 = 推荐
        recommended = []
        for s in momentum_ranking[:5]:
            if s.return_5d > 0 and s.momentum_accel > 0 and s.up_ratio > 0.5:
                recommended.append(s.block)

        return {
            "recommended_blocks": recommended[:3],
            "accelerating_count": len(accelerating),
            "top_momentum": momentum_ranking[0].block if momentum_ranking else "",
            "top_return": return_ranking[0].block if return_ranking else "",
        }


def get_sector_rotation_factor(code: str, block_name: str = None) -> float:
    """获取板块轮动因子值（供 factors.py 使用）

    Returns:
        float: [-100, 100] 板块轮动因子值
    """
    try:
        from lib.data.data_layer import get_stock_blocks
        blocks = get_stock_blocks(code)
        if not blocks:
            return 0.0

        # 获取板块动量
        analyzer = SectorRotationAnalyzer()
        result = analyzer.analyze(top_n=20)

        # 检查该股票所属板块是否在推荐列表
        recommended = result.get("rotation_signal", {}).get("recommended_blocks", [])
        stock_blocks = [b.get("block", b) if isinstance(b, dict) else b for b in blocks]

        for block in stock_blocks:
            if block in recommended:
                return 50.0  # 推荐板块加分

        # 检查板块排名
        momentum = result.get("momentum_ranking", [])
        for i, m in enumerate(momentum[:10]):
            if m.get("block") in stock_blocks:
                return max(0, 50 - i * 5)  # 排名越高分越高

        return 0.0
    except Exception as e:
        logger.debug(f"板块轮动因子计算失败: {e}")
        return 0.0
