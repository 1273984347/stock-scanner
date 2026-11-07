"""风控检查模块

从 pipeline.py 提取的组合风控和因子健康检查逻辑。
"""
import pandas as pd
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"


def check_portfolio_risk(results: dict, shibor_mult: float = 1.0) -> dict:
    """检查组合风险

    Args:
        results: 策略结果字典 {策略名: DataFrame}
        shibor_mult: 货币市场宽松程度乘数（由 pipeline.py 宏观过滤器传入）

    Returns:
        包含风控告警、风险预算、建议的字典
    """
    from lib.data.data_layer import get_market_sentiment
    from factors import evaluate_macro_gate, calc_market_exposure
    from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
    from lib.data.data_layer import _load_cache

    all_codes = set()
    total_candidates = 0
    strategy_exposure = {}
    for name, df in results.items():
        if df.empty:
            continue
        if "代码" in df.columns:
            codes = df["代码"].tolist()
            all_codes.update(codes)
            total_candidates += len(codes)
            strategy_exposure[name] = len(codes)
    if not all_codes:
        return {"alerts": [], "risk_budget": {}, "recommendation": "无持仓，风控通过"}

    alerts = []
    for name, count in strategy_exposure.items():
        if count > 5:
            alerts.append({"level": "WARNING", "rule": "策略集中度",
                           "message": f"{name} 选出 {count} 只，单策略集中度过高"})
    if total_candidates > 20:
        alerts.append({"level": "WARNING", "rule": "总持仓数",
                       "message": f"总候选 {total_candidates} 只，分散度可能不足"})
    sentiment = get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)
    if macro.state == "BLOCK_NEW_RISK":
        alerts.append({"level": "CRITICAL", "rule": "宏观门控", "message": macro.reasoning})
    elif macro.state == "ALLOW_REDUCED":
        alerts.append({"level": "WARNING", "rule": "宏观门控",
                       "message": f"{macro.reasoning}，建议仓位乘数 {macro.position_multiplier}"})

    _risk_manager = VolCorrRiskManager(VolCorrRiskConfig())
    vol_regime = "normal"
    try:
        idx_cache = _load_cache("hs300/cache", max_age_hours=4)
        if not idx_cache.empty and len(idx_cache) >= 60 and "close" in idx_cache.columns:
            exposure = calc_market_exposure(idx_cache["close"])
            vol_regime = exposure.get("state", "neutral")
    except Exception as e:
        logger.debug(f"市场暴露度计算失败，使用默认 vol_regime=normal: {e}")
    combined_mult = shibor_mult * macro.position_multiplier
    base_budget = _risk_manager.get_risk_budget(
        vol_regime if vol_regime in ("high", "normal", "low") else "normal", 0.5)
    risk_budget = {k: v * combined_mult for k, v in base_budget.items()}

    critical_count = sum(1 for a in alerts if a["level"] == "CRITICAL")
    if critical_count > 0:
        recommendation = "存在严重风险预警，建议暂停新开仓"
    elif len(alerts) > 2:
        recommendation = "多重预警，建议减仓操作"
    elif alerts:
        recommendation = "轻度预警，可谨慎操作"
    else:
        recommendation = "风控通过，可正常交易"

    return {
        "alerts": alerts,
        "risk_budget": risk_budget,
        "recommendation": recommendation,
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": combined_mult,
            "sentiment_mult": macro.position_multiplier,
            "shibor_mult": shibor_mult,
        },
        "strategy_exposure": strategy_exposure,
        "total_candidates": total_candidates
    }


def check_factor_health(results: dict) -> dict:
    """检查因子健康度

    Args:
        results: 策略结果字典

    Returns:
        包含覆盖率、退化因子、建议的字典
    """
    eval_cache = CACHE_DIR / "factor_evaluation.json"
    last_eval = None
    need_full_eval = True
    if eval_cache.exists():
        try:
            import json
            data = json.loads(eval_cache.read_text(encoding="utf-8"))
            last_eval = data.get("date", "")
            if last_eval:
                last_dt = datetime.strptime(last_eval, "%Y-%m-%d")
                if (datetime.now() - last_dt).days < 7:
                    need_full_eval = False
        except Exception as e:
            logger.debug(f"评估缓存读取失败，将执行完整评估: {e}")

    coverage = {}
    degenerate = []
    for name, df in results.items():
        if df.empty:
            continue
        factor_cols = [c for c in df.columns if c in (
            "RSI", "动量5", "置信度", "综合评分")]
        for col in factor_cols:
            if col not in coverage:
                coverage[col] = {"total": 0, "non_null": 0, "unique": 0}
            series = df[col].dropna()
            coverage[col]["total"] += len(df)
            coverage[col]["non_null"] += len(series)
            coverage[col]["unique"] += series.nunique()

    for col, stats in coverage.items():
        if stats["total"] > 0:
            cov_rate = stats["non_null"] / stats["total"]
            if cov_rate > 0 and stats["unique"] <= 1:
                degenerate.append(col)

    recommendation = "因子质量正常"
    if degenerate:
        recommendation = f"警告：因子 {degenerate} 无区分度，建议检查数据源"
    elif need_full_eval:
        recommendation = "建议运行完整因子评估（python run.py --evaluate）"

    return {
        "coverage": coverage,
        "degenerate_factors": degenerate,
        "last_evaluation": last_eval,
        "need_full_evaluation": need_full_eval,
        "recommendation": recommendation
    }
