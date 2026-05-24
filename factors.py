"""因子库 —— 整合 quant-system + ai-hedge-fund + cryptoquant-ai（A股适用版）"""

import pandas as pd
import numpy as np
import math
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import logging

from lib.utils import _safe_float

# KC通道常量（用于辅助过滤）
try:
    from lib.indicators import KC_LENGTH, KC_ATR, KC_SCALAR
except ImportError:
    KC_LENGTH, KC_ATR, KC_SCALAR = 21, 14, 2.0

logger = logging.getLogger(__name__)

# ========== 风控常量 ==========
VOL_BASE_LIMIT = 0.20
VOL_THRESHOLDS = (0.15, 0.30, 0.50)
VOL_MULTIPLIERS = (1.25, 1.0, 0.75, 0.50)
VOL_MULTIPLIER_MIN = 0.25
VOL_MULTIPLIER_MAX = 1.25


# ========== 安全转换 ==========

# 使用 lib.utils 中的统一实现
safe_float = _safe_float


# ========== 技术指标 ==========

def calc_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0).fillna(0)
    loss = (-delta.clip(upper=0)).fillna(0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    # 边界处理：avg_loss=0 表示持续上涨，RSI=100；avg_gain=0 表示持续下跌，RSI=0
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    # avg_loss 全 0 → 持续上涨 → RSI=100；avg_gain 全 0 → 持续下跌 → RSI=0
    all_loss = avg_loss == 0
    all_gain = avg_gain == 0
    result = result.where(~all_loss, 100)  # 无下跌设为 100
    result = result.where(~all_gain, 0)    # 无上涨设为 0
    return result


def calc_ema(prices: pd.Series, window: int) -> pd.Series:
    return prices.ewm(span=window, adjust=False).mean()


def calc_bollinger_bands(prices: pd.Series, window: int = 20):
    sma = prices.rolling(window).mean()
    std_dev = prices.rolling(window).std()
    upper = sma + (std_dev * 2)
    lower = sma - (std_dev * 2)
    return upper, lower


def calc_macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(prices, fast)
    ema_slow = calc_ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    high_low = high - low
    high_close = abs(high - close.shift())
    low_close = abs(low - close.shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    return true_range.rolling(period).mean()


def calc_hurst_exponent(prices: pd.Series, max_lag: int = 20) -> float:
    if len(prices) < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    tau = [max(1e-8, np.std(np.subtract(prices.values[lag:], prices.values[:-lag]))) for lag in lags]
    try:
        reg = np.polyfit(np.log(list(lags)), np.log(tau), 1)
        return reg[0]
    except (ValueError, RuntimeWarning):
        return 0.5


# ========== 新增技术因子（来自 quant-system） ==========

def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             k_period: int = 9, d_smooth: int = 3) -> Dict:
    """KDJ 随机指标"""
    if len(close) < k_period + d_smooth:
        return {"K": 50, "D": 50, "J": 50}
    low_n = low.rolling(k_period).min()
    high_n = high.rolling(k_period).max()
    denom = high_n - low_n
    rsv = ((close - low_n) / denom.replace(0, np.nan)) * 100
    k = rsv.ewm(com=d_smooth - 1, adjust=False).mean()
    d = k.ewm(com=d_smooth - 1, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "K": round(safe_float(k.iloc[-1], 50), 2),
        "D": round(safe_float(d.iloc[-1], 50), 2),
        "J": round(safe_float(j.iloc[-1], 50), 2),
    }


def calc_adx(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int = 14) -> float:
    """ADX 平均趋向指数（衡量趋势强度）"""
    if len(close) < period * 2:
        return 0.0
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean()
    atr_safe = atr_val.replace(0, np.nan)
    plus_di = 100 * plus_dm.rolling(period).mean() / atr_safe
    minus_di = 100 * minus_dm.rolling(period).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.rolling(period).mean()
    return round(safe_float(adx_val.iloc[-1], 0), 2)


def calc_cci(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int = 20) -> float:
    """CCI 商品通道指数"""
    if len(close) < period:
        return 0.0
    tp = (high + low + close) / 3
    tp_sma = tp.tail(period).mean()
    mad = (tp.tail(period) - tp_sma).abs().mean()
    if mad == 0:
        return 0.0
    return round(safe_float((tp.iloc[-1] - tp_sma) / (0.015 * mad), 0), 2)


def calc_mfi(high: pd.Series, low: pd.Series, close: pd.Series,
             volume: pd.Series, period: int = 14) -> float:
    """MFI 资金流量指标"""
    if len(close) < period + 1:
        return 50.0
    tp = (high + low + close) / 3
    mf = tp * volume
    tp_diff = tp.diff()
    pos_mf = mf.where(tp_diff > 0, 0).tail(period).sum()
    neg_mf = mf.where(tp_diff < 0, 0).tail(period).sum()
    if neg_mf == 0:
        return 100.0
    return round(safe_float(100 - (100 / (1 + pos_mf / neg_mf)), 50), 2)


def calc_trix(close: pd.Series, period: int = 12) -> float:
    """TRIX 三重指数平滑平均线"""
    if len(close) < period * 3:
        return 0.0
    e1 = close.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period, adjust=False).mean()
    e3 = e2.ewm(span=period, adjust=False).mean()
    prev = e3.shift(1)
    trix_val = (e3 - prev) / prev.replace(0, np.nan) * 100
    return round(safe_float(trix_val.iloc[-1], 0), 4)


