"""技术信号分析 —— 17 组指标 + 12 个信号 + 综合研判

来源：stock-tech-analysis skill
输入：包含 open/high/low/close/volume 列的 DataFrame
输出：每个指标的 bull/bear/neutral 信号 + 综合多数投票判断
"""
import pandas as pd
import numpy as np
import math
from typing import Dict, List

# 复用 factors.py 中的实现，避免重复定义
from factors import calc_rsi as compute_rsi


def _safe(val):
    if val is None:
        return False
    try:
        return not math.isnan(val)
    except (TypeError, ValueError):
        return False


# ========== 指标计算 ==========

def sma(series, period):
    return series.rolling(window=period, min_periods=period).mean()


def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def compute_macd(close, fast=12, slow=26, signal_period=9):
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_bollinger(close, period=20, num_std=2):
    mid = sma(close, period)
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    return mid + num_std * std, mid, mid - num_std * std


def compute_kdj(high, low, close, k_period=9, d_smooth=3):
    lowest = low.rolling(window=k_period, min_periods=k_period).min()
    highest = high.rolling(window=k_period, min_periods=k_period).max()
    denom = highest - lowest
    rsv = ((close - lowest) / denom.replace(0, np.nan)) * 100.0
    k = rsv.ewm(com=d_smooth - 1, adjust=False).mean()
    d = k.ewm(com=d_smooth - 1, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def compute_atr(high, low, close, period=14):
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window=period, min_periods=period).mean()


def compute_obv(close, volume):
    direction = np.sign(close.diff()).fillna(0)
    return (volume * direction).cumsum()


def compute_vwap(high, low, close, volume):
    tp = (high + low + close) / 3.0
    cum_vol = volume.cumsum().replace(0, np.nan)
    return (tp * volume).cumsum() / cum_vol


def compute_cci(high, low, close, period=20):
    tp = (high + low + close) / 3.0
    tp_sma = tp.rolling(window=period, min_periods=period).mean()
    mad = tp.rolling(window=period, min_periods=period).apply(
        lambda x: np.mean(np.abs(x - x.mean())), raw=True
    )
    denom = (0.015 * mad).replace(0, np.nan)
    return (tp - tp_sma) / denom


def compute_williams_r(high, low, close, period=14):
    hh = high.rolling(window=period, min_periods=period).max()
    ll = low.rolling(window=period, min_periods=period).min()
    denom = (hh - ll).replace(0, np.nan)
    return ((hh - close) / denom) * -100.0


def compute_adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr_val = tr.rolling(window=period, min_periods=period).mean()
    atr_safe = atr_val.replace(0, np.nan)
    plus_di = 100.0 * plus_dm.rolling(window=period, min_periods=period).mean() / atr_safe
    minus_di = 100.0 * minus_dm.rolling(window=period, min_periods=period).mean() / atr_safe
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_val = dx.rolling(window=period, min_periods=period).mean()
    return adx_val, plus_di, minus_di


def detect_kdj_turn(j_series: pd.Series, lookback: int = 3) -> str:
    """Detect KDJ turn (design: important is turn from extremes)

    Returns:
        "turn_up" -- J value turns up from oversold extreme
        "turn_down" -- J value turns down from overbought extreme
        "neutral" -- no clear turn
    """
    if len(j_series) < lookback + 1:
        return "neutral"

    j = j_series.iloc[-lookback - 1:]
    # Turn up: prev J < prev-prev J, current J > prev J
    if j.iloc[-2] < j.iloc[-3] and j.iloc[-1] > j.iloc[-2]:
        if j.iloc[-2] < 20:  # Turn in oversold zone
            return "turn_up"

    # Turn down: prev J > prev-prev J, current J < prev J
    if j.iloc[-2] > j.iloc[-3] and j.iloc[-1] < j.iloc[-2]:
        if j.iloc[-2] > 80:  # Turn in overbought zone
            return "turn_down"

    return "neutral"


