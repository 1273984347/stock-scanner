"""Pipeline 步骤分解模块

从 get_stock_score 拆分出来的独立步骤函数，每个函数承担单一职责。

职责划分：
- fetch_stock_data: 数据获取（hist/realtime/financial/index）
- calculate_factors: 因子计算（signals/technical）
- score_with_realtime: 有实时行情的评分路径
- score_without_realtime: 无实时行情的四维评分路径
- evaluate_with_judge_ml: 裁判团 + ML 综合评审
- check_offset_anomaly: A股偏移值异常监控
"""
import pandas as pd
import logging
from typing import Tuple, Dict, Optional, Any

from lib.data_layer import (
    get_stock_hist_multi_source, get_realtime_quotes, _get_financial_data,
    get_index_hist, get_news_signals,
)
from lib.scorer import (
    score_fundamental, score_capital_flow, score_news, score_technical,
    comprehensive_score, get_board_type, calc_deviation_value,
    check_anomaly_threshold,
)
from lib.constraints import DeterministicConstraints
from lib.ml_predictor import MLPredictor

logger = logging.getLogger(__name__)


def fetch_stock_data(code: str, days: int = 180) -> Tuple[pd.DataFrame, Optional[pd.Series], Optional[pd.Series], pd.DataFrame]:
    """数据获取步骤

    Args:
        code: 股票代码
        days: 历史数据天数

    Returns:
        Tuple[hist, rt_row, fin_data, idx_df]
        - hist: 日线历史数据
        - rt_row: 实时行情行（无则为 None）
        - fin_data: 基本面数据（无则为 None）
        - idx_df: 指数历史数据（用于偏移值监控）
    """
    # 1. 历史数据
    try:
        hist = get_stock_hist_multi_source(code, days=days, source="auto", prefer_adjusted=True)
    except Exception:
        hist = pd.DataFrame()

    # 2. 实时行情
    rt_row = None
    try:
        realtime = get_realtime_quotes()
        if not realtime.empty:
            mask = realtime["代码"].astype(str) == code
            if mask.any():
                rt_row = realtime[mask].iloc[0]
    except Exception:
        pass

    # 3. 基本面数据
    fin_data = None
    try:
        fin_data = _get_financial_data(code)
    except Exception:
        pass

    # 4. 指数历史（用于偏移值监控）
    idx_df = pd.DataFrame()
    try:
        idx_df = get_index_hist("000001", days=20)
    except Exception:
        pass

    return hist, rt_row, fin_data, idx_df


def calculate_factors(hist: pd.DataFrame, factor_config=None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """因子计算步骤

    Args:
        hist: 日线历史数据
        factor_config: 因子配置（可选）

    Returns:
        Tuple[factors, tech]
        - factors: 因子字典
        - tech: 技术信号字典
    """
    from factors import compute_all_signals
    from lib.technical_signals import calc_composite_verdict

    if hist.empty:
        return {}, {}

    # 因子计算
    result = compute_all_signals(hist, factor_config=factor_config)
    if not result:
        return {}, {}

    # 技术信号
    tech = calc_composite_verdict(hist)

    return result.get("factors", {}), tech


def score_with_realtime(code: str, hist: pd.DataFrame, factors: Dict, rt_row: pd.Series,
                        fin_data=None) -> Dict[str, Any]:
    """有实时行情的完整评分路径

    Args:
        code: 股票代码
        hist: 日线历史数据
        factors: 因子字典
        rt_row: 实时行情行
        fin_data: 基本面数据（可选）

    Returns:
        评分结果字典
    """
    from lib.strategies import full_score

    score_result = full_score(code, hist, factors, rt_row)
    return score_result


def score_without_realtime(factors: Dict) -> Dict[str, Any]:
    """无实时行情的四维评分路径

    Args:
        factors: 因子字典

    Returns:
        评分结果字典
    """
    mom_5 = factors.get("momentum_5", 0) / 100
    mom_20 = factors.get("momentum_20", 0) / 100

    tech_score = score_technical(return_10d=mom_5, return_20d=mom_20)
    fund_score = score_fundamental()
    cap_score = score_capital_flow()
    news_sig = {"major_positive": False, "institutional_buy": False,
                 "no_negative": True, "earnings_positive": False}
    news_score = score_news(**news_sig)

    score_result = comprehensive_score(fund_score, cap_score, news_score, tech_score)
    return score_result


def evaluate_with_judge_ml(code: str, hist: pd.DataFrame, factors: Dict,
                           fin_data=None, ml_prediction: Dict = None) -> Dict[str, Any]:
    """裁判团 + ML 综合评审

    Args:
        code: 股票代码
        hist: 日线历史数据
        factors: 因子字典
        fin_data: 基本面数据（可选）
        ml_prediction: ML 预测结果（可选）

    Returns:
        裁判团评审结果
    """
    from lib.judge_panel import JudgePanel

    judge_result = {}
    try:
        pe_val = factors.get("pe_ttm", None)
        if fin_data is not None and pe_val is None:
            pe_val = fin_data.get("pe")

        panel = JudgePanel(
            factors={"pe_ttm": pe_val, **factors},
            fundamentals={}, market_data=hist,
        )
        judge_result = panel.evaluate_with_ml(ml_prediction or {})
    except Exception as e:
        logger.warning(f"[pipeline] 裁判团评分失败 [{code}]: {e}")

    return judge_result


def check_offset_anomaly(code: str, hist: pd.DataFrame, factors: Dict,
                         idx_df: pd.DataFrame = None) -> Tuple[bool, float]:
    """A股偏移值异常监控

    Args:
        code: 股票代码
        hist: 日线历史数据
        factors: 因子字典
        idx_df: 指数历史数据（可选）

    Returns:
        Tuple[offset_alert, score_adjustment]
        - offset_alert: 是否触发偏移告警
        - score_adjustment: 评分调整值
    """
    if idx_df is None or idx_df.empty or "close" not in idx_df.columns:
        return False, 0.0

    try:
        stock_return = factors.get("momentum_5", 0) / 100
        idx_return = float(idx_df["close"].iloc[-1] / idx_df["close"].iloc[0] - 1)
        deviation_info = calc_deviation_value(stock_return, idx_return)
        board_type = get_board_type(code)
        threshold_result = check_anomaly_threshold(deviation_info["deviation"], board_type)

        if threshold_result.get("triggered"):
            return True, threshold_result.get("score_adjustment", 0)
    except Exception:
        pass

    return False, 0.0
