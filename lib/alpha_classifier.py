"""Alpha 来源分类器 —— V7 核心模块

对每笔交易归因 Alpha 来源：
- trend: 趋势 Alpha（市场上涨 + 突破新高）
- sector_rotation: 板块轮动 Alpha（板块评分高 + 排名靠前）
- sentiment: 情绪 Alpha（市场情绪高涨 + 北向流入）
- mean_reversion: 均值回归 Alpha（超卖 + 通道下轨）
- event_driven: 事件驱动 Alpha（涨停潮 + 资金集中流入）
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

ALPHA_TYPES = {
    "trend": "趋势 Alpha",
    "sector_rotation": "板块轮动 Alpha",
    "sentiment": "情绪 Alpha",
    "mean_reversion": "均值回归 Alpha",
    "event_driven": "事件驱动 Alpha",
}


@dataclass
class AlphaClassification:
    """Alpha 分类结果"""
    alpha_type: str = "unknown"       # 主要 Alpha 来源
    alpha_types: List[str] = field(default_factory=list)  # 所有匹配的类型
    confidence: float = 0.0           # 归因置信度 0-1
    reason: str = ""                  # 归因理由


def classify_alpha_source(
    regime: str,
    factors: dict,
    channel_filter: dict,
    data_factors: dict,
    combined_signal: dict = None,
) -> AlphaClassification:
    """根据交易时的市场条件，归因 Alpha 来源

    Args:
        regime: 市场状态（TREND_UP/MEAN_REVERSION/...）
        factors: 因子值 dict（来自 compute_all_signals["factors"]）
        channel_filter: 通道过滤 dict（来自 compute_all_signals["channel_filter"]）
        data_factors: 扩展因子 dict（来自 compute_all_signals["data_factors"]）
        combined_signal: 组合信号 dict（来自 compute_all_signals["combined"]）

    Returns:
        AlphaClassification
    """
    matches = []
    reasons = []

    # ========== 1. 趋势 Alpha ==========
    # 条件：TREND_UP + 趋势强度>2 + 动量>0
    trend_strength = factors.get("trend_strength", 0)
    momentum_20 = factors.get("momentum_20", 0)
    if regime == "TREND_UP" and trend_strength > 2 and momentum_20 > 0:
        matches.append("trend")
        reasons.append(f"TREND_UP+趋势强({trend_strength:.1f})+动量正({momentum_20:.1f}%)")

    # ========== 2. 板块轮动 Alpha ==========
    # 条件：板块评分>70
    sector_score = data_factors.get("sector_score", 50)
    if sector_score > 70:
        matches.append("sector_rotation")
        reasons.append(f"板块评分高({sector_score:.0f})")

    # ========== 3. 情绪 Alpha ==========
    # 条件：市场情绪>0.3 或 北向资金活跃
    sentiment = data_factors.get("market_sentiment", {})
    sentiment_score = sentiment.get("sentiment_score", 0) if isinstance(sentiment, dict) else 0
    northbound = data_factors.get("northbound", {})
    nb_ratio = northbound.get("activity_ratio", 1.0) if isinstance(northbound, dict) else 1.0
    if sentiment_score > 0.3 or nb_ratio > 1.5:
        matches.append("sentiment")
        reasons.append(f"情绪({sentiment_score:.2f})/北向活跃({nb_ratio:.2f})")

    # ========== 4. 均值回归 Alpha ==========
    # 条件：KC位置<0.2 或 RSI<30 + MEAN_REVERSION
    kc_pos = channel_filter.get("kc_pos", 0.5)
    rsi = factors.get("rsi_14", 50)
    if (kc_pos < 0.2 or rsi < 30) and regime == "MEAN_REVERSION":
        matches.append("mean_reversion")
        reasons.append(f"超卖(KC={kc_pos:.2f},RSI={rsi:.0f})+MEAN_REVERSION")

    # ========== 5. 事件驱动 Alpha ==========
    # 条件：涨停潮(>30) 或 资金大幅流入
    zt_count = sentiment.get("zt_count", 0) if isinstance(sentiment, dict) else 0
    up_ratio = sentiment.get("up_down_ratio", 1.0) if isinstance(sentiment, dict) else 1.0
    if zt_count > 30 or up_ratio > 3.0:
        matches.append("event_driven")
        reasons.append(f"涨停潮({zt_count})/涨跌比({up_ratio:.1f})")

    # ========== 确定主要 Alpha 类型 ==========
    if not matches:
        # 无明确匹配，根据信号方向给一个默认分类
        signal = combined_signal.get("signal", "neutral") if combined_signal else "neutral"
        if signal == "bullish":
            return AlphaClassification(
                alpha_type="trend",
                alpha_types=["trend"],
                confidence=0.3,
                reason="无明确Alpha来源，默认归类为趋势",
            )
        return AlphaClassification(
            alpha_type="unknown",
            alpha_types=[],
            confidence=0.1,
            reason="无匹配Alpha来源",
        )

    # 主要类型 = 第一个匹配（优先级：趋势>板块>情绪>均值回归>事件）
    primary = matches[0]
    confidence = min(0.5 + len(matches) * 0.15, 0.95)

    return AlphaClassification(
        alpha_type=primary,
        alpha_types=matches,
        confidence=round(confidence, 2),
        reason="; ".join(reasons),
    )


def classify_trade_historical(
    trade_date: str,
    code: str,
    hist_df=None,
    regime: str = "MEAN_REVERSION",
    sector: str = "unknown",
    as_of_date: Optional[str] = None,
) -> AlphaClassification:
    """回测用：根据历史数据事后归因

    简化版本：主要根据市场状态和板块信息归因，
    不需要实时数据（data_factors 在回测中不可用）。

    P1-5 修复（2026-06-06）：加 as_of_date 参数防前视
    - 不传 as_of_date：打 warning log 提醒，使用 hist_df 最后一行（不切片）
    - 传 as_of_date：按 date <= as_of_date 切片后再算因子（防未来数据回灌）
    - 旧行为兼容：不传时不阻断，但归因结果有未来数据风险

    Args:
        trade_date: 入场日期
        code: 股票代码
        hist_df: 历史K线数据（可选，用于计算因子）
        regime: 入场时的市场状态
        sector: 入场时的板块名称
        as_of_date: 归因时间点（YYYY-MM-DD），可选
    """
    factors = {}
    channel_filter = {"kc_pos": 0.5}
    data_factors = {"sector_score": 50}

    if hist_df is not None and len(hist_df) > 20:
        # P1-5：按 as_of_date 切片（防前视）
        if as_of_date is not None and "date" in hist_df.columns:
            hist_df = hist_df[hist_df["date"] <= as_of_date]
            if len(hist_df) < 20:
                logger.warning(
                    f"as_of_date={as_of_date} 切片后 hist_df 不足 20 行（{len(hist_df)}），"
                    f"跳过因子计算"
                )
                return classify_alpha_source(
                    regime=regime,
                    factors={},
                    channel_filter=channel_filter,
                    data_factors=data_factors,
                )
        elif as_of_date is None:
            # 防前视：未传 as_of_date 时警告
            logger.warning(
                f"classify_trade_historical 未传 as_of_date（trade_date={trade_date}），"
                f"可能使用未来数据，请传 as_of_date='{trade_date}' 防前视"
            )

        try:
            from factors import calc_rsi, calc_adx
            close = hist_df["close"]
            factors["rsi_14"] = float(calc_rsi(close, 14).iloc[-1]) if len(close) > 14 else 50
            factors["trend_strength"] = float(
                (close.tail(5).mean() / close.tail(20).mean() - 1) * 100
            ) if close.tail(20).mean() > 0 else 0
            factors["momentum_20"] = float(
                (close.iloc[-1] / close.iloc[-21] - 1) * 100
            ) if len(close) > 21 else 0

            if "high" in hist_df.columns and "low" in hist_df.columns:
                factors["adx"] = float(calc_adx(hist_df["high"], hist_df["low"], close))
        except Exception as e:
            # P1-3 修复：不再静默吞错
            logger.error(
                f"classify_trade_historical 因子计算失败: {type(e).__name__}: {e}",
                exc_info=True,
            )

    return classify_alpha_source(
        regime=regime,
        factors=factors,
        channel_filter=channel_filter,
        data_factors=data_factors,
    )