def calc_williams_r(high: pd.Series, low: pd.Series, close: pd.Series,
                    period: int = 14) -> float:
    """Williams %R 威廉指标"""
    if len(close) < period:
        return -50.0
    hh = high.tail(period).max()
    ll = low.tail(period).min()
    denom = hh - ll
    if denom == 0:
        return -50.0
    return round(safe_float(((hh - close.iloc[-1]) / denom) * -100, -50), 2)


# ========== 择时（来自 quant-system） ==========

def calc_market_exposure(close: pd.Series, volume: pd.Series = None,
                         pct_chg: pd.Series = None) -> Dict:
    """市场择时：基于基准指数判断仓位水平

    返回 (状态, 建议仓位比例)
    - bull: 0.95（趋势向上 + 波动率正常）
    - neutral: 0.70
    - bear: 0.30（趋势向下 + 波动率放大）
    """
    if len(close) < 60:
        return {"state": "neutral", "exposure": 0.70}

    ma20 = close.tail(20).mean()
    ma60 = close.tail(60).mean()
    current = close.iloc[-1]
    trend_up = current > ma20 > ma60

    returns = close.pct_change().dropna()
    short_vol = returns.tail(20).std()
    long_vol = returns.tail(60).std() if len(returns) >= 60 else short_vol
    high_vol = short_vol > long_vol * 1.5

    if trend_up and not high_vol:
        state = "bull"
        exposure = 0.95
    elif not trend_up and high_vol:
        state = "bear"
        exposure = 0.30
    else:
        state = "neutral"
        exposure = 0.70

    # 叠加成交量信号
    if volume is not None and len(volume) >= 20:
        vol_ma5 = volume.tail(5).mean()
        vol_ma20 = volume.tail(20).mean()
        if vol_ma5 > vol_ma20:
            exposure *= 1.0
        else:
            exposure *= 0.85

    # 叠加涨跌比信号
    if pct_chg is not None and len(pct_chg) >= 5:
        up_days = (pct_chg.tail(5) > 0).sum()
        exposure *= (0.6 + up_days * 0.08)

    exposure = max(0.2, min(1.0, exposure))
    return {"state": state, "exposure": round(exposure, 2)}


# ========== 市场体制检测（来自 cryptoquant-ai，A股适用版） ==========

@dataclass
class MarketRegime:
    """市场体制"""
    regime: str  # TREND_UP / TREND_DOWN / RANGE / RISK_OFF
    score: float  # -1 到 1
    confidence: float
    reasoning: str


@dataclass
class MacroGate:
    """宏观风险门控"""
    state: str  # ALLOW_FULL / ALLOW_REDUCED / BLOCK_NEW_RISK
    regime: str  # RISK_ON / NEUTRAL / RISK_OFF
    score: float  # -1 到 1
    position_multiplier: float  # 仓位乘数
    confidence_penalty: float  # 置信度惩罚
    reasoning: str


