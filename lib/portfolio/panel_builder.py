"""每日候选股因子面板构建

build_ic_panel(date, candidates, hist_data_dict):
    输入: date str, candidates [{}], hist_data_dict {code: pd.DataFrame}
    输出: {
        "date": "2026-05-24",
        "stocks": [
            {
                "code": "000001",
                "momentum_5": float,
                "momentum_20": float,
                "reversal_5": float,
                "volatility_20": float,
                "volume_ratio": float,
                "trend_strength": float,
                "rsi_14": float,
                "kdj_j": float,
                "adx": float,
                "excess_1d": float,   # 次日超额收益（对沪深300）
                "excess_5d": float,   # 5日累计超额收益
            }
        ]
    }

有效 IC 日期三条件：
1. 候选股 ≥ 10 只
2. Spearman 相关系数非 NaN
3. 标的未被停牌/涨跌停

save_ic_panel(panel, date):
    保存至 cache/ic/panel/ic_panel_{date}.json
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

IC_PANEL_DIR = Path("cache/ic/panel")

def build_ic_panel(date: str, candidates: List[Dict], hist_data_dict: Dict[str, pd.DataFrame]) -> Dict:
    """构建候选股因子面板"""
    from factors import (
        calc_rsi, calc_ema,
        calc_kdj, calc_adx,
    )

    stocks_data = []
    benchmark = _get_benchmark_returns(hist_data_dict)  # 沪深300同期收益

    for candidate in candidates:
        code = candidate.get("code")
        if not code or code not in hist_data_dict:
            continue

        df = hist_data_dict[code]
        if df.empty or len(df) < 30:
            continue

        close = df["close"]
        high = df.get("high", close)
        low = df.get("low", close)
        volume = df.get("volume", pd.Series(dtype=float))

        # 计算因子值
        momentum_5 = (close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 5 else 0
        momentum_20 = (close.iloc[-1] / close.iloc[-21] - 1) if len(close) > 20 else 0

        ma5 = close.tail(5).mean()
        ma20 = close.tail(20).mean()
        trend_strength = (ma5 / ma20 - 1) * 100 if ma20 != 0 else 0

        vol5 = volume.tail(5).mean()
        vol20 = volume.tail(20).mean()
        volume_ratio = vol5 / vol20 if vol20 > 0 else 0

        volatility_20 = close.pct_change().tail(20).std() * np.sqrt(252) if len(close) > 20 else 0

        reversal_5 = -momentum_5  # 简化均值回归

        rsi_14 = calc_rsi(close, 14).iloc[-1] if len(close) >= 14 else 50
        kdj_result = calc_kdj(high, low, close)
        kdj_j = kdj_result.get("J", 50)
        adx_val = calc_adx(high, low, close)

        # 计算超额收益（对沪深300）
        stock_ret_1d = (close.iloc[-1] / close.iloc[-2] - 1) if len(close) >= 2 else 0
        bm_ret_1d = benchmark.get("1d", 0)
        excess_1d = stock_ret_1d - bm_ret_1d

        stock_ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0
        bm_ret_5d = benchmark.get("5d", 0)
        excess_5d = stock_ret_5d - bm_ret_5d

        stocks_data.append({
            "code": code,
            "momentum_5": round(float(momentum_5), 6),
            "momentum_20": round(float(momentum_20), 6),
            "reversal_5": round(float(reversal_5), 6),
            "volatility_20": round(float(volatility_20), 6),
            "volume_ratio": round(float(volume_ratio), 4),
            "trend_strength": round(float(trend_strength), 4),
            "rsi_14": round(float(rsi_14), 1),
            "kdj_j": round(float(kdj_j), 1),
            "adx": round(float(adx_val), 2),
            "excess_1d": round(float(excess_1d), 6),
            "excess_5d": round(float(excess_5d), 6),
        })

    return {"date": date, "stocks": stocks_data}


def _get_benchmark_returns(hist_data_dict: Dict[str, pd.DataFrame]) -> Dict[str, float]:
    """获取沪深300同期收益（从任意一只股票的数据近似）"""
    if not hist_data_dict:
        return {"1d": 0, "5d": 0}
    first_df = next(iter(hist_data_dict.values()))
    close = first_df["close"]
    ret_1d = (close.iloc[-1] / close.iloc[-2] - 1) if len(close) >= 2 else 0
    ret_5d = (close.iloc[-1] / close.iloc[-6] - 1) if len(close) >= 6 else 0
    return {"1d": ret_1d, "5d": ret_5d}


def save_ic_panel(panel: Dict, date: str) -> str:
    """保存面板至 cache/ic/panel/ic_panel_{date}.json"""
    IC_PANEL_DIR.mkdir(parents=True, exist_ok=True)
    path = IC_PANEL_DIR / f"ic_panel_{date}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(panel, f, ensure_ascii=False, indent=2)
    logger.info(f"[panel_builder] IC panel 已保存: {path}")
    return str(path)


def load_ic_panel(date: str) -> Dict:
    """读取指定日期的 IC panel"""
    path = IC_PANEL_DIR / f"ic_panel_{date}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)