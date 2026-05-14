"""
因子监控器 — IC tracking, decay detection, redundancy detection.
"""
import json, logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from config import MonitorConfig

logger = logging.getLogger(__name__)

_NON_FACTOR_COLS: set = {"code", "date", "excess_5d", "excess_10d", "excess_20d"}


def _is_factor_column(col: str) -> bool:
    """True when *col* is a user-defined factor (not metadata / return)."""
    if col in _NON_FACTOR_COLS or col.startswith("benchmark_ret_"):
        return False
    return True


class FactorMonitor:
    """Monitor factor health: IC tracking, decay detection, redundancy detection."""

    def __init__(self, config: Optional[MonitorConfig] = None):
        if config is None:
            config = MonitorConfig()
        self.ic_threshold = config.factor_ic_threshold       # default 0.02
        self.corr_threshold = config.factor_corr_threshold   # default 0.95

    def compute_ic(self, panel: pd.DataFrame) -> Dict[str, Dict]:
        """Spearman rank IC per factor cross-sectionally per date.

        Only dates with >= 10 stocks are considered.
        Returns {factor: {mean_ic, std_ic, ir, win_rate, n_dates, recent_ic}}.
        """
        panel = panel.copy()
        factor_cols = [c for c in panel.columns if _is_factor_column(c)]
        if not factor_cols or "date" not in panel or "excess_5d" not in panel:
            logger.warning("compute_ic: no factor columns or missing date/excess_5d")
            return {}

        raw: Dict[str, list] = {f: [] for f in factor_cols}
        for _date, grp in panel.groupby("date", sort=True):
            grp = grp.dropna(subset=["excess_5d"] + factor_cols)
            if len(grp) < 10:
                continue
            excess = grp["excess_5d"].values
            for factor in factor_cols:
                fv = grp[factor].values
                if np.nanstd(fv) == 0 or np.nanstd(excess) == 0:
                    continue
                try:
                    ic, _ = spearmanr(fv, excess)
                    if not np.isnan(ic):
                        raw[factor].append(ic)
                except Exception:
                    continue

        out: Dict[str, Dict] = {}
        for factor, ic_list in raw.items():
            if len(ic_list) < 2:
                continue
            arr = np.array(ic_list)
            mean_ic = float(np.mean(arr))
            std_ic = float(np.std(arr, ddof=1))
            out[factor] = {
                "mean_ic": mean_ic,
                "std_ic": std_ic,
                "ir": mean_ic / std_ic if std_ic > 0 else 0.0,
                "win_rate": float(np.mean(arr > 0)),
                "n_dates": len(ic_list),
                "recent_ic": [round(v, 6) for v in ic_list[-20:]],
            }
        return out

    def detect_redundancy(self, panel: pd.DataFrame) -> List[Tuple[str, str, float]]:
        """Factor pairs whose Spearman correlation exceeds *corr_threshold*."""
        factor_cols = [c for c in panel.columns if _is_factor_column(c)]
        if len(factor_cols) < 2:
            return []

        corr = panel[factor_cols].corr(method="spearman")
        pairs: List[Tuple[str, str, float]] = []
        for i, fa in enumerate(factor_cols):
            for fb in factor_cols[i + 1:]:
                v = corr.loc[fa, fb]
                if abs(v) > self.corr_threshold:
                    pairs.append((fa, fb, round(v, 4)))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        return pairs

    def flag_decayed(self, ic_results: Dict[str, Dict]) -> List[str]:
        """Factors where mean_ic < threshold AND win_rate < 0.5."""
        return [
            f for f, m in ic_results.items()
            if m.get("mean_ic", 0) < self.ic_threshold and m.get("win_rate", 0) < 0.5
        ]

    def log_health(
        self,
        ic_results: Dict[str, Dict],
        decayed: List[str],
        redundant: List[Tuple[str, str, float]],
    ) -> None:
        """Append a health snapshot to ``cache/factor_health_log.json``."""
        log_path = Path("cache/factor_health_log.json")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": pd.Timestamp.now().isoformat(),
            "n_factors": len(ic_results),
            "n_decayed": len(decayed),
            "n_redundant_pairs": len(redundant),
            "ic_summary": {
                f: {k: v for k, v in m.items() if k != "recent_ic"}
                for f, m in ic_results.items()
            },
            "decayed_factors": decayed,
            "redundant_pairs": [
                {"factor_a": a, "factor_b": b, "correlation": c}
                for a, b, c in redundant
            ],
        }

        existing = []
        if log_path.exists():
            try:
                with open(log_path, encoding="utf-8") as f:
                    existing = json.load(f)
                if not isinstance(existing, list):
                    existing = [existing]
            except Exception:
                existing = []
        existing.append(entry)

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        logger.info(
            "Factor health logged: %d factors, %d decayed, %d redundant pairs",
            len(ic_results), len(decayed), len(redundant),
        )
