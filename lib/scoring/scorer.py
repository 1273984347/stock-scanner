"""综合评分 —— 四维加权评分 + 偏移值异常监控

来源：股票综合分析器 skill
维度：基本面 35% + 资金面 35% + 消息面 20% + 技术面 10%
"""
import logging

import pandas as pd
import numpy as np
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# RF-01 Phase 4：权重定义已收口到唯一落点 lib/scoring/scoring_weights.py。
# 这里保留 re-export（同签名、同值、同一对象引用）⇒ 旧 import 全部可用，数值零变更。
from lib.scoring.scoring_weights import WEIGHTS_4D, SIX_FACTOR_WEIGHTS  # noqa: E402

# ========== 评分阈值集中常量（避免散落 magic number）==========
# 4 维综合（0-10 分制）
SCORE_4D = {
    "rating_strong_buy": 8.0,
    "rating_buy": 6.0,
    "rating_hold": 4.0,
    "rating_cautious": 2.0,
}
# 6 因子（0-100 分制）
SCORE_6F = {
    "rating_strong_buy": 80,
    "rating_buy": 70,
    "rating_hold": 60,
    "rating_cautious": 40,
}
# 偏移值监控（异常阈值）
DEVIATION = {
    "near_trigger_ratio": 0.8,    # 触发阈值 0.8 倍
    "warning_ratio": 0.6,        # 警告阈值 0.6 倍
    "mild_ratio": 0.4,           # 轻度 0.4 倍
    "score_adj_triggered": -2.0,
    "score_adj_near": -1.5,
    "score_adj_warning": -1.0,
}


def score_fundamental(pe: float = None, roe: float = None,
                      net_growth: float = None, gross_margin: float = None,
                      debt_ratio: float = None, ocf_healthy: bool = True) -> float:
    """基本面评分（0-10 分，权重 35%）

    - ROE > 15% → +3
    - PE 合理（0 < PE < 30）→ +1
    - 净利增长 > 20% → +2
    - 毛利率 > 30% → +2
    - 负债率 < 50% → +1
    - 经营现金流健康 → +1
    """
    score = 0.0
    if roe is not None and roe > 0.15:
        score += 3.0
    if pe is not None and 0 < pe < 30:
        score += 1.0
    if net_growth is not None and net_growth > 0.20:
        score += 2.0
    if gross_margin is not None and gross_margin > 0.30:
        score += 2.0
    if debt_ratio is not None and debt_ratio < 0.50:
        score += 1.0
    if ocf_healthy:
        score += 1.0
    return min(score, 10.0)


def score_capital_flow(main_net_ratio: float = 0,
                       net_inflow_5d: float = 0,
                       northbound_increase: bool = False,
                       super_large_net_inflow: bool = False,
                       turnover_rate: float = 0) -> float:
    """资金面评分（0-10 分，权重 35%）

    - 主力净流入比 > 10% → +4, 5-10% → +3, 0-5% → +2, -5%-0% → +1
    - 5 日净流入 > 1 亿 → +2
    - 北向增加 → +2
    - 超大单净流入 → +1
    - 换手率 3-8% → +1
    """
    score = 0.0
    if main_net_ratio > 0.10:
        score += 4.0
    elif main_net_ratio > 0.05:
        score += 3.0
    elif main_net_ratio > 0:
        score += 2.0
    elif main_net_ratio > -0.05:
        score += 1.0

    if net_inflow_5d > 1e8:
        score += 2.0
    if northbound_increase:
        score += 2.0
    if super_large_net_inflow:
        score += 1.0
    if 0.03 <= turnover_rate <= 0.08:
        score += 1.0
    return min(score, 10.0)


