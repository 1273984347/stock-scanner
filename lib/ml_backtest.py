"""ML 回测验证 -- 比较纯 ML/纯规则/混合三种模式的多空收益"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
BACKTEST_DIR = Path(__file__).resolve().parent.parent / "cache" / "backtest_results"
DEFAULT_TC = 0.006


class MLBacktest:
    """ML 回测验证器"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.target_col = config.get("target_col", "excess_5d")
        self.top_pct = config.get("top_pct", 0.1)
        self.tc = config.get("transaction_cost", DEFAULT_TC)
        self.step = config.get("step", 5)

    def run(self, panel, ml_scores=None, rule_scores=None) -> Dict[str, Any]:
        if self.target_col not in panel.columns:
            raise KeyError(f"目标列 '{self.target_col}' 不在 panel 中")

        n = len(panel)
        scores: Dict[str, np.ndarray] = {}
        scores["ml_only"] = ml_scores.values if ml_scores is not None else np.full(n, np.nan)
        scores["rule_only"] = rule_scores.values if rule_scores is not None else np.full(n, np.nan)
        if ml_scores is not None and rule_scores is not None:
            scores["hybrid"] = 0.5 * ml_scores.values + 0.5 * rule_scores.values
        else:
            scores["hybrid"] = np.full(n, np.nan)

        dates = sorted(panel["date"].unique())
        excess = panel[self.target_col].values
        date_vals = panel["date"].values

        # excess_5d 是 5 日累计收益，跳步避免重叠
        sampled_dates = [d for i, d in enumerate(dates) if i % self.step == 0]

        daily_returns: Dict[str, list] = {
            "ml_only": [], "rule_only": [], "hybrid": [], "benchmark": [],
        }
        date_labels = []

        for d in sampled_dates:
            mask = date_vals == d
            idx = np.where(mask)[0]
            if len(idx) < 10:
                continue
            day_excess = excess[idx]
            date_labels.append(d)
            daily_returns["benchmark"].append(float(np.mean(day_excess)))
            for mode in ("ml_only", "rule_only", "hybrid"):
                s = scores[mode][idx]
                daily_returns[mode].append(self._long_short_return(day_excess, s))

        periods_per_year = 252.0 / self.step
        results = {}
        for mode in ("ml_only", "rule_only", "hybrid", "benchmark"):
            arr = np.array(daily_returns[mode])
            results[mode] = self._compute_metrics(arr, periods_per_year)

        report = self._generate_report(results, date_labels)
        return {"metrics": results, "report": report}

    def _long_short_return(self, excess_returns, scores) -> float:
        valid = ~(np.isnan(scores) | np.isnan(excess_returns))
        n_valid = int(valid.sum())
        if n_valid < 10:
            return 0.0
        ve = excess_returns[valid]
        vs = scores[valid]
        n_long = max(1, int(n_valid * self.top_pct))
        si = np.argsort(vs)
        short_ret = float(np.mean(ve[si[:n_long]]))
        long_ret = float(np.mean(ve[si[-n_long:]]))
        return (long_ret - short_ret) - self.tc

    @staticmethod
    def _compute_metrics(returns: np.ndarray, periods_per_year: float = 50.0) -> Dict[str, Any]:
        n = len(returns)
        if n == 0:
            return dict.fromkeys(
                ["total_return", "annual_return", "volatility",
                 "sharpe", "max_drawdown", "win_rate", "n_days"], 0.0)
        total_ret = float(np.prod(1.0 + returns) - 1.0)
        ann_ret = (1.0 + total_ret) ** (periods_per_year / n) - 1.0
        vol = float(np.std(returns, ddof=1)) * np.sqrt(periods_per_year)
        sharpe = ann_ret / vol if vol > 1e-10 else 0.0
        if np.isnan(total_ret): total_ret = 0.0
        if np.isnan(ann_ret): ann_ret = 0.0
        if np.isnan(vol): vol = 0.0
        if np.isnan(sharpe): sharpe = 0.0
        mdd = MLBacktest._max_drawdown(returns)
        win_rate = float(np.mean(returns > 0))
        return {"total_return": round(total_ret, 6), "annual_return": round(ann_ret, 6),
                "volatility": round(vol, 6), "sharpe": round(sharpe, 4),
                "max_drawdown": round(mdd, 6), "win_rate": round(win_rate, 4), "n_days": n}

    @staticmethod
    def _max_drawdown(returns):
        cum = np.cumprod(1.0 + returns)
        peak = np.maximum.accumulate(cum)
        dd = (peak - cum) / peak
        return float(np.max(dd)) if len(dd) > 0 else 0.0

    @staticmethod
    def _generate_report(results, date_labels) -> str:
        start = str(date_labels[0])[:10] if date_labels else "?"
        end = str(date_labels[-1])[:10] if date_labels else "?"
        lines = ["=" * 70, f"  ML Backtest Report",
                 f"  Period: {start} ~ {end}  Days: {len(date_labels)}", "=" * 70]
        for mode in ("ml_only", "rule_only", "hybrid", "benchmark"):
            m = results[mode]
            lines.append(f"\n--- {mode} ---")
            lines.append(f"  Total Return:     {m['total_return']:.4%}")
            lines.append(f"  Annual Return:    {m['annual_return']:.4%}")
            lines.append(f"  Volatility:       {m['volatility']:.4%}")
            lines.append(f"  Sharpe:           {m['sharpe']:.4f}")
            lines.append(f"  Max Drawdown:     {m['max_drawdown']:.4%}")
            lines.append(f"  Win Rate:         {m['win_rate']:.4%}")
            lines.append(f"  Trading Days:     {m['n_days']}")
        return "\n".join(lines)

    def save_report(self, results) -> str:
        out_dir = Path(BACKTEST_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"backtest_{ts}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"timestamp": ts, "metrics": results["metrics"], "report": results["report"]},
                      f, indent=2, ensure_ascii=False)
        logger.info(f"回测结果已保存: {path}")
        return str(path)