def detect_market_regime(prices: pd.Series, volume: pd.Series = None,
                         market_sentiment: float = 0) -> MarketRegime:
    """检测市场体制（A股适用版）

    A股可用指标：
    - 趋势：EMA 多周期
    - 动量：多周期收益率
    - 量能：成交量变化
    - 情绪：涨跌比、涨停数（外部输入）

    去掉加密货币特有指标（资金费率、订单簿、链上数据）
    """
    if len(prices) < 60:
        return MarketRegime("UNKNOWN", 0, 0, "数据不足")

    # 趋势分数
    ema_20 = calc_ema(prices, 20).iloc[-1]
    ema_60 = calc_ema(prices, 60).iloc[-1]
    trend_score = (ema_20 / ema_60 - 1) * 10 if ema_60 != 0 else 0
    trend_score = max(-1, min(1, trend_score))

    # 动量分数
    returns = prices.pct_change()
    mom_5 = returns.tail(5).sum() * 10
    mom_20 = returns.tail(20).sum() * 5
    momentum_score = (mom_5 * 0.6 + mom_20 * 0.4)
    momentum_score = max(-1, min(1, momentum_score))

    # 量能分数
    volume_score = 0
    if volume is not None and len(volume) > 20:
        vol_5 = volume.tail(5).mean()
        vol_20 = volume.tail(20).mean()
        # 放量上涨看多，缩量下跌看空
        price_change = (prices.iloc[-1] / prices.iloc[-6] - 1)
        if vol_5 > vol_20 * 1.2 and price_change > 0:
            volume_score = 0.5
        elif vol_5 > vol_20 * 1.2 and price_change < 0:
            volume_score = -0.5
        elif vol_5 < vol_20 * 0.8:
            volume_score = -0.3

    # 波动率分数
    volatility = returns.tail(20).std() * math.sqrt(252)
    volatility_spike = min(volatility / 0.30, 1)  # 30% 年化波动率为基准

    # 综合体制分数
    regime_score = (
        trend_score * 0.35 +
        momentum_score * 0.30 +
        volume_score * 0.20 +
        market_sentiment * 0.15 -
        volatility_spike * 0.15
    )
    regime_score = max(-1, min(1, regime_score))

    # 判断体制
    if volatility_spike > 0.85:
        regime = "RISK_OFF"
        confidence = 0.95
        reasoning = f"波动率过高({volatility:.1%})，市场恐慌"
    elif regime_score > 0.35:
        regime = "TREND_UP"
        confidence = min(0.7 + abs(regime_score) * 0.3, 0.95)
        reasoning = f"趋势向上，分数={regime_score:.3f}"
    elif regime_score < -0.35:
        regime = "TREND_DOWN"
        confidence = min(0.7 + abs(regime_score) * 0.3, 0.95)
        reasoning = f"趋势向下，分数={regime_score:.3f}"
    else:
        regime = "RANGE"
        confidence = 0.5
        reasoning = f"震荡区间，分数={regime_score:.3f}"

    return MarketRegime(regime, regime_score, confidence, reasoning)


def evaluate_macro_gate(macro_score: float = 0) -> MacroGate:
    """宏观风险门控（A股适用版）

    A股宏观指标：
    - 北向资金流入/流出
    - 大盘指数趋势
    - 两融余额变化
    - 市场情绪（涨跌比）

    macro_score 由外部计算后传入，范围 -1 到 1
    """
    if macro_score is None:
        macro_score = 0.0
    score = max(-1, min(1, macro_score))

    if score <= -0.30:
        return MacroGate(
            state="BLOCK_NEW_RISK",
            regime="RISK_OFF",
            score=score,
            position_multiplier=0,
            confidence_penalty=100,
            reasoning=f"宏观风险高({score:.2f})，禁止新开仓位"
        )
    elif score <= -0.10:
        return MacroGate(
            state="ALLOW_REDUCED",
            regime="NEUTRAL",
            score=score,
            position_multiplier=0.5,
            confidence_penalty=8,
            reasoning=f"宏观风险偏高({score:.2f})，仓位减半"
        )
    elif score >= 0.20:
        return MacroGate(
            state="ALLOW_FULL",
            regime="RISK_ON",
            score=score,
            position_multiplier=1.0,
            confidence_penalty=0,
            reasoning=f"宏观环境良好({score:.2f})，正常交易"
        )
    else:
        return MacroGate(
            state="ALLOW_FULL",
            regime="NEUTRAL",
            score=score,
            position_multiplier=1.0,
            confidence_penalty=0,
            reasoning=f"宏观环境中性({score:.2f})，正常交易"
        )


