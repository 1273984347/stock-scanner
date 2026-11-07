from lib.data_utils import safe_div

"""基本面分析 —— 27 个财务指标 + 杜邦分解

来源：stock-finance-profiler skill
输入：利润表/资产负债表/现金流量表数据（dict 或 Series）
输出：分类指标 dict + 杜邦分解结果
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional


def _safe_div(num, den):
    return safe_div(num, den)


def compute_fundamentals(income: Dict, balance: Dict, cashflow: Dict = None,
                         market: Dict = None, prior: Dict = None) -> Dict:
    """计算 27 个财务指标

    Args:
        income: 利润表 {"revenue", "cost_of_goods_sold", "operating_income",
                "net_income", "interest_expense", "depreciation_amortization",
                "income_tax"}
        balance: 资产负债表 {"total_assets", "total_liabilities", "shareholders_equity",
                 "current_assets", "current_liabilities", "inventory",
                 "accounts_receivable", "cash_and_equivalents"}
        cashflow: 现金流量表 {"operating_cash_flow", "capital_expenditure"}
        market: 市场数据 {"shares_outstanding", "stock_price"}
        prior: 上期数据 {"revenue", "net_income"}

    Returns:
        分类指标 dict
    """
    cashflow = cashflow or {}
    market = market or {}
    prior = prior or {}

    rev = income.get("revenue")
    cogs = income.get("cost_of_goods_sold")
    oi = income.get("operating_income")
    ni = income.get("net_income")
    ie = income.get("interest_expense")
    da = income.get("depreciation_amortization")
    tax = income.get("income_tax")

    ta = balance.get("total_assets")
    tl = balance.get("total_liabilities")
    eq = balance.get("shareholders_equity")
    ca = balance.get("current_assets")
    cl = balance.get("current_liabilities")
    inv = balance.get("inventory")
    ar = balance.get("accounts_receivable")
    cash = balance.get("cash_and_equivalents")

    ocf = cashflow.get("operating_cash_flow")
    capex = cashflow.get("capital_expenditure")

    shares = market.get("shares_outstanding")
    price = market.get("stock_price")

    gp = (rev - cogs) if rev is not None and cogs is not None else None
    ebit = oi if oi is not None else (
        (ni + tax + ie) if all(v is not None for v in [ni, tax, ie]) else None)
    ebitda = (ebit + da) if ebit is not None and da is not None else None
    wc = (ca - cl) if ca is not None and cl is not None else None

    results = {}

    # 盈利能力（6）
    results["profitability"] = {
        "ROE": _safe_div(ni, eq),
        "ROA": _safe_div(ni, ta),
        "gross_margin": _safe_div(gp, rev),
        "net_margin": _safe_div(ni, rev),
        "operating_margin": _safe_div(oi, rev),
        "ebitda_margin": _safe_div(ebitda, rev),
    }

    # 偿债能力（4）
    results["solvency"] = {
        "debt_to_asset": _safe_div(tl, ta),
        "debt_to_equity": _safe_div(tl, eq),
        "equity_multiplier": _safe_div(ta, eq),
        "interest_coverage": _safe_div(ebit, ie),
    }

    # 流动性（3）
    results["liquidity"] = {
        "current_ratio": _safe_div(ca, cl),
        "quick_ratio": _safe_div((ca - inv) if ca is not None and inv is not None else None, cl),
        "cash_ratio": _safe_div(cash, cl),
    }

    # 效率（6）
    inv_turnover = _safe_div(cogs, inv)
    ar_turnover = _safe_div(rev, ar)
    results["efficiency"] = {
        "asset_turnover": _safe_div(rev, ta),
        "inventory_turnover": inv_turnover,
        "days_inventory": _safe_div(365, inv_turnover),
        "receivables_turnover": ar_turnover,
        "days_sales_outstanding": _safe_div(365, ar_turnover),
        "working_capital_turnover": _safe_div(rev, wc) if wc is not None and wc > 0 else None,
    }

    # 每股（3）
    results["per_share"] = {
        "EPS": _safe_div(ni, shares),
        "BVPS": _safe_div(eq, shares),
        "PE": _safe_div(price, _safe_div(ni, shares)) if shares is not None and shares != 0 else None,
    }

    # 现金流（3）
    results["cash_flow"] = {
        "ocf_ratio": _safe_div(ocf, cl),
        "free_cash_flow": (ocf - capex) if ocf is not None and capex is not None else None,
        "cf_to_ni": _safe_div(ocf, ni),
    }

    # 成长（2）
    results["growth"] = {
        "revenue_growth": _safe_div(
            (rev - prior.get("revenue")) if rev is not None and prior.get("revenue") is not None else None,
            prior.get("revenue")),
        "net_income_growth": _safe_div(
            (ni - prior.get("net_income")) if ni is not None and prior.get("net_income") is not None else None,
            prior.get("net_income")),
    }

    # 杜邦分解
    net_margin = _safe_div(ni, rev)
    asset_turnover = _safe_div(rev, ta)
    eq_mult = _safe_div(ta, eq)
    roe_decomposed = (net_margin * asset_turnover * eq_mult) if all(
        v is not None for v in [net_margin, asset_turnover, eq_mult]) else None

    results["dupont"] = {
        "net_profit_margin": net_margin,
        "asset_turnover": asset_turnover,
        "equity_multiplier": eq_mult,
        "roe_decomposed": roe_decomposed,
        "roe_direct": _safe_div(ni, eq),
    }

    return results


def fundamental_score(fundamentals: Dict) -> float:
    """根据财务指标计算基本面综合得分（0-10 分）

    评分规则（来自股票综合分析器 skill）：
    - ROE > 15% → +2
    - 净利增长 > 20% → +1.5
    - 毛利率 > 30% → +1.5
    - 资产负债率 < 50% → +1.5
    - 经营现金流健康（OCF/NI > 0.5）→ +1
    - 流动比率 > 1.5 → +1
    - 利息保障 > 3 → +0.5
    - 自由现金流为正 → +1
    """
    score = 0.0

    prof = fundamentals.get("profitability", {})
    solv = fundamentals.get("solvency", {})
    liq = fundamentals.get("liquidity", {})
    cf = fundamentals.get("cash_flow", {})
    growth = fundamentals.get("growth", {})

    # ROE > 15%
    roe = prof.get("ROE")
    if roe is not None and roe > 0.15:
        score += 2.0

    # 净利增长 > 20%
    ni_growth = growth.get("net_income_growth")
    if ni_growth is not None and ni_growth > 0.20:
        score += 1.5

    # 毛利率 > 30%
    gm = prof.get("gross_margin")
    if gm is not None and gm > 0.30:
        score += 1.5

    # 资产负债率 < 50%
    debt = solv.get("debt_to_asset")
    if debt is not None and debt < 0.50:
        score += 1.5

    # 经营现金流健康
    ocf_ratio = cf.get("cf_to_ni")
    if ocf_ratio is not None and ocf_ratio > 0.5:
        score += 1.0

    # 流动比率 > 1.5
    cr = liq.get("current_ratio")
    if cr is not None and cr > 1.5:
        score += 1.0

    # 利息保障 > 3
    ic = solv.get("interest_coverage")
    if ic is not None and ic > 3:
        score += 0.5

    # 自由现金流为正
    fcf = cf.get("free_cash_flow")
    if fcf is not None and fcf > 0:
        score += 1.0

    return min(score, 10.0)