def multi_period_kdj_verdict(daily_kdj: dict, h60_kdj: dict = None, m15_kdj: dict = None) -> dict:
    """Multi-period KDJ comprehensive judgment

    Daily for direction, 60-min for rhythm, 15-min for execution
    """
    score = 0
    reasons = []

    # Daily direction (highest weight)
    if daily_kdj.get("j", 50) < 80 and daily_kdj.get("k", 50) > daily_kdj.get("d", 50):
        score += 40
        reasons.append("日线KDJ偏多")
    elif daily_kdj.get("j", 50) > 80:
        score -= 20
        reasons.append("日线过热")

    if daily_kdj.get("turn") == "turn_up":
        score += 20
        reasons.append("日线拐头向上")

    # 60-min rhythm
    if h60_kdj:
        if h60_kdj.get("k", 50) > h60_kdj.get("d", 50):
            score += 20
            reasons.append("60分钟偏多")
        if h60_kdj.get("turn") == "turn_up":
            score += 10
            reasons.append("60分钟拐头")

    # 15-min execution
    if m15_kdj:
        if m15_kdj.get("k", 50) > m15_kdj.get("d", 50):
            score += 10
            reasons.append("15分钟偏多")

    # Verdict
    if score >= 60:
        verdict = "buy"
    elif score >= 30:
        verdict = "hold"
    elif score >= 0:
        verdict = "wait"
    else:
        verdict = "avoid"

    return {"verdict": verdict, "score": score, "reason": " | ".join(reasons)}


def compute_roc(close, period=12):
    prev = close.shift(period).replace(0, np.nan)
    return (close - close.shift(period)) / prev * 100.0


def compute_mfi(high, low, close, volume, period=14):
    tp = (high + low + close) / 3.0
    mf = tp * volume
    tp_diff = tp.diff()
    pos_mf = mf.where(tp_diff > 0, 0.0).rolling(window=period, min_periods=period).sum()
    neg_mf = mf.where(tp_diff < 0, 0.0).rolling(window=period, min_periods=period).sum()
    neg_safe = neg_mf.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + pos_mf / neg_safe))


