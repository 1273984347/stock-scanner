"""
市场体制检测器 —— 四态模型

参考：因子.txt (V4修正版)

四种状态：
- TREND_UP:    积极做多
- MEAN_REVERSION: 短线回归
- COMPRESSION: 少做等突破
- TREND_DOWN:  不做多
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple


# ========== 四态常量 ==========

TREND_UP = "TREND_UP"
MEAN_REVERSION = "MEAN_REVERSION"
COMPRESSION = "COMPRESSION"
TREND_DOWN = "TREND_DOWN"

REGIME_STATES = [TREND_UP, MEAN_REVERSION, COMPRESSION, TREND_DOWN]

# 状态 → 仓位系数（默认值，可被 position_size 覆盖）
REGIME_POSITION_SCALAR = {
    TREND_UP: (0.75, 1.00),       # (min, max)
    MEAN_REVERSION: (0.25, 0.50),
    COMPRESSION: (0.00, 0.25),
    TREND_DOWN: (0.00, 0.00),
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


def _detect_compression(kc_range: pd.Series, atr: pd.Series, lookback: int = 20) -> float:
    """检测压缩程度

    Returns:
        float: 0 (不压缩) ~ 1 (高度压缩)
    """
    if len(kc_range) < lookback or len(atr) < lookback:
        return 0.0

    # ATR 收缩比（当前 vs 历史平均）
    atr_ratio = atr.iloc[-1] / atr.iloc[-lookback:].mean()

    # 通道宽度收缩比
    range_ratio = kc_range.iloc[-1] / kc_range.iloc[-lookback:].mean()

    # 两者都收缩 = 压缩
    compression = (1 - atr_ratio) * 0.5 + (1 - range_ratio) * 0.5
    return float(max(0, min(1, compression)))


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
) -> Dict:
    """检测市场体制

    Args:
        index_df: 指数行情数据（必须包含 high, low, close）
        kc_mid: 凯尔特纳中轨（可选，不传则自动计算）
        kc_upper: 凯尔特纳上轨（可选）
        kc_lower: 凯尔特纳下轨（可选）
        atr: ATR（可选）

    Returns:
        Dict:
          - regime: str, 状态名称
          - confidence: float, 置信度 0~1
          - indicators: dict, 各指标值
          - position_scalar: (min, max), 建议仓位系数
    """
    if index_df is None or index_df.empty or len(index_df) < 30:
        return _default_regime()

    close = index_df["close"]
    high = index_df["high"]
    low = index_df["low"]

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
        atr = pd.Series(tr, index=index_df.index).ewm(span=14, adjust=False).mean()

    kc_range = kc_mid + 2 * atr - (kc_mid - 2 * atr)

    # 计算 Donchian
    dc = _calculate_donchian(index_df, period=20)
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
    atr_ratio = float(atr.iloc[-1] / (atr.iloc[-20:].mean() + 1e-9))

    # 5. 压缩程度
    compression = _detect_compression(kc_range, atr)

    # 6. 波动率扩张
    vol_expanding = _detect_volatility_expansion(atr)

    # 7. 趋势得分
    trend_score = _detect_trend(index_df, kc_mid)

    # ========== 状态判断 ==========
    indicators = {
        "price_vs_ema": round(price_vs_ema, 4),
        "ema_slope": round(ema_slope, 4),
        "dc_slope": round(dc_slope, 4),
        "atr_ratio": round(atr_ratio, 4),
        "compression": round(compression, 4),
        "vol_expanding": vol_expanding,
        "trend_score": round(trend_score, 4),
    }

    # TREND_UP: 价格>EMA + EMA向上 + ATR扩张 + Donchian上移
    if (price_vs_ema > 0.01 and
            ema_slope > 0.05 and
            dc_slope > 0 and
            atr_ratio > 1.1):
        regime = TREND_UP
        confidence = min(0.95, 0.5 + abs(trend_score) * 0.4 + (atr_ratio - 1) * 0.3)

    # TREND_DOWN: 价格<EMA + EMA向下 + Donchian下移
    elif (price_vs_ema < -0.01 and
          ema_slope < -0.05 and
          dc_slope < 0):
        regime = TREND_DOWN
        confidence = min(0.95, 0.5 + abs(trend_score) * 0.4)

    # COMPRESSION: 压缩明显 + 方向不明确
    elif compression > 0.6 and abs(trend_score) < 0.3:
        regime = COMPRESSION
        confidence = min(0.85, 0.4 + compression * 0.4)

    # MEAN_REVERSION: EMA走平 + 震荡
    elif abs(ema_slope) < 0.1 and abs(trend_score) < 0.4:
        regime = MEAN_REVERSION
        confidence = min(0.80, 0.4 + (1 - compression) * 0.3)

    # Fallback: 根据趋势得分
    else:
        if trend_score > 0.3:
            regime = TREND_UP
        elif trend_score < -0.3:
            regime = TREND_DOWN
        else:
            regime = MEAN_REVERSION
        confidence = 0.50

    indicators["confidence"] = round(float(confidence), 3)

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
    """默认状态（数据不足时）"""
    return {
        "regime": MEAN_REVERSION,
        "confidence": 0.3,
        "indicators": {},
        "position_scalar": REGIME_POSITION_SCALAR[MEAN_REVERSION],
        "atr_ratio": 1.0,
        "compression": 0.0,
    }


def get_regime_description(regime: str) -> str:
    """获取状态描述"""
    descriptions = {
        TREND_UP: "上升趋势：积极做多，仓位75%-100%",
        MEAN_REVERSION: "均值回归：短线回归操作，仓位25%-50%",
        COMPRESSION: "压缩区间：少做等突破，仓位0%-25%",
        TREND_DOWN: "下跌趋势：不做多，仓位0%",
    }
    return descriptions.get(regime, "未知状态")
