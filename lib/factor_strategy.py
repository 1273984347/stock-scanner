"""因子策略引擎：中性化 + IC 衰减加权 + 线性合成"""
import numpy as np
import pandas as pd
from typing import Dict, List
from scipy.stats import spearmanr
import logging
logger = logging.getLogger(__name__)

FACTOR_META = {
    "momentum":    {"name": "动量",     "direction": 1,  "col": "momentum_20_rank"},
    "low_vol":     {"name": "低波",     "direction": -1, "col": "volatility_20_rank"},
    "capital_flow":{"name": "资金流",   "direction": 1,  "col": "volume_ratio_rank"},
}

class FactorStrategy:
    """因子线性合成策略，IC 衰减加权"""

    def __init__(self, half_life: int = 60, ic_window: int = 252):
        self.half_life = half_life
        self.ic_window = ic_window
        self.weights: Dict[str, float] = {}
        self.ic_history: Dict[str, List[float]] = {}

    def neutralize(self, panel: pd.DataFrame) -> pd.DataFrame:
        """行业 + 市值中性化"""
        df = panel.copy()
        _has_mcap = "log_market_cap" in df.columns and df["log_market_cap"].notna().sum() > 50

        for fname, meta in FACTOR_META.items():
            col = meta["col"]
            if col not in df.columns:
                continue
            raw = df[col]
            if _has_mcap:
                from sklearn.linear_model import LinearRegression
                valid = raw.notna() & df["log_market_cap"].notna()
                if valid.sum() > 30:
                    X = df.loc[valid, "log_market_cap"].values.reshape(-1, 1)
                    y = raw[valid].values
                    lr = LinearRegression().fit(X, y)
                    df[f"{fname}_neutral"] = y - lr.predict(X)
                else:
                    df[f"{fname}_neutral"] = raw
            else:
                df[f"{fname}_neutral"] = raw

            df[f"{fname}_rank"] = df.groupby("date")[f"{fname}_neutral"].rank(pct=True).fillna(0.5)
            if meta["direction"] == -1:
                df[f"{fname}_rank"] = 1 - df[f"{fname}_rank"]
        return df

    def compute_ic(self, panel: pd.DataFrame) -> Dict[str, List[float]]:
        """计算日度 Rank IC"""
        ic_data: Dict[str, List[float]] = {f: [] for f in FACTOR_META}
        for date in sorted(panel["date"].unique()):
            day = panel[panel["date"] == date].dropna(subset=["excess_5d"])
            if len(day) < 10:
                continue
            for fname in FACTOR_META:
                col = f"{fname}_rank"
                if col not in day.columns:
                    continue
                common = day[col].notna() & day["excess_5d"].notna()
                if common.sum() < 10:
                    continue
                ic, _ = spearmanr(day.loc[common, col], day.loc[common, "excess_5d"])
                ic_data[fname].append(float(ic) if not np.isnan(ic) else 0.0)
        self.ic_history = ic_data
        return ic_data

    def compute_weights(self) -> Dict[str, float]:
        """指数衰减加权 IC → 权重"""
        weights = {}
        for fname, ics in self.ic_history.items():
            if not ics:
                weights[fname] = 0.0
                continue
            ics = ics[-self.ic_window:]
            n = len(ics)
            if n < 10:
                weights[fname] = 0.0
                continue
            decay = np.exp(-np.arange(n) / self.half_life)
            decay = decay / decay.sum()
            avg_ic = np.average(ics, weights=decay)
            weights[fname] = max(0, avg_ic)
        total = sum(weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in weights.items()}
        else:
            self.weights = {k: 1.0 / len(FACTOR_META) for k in FACTOR_META}
        return self.weights

    def score(self, panel: pd.DataFrame) -> pd.Series:
        """输出综合得分"""
        df = self.neutralize(panel)
        self.compute_ic(df)
        self.compute_weights()
        logger.info(f"Weights: {self.weights}")
        score = pd.Series(0.0, index=df.index)
        for fname, w in self.weights.items():
            col = f"{fname}_rank"
            if col in df.columns and w > 0:
                score += df[col] * w
        return score


def run_backtest(path: str = "cache/panel_v4_final.parquet"):
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)

    panel = pd.read_parquet(path).dropna(subset=["excess_5d"])
    dates = sorted(panel["date"].unique())
    split = int(len(dates) * 0.5)
    train = panel[panel["date"].isin(set(dates[:split]))].copy()
    test = panel[panel["date"].isin(set(dates[split:]))].copy()

    fs = FactorStrategy()
    fs.score(train)
    logger.info(f"Trained weights: {fs.weights}")

    test_df = fs.neutralize(test)
    test_score = pd.Series(0.0, index=test.index)
    for fname, w in fs.weights.items():
        col = f"{fname}_rank"
        if col in test_df.columns and w > 0:
            test_score += test_df[col] * w

    from lib.ml_backtest import MLBacktest
    rule_scores = test["momentum_20_rank"].fillna(0.5) - 0.5
    bt = MLBacktest({"step": 5})
    result = bt.run(test, test_score, rule_scores)
    print(result.get("report", ""))
    return result

if __name__ == "__main__":
    run_backtest()