# ========== 5 大策略信号 ==========

def calc_trend_signal(prices: pd.Series) -> Dict:
    """趋势跟踪策略"""
    if len(prices) < 55:
        return {"signal": "neutral", "confidence": 0.5}

    ema_8 = calc_ema(prices, 8)
    ema_21 = calc_ema(prices, 21)
    ema_55 = calc_ema(prices, 55)

    short_trend = ema_8.iloc[-1] > ema_21.iloc[-1]
    medium_trend = ema_21.iloc[-1] > ema_55.iloc[-1]

    returns = prices.pct_change()
    rolling_std_14 = returns.rolling(14).std().replace(0, np.nan)
    adx_approx = abs(returns.rolling(14).mean()) / rolling_std_14 * 100
    trend_strength = min(adx_approx.iloc[-1] / 100, 1.0) if not np.isnan(adx_approx.iloc[-1]) else 0.5

    if short_trend and medium_trend:
        return {"signal": "bullish", "confidence": trend_strength}
    elif not short_trend and not medium_trend:
        return {"signal": "bearish", "confidence": trend_strength}
    else:
        return {"signal": "neutral", "confidence": 0.5}


def calc_mean_reversion_signal(prices: pd.Series) -> Dict:
    """均值回归策略"""
    if len(prices) < 50:
        return {"signal": "neutral", "confidence": 0.5}

    ma_50 = prices.rolling(50).mean()
    std_50 = prices.rolling(50).std()
    z_score = (prices - ma_50) / std_50.replace(0, np.nan)

    bb_upper, bb_lower = calc_bollinger_bands(prices)
    price_vs_bb = (prices.iloc[-1] - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) if (bb_upper.iloc[-1] - bb_lower.iloc[-1]) > 0 else 0.5

    rsi = calc_rsi(prices, 14)
    z = safe_float(z_score.iloc[-1], 0)
    rsi_val = safe_float(rsi.iloc[-1], 50)

    if z < -2 and price_vs_bb < 0.2:
        signal = "bullish"
        confidence = min(abs(z) / 4, 1.0)
    elif z > 2 and price_vs_bb > 0.8:
        signal = "bearish"
        confidence = min(abs(z) / 4, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal, "confidence": confidence,
        "z_score": round(z, 2), "rsi": round(rsi_val, 1),
    }


def calc_momentum_signal(prices: pd.Series, volume: pd.Series = None) -> Dict:
    """动量策略"""
    if len(prices) < 126:
        return {"signal": "neutral", "confidence": 0.5}

    returns = prices.pct_change()
    mom_1m = returns.rolling(21).sum().iloc[-1]
    mom_3m = returns.rolling(63).sum().iloc[-1]
    mom_6m = returns.rolling(126).sum().iloc[-1]

    momentum_score = 0.4 * mom_1m + 0.3 * mom_3m + 0.3 * mom_6m

    volume_confirmation = True
    if volume is not None and len(volume) > 21:
        vol_ma = volume.rolling(21).mean()
        volume_confirmation = volume.iloc[-1] > vol_ma.iloc[-1]

    if momentum_score > 0.05 and volume_confirmation:
        signal = "bullish"
        confidence = min(abs(momentum_score) * 5, 1.0)
    elif momentum_score < -0.05 and volume_confirmation:
        signal = "bearish"
        confidence = min(abs(momentum_score) * 5, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal, "confidence": confidence,
        "momentum_1m": round(safe_float(mom_1m) * 100, 2),
        "momentum_3m": round(safe_float(mom_3m) * 100, 2),
    }


