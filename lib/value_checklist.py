"""价值投资评分 —— 巴菲特/格雷厄姆 20 项检查清单

来源：value-invest-scorer skill
4 个维度 × 5 项 = 20 项，每项 1-5 分，总分 100
"""
from typing import Dict, Optional


def value_score(financial_data: Dict) -> Dict:
    """20 项价值投资评分

    Args:
        financial_data: {
            # 护城河（需人工判断或外部数据）
            "brand_score": 1-5,           # 品牌价值
            "switching_cost_score": 1-5,  # 转换成本
            "network_effect_score": 1-5,  # 网络效应
            "cost_advantage_score": 1-5,  # 成本优势
            "entry_barrier_score": 1-5,   # 进入壁垒

            # 管理层（需人工判断或外部数据）
            "integrity_score": 1-5,       # 诚信
            "capital_allocation_score": 1-5, # 资本配置
            "insider_ownership_score": 1-5,  # 内部人持股
            "track_record_score": 1-5,    # 履历
            "culture_score": 1-5,         # 文化

            # 财务（可自动计算）
            "roe_stability_score": 1-5,   # ROE 长期稳定性
            "debt_score": 1-5,            # 负债水平
            "fcf_score": 1-5,             # 自由现金流
            "margin_score": 1-5,          # 利润率趋势
            "earnings_quality_score": 1-5,# 盈利质量

            # 估值（可自动计算）
            "margin_of_safety_score": 1-5,# 安全边际
            "pe_pb_percentile_score": 1-5,# PE/PB 百分位
            "fcf_yield_score": 1-5,       # FCF 收益率
            "earnings_yield_score": 1-5,  # 盈利收益率
            "dividend_score": 1-5,        # 股息率
        }

    Returns:
        {"total": int, "rating": str, "stars": int, "dimensions": dict}
    """
    dimensions = {
        "moat": {
            "label": "护城河",
            "items": [
                ("品牌价值", financial_data.get("brand_score", 3)),
                ("转换成本", financial_data.get("switching_cost_score", 3)),
                ("网络效应", financial_data.get("network_effect_score", 3)),
                ("成本优势", financial_data.get("cost_advantage_score", 3)),
                ("进入壁垒", financial_data.get("entry_barrier_score", 3)),
            ],
        },
        "management": {
            "label": "管理层",
            "items": [
                ("诚信与股东导向", financial_data.get("integrity_score", 3)),
                ("资本配置能力", financial_data.get("capital_allocation_score", 3)),
                ("内部人持股", financial_data.get("insider_ownership_score", 3)),
                ("履历记录", financial_data.get("track_record_score", 3)),
                ("文化与接班人", financial_data.get("culture_score", 3)),
            ],
        },
        "financials": {
            "label": "财务健康",
            "items": [
                ("ROE 稳定性", financial_data.get("roe_stability_score", 3)),
                ("负债水平", financial_data.get("debt_score", 3)),
                ("自由现金流", financial_data.get("fcf_score", 3)),
                ("利润率趋势", financial_data.get("margin_score", 3)),
                ("盈利质量", financial_data.get("earnings_quality_score", 3)),
            ],
        },
        "valuation": {
            "label": "估值",
            "items": [
                ("安全边际", financial_data.get("margin_of_safety_score", 3)),
                ("PE/PB 百分位", financial_data.get("pe_pb_percentile_score", 3)),
                ("FCF 收益率", financial_data.get("fcf_yield_score", 3)),
                ("盈利收益率", financial_data.get("earnings_yield_score", 3)),
                ("股息率", financial_data.get("dividend_score", 3)),
            ],
        },
    }

    total = 0
    for dim in dimensions.values():
        dim["score"] = sum(score for _, score in dim["items"])
        total += dim["score"]

    if total >= 85:
        rating, stars = "强烈推荐", 5
    elif total >= 70:
        rating, stars = "值得关注", 4
    elif total >= 55:
        rating, stars = "一般", 3
    elif total >= 40:
        rating, stars = "谨慎", 2
    else:
        rating, stars = "回避", 1

    return {
        "total": total,
        "max": 100,
        "rating": rating,
        "stars": stars,
        "dimensions": dimensions,
    }


def auto_score_financials(roe_history: list = None, debt_ratio: float = None,
                          fcf_positive_years: int = 0, gross_margin: float = None,
                          ocf_to_ni: float = None) -> Dict:
    """自动计算财务维度评分（5 项中的可量化部分）

    Args:
        roe_history: 近 N 年 ROE 列表
        debt_ratio: 资产负债率
        fcf_positive_years: FCF 为正的年数
        gross_margin: 毛利率
        ocf_to_ni: 经营现金流/净利润

    Returns:
        {"roe_stability_score": int, "debt_score": int, ...}
    """
    scores = {}

    # ROE 稳定性：10 年 > 15% = 5, > 10% = 4, > 5% = 3
    if roe_history:
        avg_roe = sum(roe_history) / len(roe_history)
        if avg_roe > 0.15 and all(r > 0.10 for r in roe_history):
            scores["roe_stability_score"] = 5
        elif avg_roe > 0.10:
            scores["roe_stability_score"] = 4
        elif avg_roe > 0.05:
            scores["roe_stability_score"] = 3
        else:
            scores["roe_stability_score"] = 2

    # 负债水平：< 30% = 5, < 50% = 4, < 70% = 3
    if debt_ratio is not None:
        if debt_ratio < 0.30:
            scores["debt_score"] = 5
        elif debt_ratio < 0.50:
            scores["debt_score"] = 4
        elif debt_ratio < 0.70:
            scores["debt_score"] = 3
        else:
            scores["debt_score"] = 2

    # 自由现金流：连续 5 年为正 = 5, 3-4 年 = 4, 1-2 年 = 3
    if fcf_positive_years >= 5:
        scores["fcf_score"] = 5
    elif fcf_positive_years >= 3:
        scores["fcf_score"] = 4
    elif fcf_positive_years >= 1:
        scores["fcf_score"] = 3
    else:
        scores["fcf_score"] = 1

    # 利润率：> 40% = 5, > 30% = 4, > 20% = 3
    if gross_margin is not None:
        if gross_margin > 0.40:
            scores["margin_score"] = 5
        elif gross_margin > 0.30:
            scores["margin_score"] = 4
        elif gross_margin > 0.20:
            scores["margin_score"] = 3
        else:
            scores["margin_score"] = 2

    # 盈利质量：OCF/NI > 1.2 = 5, > 1.0 = 4, > 0.8 = 3
    if ocf_to_ni is not None:
        if ocf_to_ni > 1.2:
            scores["earnings_quality_score"] = 5
        elif ocf_to_ni > 1.0:
            scores["earnings_quality_score"] = 4
        elif ocf_to_ni > 0.8:
            scores["earnings_quality_score"] = 3
        else:
            scores["earnings_quality_score"] = 2

    return scores
