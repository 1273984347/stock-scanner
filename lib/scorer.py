"""综合评分 —— 四维加权评分 + 偏移值异常监控

来源：股票综合分析器 skill
维度：基本面 35% + 资金面 35% + 消息面 20% + 技术面 10%
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional


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
               no_negative: bool = True,
               earnings_positive: bool = False) -> float:
    """消息面评分（0-10 分，权重 20%）"""
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


def comprehensive_score(fundamental: float, capital_flow: float,
                        news: float, technical: float) -> Dict:
    """综合评分

    Returns:
        {"score": float, "rating": str, "breakdown": dict}
        score: 0-10 加权总分
        rating: 强烈买入/买入/持有/谨慎/回避
    """
    weighted = (fundamental * 0.35 + capital_flow * 0.35 +
                news * 0.20 + technical * 0.10)

    if weighted >= 8:
        rating = "强烈买入"
    elif weighted >= 6:
        rating = "买入"
    elif weighted >= 4:
        rating = "持有"
    elif weighted >= 2:
        rating = "谨慎"
    else:
        rating = "回避"

    return {
        "score": round(weighted, 2),
        "rating": rating,
        "breakdown": {
            "fundamental": round(fundamental, 2),
            "capital_flow": round(capital_flow, 2),
            "news": round(news, 2),
            "technical": round(technical, 2),
        },
        "weights": {"fundamental": 0.35, "capital_flow": 0.35, "news": 0.20, "technical": 0.10},
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
        score_adj = -2.0
    elif abs_dev >= threshold * 0.8:
        level = "near_trigger"
        score_adj = -1.5
    elif abs_dev >= threshold * 0.6:
        level = "warning"
        score_adj = -1.0
    elif abs_dev >= threshold * 0.4:
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
