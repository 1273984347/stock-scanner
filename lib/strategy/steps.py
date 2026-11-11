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

from lib.data.data_layer import (
    get_stock_hist_multi_source, get_realtime_quotes, _get_financial_data,
    get_index_hist, get_news_signals,
)
from lib.scoring.scorer import (
    score_fundamental, score_capital_flow, score_news, score_technical,
    get_board_type, calc_deviation_value,
    check_anomaly_threshold,
)
# RF-01 Phase 4 阶段 B：四维评分决策统一经网关（flag 默认 off => 数值零变更）
from lib.scoring.scoring_gateway import score_4d
from lib.risk.selection_constraints import DeterministicConstraints
from lib.ml.ml_predictor import MLPredictor

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
    except Exception as e:
        logger.debug(f"[steps] 历史数据获取失败 [{code}]: {e}")
        hist = pd.DataFrame()

    # 2. 实时行情
    rt_row = None
    try:
        realtime = get_realtime_quotes()
        if not realtime.empty:
            mask = realtime["代码"].astype(str) == code
            if mask.any():
                rt_row = realtime[mask].iloc[0]
    except Exception as e:
        logger.debug(f"[steps] 实时行情获取失败 [{code}]: {e}")

    # 3. 基本面数据（akshare/baostock）
    fin_data = None
    try:
        _fin_series = _get_financial_data(code)
        if _fin_series is not None:
            fin_data = _fin_series.to_dict()
    except Exception as e:
        logger.debug(f"[steps] 基本面数据获取失败 [{code}]: {e}")

    # 3.5 TDX 本地财务数据（补充 ROE/毛利率/负债率等，优先本地无需网络）
    try:
        from lib.scoring.data_tdx_fin_adapter import get_stock_fundamentals
        tdx_fundamentals = get_stock_fundamentals(code)
        if tdx_fundamentals:
            if fin_data is None:
                fin_data = {}
            fin_data["tdx_fundamentals"] = tdx_fundamentals
    except Exception as e:
        logger.debug(f"[steps] TDX 财务数据获取失败 [{code}]: {e}")

    # 4. 指数历史（用于偏移值监控）
    idx_df = pd.DataFrame()
    try:
        idx_df = get_index_hist("000001", days=20)
    except Exception as e:
        logger.debug(f"[steps] 指数历史获取失败: {e}")

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
    from lib.scoring.technical_signals import calc_composite_verdict

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
    from lib.strategy.strategies import full_score

    score_result = full_score(code, hist, factors, rt_row)
    return score_result


def compute_4d_dimensions(code: str, factors: Dict,
                          fin_data: Optional[pd.Series] = None) -> Dict[str, float]:
    """提取四维评分的四个维度分（纯计算，不含评分决策）。

    RF-01 Phase 4 阶段 B：本函数体是 `score_without_realtime` 原逻辑的**原样搬家**，
    未改动任何一行计算。抽出的目的是让消费者只依赖网关 `score_4d`，
    而不是把策略层函数注册进评分内核（那会模糊「适配器 = 评分实现」的边界）。

    ⚠️ 这里每个分支都影响评分口径：任何「顺手优化」必须走 ADR + L5/L6 对拍。

    Args:
        code: 股票代码
        factors: 因子字典
        fin_data: 基本面数据（PE/PB/市值 + TDX财务指标）

    Returns:
        {fundamental, capital_flow, news, technical} 四个 0–10 的维度分
    """
    mom_5 = factors.get("momentum_5", 0) / 100
    mom_20 = factors.get("momentum_20", 0) / 100

    tech_score = score_technical(return_10d=mom_5, return_20d=mom_20)

    # 提取基本面评分参数
    pe = None
    roe = None
    net_growth = None
    gross_margin = None
    debt_ratio = None
    ocf_healthy = True

    if fin_data is not None:
        pe = fin_data.get("pe") if isinstance(fin_data, dict) else getattr(fin_data, "pe", None)
        if fin_data.get("gross_margin") is not None and gross_margin is None:
            gross_margin = fin_data.get("gross_margin")

        # OCF 健康判断：经营现金流 > 0
        ocf = fin_data.get("operating_cash_flow")
        ocf_healthy = ocf is not None and ocf > 0

        # 从 TDX 财务指标补充 ROE/毛利率/负债率
        # fin_data 可能是 dict（包含 tdx_fundamentals）或 pd.Series（FinancialCache 缓存格式）
        tdx_fundamentals = fin_data.get("tdx_fundamentals") if isinstance(fin_data, dict) else None
        if tdx_fundamentals:
            prof = tdx_fundamentals.get("profitability", {})
            solv = tdx_fundamentals.get("solvency", {})
            growth = tdx_fundamentals.get("growth", {})
            if roe is None:
                roe = prof.get("ROE")
            if gross_margin is None:
                gross_margin = prof.get("gross_margin")
            if debt_ratio is None:
                debt_ratio = solv.get("debt_to_asset")
            if net_growth is None:
                net_growth = growth.get("net_income_growth")
        else:
            # pd.Series 格式：ROE 在 _roe 字段，负债率在 _debt_to_asset 字段
            if roe is None:
                roe = fin_data.get("_roe") if not isinstance(fin_data, dict) else None
            if debt_ratio is None:
                debt_ratio = fin_data.get("_debt_to_asset") if not isinstance(fin_data, dict) else None
            if net_growth is None:
                net_growth = fin_data.get("revenue_growth") if not isinstance(fin_data, dict) else None

    fund_score = score_fundamental(
        pe=pe, roe=roe, net_growth=net_growth,
        gross_margin=gross_margin, debt_ratio=debt_ratio, ocf_healthy=ocf_healthy
    )

    # 资金流评分（3层fallback：东方财富→腾讯估算→akshare）
    cap_score = 0.0
    try:
        from lib.data.data_layer import get_stock_fund_flow
        ff = get_stock_fund_flow(code)
        cap_score = score_capital_flow(
            main_net_ratio=ff.get("main_net_ratio", 0),
            net_inflow_5d=ff.get("net_inflow_5d", 0),
        )
    except Exception as e:
        logger.debug(f"[steps] 资金流评分失败 [{code}]: {e}")

    news_sig = {"major_positive": False, "institutional_buy": False,
                 "no_negative": True, "earnings_positive": False}
    news_score = score_news(**news_sig)

    return {
        "fundamental": fund_score,
        "capital_flow": cap_score,
        "news": news_score,
        "technical": tech_score,
    }


def score_without_realtime(code: str, factors: Dict,
                           fin_data: Optional[pd.Series] = None) -> Dict[str, Any]:
    """无实时行情的四维评分路径 —— `pipeline` 消费者。

    维度计算在 `compute_4d_dimensions`（原逻辑原样搬出），评分决策统一经
    `scoring_gateway.score_4d(...)`（P7-3 收口后唯一路径 = ScoringKernel，
    数值由 L5 基线锁定与抽出前完全一致）。
    """
    dims = compute_4d_dimensions(code, factors, fin_data)
    return score_4d(
        dims["fundamental"], dims["capital_flow"],
        dims["news"], dims["technical"],
    )


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
    from lib.scoring.judge_panel import JudgePanel

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
    except Exception as e:
        logger.debug(f"[steps] 偏移值监控失败 [{code}]: {e}")

    return False, 0.0
