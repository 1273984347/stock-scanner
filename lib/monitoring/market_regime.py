"""
市场体制检测器 —— 六态模型

参考：因子.txt (V4修正版)

六种状态：
- TREND_UP:       积极做多
- MEAN_REVERSION: 短线回归
- HIGH_VOLATILITY: 高波动，少做等突破
- LOW_VOLATILITY: 低波动，适度加仓
- TREND_DOWN:     不做多
- RISK_OFF:       极端波动，禁止一切开仓
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple
import logging
import threading

logger = logging.getLogger(__name__)


# ========== 六态常量 ==========

TREND_UP = "TREND_UP"
MEAN_REVERSION = "MEAN_REVERSION"
HIGH_VOLATILITY = "HIGH_VOLATILITY"
LOW_VOLATILITY = "LOW_VOLATILITY"
TREND_DOWN = "TREND_DOWN"
RISK_OFF = "RISK_OFF"

# P1-1: COMPRESSION 别名反语义（压缩 ≠ 高波动）已删除。旧代码点应改用 LOW_VOLATILITY。

REGIME_STATES = [TREND_UP, MEAN_REVERSION, HIGH_VOLATILITY, LOW_VOLATILITY, TREND_DOWN, RISK_OFF]

# ========== 迟滞机制缓存 ==========
_cached_regime: str = HIGH_VOLATILITY  # 当前稳定状态
_regime_candidate: str = HIGH_VOLATILITY  # 候选状态（正在累积确认）
_regime_stable_count: int = 0  # 候选状态连续出现次数
_hysteresis_lock = threading.Lock()


def reset_hysteresis():
    """重置迟滞状态（测试用）"""
    global _cached_regime, _regime_candidate, _regime_stable_count
    with _hysteresis_lock:
        _cached_regime = HIGH_VOLATILITY
        _regime_candidate = HIGH_VOLATILITY
        _regime_stable_count = 0

# 状态 → 仓位系数（默认值，可被 position_size 覆盖）
REGIME_POSITION_SCALAR = {
    TREND_UP: (0.75, 1.00),       # (min, max)
    MEAN_REVERSION: (0.25, 0.50),
    HIGH_VOLATILITY: (0.00, 0.25),
    LOW_VOLATILITY: (0.50, 0.75),
    TREND_DOWN: (0.00, 0.00),
    RISK_OFF: (0.00, 0.00),       # 极端波动，禁止一切开仓
}

# P2-#2 修复: 六态判定阈值集中（消除 magic number）
# 参考：因子.txt V4 修正版
REGIME_THRESHOLDS = {
    # RISK_OFF: 极端 ATR 扩张
    "RISK_OFF_ATR_RATIO": 2.0,
    "RISK_OFF_BASE_CONFIDENCE": 0.7,
    "RISK_OFF_CONFIDENCE_SLOPE": 0.2,
    "RISK_OFF_CONFIDENCE_CAP": 0.95,
    # TREND_UP: 价格>EMA + EMA向上 + ATR扩张 + Donchian上移
    "TREND_UP_PRICE_VS_EMA": 0.01,
    "TREND_UP_EMA_SLOPE": 0.05,
    "TREND_UP_ATR_RATIO": 1.1,
    "TREND_UP_BASE_CONFIDENCE": 0.5,
    "TREND_UP_TREND_WEIGHT": 0.4,
    "TREND_UP_ATR_WEIGHT": 0.3,
    # TREND_DOWN: 价格<EMA + EMA向下 + Donchian下移
    "TREND_DOWN_PRICE_VS_EMA": -0.01,
    "TREND_DOWN_EMA_SLOPE": -0.05,
    "TREND_DOWN_BASE_CONFIDENCE": 0.5,
    "TREND_DOWN_TREND_WEIGHT": 0.4,
    # HIGH_VOLATILITY: 波动率扩张 + 方向不明确
    "HIGH_VOL_CONTRACTION": 0.6,
    "HIGH_VOL_TREND_ABS_MAX": 0.3,
    "HIGH_VOL_BASE_CONFIDENCE": 0.4,
    "HIGH_VOL_CONFIDENCE_WEIGHT": 0.4,
    "HIGH_VOL_CONFIDENCE_CAP": 0.85,
    # LOW_VOLATILITY: 波动率收缩 + 趋势不明确
    "LOW_VOL_ATR_RATIO": 0.7,
    "LOW_VOL_TREND_ABS_MAX": 0.2,
    "LOW_VOL_BASE_CONFIDENCE": 0.4,
    "LOW_VOL_INV_ATR_WEIGHT": 0.3,
    "LOW_VOL_CONFIDENCE_CAP": 0.80,
    # MEAN_REVERSION: EMA走平 + 震荡 + Donchian收窄 + Keltner反复触碰
    "MEAN_REV_EMA_SLOPE_ABS": 0.1,
    "MEAN_REV_TREND_ABS_MAX": 0.4,
    "MEAN_REV_KELTNER_TOUCHES": 3,
    "MEAN_REV_BASE_CONFIDENCE": 0.4,
    "MEAN_REV_INV_CONTRACTION_WEIGHT": 0.3,
    "MEAN_REV_CONFIDENCE_CAP": 0.80,
    # Fallback: 根据趋势得分
    "FALLBACK_TREND_THRESHOLD": 0.3,
    "FALLBACK_CONFIDENCE": 0.50,
}


def _calculate_donchian(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """计算 Donchian 通道"""
    result = pd.DataFrame(index=df.index)
    result["dc_upper"] = df["high"].rolling(period).max()
    result["dc_lower"] = df["low"].rolling(period).min()
    result["dc_mid"] = (result["dc_upper"] + result["dc_lower"]) / 2
    return result


def _detect_trend(df: pd.DataFrame, kc_mid: pd.Series) -> float:
    """检测趋势方向和强度

    Returns:
        float: -1 (下跌) ~ 0 (震荡) ~ +1 (上涨)
    """
    if kc_mid is None or len(kc_mid) < 5:
        return 0.0

    # 5周期斜率（百分比）
    slope = kc_mid.pct_change(5).iloc[-1] if len(kc_mid) > 0 else 0.0

    # 归一化到 -1~+1（斜率 ±1% 视为强）
    trend = max(-1, min(1, slope * 50))
    return float(trend)


def _detect_joint_contraction(kc_range: pd.Series, atr: pd.Series, lookback: int = 20) -> float:
    """检测 ATR 与 Keltner 通道的联合收缩程度

    P2-#3 修复: 原名 `_detect_compression` 误导 — 该函数实际是计算两个 ratio 的
    均值（ATR 收缩比 + 通道宽度收缩比），并非单纯的"压缩"含义。改名以消除歧义。

    Returns:
        float: 0 (不收缩) ~ 1 (高度收缩)
    """
    if len(kc_range) < lookback or len(atr) < lookback:
        return 0.0

    # ATR 收缩比（当前 vs 历史平均）
    atr_ratio = atr.iloc[-1] / atr.iloc[-lookback:].mean()

    # 通道宽度收缩比
    range_ratio = kc_range.iloc[-1] / kc_range.iloc[-lookback:].mean()

    # 两者都收缩 = 联合收缩
    contraction = (1 - atr_ratio) * 0.5 + (1 - range_ratio) * 0.5
    return float(max(0, min(1, contraction)))


def _detect_volatility_expansion(atr: pd.Series, lookback: int = 20) -> bool:
    """检测波动率是否正在扩张"""
    if len(atr) < lookback:
        return False
    return bool(atr.iloc[-1] > atr.iloc[-lookback:].mean() * 1.2)


def detect_market_regime(
    index_df: pd.DataFrame,
    kc_mid: pd.Series = None,
    kc_upper: pd.Series = None,
    kc_lower: pd.Series = None,
    atr: pd.Series = None,
    use_hysteresis: bool = False,
    hysteresis_days: int = 3,
) -> Dict:
    """检测市场体制

    Args:
        index_df: 指数行情数据（必须包含 high, low, close）
        kc_mid: 凯尔特纳中轨（可选，不传则自动计算）
        kc_upper: 凯尔特纳上轨（可选）
        kc_lower: 凯尔特纳下轨（可选）
        atr: ATR（可选）
        use_hysteresis: 是否启用迟滞机制（默认 False，实盘建议 True）
        hysteresis_days: 连续多少天确认才切换状态（默认 3）

    Returns:
        Dict:
          - regime: str, 状态名称
          - confidence: float, 置信度 0~1
          - indicators: dict, 各指标值
          - position_scalar: (min, max), 建议仓位系数
    """
    global _cached_regime, _regime_candidate, _regime_stable_count

    if index_df is None or index_df.empty or len(index_df) < 30:
        return _default_regime()

    # 使用 T-1 数据判断市场状态（避免未来数据泄漏）
    # 实盘中当天收盘前无法知道今天的收盘价，必须用昨天的数据
    if len(index_df) < 2:
        return _default_regime()
    df = index_df.iloc[:-1]  # 排除最后一天（今天）
    close = df["close"]
    high = df["high"]
    low = df["low"]

    # 计算凯尔特纳通道（简化版）
    if kc_mid is None:
        kc_mid = close.ewm(span=21, adjust=False).mean()

    # 计算 ATR
    if atr is None:
        tr = np.zeros(len(high))
        tr[0] = high.iloc[0] - low.iloc[0]
        for i in range(1, len(high)):
            h_l = high.iloc[i] - low.iloc[i]
            h_pc = abs(high.iloc[i] - close.iloc[i - 1])
            l_pc = abs(low.iloc[i] - close.iloc[i - 1])
            tr[i] = max(h_l, h_pc, l_pc)
        atr = pd.Series(tr, index=df.index).ewm(span=14, adjust=False).mean()

    kc_range = kc_mid + 2 * atr - (kc_mid - 2 * atr)

    # 计算 Donchian
    dc = _calculate_donchian(df, period=20)
    dc_upper = dc["dc_upper"]
    dc_lower = dc["dc_lower"]

    # ========== 各项指标 ==========
    # 1. 价格 vs 21EMA
    price_vs_ema = float((close.iloc[-1] - kc_mid.iloc[-1]) / (kc_mid.iloc[-1] + 1e-9))

    # 2. EMA 斜率
    ema_slope = float(kc_mid.pct_change(5).iloc[-1] * 100)

    # 3. Donchian 方向
    dc_slope = float(dc["dc_mid"].pct_change(5).iloc[-1] * 100)

    # 4. ATR 变化
    atr_mean = atr.iloc[-20:].mean()
    atr_ratio = float(atr.iloc[-1] / atr_mean) if (pd.notna(atr_mean) and atr_mean > 0) else 1.0

    # 5. 联合收缩程度（ATR + 通道宽度）
    compression = _detect_joint_contraction(kc_range, atr)

    # 6. 波动率扩张
    vol_expanding = _detect_volatility_expansion(atr)

    # 7. 趋势得分
    trend_score = _detect_trend(index_df, kc_mid)

    # 8. Ride rail detection: price near upper Keltner band (deviation < 5%)
    keltner_upper = kc_mid + 2 * atr
    keltner_lower = kc_mid - 2 * atr
    ride_rail = bool(abs(close.iloc[-1] - keltner_upper.iloc[-1]) / keltner_upper.iloc[-1] < 0.05)

    # 8b. Keltner 反复触碰：近10天内价格多次触及上下轨
    recent_close = close.iloc[-10:]
    touches_upper = (recent_close >= keltner_upper.iloc[-10:] * 0.98).sum()
    touches_lower = (recent_close <= keltner_lower.iloc[-10:] * 1.02).sum()
    keltner_touches = int(touches_upper + touches_lower)

    # 9. Donchian channel narrowing check
    dc_width = dc_upper - dc_lower
    dc_width_ratio = dc_width.iloc[-1] / dc_width.iloc[-20:].mean() if dc_width.iloc[-20:].mean() > 0 else 1
    dc_narrow = dc_width_ratio < 0.7  # Donchian channel narrowed to <70% of historical

    # ========== 状态判断 ==========
    indicators = {
        "price_vs_ema": round(price_vs_ema, 4),
        "ema_slope": round(ema_slope, 4),
        "dc_slope": round(dc_slope, 4),
        "atr_ratio": round(atr_ratio, 4),
        "compression": round(compression, 4),
        "vol_expanding": vol_expanding,
        "trend_score": round(trend_score, 4),
        "ride_rail": ride_rail,
        "dc_width_ratio": round(dc_width_ratio, 4),
        "dc_narrow": dc_narrow,
        "keltner_touches": keltner_touches,
    }

    # RISK_OFF: 极端波动扩张
    T = REGIME_THRESHOLDS
    if atr_ratio > T["RISK_OFF_ATR_RATIO"]:
        regime = RISK_OFF
        confidence = min(
            T["RISK_OFF_CONFIDENCE_CAP"],
            T["RISK_OFF_BASE_CONFIDENCE"]
            + (atr_ratio - T["RISK_OFF_ATR_RATIO"]) * T["RISK_OFF_CONFIDENCE_SLOPE"]
        )

    # TREND_UP: 价格>EMA + EMA向上 + ATR扩张 + Donchian上移
    elif (price_vs_ema > T["TREND_UP_PRICE_VS_EMA"] and
            ema_slope > T["TREND_UP_EMA_SLOPE"] and
            dc_slope > 0 and
            atr_ratio > T["TREND_UP_ATR_RATIO"]):
        regime = TREND_UP
        confidence = min(
            T["RISK_OFF_CONFIDENCE_CAP"],
            T["TREND_UP_BASE_CONFIDENCE"]
            + abs(trend_score) * T["TREND_UP_TREND_WEIGHT"]
            + (atr_ratio - 1) * T["TREND_UP_ATR_WEIGHT"]
        )

    # TREND_DOWN: 价格<EMA + EMA向下 + Donchian下移
    elif (price_vs_ema < T["TREND_DOWN_PRICE_VS_EMA"] and
          ema_slope < T["TREND_DOWN_EMA_SLOPE"] and
          dc_slope < 0):
        regime = TREND_DOWN
        confidence = min(
            T["RISK_OFF_CONFIDENCE_CAP"],
            T["TREND_DOWN_BASE_CONFIDENCE"]
            + abs(trend_score) * T["TREND_DOWN_TREND_WEIGHT"]
        )

    # HIGH_VOLATILITY: 波动率扩张 + 方向不明确（原 COMPRESSION）
    elif compression > T["HIGH_VOL_CONTRACTION"] and abs(trend_score) < T["HIGH_VOL_TREND_ABS_MAX"]:
        regime = HIGH_VOLATILITY
        confidence = min(
            T["HIGH_VOL_CONFIDENCE_CAP"],
            T["HIGH_VOL_BASE_CONFIDENCE"]
            + compression * T["HIGH_VOL_CONFIDENCE_WEIGHT"]
        )

    # LOW_VOLATILITY: 波动率收缩 + 趋势不明确
    elif atr_ratio < T["LOW_VOL_ATR_RATIO"] and abs(trend_score) < T["LOW_VOL_TREND_ABS_MAX"]:
        regime = LOW_VOLATILITY
        confidence = min(
            T["LOW_VOL_CONFIDENCE_CAP"],
            T["LOW_VOL_BASE_CONFIDENCE"]
            + (1 - atr_ratio) * T["LOW_VOL_INV_ATR_WEIGHT"]
        )

    # MEAN_REVERSION: EMA走平 + 震荡 + Donchian收窄 + Keltner反复触碰
    elif (abs(ema_slope) < T["MEAN_REV_EMA_SLOPE_ABS"]
          and abs(trend_score) < T["MEAN_REV_TREND_ABS_MAX"]
          and dc_narrow
          and keltner_touches >= T["MEAN_REV_KELTNER_TOUCHES"]):
        regime = MEAN_REVERSION
        confidence = min(
            T["MEAN_REV_CONFIDENCE_CAP"],
            T["MEAN_REV_BASE_CONFIDENCE"]
            + (1 - compression) * T["MEAN_REV_INV_CONTRACTION_WEIGHT"]
        )

    # Fallback: 根据趋势得分
    else:
        if trend_score > T["FALLBACK_TREND_THRESHOLD"]:
            regime = TREND_UP
        elif trend_score < -T["FALLBACK_TREND_THRESHOLD"]:
            regime = TREND_DOWN
        else:
            regime = MEAN_REVERSION
        confidence = T["FALLBACK_CONFIDENCE"]

    indicators["confidence"] = round(float(confidence), 3)

    # ========== 迟滞机制 ==========
    # 实盘中频繁切换状态会导致策略混乱，需要连续 N 天确认才切换
    with _hysteresis_lock:
        if use_hysteresis:
            if regime == _regime_candidate:
                _regime_stable_count += 1
            else:
                _regime_candidate = regime
                _regime_stable_count = 1

            if _regime_stable_count >= hysteresis_days:
                _cached_regime = regime
            else:
                # 未达到确认天数，保持当前稳定状态
                regime = _cached_regime
                indicators["hysteresis_pending"] = True
                indicators["hysteresis_count"] = _regime_stable_count
        else:
            _cached_regime = regime

    position_scalar = REGIME_POSITION_SCALAR[regime]

    return {
        "regime": regime,
        "confidence": float(confidence),
        "indicators": indicators,
        "position_scalar": position_scalar,
        "atr_ratio": atr_ratio,
        "compression": compression,
    }


def _default_regime() -> Dict:
    """默认状态（数据不足时）— 使用缓存状态或 HIGH_VOLATILITY

    P2-#4 修复: position_scalar 字段也跟随 _cached_regime（实盘可能给错误风险敞口）
    """
    effective_regime = _cached_regime if _cached_regime else HIGH_VOLATILITY
    return {
        "regime": effective_regime,
        "confidence": 0.3,
        "indicators": {},
        "position_scalar": REGIME_POSITION_SCALAR[effective_regime],
        "atr_ratio": 1.0,
        "compression": 0.0,
    }


def get_regime_description(regime: str) -> str:
    """获取状态描述"""
    descriptions = {
        TREND_UP: "上升趋势：积极做多，仓位75%-100%",
        MEAN_REVERSION: "均值回归：短线回归操作，仓位25%-50%",
        HIGH_VOLATILITY: "高波动：少做等突破，仓位0%-25%",
        LOW_VOLATILITY: "低波动：适度加仓，仓位50%-75%",
        TREND_DOWN: "下跌趋势：不做多，仓位0%",
        RISK_OFF: "极端波动：市场恐慌，禁止一切开仓",
    }
    return descriptions.get(regime, "未知状态")


# ========== HS300 驱动的市场状态检测 ==========

HS300_CODE = "000300"  # 沪深300指数代码


def detect_market_regime_from_hs300(days: int = 60, use_hysteresis: bool = False, hysteresis_days: int = 3) -> Dict:
    """获取 HS300 数据，输出 V6 六态体制

    这是市场状态检测的主入口。V6 框架要求：
    - 市场状态由沪深300指数决定，而非个股
    - TREND_DOWN 时禁止开仓

    Args:
        days: 获取历史数据天数（默认60）

    Returns:
        Dict:
          - regime: str, 状态名称
          - confidence: float, 置信度 0~1
          - indicators: dict, 各指标值
          - position_scalar: (min, max), 建议仓位系数
          - index_code: str, "000300"
    """
    try:
        from lib.data.data_layer import get_index_hist
        index_df = get_index_hist(HS300_CODE, days=days)
        if index_df.empty or len(index_df) < 30:
            return _default_regime()
        result = detect_market_regime(index_df, use_hysteresis=use_hysteresis, hysteresis_days=hysteresis_days)
        result["index_code"] = HS300_CODE
        return result
    except Exception as e:
        logger.debug(f"[market_regime] HS300 市场状态检测失败: {e}")
        return _default_regime()
