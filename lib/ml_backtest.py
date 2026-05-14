"""ML 回测验证 -- 比较纯 ML/纯规则/混合三种模式的多空收益

在测试集面板上构建日频多空组合, 对比三种评分策略的表现差异。
依赖: numpy, pandas
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

BACKTEST_DIR = Path(__file__).resolve().parent.parent / "cache" / "backtest_results"
DEFAULT_TC = 0.006  # 0.3% per side, round trip = 0.6%


class MLBacktest:
    """ML 回测验证器

    Parameters
    ----------
    config : dict, optional
        target_col: 目标收益列, 默认 "excess_5d"
        top_pct: 多空分位数阈值, 默认 0.1
        transaction_cost: 交易成本, 默认 0.006
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        config = config or {}
        self.target_col = config.get("target_col", "excess_5d")
        self.top_pct = config.get("top_pct", 0.1)
        self.tc = config.get("transaction_cost", DEFAULT_TC)

    def run(
        self,
        panel: pd.DataFrame,
        ml_scores: Optional[pd.Series] = None,
        rule_scores: Optional[pd.Series] = None,
    ) -> Dict[str, Any]:
        """运行四模式回测 -- 逐日构建多空组合, 计算日收益序列与绩效指标"""
        if self.target_col not in panel.columns:
            raise KeyError(
                f"目标列 '{self.target_col}' 不在 panel 中。"
                f"可用列: {list(panel.columns)}"
            )

        n = len(panel)
        scores: Dict[str, np.ndarray] = {}

        scores["ml_only"] = (
            ml_scores.values if ml_scores is not None else np.full(n, np.nan)
        )
        scores["rule_only"] = (
            rule_scores.values if rule_scores is not None else np.full(n, np.nan)
        )

        if ml_scores is not None and rule_scores is not None:
            scores["hybrid"] = 0.5 * ml_scores.values + 0.5 * rule_scores.values
        else:
            scores["hybrid"] = np.full(n, np.nan)

        dates = sorted(panel["date"].unique())
        excess = panel[self.target_col].values
        date_vals = panel["date"].values

        daily_returns: Dict[str, list] = {
            "ml_only": [], "rule_only": [], "hybrid": [], "benchmark": [],
        }
        date_labels: list = []

        for d in dates:
            mask = date_vals == d
            idx = np.where(mask)[0]
            if len(idx) < 10:
                continue

            day_excess = excess[idx]
            date_labels.append(d)

            # Benchmark: 等权平均超额收益, 无交易成本
            daily_returns["benchmark"].append(float(np.mean(day_excess)))

            for mode in ("ml_only", "rule_only", "hybrid"):
                s = scores[mode][idx]
                daily_returns[mode].append(self._long_short_return(day_excess, s))

        results: Dict[str, Dict[str, Any]] = {}
        for mode in ("ml_only", "rule_only", "hybrid", "benchmark"):
            arr = np.array(daily_returns[mode])
            results[mode] = self._compute_metrics(arr)

        report = self._generate_report(results, date_labels)
        return {"metrics": results, "report": report}

    # ========== 内部方法 ==========

    def _long_short_return(
        self, excess_returns: np.ndarray, scores: np.ndarray
    ) -> float:
        """单日多空收益: Top P%做多, Bottom P%做空, 等权, 扣除交易成本"""
        valid = ~(np.isnan(scores) | np.isnan(excess_returns))
        n_valid = int(valid.sum())
        if n_valid < 10:
            return 0.0

        valid_excess = excess_returns[valid]
        valid_scores = scores[valid]
        n_long = max(1, int(n_valid * self.top_pct))

        sorted_idx = np.argsort(valid_scores)
        short_ret = float(np.mean(valid_excess[sorted_idx[:n_long]]))
        long_ret = float(np.mean(valid_excess[sorted_idx[-n_long:]]))

        return (long_ret - short_ret) - self.tc

    @staticmethod
    def _compute_metrics(returns: np.ndarray) -> Dict[str, Any]:
        """计算绩效指标: total_return / annual_return / vol / sharpe / mdd / win_rate"""
        n = len(returns)
        if n == 0:
            return dict.fromkeys(
                ["total_return", "annual_return", "volatility",
                 "sharpe", "max_drawdown", "win_rate", "n_days"], 0.0
            )

        total_ret = float(np.prod(1.0 + returns) - 1.0)
        ann_ret = (1.0 + total_ret) ** (252.0 / n) - 1.0
        vol = float(np.std(returns, ddof=1)) * np.sqrt(252.0)
        sharpe = ann_ret / vol if vol > 1e-10 else 0.0
        mdd = MLBacktest._max_drawdown(returns)
        win_rate = float(np.mean(returns > 0))

        return {
            "total_return": round(total_ret, 6),
            "annual_return": round(ann_ret, 6),
            "volatility": round(vol, 6),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(mdd, 6),
            "win_rate": round(win_rate, 4),
            "n_days": n,
        }

    @staticmethod
    def _max_drawdown(returns: np.ndarray) -> float:
        """峰值到谷值的最大回撤 (正值, 如 0.25 = 25%)"""
        cum = np.cumprod(1.0 + returns)
        peak = np.maximum.accumulate(cum)
        dd = (peak - cum) / peak
        return float(np.max(dd)) if len(dd) > 0 else 0.0

    @staticmethod
    def _generate_report(
        results: Dict[str, dict], date_labels: list,
    ) -> str:
        """生成文本回测报告"""
        start = str(date_labels[0])[:10] if date_labels else "?"
        end = str(date_labels[-1])[:10] if date_labels else "?"

        lines = [
            "=" * 70,
            "  ML Backtest Report",
            f"  Period: {start} ~ {end}  Days: {len(date_labels)}",
            "=" * 70,
        ]
        for mode in ("ml_only", "rule_only", "hybrid", "benchmark"):
            m = results[mode]
            lines.append("")
            lines.append(f"--- {mode} ---")
            lines.append(f"  Total Return:     {m['total_return']:.4%}")
            lines.append(f"  Annual Return:    {m['annual_return']:.4%}")
            lines.append(f"  Volatility:       {m['volatility']:.4%}")
            lines.append(f"  Sharpe:           {m['sharpe']:.4f}")
            lines.append(f"  Max Drawdown:     {m['max_drawdown']:.4%}")
            lines.append(f"  Win Rate:         {m['win_rate']:.4%}")
            lines.append(f"  Trading Days:     {m['n_days']}")
        lines.append("")
        return "\n".join(lines)

    # ========== 持久化 ==========

    def save_report(self, results: Dict[str, Any]) -> str:
        """保存回测结果到 JSON 文件, 返回文件路径"""
        out_dir = Path(BACKTEST_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"backtest_{ts}.json"

        payload = {
            "timestamp": ts,
            "metrics": results["metrics"],
            "report": results["report"],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        logger.info(f"回测结果已保存: {path}")
        return str(path)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bt = MLBacktest()
    print("MLBacktest 导入成功")
    print(f"  target_col={bt.target_col}  top_pct={bt.top_pct}  tc={bt.tc}")
