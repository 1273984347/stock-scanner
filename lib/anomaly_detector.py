from lib.data_utils import safe_div

"""财报异常检测 —— 10 条规则检测财务异常

来源：financial-report-reader skill
输入：多期财务数据（利润表/资产负债表/现金流量表）
输出：异常列表 [{rule, severity, trigger, detail}]
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "ar_threshold": 0.20,        # 应收增速 - 营收增速
    "inv_threshold": 0.15,       # 存货增速 - 营收增速
    "ocf_ratio": 0.50,           # 经营现金流 / 净利润
    "margin_threshold": 0.05,    # 毛利率环比变化
    "net_margin_threshold": 0.03, # 净利率环比变化
    "debt_ceiling": 0.70,        # 资产负债率
    "current_floor": 1.00,       # 流动比率
    "goodwill_ceiling": 0.30,    # 商誉/总资产
    "neg_ocf_periods": 2,        # 连续负经营现金流期数
    "ap_threshold": 0.20,        # 应付增速 - 成本增速
}


def _safe_div(a, b):
    return safe_div(a, b)


def _pct_change(new, old):
    if old is None or new is None or old == 0:
        return None
    return (new - old) / abs(old)


def detect_anomalies(periods_data: List[Dict], thresholds: Dict = None) -> List[Dict]:
    """检测财务异常

    Args:
        periods_data: 多期数据列表，每期包含:
            {
                "period": "2024Q1",
                "revenue": float, "cost_of_revenue": float,
                "operating_income": float, "net_income": float,
                "accounts_receivable": float, "inventory": float,
                "total_current_assets": float, "total_assets": float,
                "total_current_liabilities": float, "total_liabilities": float,
                "total_equity": float, "goodwill": float,
                "accounts_payable": float,
                "operating_cash_flow": float, "capex": float,
            }
        thresholds: 自定义阈值（可选）

    Returns:
        [{"rule": str, "period": str, "severity": str, "detail": str, "implication": str}]
    """
    if not periods_data or len(periods_data) < 2:
        return []

    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    alerts = []
    n = len(periods_data)

    # 预计算各期比率
    ratios = []
    for d in periods_data:
        r = {}
        rev = d.get("revenue")
        cogs = d.get("cost_of_revenue")
        r["gross_margin"] = _safe_div((rev - cogs) if rev is not None and cogs is not None else None, rev)
        r["net_margin"] = _safe_div(d.get("net_income"), rev)
        r["debt_ratio"] = _safe_div(d.get("total_liabilities"), d.get("total_assets"))
        r["current_ratio"] = _safe_div(d.get("total_current_assets"), d.get("total_current_liabilities"))
        r["goodwill_ratio"] = _safe_div(d.get("goodwill"), d.get("total_assets"))
        ratios.append(r)

    # 预计算同比变化
    def yoy(key):
        vals = [d.get(key) for d in periods_data]
        return [_pct_change(vals[i], vals[i - 1]) if i > 0 else None for i in range(n)]

    rev_yoy = yoy("revenue")
    ar_yoy = yoy("accounts_receivable")
    inv_yoy = yoy("inventory")
    cogs_yoy = yoy("cost_of_revenue")
    ap_yoy = yoy("accounts_payable")

    # Rule 1: 应收暴增
    for i in range(n):
        if ar_yoy[i] is not None and rev_yoy[i] is not None:
            gap = ar_yoy[i] - rev_yoy[i]
            if gap > t["ar_threshold"]:
                alerts.append({
                    "rule": "应收账款暴增",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "high" if gap > t["ar_threshold"] * 2 else "medium",
                    "trigger": f"应收增速-营收增速 = {gap:.1%}",
                    "detail": f"应收增速 {ar_yoy[i]:.1%} 远超营收增速 {rev_yoy[i]:.1%}",
                    "implication": "可能存在收入确认激进或客户回款困难",
                })

    # Rule 2: 现金流背离利润
    for i in range(n):
        ni = periods_data[i].get("net_income")
        ocf = periods_data[i].get("operating_cash_flow")
        if ni is not None and ocf is not None:
            if ni > 0 and ocf < 0:
                alerts.append({
                    "rule": "现金流背离利润",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "high",
                    "trigger": f"净利润>0 但经营现金流<0",
                    "detail": f"净利润 {ni:,.0f} 为正但经营现金流 {ocf:,.0f} 为负",
                    "implication": "盈利质量堪忧，利润可能含大量应计项",
                })
            elif ni > 0 and ocf > 0 and ocf / ni < t["ocf_ratio"]:
                alerts.append({
                    "rule": "现金流背离利润",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "medium",
                    "trigger": f"OCF/净利润 = {ocf/ni:.2f} < {t['ocf_ratio']}",
                    "detail": f"经营现金流/净利润 = {ocf/ni:.2f}",
                    "implication": "净利润向现金的转化效率偏低",
                })

    # Rule 3: 存货堆积
    for i in range(n):
        if inv_yoy[i] is not None and rev_yoy[i] is not None:
            gap = inv_yoy[i] - rev_yoy[i]
            if gap > t["inv_threshold"]:
                alerts.append({
                    "rule": "存货积压",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "medium",
                    "trigger": f"存货增速-营收增速 = {gap:.1%}",
                    "detail": f"存货增速 {inv_yoy[i]:.1%} 高于营收增速 {rev_yoy[i]:.1%}",
                    "implication": "产品可能滞销，存在跌价减值风险",
                })

    # Rule 4: 毛利率突变
    for i in range(1, n):
        gm_now = ratios[i].get("gross_margin")
        gm_prev = ratios[i - 1].get("gross_margin")
        if gm_now is not None and gm_prev is not None:
            delta = gm_now - gm_prev
            if abs(delta) > t["margin_threshold"]:
                alerts.append({
                    "rule": "毛利率突变",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "medium",
                    "trigger": f"环比变化 {delta:+.1%}",
                    "detail": f"毛利率 {gm_prev:.1%} → {gm_now:.1%}",
                    "implication": "定价能力或成本结构发生显著变化",
                })

    # Rule 5: 净利率突变
    for i in range(1, n):
        nm_now = ratios[i].get("net_margin")
        nm_prev = ratios[i - 1].get("net_margin")
        if nm_now is not None and nm_prev is not None:
            delta = nm_now - nm_prev
            if abs(delta) > t["net_margin_threshold"]:
                alerts.append({
                    "rule": "净利率突变",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "medium",
                    "trigger": f"环比变化 {delta:+.1%}",
                    "detail": f"净利率 {nm_prev:.1%} → {nm_now:.1%}",
                    "implication": "费用管控异常或非经常性损益影响",
                })

    # Rule 6: 持续负经营现金流
    streak = 0
    for i in range(n):
        ocf = periods_data[i].get("operating_cash_flow")
        if ocf is not None and ocf < 0:
            streak += 1
            if streak >= t["neg_ocf_periods"]:
                alerts.append({
                    "rule": "经营现金流持续为负",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "high" if streak >= 3 else "medium",
                    "trigger": f"连续 {streak} 期为负",
                    "detail": f"经营现金流已连续 {streak} 个报告期为负",
                    "implication": "企业自身造血能力不足",
                })
        else:
            streak = 0

    # Rule 7: 商誉占比过高
    for i in range(n):
        gw = ratios[i].get("goodwill_ratio")
        if gw is not None and gw > t["goodwill_ceiling"]:
            alerts.append({
                "rule": "商誉占比过高",
                "period": periods_data[i].get("period", f"第{i+1}期"),
                "severity": "medium",
                "trigger": f"商誉/总资产 = {gw:.1%} > {t['goodwill_ceiling']:.0%}",
                "detail": f"商誉占比 {gw:.1%}",
                "implication": "若并购标的业绩不达预期，存在大额减值风险",
            })

    # Rule 8: 资产负债率过高
    for i in range(n):
        dr = ratios[i].get("debt_ratio")
        if dr is not None and dr > t["debt_ceiling"]:
            alerts.append({
                "rule": "资产负债率过高",
                "period": periods_data[i].get("period", f"第{i+1}期"),
                "severity": "medium",
                "trigger": f"资产负债率 {dr:.1%} > {t['debt_ceiling']:.0%}",
                "detail": f"资产负债率 {dr:.1%}",
                "implication": "财务杠杆偏高，偿债压力较大",
            })

    # Rule 9: 流动比率过低
    for i in range(n):
        cr = ratios[i].get("current_ratio")
        if cr is not None and cr < t["current_floor"]:
            alerts.append({
                "rule": "流动比率过低",
                "period": periods_data[i].get("period", f"第{i+1}期"),
                "severity": "medium" if cr > 0.7 else "high",
                "trigger": f"流动比率 {cr:.2f} < {t['current_floor']:.2f}",
                "detail": f"流动比率 {cr:.2f}",
                "implication": "短期偿债能力不足，存在流动性风险",
            })

    # Rule 10: 应付账款异常
    for i in range(1, n):
        if ap_yoy[i] is not None and cogs_yoy[i] is not None:
            gap = abs(ap_yoy[i] - cogs_yoy[i])
            if gap > t["ap_threshold"]:
                if ap_yoy[i] > cogs_yoy[i]:
                    msg = "应付增速远超成本增速，可能依赖延长账期缓解资金压力"
                else:
                    msg = "应付增速远低于成本增速，可能面临供应商要求缩短账期"
                alerts.append({
                    "rule": "应付账款异常",
                    "period": periods_data[i].get("period", f"第{i+1}期"),
                    "severity": "low",
                    "trigger": f"AP增速-成本增速 差值 {gap:.1%}",
                    "detail": f"应付增速 {ap_yoy[i]:.1%} vs 成本增速 {cogs_yoy[i]:.1%}",
                    "implication": msg,
                })

    return alerts


def anomaly_summary(alerts: List[Dict]) -> Dict:
    """异常检测汇总"""
    if not alerts:
        return {"total": 0, "high": 0, "medium": 0, "low": 0, "score_penalty": 0}

    high = sum(1 for a in alerts if a["severity"] == "high")
    medium = sum(1 for a in alerts if a["severity"] == "medium")
    low = sum(1 for a in alerts if a["severity"] == "low")

    # 扣分制：high -3, medium -1.5, low -0.5
    score_penalty = high * 3.0 + medium * 1.5 + low * 0.5

    return {
        "total": len(alerts),
        "high": high,
        "medium": medium,
        "low": low,
        "score_penalty": round(score_penalty, 1),
    }
