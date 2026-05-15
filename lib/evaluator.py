"""
因子评价体系 —— IC时序分析、分层回测、Alpha衰减、相关性矩阵、绩效报告

参考项目: alphalens, FactorZoo
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from scipy import stats
from scipy.cluster import hierarchy
import logging
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

logger = logging.getLogger(__name__)


@dataclass
class ICRecord:
    """单期 IC 记录"""
    date: str
    factor: str
    ic: float
    rank_ic: float
    ic_pvalue: float
    rank_ic_pvalue: float
    n_samples: int


@dataclass
class FactorScorecard:
    """因子综合评分卡"""
    factor: str
    rank_ic_mean: float = 0.0
    rank_ic_std: float = 0.0
    ic_ir: float = 0.0
    ic_win_rate: float = 0.0
    ic_t_stat: float = 0.0
    long_short_spread: float = 0.0
    monotonicity: float = 0.0
    max_drawdown_ic: float = 0.0
    turnover: float = 0.0
    composite_score: float = 0.0


@dataclass
class EvalConfig:
    """因子评价配置"""
    ic_periods: List[int] = field(default_factory=lambda: [1, 5, 10, 20])
    decay_periods: List[int] = field(default_factory=lambda: [1, 3, 5, 10, 20, 30, 60])
    n_quantiles: int = 5
    min_samples_per_group: int = 10
    correlation_method: str = "spearman"
    redundancy_threshold: float = 0.70
    rolling_window: int = 252


class FactorEvaluator:
    """因子评价器 —— 全面评估因子质量"""

    def __init__(self, config: EvalConfig = None):
        self.config = config or EvalConfig()

    def compute_ic(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series
    ) -> dict:
        """计算因子 IC（信息系数）

        Args:
            factor_values: 因子值 Series
            forward_returns: 未来收益 Series

        Returns:
            dict with ic, rank_ic, ic_pvalue, rank_ic_pvalue, n_samples
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
            return {"ic": np.nan, "rank_ic": np.nan, "ic_ir": np.nan,
                    "ic_pvalue": np.nan, "rank_ic_pvalue": np.nan,
                    "n_samples": valid.sum()}

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
            "n_samples": int(valid.sum()),
        }

    def compute_rolling_ic(
        self,
        factor_df: pd.DataFrame,
        forward_returns: pd.Series,
        window: int = None
    ) -> pd.DataFrame:
        """计算滚动 IC 序列

        Args:
            factor_df: 因子值 DataFrame, index=date, columns=factor_names
            forward_returns: 未来收益 Series, index=date
            window: 滚动窗口（交易日），默认使用 config 中的值

        Returns:
            DataFrame with columns: date, factor, rank_ic
        """
        window = window or self.config.rolling_window
        if factor_df.empty or forward_returns.empty:
            return pd.DataFrame()

        common_idx = factor_df.index.intersection(forward_returns.index)
        if len(common_idx) < window:
            logger.warning(f"共同样本不足: {len(common_idx)} < {window}")
            return pd.DataFrame()

        if not factor_df.index.equals(forward_returns.index):
            n_missing = len(factor_df.index) - len(common_idx)
            logger.warning(
                f"factor_df 与 forward_returns 的 index 不完全一致，"
                f"将对齐处理（缺失 {n_missing} 个日期）"
            )

        factor_df = factor_df.loc[common_idx]
        forward_returns = forward_returns.loc[common_idx]

        records = []
        for i in range(window - 1, len(common_idx)):
            date = common_idx[i]
            start_idx = i - window + 1
            window_factors = factor_df.iloc[start_idx:i + 1]
            window_returns = forward_returns.iloc[start_idx:i + 1]

            for col in factor_df.columns:
                valid = window_factors[col].notna() & window_returns.notna()
                if valid.sum() < self.config.min_samples_per_group:
                    continue
                try:
                    rank_ic, _ = stats.spearmanr(
                        window_factors[col][valid], window_returns[valid]
                    )
                    records.append({
                        "date": date,
                        "factor": col,
                        "rank_ic": rank_ic,
                    })
                except Exception as e:
                    logger.debug(f"IC 计算失败 factor={col} date={date}: {e}")
                    continue

        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def compute_ic_summary(
        self,
        factor_df: pd.DataFrame,
        forward_returns: pd.Series
    ) -> pd.DataFrame:
        """计算因子 IC 汇总统计

        Returns:
            DataFrame with columns: factor, rank_ic_mean, rank_ic_std,
            ic_ir, ic_win_rate, ic_t_stat, max_drawdown_ic
        """
        rolling_ic = self.compute_rolling_ic(factor_df, forward_returns)
        if rolling_ic.empty:
            return pd.DataFrame()

        summary = []
        for factor in rolling_ic["factor"].unique():
            fac_ic = rolling_ic[rolling_ic["factor"] == factor]["rank_ic"].dropna()
            if len(fac_ic) < 10:
                continue

            ic_mean = fac_ic.mean()
            ic_std = fac_ic.std()
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0
            ic_win_rate = (fac_ic > 0).mean()
            ic_t_stat = ic_mean / (ic_std / np.sqrt(len(fac_ic))) if ic_std > 0 else 0

            cumulative_ic = fac_ic.cumsum()
            rolling_max_ic = cumulative_ic.cummax()
            drawdown_ic = cumulative_ic - rolling_max_ic
            max_dd_ic = drawdown_ic.min()

            summary.append({
                "factor": factor,
                "rank_ic_mean": ic_mean,
                "rank_ic_std": ic_std,
                "ic_ir": ic_ir,
                "ic_win_rate": ic_win_rate,
                "ic_t_stat": ic_t_stat,
                "max_drawdown_ic": max_dd_ic,
                "n_periods": len(fac_ic),
            })

        return pd.DataFrame(summary).sort_values("ic_ir", ascending=False)

    def compute_ic_decay(
        self,
        factor_values: pd.Series,
        returns_matrix: pd.DataFrame
    ) -> pd.DataFrame:
        """计算因子 IC 衰减曲线

        Args:
            factor_values: 因子值 Series, index=date
            returns_matrix: 多期未来收益矩阵, columns=[1d, 3d, 5d, 10d, 20d, ...]

        Returns:
            DataFrame with columns: period, rank_ic_mean, rank_ic_std
        """
        if factor_values.empty or returns_matrix.empty:
            return pd.DataFrame()

        common_idx = factor_values.index.intersection(returns_matrix.index)
        if len(common_idx) < self.config.min_samples_per_group:
            return pd.DataFrame()

        factor_values = factor_values.loc[common_idx]
        returns_matrix = returns_matrix.loc[common_idx]

        records = []
        for col in returns_matrix.columns:
            valid = factor_values.notna() & returns_matrix[col].notna()
            if valid.sum() < self.config.min_samples_per_group:
                continue
            try:
                rank_ic, _ = stats.spearmanr(
                    factor_values[valid], returns_matrix[col][valid]
                )
                records.append({
                    "period": col,
                    "rank_ic": rank_ic,
                    "n_samples": valid.sum(),
                })
            except Exception as e:
                logger.debug(f"IC 汇总计算失败 period={col}: {e}")
                continue

        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def compute_decay_curves(
        self,
        factor_df: pd.DataFrame,
        returns_matrix: pd.DataFrame
    ) -> pd.DataFrame:
        """计算所有因子的 IC 衰减曲线

        Returns:
            DataFrame with columns: factor, period, rank_ic
        """
        all_records = []
        for col in factor_df.columns:
            decay = self.compute_ic_decay(factor_df[col], returns_matrix)
            if not decay.empty:
                decay["factor"] = col
                all_records.append(decay)

        if not all_records:
            return pd.DataFrame()
        return pd.concat(all_records, ignore_index=True)

    def layer_backtest_enhanced(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        n_groups: int = None
    ) -> pd.DataFrame:
        """增强分层回测：多空收益 + 单调性检验

        Returns:
            DataFrame with columns: group, avg_return, std_return, count,
            cumulative_return, hit_rate
        """
        n_groups = n_groups or self.config.n_quantiles
        valid = factor_values.notna() & forward_returns.notna()
        fv = factor_values[valid]
        fr = forward_returns[valid]

        if len(fv) < n_groups * self.config.min_samples_per_group:
            return pd.DataFrame()

        fv_rank = fv.rank(pct=True)
        labels = [f"Q{i+1}" for i in range(n_groups)]
        groups = pd.cut(fv_rank, bins=n_groups, labels=labels)

        result = fr.groupby(groups, observed=False).agg([
            ("avg_return", "mean"),
            ("std_return", "std"),
            ("count", "count"),
        ])
        result.columns = ["avg_return", "std_return", "count"]
        hit_rate = fr.groupby(groups, observed=False).apply(
            lambda x: (x > 0).mean()
        )
        result["hit_rate"] = hit_rate

        if len(result) >= 2:
            result["cumulative_return"] = (1 + result["avg_return"]).cumprod() - 1

        return result

    def compute_long_short_spread(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        n_groups: int = None
    ) -> float:
        """计算多空组合收益差（Top - Bottom）"""
        n_groups = n_groups or self.config.n_quantiles
        layer_result = self.layer_backtest_enhanced(
            factor_values, forward_returns, n_groups
        )
        if layer_result.empty or len(layer_result) < 2:
            return 0.0

        top_return = layer_result["avg_return"].iloc[-1]
        bottom_return = layer_result["avg_return"].iloc[0]
        return top_return - bottom_return

    def compute_monotonicity(
        self,
        factor_values: pd.Series,
        forward_returns: pd.Series,
        n_groups: int = None
    ) -> float:
        """计算分层收益单调性（Spearman 相关）

        Returns:
            单调性系数，越接近 1 表示分层收益越单调
        """
        n_groups = n_groups or self.config.n_quantiles
        layer_result = self.layer_backtest_enhanced(
            factor_values, forward_returns, n_groups
        )
        if layer_result.empty or len(layer_result) < 3:
            return 0.0

        group_ranks = np.arange(1, len(layer_result) + 1)
        avg_returns = layer_result["avg_return"].values
        mono, _ = stats.spearmanr(group_ranks, avg_returns)
        return mono if not np.isnan(mono) else 0.0

    def compute_factor_correlation(
        self,
        factor_df: pd.DataFrame,
        method: str = None
    ) -> pd.DataFrame:
        """计算因子相关性矩阵

        Args:
            factor_df: 因子值 DataFrame, columns=factor_names
            method: 'spearman' or 'pearson'

        Returns:
            相关性矩阵 DataFrame
        """
        method = method or self.config.correlation_method
        if factor_df.empty or factor_df.shape[1] < 2:
            return pd.DataFrame()

        clean_df = factor_df.dropna(axis=1, how="all")
        if method == "spearman":
            corr = clean_df.corr(method="spearman")
        else:
            corr = clean_df.corr(method="pearson")

        return corr

    def find_redundant_factors(
        self,
        factor_df: pd.DataFrame,
        threshold: float = None
    ) -> List[Tuple[str, str, float]]:
        """检测冗余因子对（相关性超过阈值）

        Returns:
            List of (factor_a, factor_b, correlation) tuples
        """
        threshold = threshold or self.config.redundancy_threshold
        corr = self.compute_factor_correlation(factor_df)
        if corr.empty:
            return []

        redundant = []
        for i in range(len(corr.columns)):
            for j in range(i + 1, len(corr.columns)):
                val = abs(corr.iloc[i, j])
                if val > threshold:
                    redundant.append((corr.columns[i], corr.columns[j], val))

        return sorted(redundant, key=lambda x: x[2], reverse=True)

    def cluster_factors(
        self,
        factor_df: pd.DataFrame
    ) -> Dict:
        """因子聚类分析

        Returns:
            Dict with 'linkage' matrix and 'order' list
        """
        corr = self.compute_factor_correlation(factor_df)
        if corr.empty or corr.shape[0] < 2:
            return {"linkage": None, "order": []}

        distance = 1 - corr.abs()
        condensed = distance.values[np.triu_indices_from(distance.values, k=1)]

        if len(condensed) == 0:
            return {"linkage": None, "order": list(corr.columns)}

        linkage = hierarchy.linkage(condensed, method="ward")
        order = [corr.columns[i] for i in hierarchy.leaves_list(linkage)]

        return {"linkage": linkage, "order": order}

    def compute_turnover(
        self,
        factor_values: pd.Series,
        n_groups: int = None
    ) -> float:
        """计算因子换手率（相邻期分位变化的平均比例）

        Args:
            factor_values: 因子值 Series
                - MultiIndex (date, code): 多期面板数据，计算跨期换手率
                - 单层 index: 单截面数据，无法计算跨期换手，返回 0.0

        Returns:
            平均换手率
        """
        n_groups = n_groups or self.config.n_quantiles
        if factor_values.empty:
            return 0.0

        if isinstance(factor_values.index, pd.MultiIndex):
            dates = factor_values.index.get_level_values(0).unique()
            if len(dates) < 2:
                return 0.0

            turnovers = []
            for i in range(1, len(dates)):
                prev = factor_values.xs(dates[i - 1], level=0)
                curr = factor_values.xs(dates[i], level=0)
                common = prev.index.intersection(curr.index)
                if len(common) < n_groups * 2:
                    continue

                prev_q = pd.qcut(prev[common].rank(pct=True), n_groups, labels=False, duplicates="drop")
                curr_q = pd.qcut(curr[common].rank(pct=True), n_groups, labels=False, duplicates="drop")
                turnover = (prev_q != curr_q).mean()
                turnovers.append(turnover)

            return np.mean(turnovers) if turnovers else 0.0
        else:
            # 单截面：无法计算换手率
            return 0.0

    def generate_scorecard(
        self,
        factor_df: pd.DataFrame,
        forward_returns: pd.Series,
        returns_matrix: pd.DataFrame = None
    ) -> pd.DataFrame:
        """生成因子综合评分卡

        评分维度:
        - IC_IR (权重 30%): 因子预测稳定性
        - IC 胜率 (权重 20%): 因子预测方向准确率
        - 多空收益差 (权重 20%): 因子区分度
        - 单调性 (权重 15%): 分层收益单调性
        - IC 最大回撤 (权重 15%): 因子失效风险

        Returns:
            DataFrame with FactorScorecard fields
        """
        ic_summary = self.compute_ic_summary(factor_df, forward_returns)
        if ic_summary.empty:
            return pd.DataFrame()

        scorecards = []
        for _, row in ic_summary.iterrows():
            factor = row["factor"]
            sc = FactorScorecard(factor=factor)

            sc.rank_ic_mean = row["rank_ic_mean"]
            sc.rank_ic_std = row["rank_ic_std"]
            sc.ic_ir = row["ic_ir"]
            sc.ic_win_rate = row["ic_win_rate"]
            sc.ic_t_stat = row["ic_t_stat"]
            sc.max_drawdown_ic = row["max_drawdown_ic"]

            if factor in factor_df.columns:
                factor_series = factor_df[factor].dropna()
                if len(factor_series) > 0:
                    sc.long_short_spread = self.compute_long_short_spread(
                        factor_series, forward_returns
                    )
                    sc.monotonicity = self.compute_monotonicity(
                        factor_series, forward_returns
                    )
                    sc.turnover = self.compute_turnover(factor_series)

            scorecards.append(sc)

        if not scorecards:
            return pd.DataFrame()

        df = pd.DataFrame([s.__dict__ for s in scorecards])

        ic_ir_norm = self._minmax_norm(df["ic_ir"].abs())
        win_rate_norm = self._minmax_norm(df["ic_win_rate"])
        spread_norm = self._minmax_norm(df["long_short_spread"].abs())
        mono_norm = self._minmax_norm(df["monotonicity"].abs())
        dd_norm = 1 - self._minmax_norm(df["max_drawdown_ic"].abs())

        df["composite_score"] = (
            ic_ir_norm * 0.30 +
            win_rate_norm * 0.20 +
            spread_norm * 0.20 +
            mono_norm * 0.15 +
            dd_norm * 0.15
        )

        return df.sort_values("composite_score", ascending=False)

    @staticmethod
    def _minmax_norm(series: pd.Series) -> pd.Series:
        """Min-Max 归一化"""
        mn, mx = series.min(), series.max()
        if mx == mn:
            return pd.Series(0.5, index=series.index)
        return (series - mn) / (mx - mn)

    def generate_report(
        self,
        factor_df: pd.DataFrame,
        forward_returns: pd.Series,
        returns_matrix: pd.DataFrame = None
    ) -> str:
        """生成因子评价报告"""
        scorecard = self.generate_scorecard(factor_df, forward_returns, returns_matrix)
        redundant = self.find_redundant_factors(factor_df)
        cluster = self.cluster_factors(factor_df)

        lines = [
            "=" * 70,
            "                    因子评价报告",
            "=" * 70,
            "",
            f"  评价因子数: {len(factor_df.columns)}",
            f"  样本量: {len(factor_df)}",
            "",
            "-" * 70,
            "  【因子综合评分】",
            "-" * 70,
        ]

        if not scorecard.empty:
            for _, row in scorecard.iterrows():
                stars = "★" * min(5, int(row["composite_score"] * 5 + 1))
                lines.append(
                    f"  {row['factor']:<25s} "
                    f"评分: {row['composite_score']:.3f} {stars}  "
                    f"IC_IR: {row['ic_ir']:+.3f}  "
                    f"胜率: {row['ic_win_rate']:.1%}"
                )
        else:
            lines.append("  (无有效因子)")

        lines += [
            "",
            "-" * 70,
            "  【因子聚类顺序】",
            "-" * 70,
            f"  {' → '.join(cluster['order'][:15])}{' ...' if len(cluster['order']) > 15 else ''}",
            "",
        ]

        if redundant:
            lines += [
                "-" * 70,
                "  【冗余因子对 (|ρ| > {:.0%})】".format(self.config.redundancy_threshold),
                "-" * 70,
            ]
            for a, b, corr in redundant[:10]:
                lines.append(f"  {a} ↔ {b}: ρ = {corr:+.3f}")
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