def score_news(major_positive: bool = False,
               institutional_buy: bool = False,
               industry_policy: bool = False,
               no_negative: bool = False,
               earnings_positive: bool = False) -> float:
    """消息面评分（0-10 分，权重 20%）

    Args:
        no_negative: 是否确认"无负面新闻"。默认 False —— 数据缺失=中性（0 分），
                     避免无新闻源时虚高 +2 分。调用方需**显式传 True**才加 +2。
        其他参数同理：默认 False = 数据缺失 = 中性，不贡献分数。
    """
    # 检测"数据缺失"场景：所有布尔信号都未传=True。
    # 这种情况下 no_negative=False 可能是"没数据"，而不是"确认有负面"。
    data_missing = not any([
        major_positive,
        institutional_buy,
        industry_policy,
        no_negative,
        earnings_positive,
    ])
    if data_missing:
        logger.warning(
            "score_news: 所有新闻信号均为 False/默认 —— 视为数据缺失。"
            "no_negative 默认 False 防止虚高入场信号。"
            "若确认无负面新闻，请显式传 no_negative=True。"
        )

    score = 0.0
    if major_positive:
        score += 3.0
    if institutional_buy:
        score += 2.0
    if industry_policy:
        score += 2.0
    if no_negative:
        score += 2.0
    if earnings_positive:
        score += 1.0
    return min(score, 10.0)


def score_technical(return_10d: float = 0, return_20d: float = 0) -> float:
    """技术面评分（0-10 分，权重 10%）

    基于近期涨幅调整：
    - 10d > 5% 且 20d > 10% → +1（趋势确认）
    - 10d < -10% 或 20d < -20% → -1（趋势走坏）
    - 10d > 20% → -1（涨幅过大风险）
    基础分 5 分
    """
    score = 5.0
    if return_10d > 0.05 and return_20d > 0.10:
        score += 1.0
    if return_10d < -0.10 or return_20d < -0.20:
        score -= 1.0
    if return_10d > 0.20:
        score -= 1.0
    return max(0, min(10, score))


def comprehensive_score(fundamental: float, capital_flow: Optional[float],
                        news: float, technical: float) -> Dict:
    """综合评分

    Args:
        capital_flow: 资金面分；\u0060None\u0060 表示**该维数据缺失**（上游取不到）。
            此时该维从加权中**剔除**，其余三维按原权重比例归一。
            P0（2026-11-20）：缺失曾被伪装成 0 分进入加权，等价于「假中性 1.0 分」，
            与真实中性(2.0)/真实强流出(0.0) 不可区分。

    Returns:
        {"score": float, "rating": str, "breakdown": dict}
        score: 0-10 加权总分
        rating: 强烈买入/买入/持有/谨慎/回避
        breakdown: 四维分项；缺失维为 \u0060None\u0060（与 0 分可区分）
    """
    dims = (
        ("fundamental", fundamental, WEIGHTS_4D["fundamental"]),
        ("capital_flow", capital_flow, WEIGHTS_4D["capital_flow"]),
        ("news", news, WEIGHTS_4D["news"]),
        ("technical", technical, WEIGHTS_4D["technical"]),
    )
    if all(v is not None for _name, v, _w in dims):
        # 四维齐全：与改前**逐字节同式**。
        # ⚠️ 不可改写成 sum(...)/sum(weights)：权重和的浮点累加是 0.9999999999999999
        #    （0.35+0.35+0.20+0.10），除以它会把 8*0.35+8*0.35+8*0.20+8*0.10
        #    从 7.999999999999999 变成 8.0 ⇒ 评级在阈值边界翻转
        #    （tests/features/test_scoring_contract.py 冻结了该既有行为）。
        weighted = (fundamental * WEIGHTS_4D["fundamental"]
                    + capital_flow * WEIGHTS_4D["capital_flow"]
                    + news * WEIGHTS_4D["news"]
                    + technical * WEIGHTS_4D["technical"])
    else:
        # 缺失维：剔除该维，其余三维按原权重比例归一
        present = [(v, w) for _name, v, w in dims if v is not None]
        total_weight = sum(w for _v, w in present)
        weighted = sum(v * w for v, w in present) / total_weight if total_weight else 0.0

    if weighted >= SCORE_4D["rating_strong_buy"]:
        rating = "强烈买入"
    elif weighted >= SCORE_4D["rating_buy"]:
        rating = "买入"
    elif weighted >= SCORE_4D["rating_hold"]:
        rating = "持有"
    elif weighted >= SCORE_4D["rating_cautious"]:
        rating = "谨慎"
    else:
        rating = "回避"

    return {
        "score": round(weighted, 2),
        "rating": rating,
        "breakdown": {
            "fundamental": round(fundamental, 2) if fundamental is not None else None,
            "capital_flow": round(capital_flow, 2) if capital_flow is not None else None,
            "news": round(news, 2) if news is not None else None,
            "technical": round(technical, 2) if technical is not None else None,
        },
        "weights": dict(WEIGHTS_4D),
    }


