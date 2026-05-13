"""
Alpha 因子合成 —— 多因子加权合成 + IC 分析 + 因子评估
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from scipy import stats
import logging

logger = logging.getLogger(__name__)


class AlphaSynthesizer:
    """多因子 Alpha 合成器"""

    def __init__(self, weights: Dict[str, float] = None):
        self.weights = weights or {}

    def synthesize(self, factor_df: pd.DataFrame,
                   weights: Dict[str, float] = None) -> pd.Series:
        """加权合成 Alpha 得分"""
        w = weights or self.weights
        if factor_df.empty:
            return pd.Series(dtype=float)

        available = [c for c in w if c in factor_df.columns]
        if not available:
            logger.warning("没有可用的因子列")
            return pd.Series(dtype=float)

        total_weight = sum(w[c] for c in available)
        if total_weight == 0:
            return pd.Series(0, index=factor_df.index)

        alpha = pd.Series(0.0, index=factor_df.index)
        for col in available:
            alpha += factor_df[col].fillna(0) * (w[col] / total_weight)

        alpha.name = "alpha_score"
        return alpha

    @staticmethod
    def compute_ic(factor_values: pd.Series, forward_returns: pd.Series) -> dict:
        """计算因子 IC（信息系数）

        factor_values 和 forward_returns 的 index 必须对齐：
        factor_values[t] 对应 forward_returns[t]（t 时刻因子值预测 t→t+N 收益）
        """
        if not factor_values.index.equals(forward_returns.index):
            common = factor_values.index.intersection(forward_returns.index)
            logger.warning(
                f"factor_values 与 forward_returns index 不一致，"
                f"对齐后剩余 {len(common)} 个样本"
            )
            factor_values = factor_values.loc[common]
            forward_returns = forward_returns.loc[common]

        valid = factor_values.notna() & forward_returns.notna()
        if valid.sum() < 10:
            return {"ic": np.nan, "rank_ic": np.nan, "ic_ir": np.nan}

        ic, ic_pvalue = stats.pearsonr(
            factor_values[valid], forward_returns[valid]
        )
        rank_ic, rank_pvalue = stats.spearmanr(
            factor_values[valid], forward_returns[valid]
        )

        return {
            "ic": ic,
            "ic_pvalue": ic_pvalue,
            "rank_ic": rank_ic,
            "rank_ic_pvalue": rank_pvalue,
            "n_samples": valid.sum(),
        }

    @staticmethod
    def compute_ic_series(factor_df: pd.DataFrame,
                          forward_returns: pd.Series) -> pd.DataFrame:
        """计算因子 IC 序列（用于时序分析）"""
        records = []
        for col in factor_df.columns:
            ic_info = AlphaSynthesizer.compute_ic(
                factor_df[col], forward_returns
            )
            ic_info["factor"] = col
            records.append(ic_info)
        return pd.DataFrame(records)

    @staticmethod
    def layer_backtest(factor_values: pd.Series, forward_returns: pd.Series,
                       n_groups: int = 5) -> pd.DataFrame:
        """分层回测：按因子值分组，看各组未来收益"""
        valid = factor_values.notna() & forward_returns.notna()
        fv = factor_values[valid]
        fr = forward_returns[valid]

        if len(fv) < n_groups * 3:
            return pd.DataFrame()

        fv_rank = fv.rank(pct=True)
        labels = [f"Q{i+1}" for i in range(n_groups)]
        groups = pd.cut(fv_rank, bins=n_groups, labels=labels)

        result = fr.groupby(groups).agg(["mean", "std", "count"])
        result.columns = ["avg_return", "std_return", "count"]
        return result

    @staticmethod
    def optimize_weights_ic_ir(factor_df: pd.DataFrame,
                               forward_returns: pd.Series) -> Dict[str, float]:
        """基于 IC_IR 优化因子权重"""
        ic_df = AlphaSynthesizer.compute_ic_series(factor_df, forward_returns)
        ic_df = ic_df.dropna(subset=["rank_ic"])

        if ic_df.empty:
            return {}

        ic_df["abs_rank_ic"] = ic_df["rank_ic"].abs()
        total = ic_df["abs_rank_ic"].sum()
        if total == 0:
            return {}

        weights = {}
        for _, row in ic_df.iterrows():
            weights[row["factor"]] = row["abs_rank_ic"] / total

        return weights

    @staticmethod
    def normalize_cross_section(factor_df: pd.DataFrame,
                                date_col: str = "trade_date") -> pd.DataFrame:
        """截面标准化：每个交易日对因子做 z-score 标准化

        Args:
            factor_df: index=code, columns=factors, 或包含 date_col 的 DataFrame
            date_col: 日期列名（如果 factor_df 包含日期列）

        Returns:
            标准化后的 DataFrame，同结构
        """
        if factor_df.empty:
            return factor_df

        df = factor_df.copy()
        factor_cols = [c for c in df.columns if c != date_col and df[c].dtype in ('float64', 'float32', 'int64', 'int32')]

        if date_col in df.columns:
            result = df.copy()
            for date, group in df.groupby(date_col):
                for col in factor_cols:
                    vals = group[col].dropna()
                    if len(vals) < 3:
                        continue
                    z = (group[col] - vals.mean()) / vals.std()
                    result.loc[group.index, col] = z
            return result
        else:
            for col in factor_cols:
                vals = df[col].dropna()
                if len(vals) < 3:
                    continue
                df[col] = (df[col] - vals.mean()) / vals.std()
            return df

    @staticmethod
    def winsorize(factor_df: pd.DataFrame, limits: tuple = (0.01, 0.01),
                  date_col: str = "trade_date") -> pd.DataFrame:
        """因子去极值：截面 Winsorize

        Args:
            factor_df: 因子 DataFrame
            limits: (下分位, 上分位) 如 (0.01, 0.01) 表示 1%-99%
            date_col: 日期列名
        """
        if factor_df.empty:
            return factor_df

        df = factor_df.copy()
        factor_cols = [c for c in df.columns if c != date_col and df[c].dtype in ('float64', 'float32', 'int64', 'int32')]

        if date_col in df.columns:
            for date, group in df.groupby(date_col):
                for col in factor_cols:
                    vals = group[col].dropna()
                    if len(vals) < 10:
                        continue
                    lo = vals.quantile(limits[0])
                    hi = vals.quantile(1 - limits[1])
                    mask = group.index.isin(vals.index)
                    df.loc[group.index[mask], col] = group.loc[mask, col].clip(lo, hi)
        else:
            for col in factor_cols:
                vals = df[col].dropna()
                if len(vals) < 10:
                    continue
                lo = vals.quantile(limits[0])
                hi = vals.quantile(1 - limits[1])
                df[col] = df[col].clip(lo, hi)

        return df

    @staticmethod
    def compute_rolling_ic_weights(
        factor_panel: Dict[str, pd.DataFrame],
        forward_returns: pd.Series,
        window: int = 60,
        min_periods: int = 20,
    ) -> Dict[str, float]:
        """基于滚动 IC 计算动态因子权重

        对每个因子计算过去 window 期的滚动 Rank IC，
        取均值作为该因子的预期 IC，按 |IC| 归一化得到权重。

        Args:
            factor_panel: {factor_name: Series(index=date, values=ic)}
                          或 {factor_name: DataFrame} 包含多期因子值
            forward_returns: 前向收益 Series(index=date)
            window: 滚动窗口大小
            min_periods: 最小样本数

        Returns:
            {factor_name: weight}
        """
        ic_means = {}
        for name, factor_series in factor_panel.items():
            if isinstance(factor_series, pd.DataFrame):
                factor_series = factor_series.iloc[:, 0] if factor_series.shape[1] > 0 else pd.Series(dtype=float)

            if factor_series.empty:
                continue

            common_idx = factor_series.index.intersection(forward_returns.index)
            if len(common_idx) < min_periods:
                continue

            fv = factor_series.loc[common_idx]
            fr = forward_returns.loc[common_idx]

            rolling_ic = fv.rolling(window, min_periods=min_periods).corr(fr)
            ic_means[name] = rolling_ic.abs().mean()

        if not ic_means:
            return {}

        total = sum(ic_means.values())
        if total == 0:
            return {}

        return {k: v / total for k, v in ic_means.items()}

    @staticmethod
    def compute_ic_decay_weights(
        factor_panel: Dict[str, pd.DataFrame],
        forward_returns: pd.Series,
        window: int = 120,
        half_life: int = 40,
        min_periods: int = 20,
    ) -> Dict[str, float]:
        """基于 IC 衰减加权计算因子权重

        越近期的 IC 权重越大（指数衰减），更能反映因子近期有效性。

        Args:
            factor_panel: {factor_name: Series(index=date)}
            forward_returns: 前向收益
            window: 回看窗口
            half_life: IC 半衰期（期数）
            min_periods: 最小样本数

        Returns:
            {factor_name: weight}
        """
        decay = np.exp(-np.log(2) / half_life)
        decay_weights = np.array([decay ** i for i in range(window)][::-1])
        decay_weights = decay_weights / decay_weights.sum()

        ic_weighted = {}
        for name, factor_series in factor_panel.items():
            if isinstance(factor_series, pd.DataFrame):
                factor_series = factor_series.iloc[:, 0] if factor_series.shape[1] > 0 else pd.Series(dtype=float)

            if factor_series.empty:
                continue

            common_idx = factor_series.index.intersection(forward_returns.index)
            if len(common_idx) < min_periods:
                continue

            fv = factor_series.loc[common_idx]
            fr = forward_returns.loc[common_idx]

            rolling_ic = fv.rolling(window, min_periods=min_periods).corr(fr)
            rolling_ic = rolling_ic.dropna()

            if len(rolling_ic) == 0:
                continue

            n = min(len(rolling_ic), window)
            w = decay_weights[-n:] if n <= len(decay_weights) else np.ones(n) / n
            w = w / w.sum()

            ic_weighted[name] = abs((rolling_ic.iloc[-n:] * w).sum())

        if not ic_weighted:
            return {}

        total = sum(ic_weighted.values())
        if total == 0:
            return {}

        return {k: v / total for k, v in ic_weighted.items()}

    @staticmethod
    def neutralize_sector(factor_scores: pd.Series,
                          industry_map: Dict[str, str]) -> pd.Series:
        """行业中性化：减去行业均值

        Args:
            factor_scores: index=code, values=score
            industry_map: {code: industry}

        Returns:
            行业中性化后的得分
        """
        if factor_scores.empty or not industry_map:
            return factor_scores

        df = pd.DataFrame({
            "score": factor_scores,
            "industry": pd.Series(industry_map).reindex(factor_scores.index),
        }).dropna()

        if df.empty:
            return factor_scores

        industry_mean = df.groupby("industry")["score"].transform("mean")
        df["score_neutral"] = df["score"] - industry_mean

        result = factor_scores.copy()
        result.loc[df.index] = df["score_neutral"]
        return result

    @staticmethod
    def compute_icir_decay_weights(
        factor_panel: Dict[str, pd.Series],
        forward_returns: pd.Series,
        lookback: int = 12,
        half_life: int = 6,
        min_periods: int = 6,
    ) -> Dict[str, float]:
        """基于过去 N 期 ICIR 的指数衰减加权

        对每个因子：
        1. 计算过去 lookback 期的滚动 Rank IC
        2. 计算 ICIR = mean(IC) / std(IC)
        3. 对 ICIR 做指数衰减加权（越近期权重越大）
        4. 按 |ICIR_decay| 归一化得到最终权重

        Args:
            factor_panel: {factor_name: Series(index=date, values=factor_value)}
            forward_returns: 前向收益 Series(index=date)
            lookback: 回看期数（默认 12 期）
            half_life: ICIR 半衰期（默认 6 期）
            min_periods: 最小有效样本数

        Returns:
            {factor_name: weight} 权重和为 1
        """
        decay_factor = np.exp(-np.log(2) / half_life)
        decay_weights = np.array([decay_factor ** i for i in range(lookback)][::-1])
        decay_weights = decay_weights / decay_weights.sum()

        icir_weighted = {}

        for name, factor_series in factor_panel.items():
            if isinstance(factor_series, pd.DataFrame):
                factor_series = factor_series.iloc[:, 0] if factor_series.shape[1] > 0 else pd.Series(dtype=float)

            if factor_series.empty:
                continue

            common_idx = factor_series.index.intersection(forward_returns.index)
            if len(common_idx) < min_periods:
                continue

            fv = factor_series.loc[common_idx].sort_index()
            fr = forward_returns.loc[common_idx].sort_index()

            rolling_ic = fv.rolling(lookback, min_periods=min_periods).corr(fr)
            rolling_ic = rolling_ic.dropna()

            if len(rolling_ic) < min_periods:
                continue

            ic_mean = rolling_ic.mean()
            ic_std = rolling_ic.std()
            if ic_std == 0 or pd.isna(ic_std):
                continue

            icir = ic_mean / ic_std

            n = min(len(rolling_ic), lookback)
            w = decay_weights[-n:] if n <= len(decay_weights) else np.ones(n) / n
            w = w / w.sum()

            icir_decay = abs((rolling_ic.iloc[-n:] * w).sum()) / (rolling_ic.iloc[-n:].std() + 1e-10)
            icir_weighted[name] = icir_decay

        if not icir_weighted:
            return {}

        total = sum(icir_weighted.values())
        if total == 0:
            return {}

        return {k: v / total for k, v in icir_weighted.items()}

    @staticmethod
    def compute_icir_rolling(
        factor_values: pd.Series,
        forward_returns: pd.Series,
        lookback: int = 12,
        min_periods: int = 6,
    ) -> float:
        """计算单个因子的滚动 ICIR

        ICIR = mean(Rank IC over lookback) / std(Rank IC over lookback)

        Args:
            factor_values: 因子值 Series(index=date)
            forward_returns: 前向收益 Series(index=date)
            lookback: 回看期数
            min_periods: 最小有效样本数

        Returns:
            ICIR 值
        """
        common_idx = factor_values.index.intersection(forward_returns.index)
        if len(common_idx) < min_periods:
            return 0.0

        fv = factor_values.loc[common_idx].sort_index()
        fr = forward_returns.loc[common_idx].sort_index()

        rolling_ic = fv.rolling(lookback, min_periods=min_periods).corr(fr)
        rolling_ic = rolling_ic.dropna()

        if len(rolling_ic) < min_periods:
            return 0.0

        ic_mean = rolling_ic.mean()
        ic_std = rolling_ic.std()
        if ic_std == 0 or pd.isna(ic_std):
            return 0.0

        return float(ic_mean / ic_std)