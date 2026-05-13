"""杀猪盘排查 —— 量价操纵检测（急速拉升/阶梯出货/高度控盘）

来源：wbh604/UZI-Skill (GitHub 1.5k stars)
集成到 constraints.py 作为 HARD 约束规则
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


def detect_pump_and_dump(hist: pd.DataFrame) -> List[Dict]:
    """检测杀猪盘特征

    Args:
        hist: 包含 open/close/high/low/volume 列的 DataFrame

    Returns:
        [{"type": str, "severity": str, "detail": str, "score": float}]
    """
    if hist.empty or len(hist) < 30:
        return []

    alerts = []
    close = hist["close"]
    volume = hist["volume"] if "volume" in hist.columns else pd.Series(0, index=close.index)
    high = hist["high"] if "high" in hist.columns else close
    low = hist["low"] if "low" in hist.columns else close

    n = len(close)

    # 1. 急速拉升检测（连续大涨）
    returns = close.pct_change()
    recent = returns.tail(5)
    rapid_up_days = (recent > 0.05).sum()
    total_gain = (close.iloc[-1] / close.iloc[-6] - 1) if n >= 6 else 0

    if rapid_up_days >= 3 and total_gain > 0.20:
        alerts.append({
            "type": "急速拉升",
            "severity": "high",
            "detail": f"近5日{rapid_up_days}天涨幅>5%，累计涨幅{total_gain:.1%}",
            "score": 0.8,
        })

    # 2. 量价背离
    price_up = close.tail(20) > close.tail(20).shift(1)
    vol_up = volume.tail(20) > volume.tail(20).shift(1)
    divergence = (price_up != vol_up).sum()
    if divergence > 14:
        alerts.append({
            "type": "量价背离",
            "severity": "medium",
            "detail": f"近20日量价方向不一致{divergence}天",
            "score": 0.5,
        })

    # 3. 振幅异常（大概率控盘）
    amplitude = (high - low) / close.shift(1).replace(0, np.nan)
    avg_amp = amplitude.tail(10).mean()
    if avg_amp > 0.08:
        alerts.append({
            "type": "振幅异常",
            "severity": "medium",
            "detail": f"近10日平均振幅{avg_amp:.1%}>8%",
            "score": 0.4,
        })

    # 4. 成交量异常（暴量后缩量）
    if n >= 40:
        vol_20 = volume.tail(20).mean()
        vol_40 = volume.tail(40).mean()
        if vol_40 > 0:
            vol_ratio = vol_20 / vol_40
            if vol_ratio < 0.3:
                alerts.append({
                    "type": "缩量异常（暴量后萎靡）",
                    "severity": "medium",
                    "detail": f"近20日均量/近40日均量={vol_ratio:.2f}<0.3",
                    "score": 0.6,
                })

    # 5. 封单异常（涨停板上的封单量）
    if n >= 10:
        close_last = close.tail(10)
        pre_close = close.shift(1).tail(10)
        limit_up_days = ((close_last - pre_close) / pre_close.replace(0, np.nan) > 0.09).sum()
        if limit_up_days >= 3:
            alerts.append({
                "type": "频繁涨停",
                "severity": "high",
                "detail": f"近10日涨停{limit_up_days}次",
                "score": 0.7,
            })

    # 6. 换手率异常（极高换手后骤降）
    if "turnover" in hist.columns:
        turnover = hist["turnover"]
        if n >= 20:
            recent_turn = turnover.tail(5).mean()
            mid_turn = turnover.tail(20).mean()
            if mid_turn > 0 and recent_turn / mid_turn > 3:
                alerts.append({
                    "type": "换手率异动",
                    "severity": "medium",
                    "detail": f"近5日换手率均值/近20日均值={recent_turn/mid_turn:.1f}",
                    "score": 0.5,
                })

    # 7. 走势独立性（与大盘背离）
    if "benchmark_return" in hist.columns:
        stock_ret = close.pct_change().tail(20).mean()
        bench_ret = hist["benchmark_return"].tail(20).mean()
        if stock_ret > 0.02 and bench_ret < -0.02:
            alerts.append({
                "type": "走势独立（逆势拉升）",
                "severity": "high",
                "detail": f"个股涨{stock_ret:.1%} vs 大盘跌{abs(bench_ret):.1%}",
                "score": 0.6,
            })

    return alerts


def is_pump_dump_risk(hist: pd.DataFrame) -> bool:
    """快速判断是否有杀猪盘风险"""
    alerts = detect_pump_and_dump(hist)
    high_risk = [a for a in alerts if a["severity"] == "high" and a["score"] > 0.5]
    return len(high_risk) >= 2