# ========== 偏移值监控（A 股专用） ==========

# 各板块异常阈值
_BOARD_THRESHOLDS = {
    "main": 0.20,       # 主板 ±20%
    "chinext": 0.30,    # 创业板 ±30%
    "star": 0.30,       # 科创板 ±30%
    "bse": 0.40,        # 北交所 ±40%
    "st": 0.12,         # ST ±12%
}


def get_board_type(code: str, name: str = "") -> str:
    """根据股票代码判断板块类型"""
    if isinstance(name, str) and "ST" in name.upper():
        return "st"
    if code.startswith("688"):
        return "star"
    if code.startswith("30"):
        return "chinext"
    if code.startswith(("8", "4")):
        return "bse"
    return "main"


def calc_deviation_value(stock_return: float, benchmark_return: float) -> Dict:
    """计算偏移值 = 个股涨幅 - 基准指数涨幅

    Args:
        stock_return: 个股期间收益率
        benchmark_return: 基准指数期间收益率

    Returns:
        {"deviation": float, "triggered": bool, "level": str}
    """
    deviation = stock_return - benchmark_return
    return {
        "stock_return": round(stock_return, 4),
        "benchmark_return": round(benchmark_return, 4),
        "deviation": round(deviation, 4),
    }


def check_anomaly_threshold(deviation: float, board_type: str) -> Dict:
    """检查偏移值是否触发异常

    Returns:
        {"triggered": bool, "level": str, "threshold": float, "score_adjustment": float}
    """
    threshold = _BOARD_THRESHOLDS.get(board_type, 0.20)
    abs_dev = abs(deviation)

    if abs_dev >= threshold:
        level = "triggered"
        score_adj = DEVIATION["score_adj_triggered"]
    elif abs_dev >= threshold * DEVIATION["near_trigger_ratio"]:
        level = "near_trigger"
        score_adj = DEVIATION["score_adj_near"]
    elif abs_dev >= threshold * DEVIATION["warning_ratio"]:
        level = "warning"
        score_adj = DEVIATION["score_adj_warning"]
    elif abs_dev >= threshold * DEVIATION["mild_ratio"]:
        level = "mild"
        score_adj = 0
    else:
        level = "safe"
        score_adj = 0

    return {
        "triggered": abs_dev >= threshold,
        "level": level,
        "threshold": threshold,
        "deviation": round(deviation, 4),
        "score_adjustment": score_adj,
    }


# ========== V6 六因子评分系统 ==========
# SIX_FACTOR_WEIGHTS 的定义已收口到 lib/scoring/scoring_weights.py（见文件头 re-export）。


def score_trend(factors: dict) -> float:
    """趋势分（0-100）

    - trend_strength > 5: 90分（强趋势）
    - trend_strength > 2: 75分
    - trend_strength > 0: 60分
    - trend_strength > -2: 40分
    - else: 20分
    """
    ts = factors.get("trend_strength", 0)
    if ts > 5:
        return 90
    elif ts > 2:
        return 75
    elif ts > 0:
        return 60
    elif ts > -2:
        return 40
    return 20


def score_momentum(factors: dict) -> float:
    """动量分（0-100）

    - momentum_20 > 10%: 90分
    - momentum_20 > 5%: 75分
    - momentum_20 > 0: 60分
    - momentum_20 > -5%: 40分
    - else: 20分
    """
    m20 = factors.get("momentum_20", 0)
    if m20 > 10:
        return 90
    elif m20 > 5:
        return 75
    elif m20 > 0:
        return 60
    elif m20 > -5:
        return 40
    return 20