def calc_volatility_signal(prices: pd.Series) -> Dict:
    """波动率策略"""
    if len(prices) < 63:
        return {"signal": "neutral", "confidence": 0.5}

    returns = prices.pct_change()
    hist_vol = returns.rolling(21).std() * math.sqrt(252)
    vol_ma = hist_vol.rolling(63).mean()
    vol_regime = hist_vol / vol_ma.replace(0, np.nan)
    vol_std = hist_vol.rolling(63).std().replace(0, np.nan)
    vol_z = (hist_vol - vol_ma) / vol_std

    current_regime = safe_float(vol_regime.iloc[-1], 1.0)
    current_z = safe_float(vol_z.iloc[-1], 0)

    if current_regime < 0.8 and current_z < -1:
        signal = "bullish"
        confidence = min(abs(current_z) / 3, 1.0)
    elif current_regime > 1.2 and current_z > 1:
        signal = "bearish"
        confidence = min(abs(current_z) / 3, 1.0)
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal, "confidence": confidence,
        "vol_regime": round(current_regime, 2),
        "annualized_vol": round(safe_float(hist_vol.iloc[-1]) * 100, 1),
    }


def calc_stat_arb_signal(prices: pd.Series) -> Dict:
    """统计套利"""
    if len(prices) < 63:
        return {"signal": "neutral", "confidence": 0.5}

    returns = prices.pct_change()
    skew = returns.rolling(63).skew()
    hurst = calc_hurst_exponent(prices)
    skew_val = safe_float(skew.iloc[-1], 0)

    if hurst < 0.4 and skew_val > 1:
        signal = "bullish"
        confidence = (0.5 - hurst) * 2
    elif hurst < 0.4 and skew_val < -1:
        signal = "bearish"
        confidence = (0.5 - hurst) * 2
    else:
        signal = "neutral"
        confidence = 0.5

    return {
        "signal": signal, "confidence": confidence,
        "hurst": round(hurst, 3), "skewness": round(skew_val, 2),
    }


# ========== 趋势突破（来自 cryptoquant-ai，A股适用版） ==========

def calc_trend_breakout_signal(prices: pd.Series, regime: str) -> Dict:
    """趋势突破策略（来自 cryptoquant-ai）

    条件：
    - TREND_UP + 突破20日高点 + 动量>0 + RSI<78 + MACD>=0 → BUY
    - TREND_DOWN + 跌破20日低点 + 动量<0 + RSI>22 + MACD<=0 → SELL
    """
    if len(prices) < 26:
        return {"signal": "neutral", "confidence": 0.5}

    last = prices.iloc[-1]
    history = prices.iloc[:-1]
    high_20 = history.tail(20).max()
    low_20 = history.tail(20).min()

    rsi = calc_rsi(prices, 14).iloc[-1]
    macd = calc_macd(prices)
    macd_hist = macd["histogram"].iloc[-1]
    momentum = (prices.iloc[-1] / prices.iloc[-5] - 1) if len(prices) > 5 else 0

    # RISK_OFF 时禁止开仓
    if regime == "RISK_OFF":
        return {"signal": "neutral", "confidence": 0.95, "reason": "RISK_OFF 环境，禁止开仓"}

    # 趋势向上突破
    if (regime == "TREND_UP" and last > high_20 and momentum > 0
            and rsi < 78 and macd_hist >= 0):
        return {
            "signal": "bullish",
            "confidence": min(0.72 + abs(momentum) * 10, 0.94),
            "reason": f"突破20日高点({high_20:.2f})，RSI={rsi:.1f}，MACD确认"
        }

    # 趋势向下突破
    if (regime == "TREND_DOWN" and last < low_20 and momentum < 0
            and rsi > 22 and macd_hist <= 0):
        return {
            "signal": "bearish",
            "confidence": min(0.72 + abs(momentum) * 10, 0.94),
            "reason": f"跌破20日低点({low_20:.2f})，RSI={rsi:.1f}，MACD确认"
        }

    return {"signal": "neutral", "confidence": 0.5, "reason": "未触发突破条件"}