def build_forward_returns(
    prices: pd.Series,
    periods: List[int] = None
) -> pd.DataFrame:
    """构建多期未来收益矩阵

    Args:
        prices: 价格 Series, index=date
        periods: 未来持有期列表，默认 [1, 5, 10, 20]

    Returns:
        DataFrame with columns like '1d', '5d', '10d', '20d'
    """
    periods = periods or [1, 5, 10, 20]
    returns = prices.pct_change().shift(-1)

    result = pd.DataFrame(index=prices.index)
    for p in periods:
        if p == 1:
            result[f"{p}d"] = returns
        else:
            result[f"{p}d"] = prices.pct_change(periods=p).shift(-p - 1)

    return result


def build_cross_sectional_returns(
    market_data: Dict[str, pd.DataFrame],
    date: str,
    forward_period: int = 20
) -> pd.Series:
    """构建截面未来收益

    Args:
        market_data: {code: DataFrame with 'close' column}
        date: 当前日期
        forward_period: 未来持有期（交易日）

    Returns:
        Series with index=code, value=future_return
    """
    target_date = pd.Timestamp(date)
    future_returns = {}

    for code, df in market_data.items():
        if df is None or df.empty:
            continue
        df = df.sort_values("trade_date")
        current_idx = df[df["trade_date"] <= target_date].index
        if len(current_idx) == 0:
            continue
        idx = current_idx[-1]
        future_idx = idx + forward_period
        if future_idx >= len(df):
            continue

        current_price = df.loc[idx, "close"]
        future_price = df.loc[future_idx, "close"]
        if current_price > 0:
            future_returns[code] = future_price / current_price - 1

    return pd.Series(future_returns, name=f"fwd_{forward_period}d")