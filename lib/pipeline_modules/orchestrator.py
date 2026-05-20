"""Pipeline 核心编排模块

从 pipeline.py 提取的核心编排逻辑。
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def run_all_strategies(
    preset: str = "balanced",
    symbols: list = None,
    include_ml: bool = True,
    include_alpha: bool = False,
) -> dict:
    """运行所有策略

    Args:
        preset: 策略预设 (balanced/aggressive/conservative)
        symbols: 股票代码列表，用于结果过滤（可选）
        include_ml: 是否启用ML增强评分
        include_alpha: 是否执行Alpha分析

    Returns:
        包含结果、风控、优化、分析的字典
    """
    from config import get_config
    from lib.data_layer import clean_cache, get_market_sentiment
    from lib.strategies import (
        get_zt_candidates, get_volume_breakout_candidates,
        get_hot_board_candidates, get_lhb_candidates, get_multi_factor_candidates,
    )
    from .risk_checker import check_portfolio_risk, check_factor_health
    from .optimizer import optimize_portfolio

    config = get_config(preset)
    results = {}
    clean_cache(max_age_hours=24)
    sentiment = get_market_sentiment()
    logger.info(f"市场情绪分数: {sentiment:.2f}")

    strategies = {
        "涨停板复盘": get_zt_candidates,
        "量价突破": get_volume_breakout_candidates,
        "热门板块": get_hot_board_candidates,
        "龙虎榜": get_lhb_candidates,
        "多因子选股": get_multi_factor_candidates,
    }

    for name, func in strategies.items():
        logger.info(f"运行策略: {name}")
        try:
            df = func(config)
            if not df.empty:
                # 如果指定了symbols，过滤结果
                if symbols:
                    df = df[df["代码"].isin(symbols)] if "代码" in df.columns else df
                if not df.empty:
                    results[name] = df
                    logger.info(f"  -> {len(df)} 只候选股")
                else:
                    logger.info(f"  -> 无候选股（过滤后）")
            else:
                logger.info(f"  -> 无候选股")
        except Exception as e:
            logger.error(f"  -> 策略执行失败: {e}")

    risk_check = check_portfolio_risk(results)
    factor_health = check_factor_health(results)
    portfolio = optimize_portfolio(results)

    alpha_analysis = {}
    if include_alpha:
        from lib.pipeline import _run_alpha_analysis
        alpha_analysis = _run_alpha_analysis(results)
    else:
        alpha_analysis = {}

    ml_ranking = {}
    if include_ml:
        from lib.pipeline import _enrich_with_ml_scores, _check_monitor_alerts
        ml_ranking = _enrich_with_ml_scores(results)
        # 因子监控预警
        _check_monitor_alerts(ml_ranking)
    else:
        ml_ranking = {}

    scan_result = {
        "results": results,
        "risk_check": risk_check,
        "factor_health": factor_health,
        "portfolio": portfolio,
        "alpha_analysis": alpha_analysis,
        "ml_ranking": ml_ranking,
        "preset": preset,
    }

    try:
        from lib.audit_logger import log_scan
        log_scan(scan_result)
    except Exception as e:
        logger.debug(f"审计日志写入失败: {e}")

    return scan_result