# ========== 多策略加权组合 ==========

STRATEGY_WEIGHTS = {
    "trend": 0.20,
    "mean_reversion": 0.15,
    "momentum": 0.20,
    "volatility": 0.10,
    "stat_arb": 0.10,
    "trend_breakout": 0.25,  # 来自 cryptoquant-ai
}


def weighted_signal_combination(signals: Dict[str, Dict], weights: Dict[str, float] = None) -> Dict:
    weights = weights or STRATEGY_WEIGHTS

    # 因子权重校验：权重和必须接近 1.0
    total_weight = sum(weights.values())
    assert abs(total_weight - 1.0) < 1e-6, f"因子权重和必须为 1.0，当前为 {total_weight}"

    signal_values = {"bullish": 1, "neutral": 0, "bearish": -1}

    weighted_sum = 0
    total_confidence = 0

    for strategy, signal in signals.items():
        if strategy not in weights:
            continue
        numeric_signal = signal_values.get(signal.get("signal", "neutral"), 0)
        weight = weights[strategy]
        confidence = signal.get("confidence", 0.5)
        weighted_sum += numeric_signal * weight * confidence
        total_confidence += weight * confidence

    final_score = weighted_sum / total_confidence if total_confidence > 0 else 0

    if final_score > 0.2:
        signal = "bullish"
    elif final_score < -0.2:
        signal = "bearish"
    else:
        signal = "neutral"

    return {"signal": signal, "confidence": round(abs(final_score), 3), "score": round(final_score, 3)}


def calc_channel_filter(hist_df: pd.DataFrame) -> Dict:
    """计算通道过滤信号（均值回归辅助）

    基于 Walk-forward IC/IR 验证结果：
    - kc_pos < 0.20（超卖）→ 支持做多
    - kc_pos > 0.80（超买）→ 支持做空

    Returns:
        Dict with keys: kc_pos, adjustment, reason
        - kc_pos: 凯尔特纳通道位置 (0-1)
        - adjustment: 置信度调整乘数 (0.5-1.3)
        - reason: 调整原因
    """
    try:
        from lib.indicators import calculate_keltner_channels
    except ImportError:
        return {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}

    if hist_df is None or len(hist_df) < KC_LENGTH + 5:
        return {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}

    try:
        close = hist_df["close"]
        high = hist_df.get("high", close)
        low = hist_df.get("low", close)

        # 计算 KC 通道
        kc_df = calculate_keltner_channels(hist_df)
        if kc_df is None or kc_df.empty:
            return {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}

        kc_upper = kc_df["kc_upper"]
        kc_lower = kc_df["kc_lower"]
        kc_range = kc_upper - kc_lower

        if kc_range.iloc[-1] <= 0:
            return {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}

        kc_pos = (close.iloc[-1] - kc_lower.iloc[-1]) / kc_range.iloc[-1]
        kc_pos = max(0, min(1, kc_pos))

        # 根据通道位置调整置信度
        if kc_pos < 0.15:
            # 强烈超卖：支持做多
            adjustment = 1.3
            reason = f"KC超卖({kc_pos:.0%})"
        elif kc_pos < 0.20:
            # 超卖：适度支持做多
            adjustment = 1.15
            reason = f"KC偏低({kc_pos:.0%})"
        elif kc_pos > 0.85:
            # 强烈超买：支持做空
            adjustment = 1.3
            reason = f"KC超买({kc_pos:.0%})"
        elif kc_pos > 0.80:
            # 超买：适度支持做空
            adjustment = 1.15
            reason = f"KC偏高({kc_pos:.0%})"
        else:
            # 中性区间
            adjustment = 1.0
            reason = f"KC中性({kc_pos:.0%})"

        return {"kc_pos": kc_pos, "adjustment": adjustment, "reason": reason}
    except Exception:
        return {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}


# ========== 综合计算 ==========

