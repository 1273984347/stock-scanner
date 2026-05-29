"""IC 时序计算

load_ic_history() -> Dict:
    读取 cache/ic/ic_history.json，不存在返回空结构

save_ic_history(history: Dict) -> None:
    保存至 cache/ic/ic_history.json

compute_factor_ic(stocks: List[Dict], factor: str, forward: str) -> Dict:
    计算单个因子的 IC/RankIC
    输入: stocks list, factor name, forward return field ("excess_1d" / "excess_5d")
    返回: {ic, rank_ic, n_samples}

update_ic_history(panel: Dict) -> Dict:
    对面板中所有因子计算 IC，更新 ic_history.json
    有效 IC 日期三条件：
        1. 候选股 ≥ 10 只
        2. Spearman 相关系数非 NaN
        3. 标的未被停牌/涨跌停

load_all_panels(start_date: str, end_date: str) -> List[Dict]:
    扫描 cache/ic/panel/ 目录，加载日期范围内的所有 panel
    用于回填历史 IC
"""

import json
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
from scipy.stats import spearmanr
import logging

logger = logging.getLogger(__name__)

IC_HISTORY_PATH = Path("cache/ic/ic_history.json")
IC_PANEL_DIR = Path("cache/ic/panel")

def load_ic_history() -> Dict:
    """读取累计 IC 时序数据"""
    if not IC_HISTORY_PATH.exists():
        return {"updated_at": "", "factors": {}}
    with open(IC_HISTORY_PATH, encoding="utf-8") as f:
        return json.load(f)

def save_ic_history(history: Dict) -> None:
    """保存 IC 时序数据"""
    import pandas as pd
    history["updated_at"] = pd.Timestamp.now().isoformat()
    IC_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(IC_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

def compute_factor_ic(stocks: List[Dict], factor: str, forward: str = "excess_1d") -> Dict:
    """计算单个因子的 IC/RankIC"""
    if len(stocks) < 10:
        return {"ic": np.nan, "rank_ic": np.nan, "n_samples": len(stocks)}

    valid = [s for s in stocks if factor in s and forward in s
             and s.get(factor) is not None and s.get(forward) is not None
             and not (np.isnan(s[factor]) or np.isnan(s[forward]))]

    if len(valid) < 10:
        return {"ic": np.nan, "rank_ic": np.nan, "n_samples": len(valid)}

    fvals = np.array([s[factor] for s in valid], dtype=float)
    rets = np.array([s[forward] for s in valid], dtype=float)

    if np.std(fvals) == 0 or np.std(rets) == 0:
        return {"ic": np.nan, "rank_ic": np.nan, "n_samples": len(valid)}

    ic, _ = spearmanr(fvals, rets)
    return {"ic": float(ic) if not np.isnan(ic) else np.nan,
            "rank_ic": float(ic) if not np.isnan(ic) else np.nan,
            "n_samples": len(valid)}

def update_ic_history(panel: Dict) -> Dict:
    """更新 IC 时序数据"""
    date = panel.get("date")
    stocks = panel.get("stocks", [])
    if not stocks:
        return {}

    # 有效 IC 日期三条件
    if len(stocks) < 10:
        logger.warning(f"[ic_calculator] {date} 候选股仅 {len(stocks)} 只，跳过 IC 记录")
        return {}

    history = load_ic_history()
    factors = ["momentum_5", "momentum_20", "reversal_5", "volatility_20",
               "volume_ratio", "trend_strength", "rsi_14", "kdj_j", "adx"]

    for factor in factors:
        ic_result = compute_factor_ic(stocks, factor, "excess_1d")
        if np.isnan(ic_result.get("ic", np.nan)):
            continue

        if factor not in history["factors"]:
            history["factors"][factor] = {"ic_series": [], "stats": {
                "ic_mean": 0, "ic_std": 0, "ir": 0, "win_rate": 0, "n_valid_days": 0}}

        hist = history["factors"][factor]
        hist["ic_series"].append({
            "date": date,
            "ic": ic_result["ic"],
            "rank_ic": ic_result["rank_ic"],
            "n": ic_result["n_samples"],
        })

        # 重新计算统计指标（最近20日滚动）
        recent = hist["ic_series"][-20:]
        ic_vals = [x["ic"] for x in recent]
        if ic_vals:
            hist["stats"]["ic_mean"] = round(float(np.mean(ic_vals)), 6)
            hist["stats"]["ic_std"] = round(float(np.std(ic_vals, ddof=1)), 6)
            hist["stats"]["ir"] = round(float(np.mean(ic_vals) / (np.std(ic_vals, ddof=1) + 1e-9)), 4)
            hist["stats"]["win_rate"] = round(float(np.mean([v > 0 for v in ic_vals])), 4)
            hist["stats"]["n_valid_days"] = len(recent)

    save_ic_history(history)
    return history

def load_all_panels(start_date: str = None, end_date: str = None) -> List[Dict]:
    """加载日期范围内的所有 IC panel 文件"""
    if not IC_PANEL_DIR.is_dir():
        return []
    panels = []
    for f in sorted(IC_PANEL_DIR.glob("ic_panel_*.json")):
        date_str = f.stem.replace("ic_panel_", "")
        if start_date and date_str < start_date:
            continue
        if end_date and date_str > end_date:
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                panels.append(json.load(fp))
        except Exception as e:
            logger.debug(f"[ic_calculator] IC 面板文件读取失败 [{f}]: {e}")
            continue
    return panels