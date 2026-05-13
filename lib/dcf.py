"""DCF 估值模型 —— 自由现金流折现 + 敏感性分析

来源：cashflow-valuation skill
纯 Python 标准库，零外部依赖
"""
import json
from typing import Dict, List, Optional


def dcf_valuation(fcf: float, growth: float, discount: float,
                  terminal_growth: float = 0.03, years: int = 5,
                  debt: float = 0, cash: float = 0,
                  shares: float = 1) -> Dict:
    """DCF 估值

    Args:
        fcf: 基期自由现金流（正数）
        growth: 年增长率（如 0.15 = 15%）
        discount: 折现率 / WACC（如 0.10 = 10%）
        terminal_growth: 永续增长率（默认 3%）
        years: 预测期年数（默认 5）
        debt: 净负债
        cash: 现金及等价物
        shares: 总股本

    Returns:
        估值结果 dict
    """
    if fcf <= 0:
        return {"error": "FCF 必须为正数"}
    if terminal_growth >= discount:
        return {"error": "永续增长率必须小于折现率"}

    # 预测期 FCF
    forecast_fcfs = []
    for y in range(1, years + 1):
        forecast_fcfs.append(fcf * (1 + growth) ** y)

    # 预测期现值
    pv_fcfs = sum(fcf_y / (1 + discount) ** y for y, fcf_y in enumerate(forecast_fcfs, 1))

    # 终值（Gordon 模型）
    terminal_fcf = forecast_fcfs[-1] * (1 + terminal_growth)
    terminal_value = terminal_fcf / (discount - terminal_growth)
    pv_terminal = terminal_value / (1 + discount) ** years

    # 企业价值
    enterprise_value = pv_fcfs + pv_terminal

    # 股权价值
    equity_value = enterprise_value - debt + cash
    per_share = equity_value / shares if shares > 0 else 0

    return {
        "forecast_fcfs": [round(f, 2) for f in forecast_fcfs],
        "pv_forecast": round(pv_fcfs, 2),
        "terminal_value": round(terminal_value, 2),
        "pv_terminal": round(pv_terminal, 2),
        "enterprise_value": round(enterprise_value, 2),
        "equity_value": round(equity_value, 2),
        "per_share_value": round(per_share, 2),
        "assumptions": {
            "fcf": fcf, "growth": growth, "discount": discount,
            "terminal_growth": terminal_growth, "years": years,
            "debt": debt, "cash": cash, "shares": shares,
        },
    }


def sensitivity_matrix(fcf: float, growth_range: List[float],
                       discount_range: List[float],
                       terminal_growth: float = 0.03, years: int = 5,
                       debt: float = 0, cash: float = 0,
                       shares: float = 1) -> Dict:
    """敏感性分析矩阵

    Args:
        fcf: 基期 FCF
        growth_range: 增长率列表（如 [0.05, 0.10, 0.15, 0.20]）
        discount_range: 折现率列表（如 [0.08, 0.10, 0.12]）

    Returns:
        {"matrix": [[...]], "growth_range": [...], "discount_range": [...]}
    """
    matrix = []
    for g in growth_range:
        row = []
        for r in discount_range:
            if g >= r:
                row.append(None)  # 无效组合
            else:
                result = dcf_valuation(fcf, g, r, terminal_growth, years, debt, cash, shares)
                row.append(result.get("per_share_value"))
        matrix.append(row)

    return {
        "matrix": matrix,
        "growth_range": growth_range,
        "discount_range": discount_range,
        "label": "每股价值（增长率 × 折现率）",
    }


def margin_of_safety(intrinsic_value: float, market_price: float) -> Dict:
    """安全边际计算

    Returns:
        {"intrinsic_value": float, "market_price": float,
         "margin_of_safety": float, "upside": float}
    """
    if intrinsic_value <= 0:
        return {"error": "内在价值必须为正"}

    margin = (intrinsic_value - market_price) / intrinsic_value
    upside = (intrinsic_value - market_price) / market_price if market_price > 0 else 0

    return {
        "intrinsic_value": round(intrinsic_value, 2),
        "market_price": round(market_price, 2),
        "margin_of_safety": round(margin, 4),
        "upside": round(upside, 4),
        "verdict": "低估" if margin > 0.25 else ("合理" if margin > 0 else "高估"),
    }
