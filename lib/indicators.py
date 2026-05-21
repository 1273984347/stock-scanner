"""
通道指标模块 —— 凯尔特纳通道 + 薛斯通道

参考：因子.txt (V4修正版) + v4.txt
- 凯尔特纳通道：EMA(21) 中轨 ± 2×ATR(14) 带宽
- 薛斯通道：短期(5) + 长期(60) 高低价移动平均通道
"""
import numpy as np
import pandas as pd
from typing import Dict, Optional


# ========== 参数配置 ==========

# 凯尔特纳通道
KC_LENGTH = 21    # EMA 周期
KC_ATR = 14       # ATR 周期
KC_SCALAR = 2.0   # 带宽倍数（上下轨 = 中轨 ± scalar × ATR）

# 薛斯通道
XOSI_SHORT = 5    # 短期通道周期
XOSI_LONG = 60    # 长期通道周期（5分钟图 ≈ 5-7小时）

# ATR 多周期（备用）
ATR_SHORT = 10
ATR_LONG = 20


# ========== 凯尔特纳通道 ==========

def calculate_atr(df: pd.DataFrame, period: int = KC_ATR) -> pd.Series:
    """计算 Average True Range (ATR)

    Args:
        df: 必须包含 high, low, close 列
        period: ATR 周期

    Returns:
        ATR Series
    """
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values

    tr = np.zeros(len(high))
    tr[0] = high[0] - low[0]
    for i in range(1, len(high)):
        h_l = high[i] - low[i]
        h_pc = abs(high[i] - close[i - 1])
        l_pc = abs(low[i] - close[i - 1])
        tr[i] = max(h_l, h_pc, l_pc)

    atr = np.zeros(len(tr))
    # 初始简单移动平均
    atr[period - 1] = np.mean(tr[:period])
    # 威尔尔德平滑
    for i in range(period, len(atr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    result = pd.Series(atr, index=df.index)
    result[:period - 1] = np.nan
    return result


def calculate_keltner_channels(
    df: pd.DataFrame,
    length: int = KC_LENGTH,
    atr_period: int = KC_ATR,
    scalar: float = KC_SCALAR,
) -> pd.DataFrame:
    """计算凯尔特纳通道

    中轨 = EMA(close, length)
    上轨 = 中轨 + scalar × ATR
    下轨 = 中轨 - scalar × ATR

    Args:
        df: 必须包含 high, low, close 列
        length: EMA 周期（默认21）
        atr_period: ATR 周期（默认14）
        scalar: 带宽倍数（默认2.0）

    Returns:
        DataFrame，包含 kc_upper, kc_mid, kc_lower, atr
    """
    if df is None or df.empty or len(df) < max(length, atr_period) + 1:
        return pd.DataFrame()

    close = df["close"]
    kc_mid = close.ewm(span=length, adjust=False).mean()
    atr = calculate_atr(df, atr_period)

    result = pd.DataFrame(index=df.index)
    result["kc_upper"] = kc_mid + scalar * atr
    result["kc_mid"] = kc_mid
    result["kc_lower"] = kc_mid - scalar * atr
    result["atr"] = atr

    return result


# ========== 薛斯通道 ==========

def calculate_xosi_channels(
    df: pd.DataFrame,
    short_period: int = XOSI_SHORT,
    long_period: int = XOSI_LONG,
) -> pd.DataFrame:
    """计算薛斯通道（Xosi Channels）

    长期通道（外轨）：
      上轨 = MA(high, long_period) + 2 × STD(high, long_period)
      下轨 = MA(low, long_period) - 2 × STD(low, long_period)

    短期通道（内轨）：
      上轨 = MA(high, short_period) + 2 × STD(high, short_period)
      下轨 = MA(low, short_period) - 2 × STD(low, short_period)

    Args:
        df: 必须包含 high, low 列
        short_period: 短期通道周期（默认5）
        long_period: 长期通道周期（默认60）

    Returns:
        DataFrame，包含 xu_long, xd_long, xu_short, xd_short
    """
    if df is None or df.empty or len(df) < long_period:
        return pd.DataFrame()

    high = df["high"]
    low = df["low"]

    result = pd.DataFrame(index=df.index)

    # 长期通道（外轨）
    result["xu_long"] = high.rolling(long_period).mean() + 2 * high.rolling(long_period).std()
    result["xd_long"] = low.rolling(long_period).mean() - 2 * low.rolling(long_period).std()

    # 短期通道（内轨）
    result["xu_short"] = high.rolling(short_period).mean() + 2 * high.rolling(short_period).std()
    result["xd_short"] = low.rolling(short_period).mean() - 2 * low.rolling(short_period).std()

    return result


# ========== 通道信号生成 ==========

def get_channel_position(
    df: pd.DataFrame,
    kc: pd.DataFrame,
    xosi: pd.DataFrame,
) -> pd.DataFrame:
    """计算价格在各通道中的相对位置

    Args:
        df: 价格数据（包含 close 列）
        kc: 凯尔特纳通道数据
        xosi: 薛斯通道数据

    Returns:
        DataFrame，包含 kc_pos (0=下轨, 1=上轨), xosi_long_pos, xosi_short_pos
    """
    if df is None or df.empty or kc is None or kc.empty or xosi is None or xosi.empty:
        return pd.DataFrame()

    close = df["close"]

    result = pd.DataFrame(index=df.index)

    # 凯尔特纳通道位置 (0-1, 0=下轨, 0.5=中轨, 1=上轨)
    kc_range = kc["kc_upper"] - kc["kc_lower"]
    result["kc_pos"] = np.where(
        kc_range > 0,
        (close - kc["kc_lower"]) / kc_range,
        0.5,
    )
    result["kc_pos"] = result["kc_pos"].clip(0, 1)

    # 薛斯长期通道位置
    xosi_long_range = xosi["xu_long"] - xosi["xd_long"]
    result["xosi_long_pos"] = np.where(
        xosi_long_range > 0,
        (close - xosi["xd_long"]) / xosi_long_range,
        0.5,
    )
    result["xosi_long_pos"] = result["xosi_long_pos"].clip(0, 1)

    # 薛斯短期通道位置
    xosi_short_range = xosi["xu_short"] - xosi["xd_short"]
    result["xosi_short_pos"] = np.where(
        xosi_short_range > 0,
        (close - xosi["xd_short"]) / xosi_short_range,
        0.5,
    )
    result["xosi_short_pos"] = result["xosi_short_pos"].clip(0, 1)

    return result


def get_channel_signals(
    df: pd.DataFrame,
    kc: pd.DataFrame,
    xosi: pd.DataFrame,
) -> pd.DataFrame:
    """生成通道信号

    信号逻辑（基于 v4.txt）：
    - 做多：触下轨 + 薛斯长期不强烈下行 + K线形态确认
    - 做空：触上轨 + 薛斯长期不强烈上行 + K线形态确认

    Args:
        df: 必须包含 high, low, close, open 列
        kc: 凯尔特纳通道数据
        xosi: 薛斯通道数据

    Returns:
        DataFrame，包含 signal (1=多, -1=空, 0=无信号), confidence, reason
    """
    if df is None or df.empty or kc is None or kc.empty or xosi is None or xosi.empty:
        return pd.DataFrame()

    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]

    result = pd.DataFrame(index=df.index)
    result["signal"] = 0  # 0=无信号, 1=做多, -1=做空
    result["confidence"] = 0.0
    result["reason"] = ""

    # 前一根K线（用于形态确认）
    prev_close = close.shift(1)
    prev_high = high.shift(1)
    prev_low = low.shift(1)

    # ========== 做多信号 ==========
    # 条件A：价格触及或接近凯尔特纳下轨（允许1%容差）
    touch_lower = (close <= kc["kc_lower"] * 1.01) & (close >= kc["kc_lower"] * 0.99)

    # 条件B：价格同时处于薛斯短期通道下轨之上（不是真破位）
    above_short = close > xosi["xd_short"] * 0.99

    # 条件C：薛斯长期通道不强烈下行（斜率判断：3周期变化）
    long_mid = (xosi["xu_long"] + xosi["xd_long"]) / 2
    slope_long = long_mid.pct_change(3) * 100  # 3周期斜率百分比
    long_not_down = slope_long > -0.1  # 不是大角度下行

    # 条件D：收盘阳线（形态确认）
    close_above_open = close > open_
    # 额外：收盘价回到下轨之上
    price_recovered = close > kc["kc_lower"]

    long_cond = (
        touch_lower &
        above_short &
        long_not_down &
        close_above_open &
        price_recovered
    )

    # ========== 做空信号（镜像） ==========
    # 条件A：价格触及或接近凯尔特纳上轨
    touch_upper = (close >= kc["kc_upper"] * 0.99) & (close <= kc["kc_upper"] * 1.01)

    # 条件B：价格同时处于薛斯短期通道上轨之下
    below_short = close < xosi["xu_short"] * 1.01

    # 条件C：薛斯长期通道不强烈上行
    long_not_up = slope_long < 0.1

    # 条件D：收盘阴线
    close_below_open = close < open_

    # 额外：收盘价回到上轨之下
    price_recovered_down = close < kc["kc_upper"]

    short_cond = (
        touch_upper &
        below_short &
        long_not_up &
        close_below_open &
        price_recovered_down
    )

    # ========== 赋值信号 ==========
    result.loc[long_cond, "signal"] = 1
    result.loc[short_cond, "signal"] = -1

    # ========== 置信度计算 ==========
    # 凯尔特纳通道位置越接近下轨/上轨，置信度越高
    kc_pos = get_channel_position(df, kc, xosi)

    # 多头：位置越接近0（接近下轨）置信度越高
    result.loc[long_cond, "confidence"] = (
        0.6 + 0.3 * (1 - kc_pos.loc[long_cond, "kc_pos"])
    ).clip(0, 1)

    # 空头：位置越接近1（接近上轨）置信度越高
    result.loc[short_cond, "confidence"] = (
        0.6 + 0.3 * kc_pos.loc[short_cond, "kc_pos"]
    ).clip(0, 1)

    # ========== 原因描述 ==========
    result.loc[long_cond, "reason"] = "凯尔特纳下轨触轨+薛斯长期不空+阳线确认"
    result.loc[short_cond, "reason"] = "凯尔特纳上轨触轨+薛斯长期不多+阴线确认"

    return result


def get_regime_from_kc(kc: pd.DataFrame, lookback: int = 20) -> Dict[str, float]:
    """从凯尔特纳通道状态推断市场体制特征

    用于辅助判断 TREND_UP / MEAN_REVERSION / COMPRESSION / TREND_DOWN

    Args:
        kc: 凯尔特纳通道数据
        lookback: 观察窗口

    Returns:
        Dict，包含 trend_score, volatility_score, compression_score
    """
    if kc is None or kc.empty or len(kc) < lookback:
        return {"trend_score": 0.0, "volatility_score": 0.0, "compression_score": 0.0}

    kc_range = kc["kc_upper"] - kc["kc_lower"]
    kc_slope = kc["kc_mid"].pct_change(5) * 100  # 5周期斜率
    atr = kc["atr"]

    # 趋势得分：通道中轨向上 + ATR 扩张
    recent_slope = kc_slope.iloc[-1] if len(kc_slope) > 0 else 0
    recent_atr_ratio = atr.iloc[-1] / atr.iloc[-lookback] if len(atr) >= lookback else 1.0
    trend_score = min(1.0, max(-1.0, recent_slope / 1.0 + (recent_atr_ratio - 1) * 2))

    # 波动率得分：ATR 绝对水平（相对历史）
    atr_recent = atr.iloc[-min(20, len(atr)):]
    volatility_score = min(1.0, atr_recent.mean() / (kc_range.mean() + 1e-9))

    # 压缩得分：通道收窄（ATR收缩 + 中轨走平）
    atr_trend = atr.iloc[-1] / atr.iloc[-min(10, len(atr))]
    slope_variation = kc_slope.iloc[-min(lookback, len(kc_slope)):].std()
    compression_score = max(0, min(1, (1 - atr_trend) * 2 + (1 - slope_variation)))

    return {
        "trend_score": float(trend_score),
        "volatility_score": float(volatility_score),
        "compression_score": float(compression_score),
    }


# ========== 便捷入口 ==========

def compute_all_channels(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """一站式计算所有通道指标

    Args:
        df: 必须包含 high, low, close, open 列

    Returns:
        Dict:
          - kc: 凯尔特纳通道
          - xosi: 薛斯通道
          - position: 通道位置
          - signals: 通道信号
    """
    if df is None or df.empty or len(df) < max(KC_LENGTH, XOSI_LONG) + 1:
        return {}

    kc = calculate_keltner_channels(df)
    xosi = calculate_xosi_channels(df)
    position = get_channel_position(df, kc, xosi)
    signals = get_channel_signals(df, kc, xosi)

    return {
        "kc": kc,
        "xosi": xosi,
        "position": position,
        "signals": signals,
    }