def compute_all_signals(hist_df: pd.DataFrame, macro_score: float = 0,
                        factor_config=None) -> Dict:
    """计算所有策略信号

    factor_config: 可选 FactorConfig 实例，控制因子开关；None=全部计算
    """
    if hist_df.empty or len(hist_df) < 30:
        return {}

    close = hist_df["close"] if "close" in hist_df.columns else pd.Series()
    volume = hist_df["volume"] if "volume" in hist_df.columns else None

    if close.empty:
        return {}

    # 1. 检测市场体制
    regime_result = detect_market_regime(close, volume=volume, market_sentiment=macro_score)
    regime = regime_result.regime

    # 2. 宏观风险门控
    macro_gate = evaluate_macro_gate(macro_score)

    # 3. 计算 6 大策略信号（受 Config 开关控制）
    signals = {}
    _use = lambda name, default=True: getattr(factor_config, name, default)

    if _use('use_trend'):
        signals["trend"] = calc_trend_signal(close)
        signals["trend_breakout"] = calc_trend_breakout_signal(close, regime)

    if _use('use_reversal'):
        signals["mean_reversion"] = calc_mean_reversion_signal(close)

    if _use('use_momentum'):
        signals["momentum"] = calc_momentum_signal(close, volume)

    if _use('use_volatility'):
        signals["volatility"] = calc_volatility_signal(close)

    if _use('use_stat_arb'):
        signals["stat_arb"] = calc_stat_arb_signal(close)

    # 4. 加权组合
    combined = weighted_signal_combination(signals)

    # 5. 应用宏观门控
    if macro_gate.state == "BLOCK_NEW_RISK":
        combined["signal"] = "neutral"
        combined["confidence"] = 0
        combined["macro_blocked"] = True
    elif macro_gate.state == "ALLOW_REDUCED":
        combined["confidence"] *= macro_gate.position_multiplier

    # 6. 通道过滤（均值回归辅助）
    # 当通道位置极端时，调整置信度：
    # - 超卖(kc_pos<0.20) + 做多信号 → boost
    # - 超买(kc_pos>0.80) + 做多信号 → reduce
    # - 超买(kc_pos>0.80) + 做空信号 → boost
    # - 超卖(kc_pos<0.20) + 做空信号 → reduce
    if combined["signal"] in ("bullish", "bearish"):
        ch_filter = calc_channel_filter(hist_df)
        adj = ch_filter.get("adjustment", 1.0)
        if adj != 1.0:
            combined["confidence"] = round(combined["confidence"] * adj, 3)
            combined["channel_reason"] = ch_filter.get("reason", "")

    # 7. 额外因子（受 Config 开关控制）
    factors = {}
    if _use('use_rsi') and len(close) > 14:
        factors["rsi_14"] = safe_float(calc_rsi(close, 14).iloc[-1], 50)
    if _use('use_trend') and len(close) > 20:
        ma5 = close.tail(5).mean()
        ma20 = close.tail(20).mean()
        factors["trend_strength"] = (ma5 / ma20 - 1) * 100 if ma20 > 0 else 0
    if _use('use_volatility') and len(close) > 20:
        factors["volatility_20"] = close.pct_change().tail(20).std() * math.sqrt(252) * 100
    if _use('use_volume') and volume is not None and len(volume) > 20:
        vol5 = volume.tail(5).mean()
        vol20 = volume.tail(20).mean()
        factors["volume_ratio"] = vol5 / vol20 if vol20 > 0 else 0
    if _use('use_momentum') and len(close) > 20:
        denom5 = close.iloc[-6]
        denom20 = close.iloc[-21]
        factors["momentum_5"] = (close.iloc[-1] / denom5 - 1) * 100 if denom5 != 0 else 0
        factors["momentum_20"] = (close.iloc[-1] / denom20 - 1) * 100 if denom20 != 0 else 0

    # 新增技术因子（KDJ/ADX/CCI/MFI/TRIX/WilliamsR，常开不关）
    has_hlc = all(c in hist_df.columns for c in ["high", "low", "close"])
    if has_hlc:
        high = hist_df["high"]
        low = hist_df["low"]
        kdj = calc_kdj(high, low, close)
        factors["kdj_j"] = kdj["J"]
        factors["adx"] = calc_adx(high, low, close)
        factors["cci"] = calc_cci(high, low, close)
        factors["williams_r"] = calc_williams_r(high, low, close)
        if volume is not None:
            factors["mfi"] = calc_mfi(high, low, close, volume)
    factors["trix"] = calc_trix(close)

    # 通道过滤结果
    ch_filter = calc_channel_filter(hist_df)

    return {
        "signals": signals,
        "combined": combined,
        "regime": {
            "regime": regime,
            "score": regime_result.score,
            "confidence": regime_result.confidence,
            "reasoning": regime_result.reasoning,
        },
        "macro_gate": {
            "state": macro_gate.state,
            "regime": macro_gate.regime,
            "multiplier": macro_gate.position_multiplier,
            "reasoning": macro_gate.reasoning,
        },
        "channel_filter": {
            "kc_pos": ch_filter.get("kc_pos", 0.5),
            "adjustment": ch_filter.get("adjustment", 1.0),
            "reason": ch_filter.get("reason", ""),
        },
        "factors": factors,
    }


