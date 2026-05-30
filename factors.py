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
    """检测市场体制（已废弃，请使用 lib/market_regime.detect_market_regime）

    .. deprecated::
        此函数已废弃，请使用 ``lib.market_regime.detect_market_regime`` 作为统一来源。
    """
    import warnings
    warnings.warn(
        "factors.detect_market_regime 已废弃，请使用 lib.market_regime.detect_market_regime",
        DeprecationWarning, stacklevel=2
    )
    from lib.market_regime import detect_market_regime as _detect
    index_df = pd.DataFrame({"close": prices, "high": prices, "low": prices})
    result = _detect(index_df)
    regime_map = {
        "TREND_UP": "TREND_UP",
        "TREND_DOWN": "TREND_DOWN",
        "MEAN_REVERSION": "RANGE",
        "COMPRESSION": "RANGE",
        "RISK_OFF": "RISK_OFF",
    }
    return MarketRegime(
        regime=regime_map.get(result["regime"], "UNKNOWN"),
        score=result["indicators"].get("trend_score", 0),
        confidence=result["confidence"],
        reasoning=f"统一到 market_regime.py: {result['regime']}"
    )


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


# ========== 多策略加权组合（基于 IC 评价 2026-05-30）==========
# IC 评价结果：volume_ratio(0.152) > adx(0.092) > williams_r(0.070) > cci(0.065) > rsi(0.061)
# 权重按 IC 比例分配，归一化后求和=1.0

STRATEGY_WEIGHTS = {
    "trend": 0.15,
    "mean_reversion": 0.10,
    "momentum": 0.20,
    "volatility": 0.15,
    "stat_arb": 0.10,
    "trend_breakout": 0.30,  # 包含 volume_ratio/adx/rsi 等高 IC 因子
}


# IC 加权因子权重（基于 2026-05-30 IC 评价，30 只股票 × 500 天）
# IC: volume_ratio(0.152) > adx(0.092) > williams_r(0.070) > cci(0.065) > rsi(0.061)
IC_FACTOR_WEIGHTS = {
    "volume_ratio": 0.25,
    "adx": 0.15,
    "williams_r": 0.12,
    "cci": 0.10,
    "rsi_14": 0.10,
    "trend_strength": 0.09,
    "kdj_j": 0.09,
    "momentum_20": 0.08,
    "volatility_20": 0.02,  # IC 存在但方向需确认
}


def compute_ic_weighted_score(factors: Dict[str, float]) -> float:
    """用 IC 加权的因子值直接计算综合评分

    与 weighted_signal_combination 的区别：
    - 不依赖策略信号（bullish/neutral/bearish），直接用因子原始值
    - 权重基于历史 IC 评价，而非手工设定
    - 返回标准化分数 [-1, 1]

    Args:
        factors: 因子值字典

    Returns:
        float: 综合评分，越高越看多
    """
    total_weight = 0
    weighted_sum = 0

    for fname, weight in IC_FACTOR_WEIGHTS.items():
        val = factors.get(fname)
        if val is None or np.isnan(val):
            continue
        # 标准化到 [-1, 1]（用 tanh 压缩）
        normalized = np.tanh(val / 50)  # 大多数因子在 [-50, 50] 范围
        weighted_sum += normalized * weight
        total_weight += weight

    if total_weight <= 0:
        return 0.0

    return float(weighted_sum / total_weight)


def adjust_score_by_data_factors(base_score: float, data_factors: Dict) -> float:
    """基于 data_factors 调整综合评分

    调整规则（每项 ±0.10，总范围 ±0.30）：
    - 高股息 → +0.10（防御性收益）
    - 基金重仓 → +0.10（机构认可）
    - 低 PE → +0.10（估值合理）
    - 板块过多 → -0.10（过度分散，可能跟风）
    """
    adj = 0.0

    # 股息率：>1.0元/股 加分（高分红股）
    div = data_factors.get("dividend", {})
    if div.get("per_share", 0) > 1.0:
        adj += 0.10

    # 基金持仓：权重>20% 加分
    fh = data_factors.get("fund_holdings", {})
    if fh.get("total_weight", 0) > 20:
        adj += 0.10

    # 估值：PE<30 且 PB<8 加分（覆盖成长股）
    val = data_factors.get("valuation", {})
    pe = val.get("pe")
    pb = val.get("pb")
    if pe is not None and pb is not None and 0 < pe < 30 and 0 < pb < 8:
        adj += 0.10

    # 板块过多（>80）减分
    blk = data_factors.get("block", {})
    if blk.get("block_count", 0) > 80:
        adj -= 0.10

    return round(max(-1, min(1, base_score + adj)), 4)