def score_volume(factors: dict) -> float:
    """量能分（0-100）

    - volume_ratio > 2.0: 90分（放量）
    - volume_ratio > 1.5: 75分
    - volume_ratio > 1.0: 60分
    - volume_ratio > 0.7: 40分
    - else: 20分（缩量）
    """
    vr = factors.get("volume_ratio", 1.0)
    if vr > 2.0:
        return 90
    elif vr > 1.5:
        return 75
    elif vr > 1.0:
        return 60
    elif vr > 0.7:
        return 40
    return 20


def score_sector(sector_score: float) -> float:
    """板块分（0-100）

    直接使用 sector_score（已归一化到 0-100）。
    缺失时返回 50（中性）。
    """
    if sector_score is None or sector_score <= 0:
        return 50
    return max(0, min(100, sector_score))


def score_volatility_quality(factors: dict) -> float:
    """波动质量分（0-100）

    ATR 太高 = 高风险，太低 = 无机会。
    最佳区间：基于 20 日波动率（未年化）15%-35%。
    注：实现读取的是未年化的 20 日窗口波动率，不是年化值。
    """
    vol = factors.get("volatility_20", 25)
    if 15 <= vol <= 35:
        return 80  # 最佳波动区间
    elif 10 <= vol < 15 or 35 < vol <= 50:
        return 60  # 可接受
    elif vol > 50:
        return 30  # 波动过高
    return 40  # 波动过低


def score_structure(factors: dict) -> float:
    """结构分（0-100）

    - ADX > 25: 强趋势结构 → 80分
    - ADX > 20: 有趋势 → 65分
    - ADX > 15: 弱趋势 → 50分
    - ADX <= 15: 无趋势 → 30分
    - KDJ 金叉加分 +10
    """
    adx = factors.get("adx", 15)
    kdj_j = factors.get("kdj_j", 50)

    if adx > 25:
        base = 80
    elif adx > 20:
        base = 65
    elif adx > 15:
        base = 50
    else:
        base = 30

    # KDJ 金叉加分（J < 30 且向上）
    if kdj_j < 30:
        base += 10

    return min(100, base)


def six_factor_score(factors: dict, sector_score: float = 50) -> dict:
    """V6 六因子评分系统

    与 comprehensive_score（4维）并存。
    六因子更关注技术面和市场结构，适合趋势交易入场决策。

    Args:
        factors: 因子值 dict（来自 compute_all_signals["factors"]）
        sector_score: 板块评分 0-100（来自 sector_score.py）

    Returns:
        dict: {score, rating, breakdown, weights}
    """
    breakdown = {
        "trend": score_trend(factors),
        "momentum": score_momentum(factors),
        "volume": score_volume(factors),
        "sector": score_sector(sector_score),
        "volatility": score_volatility_quality(factors),
        "structure": score_structure(factors),
    }

    total = sum(
        breakdown[k] * SIX_FACTOR_WEIGHTS[k]
        for k in SIX_FACTOR_WEIGHTS
    )
    total = round(max(0, min(100, total)), 1)

    # E13 6F 评级后缀迁移 (2026-06-08, lib-incremental-audit §4.2):
    # 旧 6F 与 4F 共用相同中文评级 ("强烈买入" / "买入" / ...) — 下游仓位/风控
    # 若共用 rating 字符串会产生隐性偏差 (4F 强烈买入 vs 6F 强烈买入 阈值不同)
    # 修复: 6F 加 "(6F)" 后缀, 4F 保持不变
    if total >= SCORE_6F["rating_strong_buy"]:
        rating = "强烈买入(6F)"
    elif total >= SCORE_6F["rating_buy"]:
        rating = "买入(6F)"
    elif total >= SCORE_6F["rating_hold"]:
        rating = "持有(6F)"
    elif total >= SCORE_6F["rating_cautious"]:
        rating = "谨慎(6F)"
    else:
        rating = "回避(6F)"

    return {
        "score": total,
        "rating": rating,
        "breakdown": breakdown,
        "weights": SIX_FACTOR_WEIGHTS,
    }