# ========== 风险管理 ==========

def calc_volatility_adjusted_limit(annualized_volatility: float) -> float:
    if annualized_volatility < VOL_THRESHOLDS[0]:
        vol_multiplier = VOL_MULTIPLIERS[0]
    elif annualized_volatility < VOL_THRESHOLDS[1]:
        vol_multiplier = VOL_MULTIPLIERS[1] - (annualized_volatility - VOL_THRESHOLDS[0]) * 0.5
    elif annualized_volatility < VOL_THRESHOLDS[2]:
        vol_multiplier = VOL_MULTIPLIERS[2] - (annualized_volatility - VOL_THRESHOLDS[1]) * 0.5
    else:
        vol_multiplier = VOL_MULTIPLIERS[3]
    vol_multiplier = max(VOL_MULTIPLIER_MIN, min(VOL_MULTIPLIER_MAX, vol_multiplier))
    return VOL_BASE_LIMIT * vol_multiplier


# ========== 综合评分（来自外部 skill） ==========

def calc_technical_verdict(hist_df: pd.DataFrame) -> Dict:
    """技术面综合研判（12 个信号多数投票）"""
    from lib.technical_signals import calc_composite_verdict
    return calc_composite_verdict(hist_df)


def calc_fundamental_score(income: Dict, balance: Dict,
                           cashflow: Dict = None) -> float:
    """基本面综合评分（0-10 分）"""
    from lib.fundamental import compute_fundamentals, fundamental_score
    fundamentals = compute_fundamentals(income, balance, cashflow)
    return fundamental_score(fundamentals)


def calc_anomaly_score(periods_data: list) -> Dict:
    """财报异常检测（扣分制）"""
    from lib.anomaly_detector import detect_anomalies, anomaly_summary
    alerts = detect_anomalies(periods_data)
    summary = anomaly_summary(alerts)
    return {"alerts": alerts, "summary": summary}


# ========== IC 评价 ==========

def evaluate_factor(factor_values: pd.Series, forward_returns: pd.Series) -> Dict:
    from scipy import stats
    valid = factor_values.notna() & forward_returns.notna()
    if valid.sum() < 30:
        return {"valid": False, "reason": "样本不足"}
    try:
        rank_ic, pvalue = stats.spearmanr(factor_values[valid], forward_returns[valid])
        n_groups = 5
        fv_rank = factor_values[valid].rank(pct=True)
        groups = pd.cut(fv_rank, bins=n_groups, labels=[f"Q{i+1}" for i in range(n_groups)])
        group_returns = forward_returns[valid].groupby(groups, observed=False).mean()
        mono, _ = stats.spearmanr(range(len(group_returns)), group_returns.values) if len(group_returns) >= 3 else (0, 0)
        return {
            "valid": abs(rank_ic) >= 0.02 and pvalue < 0.05,
            "rank_ic": round(rank_ic, 4),
            "pvalue": round(pvalue, 4),
            "monotonicity": round(mono, 4) if not np.isnan(mono) else 0,
            "spread": round(group_returns.iloc[-1] - group_returns.iloc[0], 4) if len(group_returns) >= 2 else 0,
        }
    except Exception as e:
        return {"valid": False, "reason": str(e)}