def compute_trix(close, period=12):
    e1 = ema(close, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    prev = e3.shift(1).replace(0, np.nan)
    return (e3 - e3.shift(1)) / prev * 100.0


# ========== 信号生成 ==========

def generate_signals(df: pd.DataFrame) -> Dict[str, str]:
    """从 DataFrame 生成 12 个多空信号"""
    if len(df) < 30:
        return {}

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]
    row = df.iloc[-1]
    signals = {}

    # MA 交叉
    sma5 = sma(close, 5).iloc[-1]
    sma20 = sma(close, 20).iloc[-1]
    if _safe(sma5) and _safe(sma20):
        signals["MA_Cross"] = "bullish" if sma5 > sma20 else "bearish"

    # MACD
    _, _, hist = compute_macd(close)
    h_val = hist.iloc[-1]
    if _safe(h_val):
        signals["MACD"] = "bullish" if h_val > 0 else "bearish"

    # RSI
    rsi_val = compute_rsi(close).iloc[-1]
    if _safe(rsi_val):
        signals["RSI"] = "bullish" if rsi_val < 30 else ("bearish" if rsi_val > 70 else "neutral")

    # Bollinger
    bb_u, _, bb_l = compute_bollinger(close)
    c = close.iloc[-1]
    if _safe(bb_u.iloc[-1]) and _safe(bb_l.iloc[-1]):
        if c < bb_l.iloc[-1]:
            signals["Bollinger"] = "bullish"
        elif c > bb_u.iloc[-1]:
            signals["Bollinger"] = "bearish"
        else:
            signals["Bollinger"] = "neutral"

    # KDJ with turn detection
    k, d, j = compute_kdj(high, low, close)
    j_val = j.iloc[-1]
    k_val = k.iloc[-1]
    d_val = d.iloc[-1]
    if _safe(j_val) and _safe(k_val) and _safe(d_val):
        turn = detect_kdj_turn(j)
        signals["KDJ_turn"] = turn
        if j_val > 80:
            if turn == "turn_down":
                signals["KDJ"] = "bearish"  # Overbought + turn down = bearish
            else:
                signals["KDJ"] = "neutral"  # Overbought but no turn = neutral
        elif j_val < 20:
            if turn == "turn_up":
                signals["KDJ"] = "bullish"  # Oversold + turn up = bullish
            else:
                signals["KDJ"] = "neutral"  # Oversold but no turn = neutral
        elif k_val > d_val:
            signals["KDJ"] = "bullish"
        else:
            signals["KDJ"] = "bearish"

    # CCI
    cci_val = compute_cci(high, low, close).iloc[-1]
    if _safe(cci_val):
        signals["CCI"] = "bullish" if cci_val < -100 else ("bearish" if cci_val > 100 else "neutral")

    # Williams %R
    wr_val = compute_williams_r(high, low, close).iloc[-1]
    if _safe(wr_val):
        signals["Williams_R"] = "bullish" if wr_val < -80 else ("bearish" if wr_val > -20 else "neutral")

    # ADX/DMI
    adx_val, plus_di, minus_di = compute_adx(high, low, close)
    adx_v = adx_val.iloc[-1]
    pdi = plus_di.iloc[-1]
    mdi = minus_di.iloc[-1]
    if _safe(adx_v) and _safe(pdi) and _safe(mdi):
        if adx_v > 25:
            signals["ADX_DMI"] = "bullish" if pdi > mdi else "bearish"
        else:
            signals["ADX_DMI"] = "neutral"

    # ROC
    roc_val = compute_roc(close).iloc[-1]
    if _safe(roc_val):
        signals["ROC"] = "bullish" if roc_val > 0 else "bearish"

    # MFI
    mfi_val = compute_mfi(high, low, close, volume).iloc[-1]
    if _safe(mfi_val):
        signals["MFI"] = "bullish" if mfi_val < 20 else ("bearish" if mfi_val > 80 else "neutral")

    # OBV
    obv = compute_obv(close, volume)
    if len(obv) >= 5:
        obv_now = obv.iloc[-1]
        obv_prev = obv.iloc[-5]
        if _safe(obv_now) and _safe(obv_prev):
            signals["OBV"] = "bullish" if obv_now > obv_prev else "bearish"

    # TRIX
    trix_val = compute_trix(close).iloc[-1]
    if _safe(trix_val):
        signals["TRIX"] = "bullish" if trix_val > 0 else "bearish"

    return signals


def summarize_signals(signals: Dict[str, str]) -> Dict:
    """多数投票综合研判"""
    total = len(signals)
    if total == 0:
        return {"verdict": "insufficient_data", "bullish_count": 0, "bearish_count": 0,
                "neutral_count": 0, "total_indicators": 0, "bullish_pct": 0.0, "bearish_pct": 0.0}

    bullish = sum(1 for s in signals.values() if s == "bullish")
    bearish = sum(1 for s in signals.values() if s == "bearish")
    neutral = total - bullish - bearish

    if bullish > bearish:
        verdict = "bullish"
    elif bearish > bullish:
        verdict = "bearish"
    else:
        verdict = "neutral"

    return {
        "verdict": verdict,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "total_indicators": total,
        "bullish_pct": round(bullish / total * 100, 1),
        "bearish_pct": round(bearish / total * 100, 1),
    }


def calc_composite_verdict(hist_df: pd.DataFrame) -> Dict:
    """计算综合技术信号研判（一步调用）

    Args:
        hist_df: 包含 open/high/low/close/volume 列的 DataFrame

    Returns:
        {"verdict": "bullish|bearish|neutral", "signals": {...}, "summary": {...}}
    """
    signals = generate_signals(hist_df)
    summary = summarize_signals(signals)
    return {"verdict": summary["verdict"], "signals": signals, "summary": summary}