def weighted_signal_combination(signals: Dict[str, Dict], weights: Dict[str, float] = None) -> Dict:
    weights = weights or STRATEGY_WEIGHTS

    # 因子权重校验：权重和必须接近 1.0（警告而非崩溃）
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > 1e-6:
        logger.warning(f"因子权重和不为 1.0（当前 {total_weight:.4f}），自动归一化")
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}

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


# ========== 资金情绪因子（来自 ScJyData）==========

def get_market_sentiment_factor(days: int = 10) -> Dict:
    """资金情绪因子：基于涨跌停、上涨下跌家数、期货持仓的市场情绪

    数据来源：D:/TDX/vipdoc/ds/ScJyData_zbca.csv
    可用字段（2026-05-27数据）：
      - IH5_JCC_V: 中证500期货持仓量（IM，负值表示净空头）
      - IF6_JCC_V: 沪深300期货持仓量（IF）
      - IC7_JCC_V: 中证1000期货持仓量（IC）
      - DBZJ15_FBA/DBZJ15_ZBA: 期货保证金
      - LB23_NUM: 龙虎榜上榜家数
      - LB24_ZTNUM/LB24_DTNUM: 涨跌停股数
      - ZTGD30: 30日涨停股数
      - UP31_NUM/DOWN31_NUM: 31日内上涨/下跌家数（重要！）
      - ZT35_HSBNUM/ZT35_FBL: 涨停板数量/幅比例
      - CXG28_HH_NUM/CXG28_LL_NUM: 创新高/新低数量

    Returns:
        dict with keys:
          - up_down_ratio: 上涨/下跌比（>1 看多，<1 看空）
          - zt_count: 涨停股数量
          - dt_count: 跌停股数量
          - futures_net: 期货净持仓（IM+IF+IC，越负越看空）
          - sentiment_score: 综合情绪分数 (-1 到 1)
          - source: 数据来源标记
    """
    try:
        from lib.data_layer import get_latest_market_stats
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        stats = get_latest_market_stats()
        if not isinstance(stats, dict):
            return {"error": f"unexpected stats type: {type(stats)}"}

        # 提取关键指标（使用 .get() 避免 KeyError）
        up_count = float(stats.get("UP31_NUM", 0) or 0)
        down_count = float(stats.get("DOWN31_NUM", 0) or 0)
        zt_count = float(stats.get("LB24_ZTNUM", 0) or 0)
        dt_count = float(stats.get("LB24_DTNUM", 0) or 0)
        zt30 = float(stats.get("ZTGD30", 0) or 0)
        ih5_jcc = float(stats.get("IH5_JCC_V", 0) or 0)
        if6_jcc = float(stats.get("IF6_JCC_V", 0) or 0)
        ic7_jcc = float(stats.get("IC7_JCC_V", 0) or 0)
        dbzj = float(stats.get("DBZJ15_FBA", 0) or 0)
        new_high = float(stats.get("CXG28_HH_NUM", 0) or 0)
        new_low = float(stats.get("CXG28_LL_NUM", 0) or 0)
        margin_balance = float(stats.get("DBZJ15_ZBA", 0) or 0)

        # 上涨/下跌比
        up_down_ratio = up_count / down_count if down_count > 0 else 1.0

        # 期货净持仓（反映机构情绪，负值 = 净空头）
        futures_net = ih5_jcc + if6_jcc + ic7_jcc

        # 综合情绪评分（经验公式）
        score = 0.0

        # 1. 上涨下跌比（权重 40%）
        if up_down_ratio > 0.5:
            score += min(0.4, (up_down_ratio - 0.5) * 0.8)
        elif up_down_ratio < 0.3:
            score -= min(0.4, (0.3 - up_down_ratio) * 1.0)

        # 2. 涨跌停（权重 20%）
        if zt_count > 30:
            score += 0.2
        elif zt_count < 10:
            score -= 0.1
        if dt_count > 30:
            score -= 0.2
        elif dt_count < 10:
            score += 0.05

        # 3. 期货净持仓（权重 25%）
        # 期货持仓为负表示净空头，负值越大（绝对值）越看空
        if futures_net < -50000:
            score -= 0.25
        elif futures_net < -20000:
            score -= 0.15
        elif futures_net > 50000:
            score += 0.15

        # 4. 创新高新低（权重 15%）
        if new_high > new_low * 2:
            score += 0.15
        elif new_low > new_high * 2:
            score -= 0.15

        score = max(-1.0, min(1.0, score))

        return {
            "up_count": int(up_count),
            "down_count": int(down_count),
            "up_down_ratio": round(up_down_ratio, 3),
            "zt_count": int(zt_count),
            "dt_count": int(dt_count),
            "zt30": int(zt30),
            "futures_net": round(futures_net, 0),
            "dbzj": round(dbzj, 1),
            "margin_balance": round(margin_balance, 1),
            "new_high": int(new_high),
            "new_low": int(new_low),
            "sentiment_score": round(score, 3),
            "date": str(stats.get("date", "unknown")),
            "source": "ScJyData",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


def get_market_sentiment_history(days: int = 30) -> pd.DataFrame:
    """获取市场情绪历史数据（用于时序分析）"""
    try:
        from lib.data_layer import get_market_stats
    except ImportError:
        return pd.DataFrame()

    try:
        df = get_market_stats(days=days)
        if df.empty:
            return pd.DataFrame()
        return df[["date", "LHB16_BAMT_B", "LHB16_BAMT_S", "ZTGD30",
                    "IH5_JCC_V", "IF6_JCC_V", "IC7_JCC_V"]].dropna()
    except Exception:
        return pd.DataFrame()


# ========== 北向资金因子（来自 HSJ 沪股通）==========

def get_northbound_flow_factor(code: str, market: str = "sh", days: int = 20) -> Dict:
    """北向资金因子：基于沪股通交易数据

    数据来源：D:/TDX/vipdoc/hsj/（sh/sz/bj/lday/）
    数据列：date, open, high, low, close, volume, amount

    注意：HSJ 数据只有日线 OHLCV+amount（总成交额），没有直接的
    买/卖分解。amount = close × volume。

    计算方法：
      - activity: 日均成交额（反映北向活跃度）
      - activity_ratio: 最近/历史成交额比（异常活跃度信号）
      - price_momentum: 价格动量（来自 close 序列）
      - trend: 趋势判断（increasing/decreasing/stable）

    Returns:
        dict with keys:
          - activity: 日均成交额（万元）
          - activity_ratio: 活跃度比（相比历史）
          - price_return_5d: 5日收益率
          - price_return_20d: 20日收益率
          - trend: 趋势（increasing/decreasing/stable）
          - record_count: 记录数
          - source: 数据来源
    """
    try:
        from lib.data_layer import get_hsj_hist
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        df = get_hsj_hist(code, market=market, days=days)
        if df.empty or len(df) < 5:
            return {"error": "insufficient HSJ data"}

        # 计算成交额相关指标
        amounts = df["amount"].values
        volumes = df["volume"].values if "volume" in df.columns else None
        closes = df["close"].values

        # 日均成交额
        activity = float(amounts.mean())

        # 活跃度比（近期 vs 早期）
        if len(amounts) >= 10:
            recent_avg = float(amounts[-5:].mean())
            early_avg = float(amounts[:5].mean())
            activity_ratio = recent_avg / early_avg if early_avg > 0 else 1.0
        else:
            activity_ratio = 1.0

        # 价格动量
        if len(closes) >= 6:
            price_return_5d = (closes[-1] / closes[-6] - 1) * 100 if closes[-6] != 0 else 0.0
        else:
            price_return_5d = 0.0

        if len(closes) >= 21:
            price_return_20d = (closes[-1] / closes[-21] - 1) * 100 if closes[-21] != 0 else 0.0
        else:
            price_return_20d = 0.0

        # 趋势判断（基于成交额）
        if len(amounts) >= 5:
            recent = amounts[-5:].mean()
            prev = amounts[-10:-5].mean() if len(amounts) >= 10 else recent
            if recent > prev * 1.15:
                trend = "increasing"
            elif recent < prev * 0.85:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        # 相对价格变化（direction signal）
        direction = "long" if price_return_5d > 1 else "short" if price_return_5d < -1 else "neutral"

        return {
            "activity": round(activity, 2),
            "activity_ratio": round(activity_ratio, 3),
            "price_return_5d": round(price_return_5d, 2),
            "price_return_20d": round(price_return_20d, 2),
            "trend": trend,
            "direction": direction,
            "latest_close": round(float(closes[-1]), 2),
            "record_count": len(df),
            "source": "HSJ",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


def get_northbound_top_stocks(top_n: int = 20, days: int = 20) -> List[Dict]:
    """北向资金监控：扫描全市场 HSJ 数据，返回活跃度提升最大的 Top N 股票

    扫描 D:/TDX/vipdoc/hsj/{sh,sz,bj}/lday/ 下所有 .day 文件，
    计算每只股票的 activity_ratio（近5日均额 / 前15日均额），
    按比值降序返回 Top N。

    Returns:
        List[dict] with keys: code, market, activity, activity_ratio,
                   price_return_5d, trend
    """
    import struct
    from pathlib import Path

    TDX_DATA_PATH = "D:/TDX/vipdoc"
    results = []

    for market in ["sh", "sz", "bj"]:
        hsj_dir = Path(TDX_DATA_PATH) / "hsj" / market / "lday"
        if not hsj_dir.exists():
            continue

        for fpath in hsj_dir.glob("*.day"):
            code = fpath.stem  # e.g. "sh600000"
            try:
                with open(fpath, "rb") as f:
                    f.seek(0, 2)
                    file_size = f.tell()
                    n_records = file_size // 32
                    if n_records < days:
                        continue
                    f.seek(-(days * 32), 2)
                    raw = f.read(days * 32)

                amounts = []
                closes = []
                for i in range(0, len(raw), 32):
                    date_idx, o, h, l, c, amt, vol, _ = struct.unpack_from("<IiiiiiII", raw, i)
                    if not (20200101 <= date_idx <= 20301231):
                        continue
                    amounts.append(float(amt))
                    closes.append(float(c))

                if len(amounts) < 10:
                    continue

                # activity_ratio: recent 5d avg / previous period avg
                recent = np.mean(amounts[-5:])
                earlier = np.mean(amounts[:-5]) if len(amounts) > 5 else recent
                ratio = recent / earlier if earlier > 0 else 1.0

                # price return
                ret5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 and closes[-6] != 0 else 0.0

                # trend
                if len(amounts) >= 10:
                    r5 = np.mean(amounts[-5:])
                    p5 = np.mean(amounts[-10:-5])
                    trend = "increasing" if r5 > p5 * 1.15 else "decreasing" if r5 < p5 * 0.85 else "stable"
                else:
                    trend = "stable"

                # Filter: only stock codes (skip ETFs/indices/bonds)
                stock_code = code[2:]  # remove market prefix
                if not (stock_code.startswith(("000", "001", "002", "003",
                        "300", "301", "600", "601", "603", "605", "688"))
                        and len(stock_code) == 6):
                    continue

                results.append({
                    "code": code,
                    "market": market,
                    "activity": round(float(np.mean(amounts)), 2),
                    "activity_ratio": round(ratio, 3),
                    "price_return_5d": round(ret5, 2),
                    "trend": trend,
                })
            except Exception:
                continue

    # Sort by activity_ratio descending
    results.sort(key=lambda x: x["activity_ratio"], reverse=True)
    return results[:top_n]


def get_sector_performance(top_n: int = 10, days: int = 20) -> List[Dict]:
    """板块轮动：计算各板块近 N 天的平均收益率，返回热门板块

    扫描 infoharbor_block 的板块映射，对每个板块内的股票
    计算平均收益率，按收益排序返回 Top N 板块。

    Returns:
        List[dict] with keys: block, stock_count, avg_return, top_stocks
    """
    import struct
    from pathlib import Path
    from lib.data_layer import get_all_blocks

    blocks = get_all_blocks()
    if not blocks:
        return []

    TDX_DATA_PATH = "D:/TDX/vipdoc"
    block_perf = []

    for block_name, stock_codes in blocks.items():
        if len(stock_codes) < 5:  # skip tiny blocks
            continue

        returns = []
        for code in stock_codes[:50]:  # sample up to 50 stocks per block
            market = "sh" if code.startswith(("6", "9")) else "sz"
            fpath = Path(TDX_DATA_PATH) / market / "lday_4" / f"{market}{code}.day"
            if not fpath.exists():
                continue
            try:
                with open(fpath, "rb") as f:
                    f.seek(0, 2)
                    n = f.tell() // 32
                    if n < days:
                        continue
                    f.seek(-(days * 32), 2)
                    raw = f.read(days * 32)
                closes = []
                for i in range(0, len(raw), 32):
                    _, _, _, _, c, _, _, _ = struct.unpack_from("<IiiiiiII", raw, i)
                    closes.append(c)
                if len(closes) >= 2 and closes[0] != 0:
                    ret = (closes[-1] / closes[0] - 1) * 100
                    returns.append(ret)
            except Exception:
                continue

        if not returns:
            continue

        avg_ret = float(np.mean(returns))
        block_perf.append({
            "block": block_name,
            "stock_count": len(stock_codes),
            "avg_return": round(avg_ret, 2),
            "sampled": len(returns),
        })

    block_perf.sort(key=lambda x: x["avg_return"], reverse=True)
    return block_perf[:top_n]


# ========== 财务估值因子（来自 CW/gpcw）==========

def get_financial_valuation_factor(code: str) -> Dict:
    """财务估值因子：基于 TDX 财务报表数据

    数据来源：D:/TDX/vipdoc/cw/gpcw*.dat（446字段/股票）
    注意：字段具体含义（PE/PB/ROE等）尚未完全解码，
         当前使用原始字段索引作为特征值。

    Returns:
        dict with keys:
          - field_count: 非空字段数（反映财务数据完整性）
          - latest_main_values: 最新主值字典（前20字段）
          - data_freshness: 数据新鲜度
          - source: 数据来源
    """
    try:
        from lib.data_layer import get_stock_financial, get_financial_file_info
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        df = get_stock_financial(code)
        if df.empty:
            return {"error": f"no financial data for {code}"}

        info = get_financial_file_info()

        # 取最新字段值（前10个非空）
        latest_values = {}
        for _, row in df.head(10).iterrows():
            latest_values[f"field_{int(row['field_index'])}"] = {
                "main": int(row["value_main"]),
                "aux": int(row["value_aux"]),
            }

        return {
            "field_count": len(df),
            "latest_main_values": latest_values,
            "file_date": info.get("filename", "unknown"),
            "source": "CW/gpcw",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


# ========== 高频盘口因子（来自 GP 高频Tick）==========

def get_gp_market_factor(code: str, market: str = "sz") -> Dict:
    """高频盘口因子：基于 GP 高频Tick数据

    数据来源：D:/TDX/vipdoc/gp/gpsz*.dat（43种记录类型）
    已知记录类型：
      - 0x01 (1): 年报/季报财务数据
      - 0x03 (3): 日线指标A
      - 0x2F (47): 最近日线（约118条，2025-2026）

    注意：GP文件格式尚未完全逆向，字段具体含义待确定。
         当前版本提供已解码的基本指标。

    Returns:
        dict with keys:
          - annual_count: 年报/季报记录数
          - recent_count: 最近日线记录数
          - latest_type: 最新记录类型
          - latest_value_main: 最新主值
          - source: 数据来源
    """
    try:
        from lib.data_tdx_gp import get_gp_data
    except ImportError:
        return {"error": "data_tdx_gp not available"}

    try:
        df = get_gp_data(code, market=market)
        if df.empty:
            return {"error": f"no GP data for {code} ({market})"}

        # 统计各类型记录数
        type_counts = df["record_type"].value_counts().to_dict()

        # 取最新记录
        latest = df.sort_values("date").iloc[-1]

        return {
            "annual_count": type_counts.get(1, 0),  # 0x01
            "recent_count": type_counts.get(47, 0),  # 0x2F
            "daily_count": type_counts.get(3, 0),  # 0x03
            "total_records": len(df),
            "type_count": len(type_counts),
            "latest_date": int(latest["date"]),
            "latest_type": int(latest["record_type"]),
            "latest_value_main": round(float(latest["value_main"]), 4),
            "source": "GP/tdxgp",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


# ========== 港股通因子（来自 GGT）==========

def get_ggt_market_factor(code: str, days: int = 20) -> Dict:
    """港股通因子：基于港股通日线交易数据

    数据来源：D:/TDX/vipdoc/ggt/lday/
    数据列：date, open, high, low, close, volume, amount

    Returns:
        dict with keys:
          - activity: 日均成交额（万元）
          - activity_ratio: 活跃度比（近期 vs 早期）
          - price_return_5d: 5日收益率
          - price_return_20d: 20日收益率
          - trend: 趋势（increasing/decreasing/stable）
          - record_count: 记录数
          - source: 数据来源
    """
    try:
        from lib.data_layer import get_ggt_hist
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        df = get_ggt_hist(code, days=days)
        if df.empty or len(df) < 5:
            return {"error": "insufficient GGT data"}

        amounts = df["amount"].values
        volumes = df["volume"].values if "volume" in df.columns else None
        closes = df["close"].values

        # 日均成交额
        activity = float(amounts.mean())

        # 活跃度比
        if len(amounts) >= 10:
            recent_avg = float(amounts[-5:].mean())
            early_avg = float(amounts[:5].mean())
            activity_ratio = recent_avg / early_avg if early_avg > 0 else 1.0
        else:
            activity_ratio = 1.0

        # 价格动量
        if len(closes) >= 6:
            price_return_5d = (closes[-1] / closes[-6] - 1) * 100 if closes[-6] != 0 else 0.0
        else:
            price_return_5d = 0.0

        if len(closes) >= 21:
            price_return_20d = (closes[-1] / closes[-21] - 1) * 100 if closes[-21] != 0 else 0.0
        else:
            price_return_20d = 0.0

        # 趋势判断
        if len(amounts) >= 5:
            recent = amounts[-5:].mean()
            prev = amounts[-10:-5].mean() if len(amounts) >= 10 else recent
            if recent > prev * 1.15:
                trend = "increasing"
            elif recent < prev * 0.85:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        direction = "long" if price_return_5d > 1 else "short" if price_return_5d < -1 else "neutral"

        return {
            "activity": round(activity, 2),
            "activity_ratio": round(activity_ratio, 3),
            "price_return_5d": round(price_return_5d, 2),
            "price_return_20d": round(price_return_20d, 2),
            "trend": trend,
            "direction": direction,
            "latest_close": round(float(closes[-1]), 2),
            "record_count": len(df),
            "source": "GGT",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


# ========== 新三板因子（来自 XSB）==========

def get_xsb_market_factor(code: str, days: int = 20) -> Dict:
    """新三板因子：基于新三板日线数据

    数据来源：D:/TDX/vipdoc/xsb/lday/

    注意：新三板流动性较差，信号仅供参考。

    Returns:
        dict with keys:
          - activity: 日均成交额
          - price_return_5d: 5日收益率
          - trend: 趋势
          - record_count: 记录数
          - source: 数据来源
    """
    try:
        from lib.data_layer import get_xsb_hist
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        df = get_xsb_hist(code, days=days)
        if df.empty or len(df) < 5:
            return {"error": "insufficient XSB data"}

        amounts = df["amount"].values if "amount" in df.columns else None
        closes = df["close"].values

        activity = float(amounts.mean()) if amounts is not None else 0.0

        if len(closes) >= 6:
            price_return_5d = (closes[-1] / closes[-6] - 1) * 100 if closes[-6] != 0 else 0.0
        else:
            price_return_5d = 0.0

        if len(closes) >= 5:
            recent = closes[-5:].mean()
            prev = closes[-10:-5].mean() if len(closes) >= 10 else recent
            if recent > prev * 1.05:
                trend = "increasing"
            elif recent < prev * 0.95:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return {
            "activity": round(activity, 2),
            "price_return_5d": round(price_return_5d, 2),
            "trend": trend,
            "record_count": len(df),
            "source": "XSB",
        }
    except Exception as e:
        return {"error": "Data fetch failed"}


# ========== 指数增强因子（来自 CSI）==========

def get_index_enhancement_factor(code: str, market: str = "sh") -> Dict:
    """指数增强因子：获取主要指数信息

    数据来源：D:/TDX/vipdoc/csi/zs/

    主要指数：
      - 000300: 沪深300
      - 000905: 中证500
      - 000852: 中证1000

    Returns:
        dict with keys:
          - index_code: 指数代码
          - index_name: 指数名称
          - index_latest_close: 最新收盘点位
          - source: 数据来源
    """
    try:
        from lib.data_layer import get_index_hist
    except ImportError:
        return {"error": "data_layer not available"}

    try:
        # 判断市场并确定对应的指数代码
        if market == "sh":
            index_code = "000300"  # 上证 -> 沪深300
        elif market == "sz":
            index_code = "000300"  # 深证 -> 沪深300
        else:
            return {"error": "unsupported market for index factor"}

        # 获取指数历史
        index_df = get_index_hist(index_code, days=5)
        if index_df.empty:
            return {"error": f"no index data for {index_code}"}

        result = {
            "index_code": index_code,
            "index_name": f"CSI{index_code}",
            "index_latest_close": round(float(index_df["close"].iloc[-1]), 2),
            "index_change_pct": round(
                (float(index_df["close"].iloc[-1]) / float(index_df["close"].iloc[-2]) - 1) * 100
                if len(index_df) >= 2 else 0.0, 2),
            "source": "CSI",
        }

        return result
    except Exception as e:
        return {"error": "Data fetch failed"}


# ========== 综合计算 ==========

def compute_all_signals(hist_df: pd.DataFrame, macro_score: float = 0,
                        factor_config=None, code: str = None, market: str = "sh") -> Dict:
    """计算所有策略信号

    Args:
        hist_df: 历史K线数据（必须包含 close 列）
        macro_score: 宏观评分 (-1 到 1)
        factor_config: 可选 FactorConfig 实例，控制因子开关；None=全部计算
        code: 股票代码（6位，如 "600000"），用于获取新数据源因子
        market: 市场（sh/sz/bj），默认 sh
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
    ch_filter = {"kc_pos": 0.5, "adjustment": 1.0, "reason": ""}
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

    # 8. 新数据源因子（HSJ/ScJyData/CW/GP/GGT/XSB/CSI）
    data_factors = {}
    if code is not None:
        # 北向资金因子（HSJ 沪股通）
        if _use('use_hsj', default=True):
            hsj_factor = get_northbound_flow_factor(code, market=market, days=20)
            if "error" not in hsj_factor:
                data_factors["northbound"] = hsj_factor

        # 财务估值因子（CW/gpcw）
        if _use('use_financial', default=True):
            fin_factor = get_financial_valuation_factor(code)
            if "error" not in fin_factor:
                data_factors["financial"] = fin_factor

        # 高频盘口因子（GP/tdxgp）
        if _use('use_gp', default=True):
            gp_factor = get_gp_market_factor(code, market=market)
            if "error" not in gp_factor:
                data_factors["gp"] = gp_factor

        # 港股通因子（GGT）
        if _use('use_ggt', default=False):
            ggt_factor = get_ggt_market_factor(code, days=20)
            if "error" not in ggt_factor:
                data_factors["ggt"] = ggt_factor

        # 新三板因子（XSB，仅当市场为 bj 时）
        if market == "bj" and _use('use_xsb', default=False):
            xsb_factor = get_xsb_market_factor(code, days=20)
            if "error" not in xsb_factor:
                data_factors["xsb"] = xsb_factor

        # 指数增强因子（CSI）
        if _use('use_csi', default=False):
            index_factor = get_index_enhancement_factor(code, market=market)
            if "error" not in index_factor:
                data_factors["index_enhancement"] = index_factor

        # 板块映射因子（infoharbor_block）
        if _use('use_block', default=True):
            from lib.data_layer import get_stock_blocks
            blocks = get_stock_blocks(code)
            if blocks:
                data_factors["block"] = {
                    "block_count": len(blocks),
                    "blocks": blocks[:10],  # top 10 板块
                }

        # 估值因子（PE/PB/市值）
        if _use('use_valuation', default=True):
            from lib.data_layer import get_stock_valuation
            val = get_stock_valuation(code)
            if val:
                data_factors["valuation"] = val

        # 基金持仓因子
        if _use('use_fund_hold', default=True):
            from lib.data_layer import get_fund_holdings
            fh = get_fund_holdings(code)
            if fh:
                data_factors["fund_holdings"] = fh

        # 股息率因子（gbbq 除权除息）
        if _use('use_dividend', default=True):
            from lib.data_layer import get_dividend_amount
            div = get_dividend_amount(code)
            if div > 0:
                data_factors["dividend"] = {"per_share": round(div, 4)}

    # 市场情绪因子（ScJyData，不需要 code）
    if _use('use_scjy', default=True):
        sentiment = get_market_sentiment_factor(days=10)
        if "error" not in sentiment:
            data_factors["market_sentiment"] = sentiment

    # 8b. IC 加权因子评分（替代策略信号组合）
    ic_score = compute_ic_weighted_score(factors)

    # 8c. 基于 data_factors 调整评分
    if data_factors:
        ic_score = adjust_score_by_data_factors(ic_score, data_factors)

    return {
        "signals": signals,
        "combined": combined,
        "ic_score": round(ic_score, 4),  # IC 加权综合评分
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
        "data_factors": data_factors,
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
        return {"valid": False, "reason": "Validation failed"}
