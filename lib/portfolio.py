"""投资组合构建器 —— 从 ML 评分构建组合权重

提供三种优化方法：
1. equal_weight: 等权（默认）
2. mean_variance: Ledoit-Wolf 收缩 + 最大夏普
3. risk_parity: 逆波动率加权

约束：
- max_single_weight: 单票权重上限（默认 0.20）
- max_sector_exposure: 行业暴露上限（默认 0.30）
- top_n: 最大持仓数（默认 10）

外部乘数：
- position_multiplier 处理宏观门禁/回撤等外部信号
- 最终仓位 = position_multiplier × portfolio_weight
"""
import logging
from datetime import datetime
from typing import Dict, Optional

import numpy as np
import pandas as pd

from config import PortfolioConfig

logger = logging.getLogger(__name__)


class PortfolioBuilder:
    """投资组合构建器

    将 ML 评分转换为可执行的组合权重，支持三种优化方法和两层约束。

    Parameters
    ----------
    config : PortfolioConfig, optional
        组合配置。默认使用 PortfolioConfig()，即等权/单票上限20%/行业上限30%/前10只。
    """

    def __init__(self, config: Optional[PortfolioConfig] = None):
        self.config = config or PortfolioConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        ml_scores: pd.Series,
        current_holdings: Optional[dict] = None,
        market_data: Optional[pd.DataFrame] = None,
        sector_map: Optional[dict] = None,
        position_multiplier: float = 1.0,
    ) -> dict:
        """从 ML 评分构建组合权重

        Parameters
        ----------
        ml_scores : pd.Series
            ML 评分，index 为股票代码，values 为评分（高分=优）。
        current_holdings : dict, optional
            当前持仓 {code: weight}，当前版本暂未使用（保留接口兼容）。
        market_data : pd.DataFrame, optional
            行情数据，用于计算协方差矩阵和波动率。
            需含 code/date/close 或收益率列。
        sector_map : dict, optional
            股票到行业映射 {code: sector_name}，用于行业暴露约束。
        position_multiplier : float, optional
            外部信号乘数（宏观门禁/回撤等），默认 1.0 表示全仓位。
            总信号乘数由外部计算后传入（通常是各信号的最小值）。

        Returns
        -------
        dict
            {code: {weight, direction, target_weight, limit_price, timestamp},
             _meta: {method, n_positions, position_multiplier, timestamp}}
        """
        self.config.top_n  # ensure config is accessible

        if ml_scores is None or ml_scores.empty:
            logger.warning("ML scores 为空，返回空组合")
            return self._empty_result(position_multiplier)

        # 可用股票数少于 top_n 时动态调整
        n_available = len(ml_scores)
        top_n = min(self.config.top_n, n_available)

        # 按评分降序取 top N
        sorted_scores = ml_scores.sort_values(ascending=False).head(top_n)
        codes = sorted_scores.index.tolist()

        # 根据配置选择优化方法
        method = self.config.optimize_method

        if method == "mean_variance":
            weights = self._mean_variance(sorted_scores, codes, market_data)
        elif method == "risk_parity":
            weights = self._risk_parity(sorted_scores, codes, market_data)
        else:
            weights = self._equal_weight(sorted_scores, codes)

        # 无有效权重时回退到等权
        if not weights or all(w == 0 for w in weights.values()):
            logger.warning("优化结果为空，回退到等权")
            weights = self._equal_weight(sorted_scores, codes)

        # 约束裁剪：单票上限 + 行业上限
        weights = self._apply_constraints(weights, codes, sector_map)

        # 应用外部乘数（宏观门禁/回撤等）
        total_weight = sum(w for w in weights.values() if w > 0)
        scaled_weights = {}
        for code, w in weights.items():
            if w > 0:
                scaled_weights[code] = w * position_multiplier

        # 放弃乘数后权重为 0 的持仓
        active_weights = {c: w for c, w in scaled_weights.items() if w > 0}

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        result = {}
        for code, w in active_weights.items():
            result[code] = {
                "weight": round(w, 4),
                "direction": "BUY",
                "target_weight": round(w, 4),
                "limit_price": None,
                "timestamp": timestamp,
            }

        result["_meta"] = {
            "method": method,
            "n_positions": len(active_weights),
            "position_multiplier": position_multiplier,
            "timestamp": timestamp,
        }

        logger.info(
            "组合构建完成: method=%s n=%d position_multiplier=%.2f",
            method, len(active_weights), position_multiplier,
        )
        return result

    # ------------------------------------------------------------------
    # 三种优化方法
    # ------------------------------------------------------------------

    def _equal_weight(self, scores: pd.Series, codes: list) -> Dict[str, float]:
        """等权：前 N 只股票平分 100% 权重

        Parameters
        ----------
        scores : pd.Series
            评分序列（仅用于接口一致）。
        codes : list
            选中股票代码列表。

        Returns
        -------
        dict
            {code: weight}，权重和为 1。
        """
        n = len(codes)
        if n == 0:
            return {}
        weight = 1.0 / n
        return {code: weight for code in codes}

    def _mean_variance(
        self,
        scores: pd.Series,
        codes: list,
        market_data: Optional[pd.DataFrame],
    ) -> Dict[str, float]:
        """均值方差优化

        1. 从 market_data 提取 60 日收益率序列
        2. Ledoit-Wolf 收缩估计协方差矩阵
        3. ML 评分映射为预期收益（归一化后 × 基准系数）
        4. 最大夏普比优化（封闭解）
        5. 禁止做空（w >= 0）

        Parameters
        ----------
        scores : pd.Series
            评分序列，index=code。
        codes : list
            目标股票代码。
        market_data : pd.DataFrame or None
            行情数据。

        Returns
        -------
        dict
            {code: weight}。
        """
        returns = self._extract_returns(codes, market_data, periods=60)
        if returns is None or returns.shape[1] < 2:
            logger.warning("mean_variance: 收益率数据不足，回退到等权")
            return self._equal_weight(scores, codes)

        # 对齐可用代码
        available = [c for c in codes if c in returns.columns]
        if len(available) < 2:
            logger.warning("mean_variance: 可用股票不足 2 只，回退到等权")
            return self._equal_weight(scores, codes)

        ret_matrix = returns[available]

        try:
            # Ledoit-Wolf 收缩协方差
            from sklearn.covariance import LedoitWolf

            lw = LedoitWolf().fit(ret_matrix.values)
            cov = lw.covariance_

            # 预期收益：ML 评分归一化后映射到日收益率
            score_vals = scores[available].values
            score_norm = (
                (score_vals - score_vals.mean()) / score_vals.std()
                if score_vals.std() > 1e-8
                else np.zeros_like(score_vals)
            )
            expected_returns = score_norm * 0.001  # 1 std = 0.1% 日收益

            # 最大夏普比
            weights = self._max_sharpe(expected_returns, cov)

            result = {
                code: float(w)
                for code, w in zip(available, weights)
                if float(w) > 0
            }

            if not result:
                logger.warning("mean_variance: 所有权重为 0")
                return self._equal_weight(scores, codes)

            return result

        except ImportError:
            logger.warning("mean_variance: sklearn 未安装，回退到等权")
            return self._equal_weight(scores, codes)
        except Exception as e:
            logger.error("mean_variance 优化失败 (%s)，回退到等权", e)
            return self._equal_weight(scores, codes)

    def _risk_parity(
        self,
        scores: pd.Series,
        codes: list,
        market_data: Optional[pd.DataFrame],
    ) -> Dict[str, float]:
        """风险平价：逆波动率加权

        波动率越低，权重越高。适合风险分散场景。

        Parameters
        ----------
        scores : pd.Series
            评分序列（仅用于选择 top N）。
        codes : list
            目标股票代码。
        market_data : pd.DataFrame or None
            行情数据。

        Returns
        -------
        dict
            {code: weight}。
        """
        returns = self._extract_returns(codes, market_data, periods=60)
        if returns is None or returns.shape[1] < 2:
            logger.warning("risk_parity: 收益率数据不足，回退到等权")
            return self._equal_weight(scores, codes)

        available = [c for c in codes if c in returns.columns]
        if len(available) < 1:
            logger.warning("risk_parity: 无可用波动率数据，回退到等权")
            return self._equal_weight(scores, codes)

        try:
            # 日波动率 → 年化
            daily_vols = returns[available].std()
            vols = daily_vols.values * np.sqrt(252)

            # 逆波动率
            inv_vol = 1.0 / np.maximum(vols, 1e-6)
            weights = inv_vol / inv_vol.sum()

            # 单票约束（初步裁剪，_apply_constraints 中会再处理）
            max_w = self.config.max_single_weight
            weights = np.clip(weights, 0, max_w)
            weights = weights / weights.sum()

            return {
                code: float(w)
                for code, w in zip(available, weights)
                if float(w) > 0
            }

        except Exception as e:
            logger.error("risk_parity 优化失败 (%s)，回退到等权", e)
            return self._equal_weight(scores, codes)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_returns(
        codes: list,
        market_data: Optional[pd.DataFrame],
        periods: int = 60,
    ) -> Optional[pd.DataFrame]:
        """从行情数据提取收益率矩阵

        支持两种格式：
        1. 直接含收益率列（return / pct_chg 等）
        2. 通过收盘价计算收益率（close / adj_close 等）

        Parameters
        ----------
        codes : list
            股票代码列表。
        market_data : pd.DataFrame or None
            行情数据，需含 code/date 和收盘价或收益率。
        periods : int
            回溯周期数（交易日），默认 60。

        Returns
        -------
        pd.DataFrame or None
            {date x code} 收益率矩阵，行为日期、列为股票。
            数据不足时返回 None。
        """
        if market_data is None or market_data.empty:
            return None

        # 常见的列名变体
        code_col = _match_col(market_data, {"code", "symbol", "ts_code", "stock_code"}) or "code"
        date_col = _match_col(market_data, {"date", "trade_date", "datetime", "tradedate"}) or "date"
        ret_col = _match_col(market_data, {"return", "returns", "ret", "pct_chg", "pct_change"})
        price_col = _match_col(market_data, {"close", "adj_close", "adjclose", "price"})

        if date_col not in market_data.columns:
            return None

        # 过滤目标股票
        df = market_data[market_data[code_col].isin(codes)].copy()
        if df.empty:
            return None

        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values([code_col, date_col])

        if ret_col and ret_col in df.columns:
            # 方案 A：直接使用收益率列
            df_sorted = df.sort_values(date_col)
            df_grouped = (
                df_sorted.groupby(code_col)
                .tail(periods)
            )
            result = df_grouped.pivot_table(
                index=date_col, columns=code_col, values=ret_col
            )
        elif price_col and price_col in df.columns:
            # 方案 B：通过收盘价计算收益率
            df["_ret"] = df.groupby(code_col)[price_col].pct_change()
            # pct_change 损失第一行，所以多取一天
            df_grouped = (
                df.sort_values(date_col)
                .groupby(code_col)
                .tail(periods + 1)
            )
            result = df_grouped.pivot_table(
                index=date_col, columns=code_col, values="_ret"
            )
            # 删除因 pct_change 产生的 NaN 行
            result = result.iloc[1:] if len(result) > 1 else result
        else:
            logger.warning("market_data 缺少收益率列和价格列，无法计算收益")
            return None

        # 只保留目标股票列，去掉全是 NaN 的行
        result = result[[c for c in codes if c in result.columns]]
        result = result.dropna(how="all")

        if result.empty or result.shape[1] < 1:
            return None

        return result

    @staticmethod
    def _max_sharpe(expected_returns: np.ndarray, cov: np.ndarray) -> np.ndarray:
        """最大夏普比权重（封闭解）

        解析解：w = (Σ⁻¹ μ) / (1ᵀ Σ⁻¹ μ)
        并裁剪非负以禁止做空。

        Parameters
        ----------
        expected_returns : np.ndarray
            预期收益向量 (n, )。
        cov : np.ndarray
            协方差矩阵 (n x n)。

        Returns
        -------
        np.ndarray
            权重向量，和为 1。
        """
        n = len(expected_returns)
        if n == 0:
            return np.array([], dtype=float)

        try:
            inv_cov = np.linalg.pinv(cov)
            numer = inv_cov @ expected_returns
            denom = np.ones(n) @ numer
            if abs(denom) < 1e-12:
                return np.ones(n) / n

            w = numer / denom
            w = np.maximum(w, 0)  # 禁止做空

            total = w.sum()
            if total > 1e-12:
                w = w / total
            else:
                w = np.ones(n) / n
            return w

        except np.linalg.LinAlgError:
            return np.ones(n) / n

    def _apply_constraints(
        self,
        weights: Dict[str, float],
        codes: list,
        sector_map: Optional[dict] = None,
    ) -> Dict[str, float]:
        """应用组合约束

        约束：
        1. max_single_weight: 单票权重不超过上限（超过则裁剪）
        2. max_sector_exposure: 单行业总权重不超过上限（比例削减）

        超限部分不重新分配（即组合可能不满仓），保留现金余量。

        Parameters
        ----------
        weights : dict
            {code: weight}，优化器输出的权重。
        codes : list
            股票代码列表。
        sector_map : dict or None
            {code: sector_name}。

        Returns
        -------
        dict
            约束后的权重，总和 ≤ 1。
        """
        if not weights:
            return {}

        max_single = self.config.max_single_weight
        max_sector = self.config.max_sector_exposure

        result = dict(weights)

        # 1) 单票约束：超限则裁剪到上限
        for code in list(result.keys()):
            result[code] = min(result[code], max_single)

        # 2) 行业约束：超限行业按比例削减
        if sector_map:
            # 多次迭代以确保嵌套约束收敛
            for _ in range(5):
                sector_w = {}
                for code in result:
                    sec = sector_map.get(code, "其他")
                    sector_w[sec] = sector_w.get(sec, 0) + result[code]

                over_exposed = {
                    sec: total for sec, total in sector_w.items()
                    if total > max_sector + 1e-10
                }
                if not over_exposed:
                    break

                for sec, total in over_exposed.items():
                    scale = max_sector / total
                    for code in list(result.keys()):
                        if sector_map.get(code, "其他") == sec:
                            result[code] *= scale

        # 3) 清理极小权重（噪声）
        return {c: w for c, w in result.items() if w >= 0.001}

    def _empty_result(self, position_multiplier: float) -> dict:
        """返回空结果"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return {
            "_meta": {
                "method": self.config.optimize_method,
                "n_positions": 0,
                "position_multiplier": position_multiplier,
                "timestamp": timestamp,
            }
        }


def _match_col(df: pd.DataFrame, candidates: set) -> Optional[str]:
    """匹配列名（忽略大小写）"""
    lower_map = {c.lower(): c for c in df.columns}
    for candidate in candidates:
        if candidate in lower_map:
            return lower_map[candidate]
    return None
