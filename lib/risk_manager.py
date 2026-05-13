"""
风控模块 —— 实时风险监控、止损止盈、异常预警
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class RiskRule:
    """风控规则"""
    name: str
    check_fn: Callable
    action: str  # WARN / REDUCE / STOP
    description: str = ""


@dataclass
class RiskAlert:
    """风控预警"""
    timestamp: str
    rule_name: str
    level: str  # INFO / WARNING / CRITICAL
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class RiskConfig:
    """风控参数配置"""
    max_drawdown_stop: float = 0.20
    single_stock_stop_loss: float = 0.08
    single_stock_take_profit: float = 0.30
    max_single_position: float = 0.15
    max_industry_exposure: float = 0.30
    daily_loss_limit: float = 0.05
    enable_trailing_stop: bool = True
    trailing_stop_pct: float = 0.05


class RiskManager:
    """风控管理器"""

    def __init__(self, config: RiskConfig = None):
        self.config = config or RiskConfig()
        self.rules: List[RiskRule] = []
        self.alerts: List[RiskAlert] = []
        self._register_default_rules()

    def _register_default_rules(self):
        """注册默认风控规则"""
        self.rules = [
            RiskRule(
                name="max_drawdown",
                check_fn=self._check_max_drawdown,
                action="STOP",
                description=f"总回撤超过 {self.config.max_drawdown_stop:.0%} 停止交易"
            ),
            RiskRule(
                name="single_stock_loss",
                check_fn=self._check_single_stock_loss,
                action="REDUCE",
                description=f"单票亏损超过 {self.config.single_stock_stop_loss:.0%} 止损"
            ),
            RiskRule(
                name="concentration",
                check_fn=self._check_concentration,
                action="WARN",
                description=f"单票集中度超过 {self.config.max_single_position:.0%} 预警"
            ),
            RiskRule(
                name="industry_concentration",
                check_fn=self._check_industry_concentration,
                action="WARN",
                description=f"行业集中度超过 {self.config.max_industry_exposure:.0%} 预警"
            ),
            RiskRule(
                name="daily_loss_limit",
                check_fn=self._check_daily_loss,
                action="STOP",
                description=f"单日亏损超过 {self.config.daily_loss_limit:.0%} 暂停"
            ),
        ]

    def add_rule(self, rule: RiskRule):
        """添加自定义风控规则 —— 这是你个性化的关键"""
        self.rules.append(rule)
        logger.info(f"添加风控规则: {rule.name}")

    def check_all(self, portfolio_value: float, positions: Dict,
                  daily_return: float = 0, industry_map: Dict[str, str] = None,
                  max_drawdown: float = 0) -> List[RiskAlert]:
        """执行所有风控检查"""
        self.alerts = []
        context = {
            "portfolio_value": portfolio_value,
            "positions": positions,
            "daily_return": daily_return,
            "industry_map": industry_map or {},
            "max_drawdown": max_drawdown,
        }

        for rule in self.rules:
            try:
                result = rule.check_fn(context)
                if result:
                    if isinstance(result, list):
                        self.alerts.extend(result)
                    else:
                        self.alerts.append(result)
            except Exception as e:
                logger.error(f"风控规则 {rule.name} 执行失败: {e}")

        return self.alerts

    def _check_max_drawdown(self, ctx: dict) -> Optional[RiskAlert]:
        if ctx["max_drawdown"] < -self.config.max_drawdown_stop:
            return RiskAlert(
                timestamp=datetime.now().isoformat(),
                rule_name="max_drawdown",
                level="CRITICAL",
                message=f"最大回撤 {ctx['max_drawdown']:.2%} 超过 {self.config.max_drawdown_stop:.0%} 阈值",
                details={"max_drawdown": ctx["max_drawdown"]}
            )
        return None

    def _check_single_stock_loss(self, ctx: dict) -> List[RiskAlert]:
        alerts = []
        for code, pos in ctx["positions"].items():
            pnl_pct = pos.get("pnl_pct", 0)
            if pnl_pct < -self.config.single_stock_stop_loss:
                alerts.append(RiskAlert(
                    timestamp=datetime.now().isoformat(),
                    rule_name="single_stock_loss",
                    level="WARNING",
                    message=f"{code} 亏损 {pnl_pct:.2%}，触发止损",
                    details={"code": code, "pnl_pct": pnl_pct}
                ))
        return alerts

    def _check_concentration(self, ctx: dict) -> List[RiskAlert]:
        alerts = []
        for code, pos in ctx["positions"].items():
            weight = pos.get("weight", 0)
            if weight > self.config.max_single_position:
                alerts.append(RiskAlert(
                    timestamp=datetime.now().isoformat(),
                    rule_name="concentration",
                    level="WARNING",
                    message=f"{code} 仓位 {weight:.2%} 超过 {self.config.max_single_position:.0%}",
                    details={"code": code, "weight": weight}
                ))
        return alerts

    def _check_industry_concentration(self, ctx: dict) -> List[RiskAlert]:
        industry_map = ctx.get("industry_map", {})
        if not industry_map:
            return []

        industry_weights = {}
        for code, pos in ctx["positions"].items():
            ind = industry_map.get(code, "未知")
            industry_weights[ind] = industry_weights.get(ind, 0) + pos.get("weight", 0)

        alerts = []
        for ind, w in industry_weights.items():
            if w > self.config.max_industry_exposure:
                alerts.append(RiskAlert(
                    timestamp=datetime.now().isoformat(),
                    rule_name="industry_concentration",
                    level="WARNING",
                    message=f"行业 {ind} 集中度 {w:.2%} 超过 {self.config.max_industry_exposure:.0%}",
                    details={"industry": ind, "weight": w}
                ))
        return alerts

    def _check_daily_loss(self, ctx: dict) -> Optional[RiskAlert]:
        if ctx["daily_return"] < -self.config.daily_loss_limit:
            return RiskAlert(
                timestamp=datetime.now().isoformat(),
                rule_name="daily_loss_limit",
                level="CRITICAL",
                message=f"单日亏损 {ctx['daily_return']:.2%} 超过 {self.config.daily_loss_limit:.0%}",
                details={"daily_return": ctx["daily_return"]}
            )
        return None

    def get_active_alerts(self, min_level: str = "WARNING") -> List[RiskAlert]:
        """获取活跃预警"""
        levels = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}
        return [a for a in self.alerts if levels.get(a.level, 0) >= levels.get(min_level, 0)]

    def check_position_risks(self, positions: Dict, prices: Dict[str, float],
                              date: str) -> List[Tuple[str, str]]:
        """检查持仓止损/止盈，返回 [(code, reason), ...] 强制卖出列表"""
        forced_sells = []
        for code, holding in positions.items():
            current_price = prices.get(code)
            if current_price is None:
                continue

            pnl_pct = (current_price - holding.cost_price) / holding.cost_price

            if pnl_pct <= -self.config.single_stock_stop_loss:
                forced_sells.append((code, f"止损 ({pnl_pct:.2%})"))
                logger.info(f"{date} {code} 触发止损: {pnl_pct:.2%}")

            elif pnl_pct >= self.config.single_stock_take_profit:
                forced_sells.append((code, f"止盈 ({pnl_pct:.2%})"))
                logger.info(f"{date} {code} 触发止盈: {pnl_pct:.2%}")

        return forced_sells

    def is_daily_loss_limit_triggered(self, daily_pnl: float,
                                       prev_total_value: float) -> bool:
        """检查日亏损是否触发暂停"""
        if prev_total_value <= 0:
            return False
        daily_return = daily_pnl / prev_total_value
        if daily_return < -self.config.daily_loss_limit:
            logger.warning(f"日亏损 {daily_return:.2%} 触发暂停")
            return True
        return False

    def apply_concentration_limits(self, signals: list,
                                    total_value: float) -> list:
        """对信号施加集中度限制，调整权重"""
        if not signals:
            return signals

        for s in signals:
            max_weight = self.config.max_single_position
            if s.weight > max_weight:
                s.weight = max_weight

        total_weight = sum(s.weight for s in signals)
        if total_weight > 1.0:
            scale = 1.0 / total_weight
            for s in signals:
                s.weight *= scale

        return signals


@dataclass
class VolCorrRiskConfig(RiskConfig):
    """波动率+相关性联合风控配置"""
    target_portfolio_vol: float = 0.20
    max_portfolio_vol: float = 0.35
    vol_lookback: int = 60
    max_pairwise_correlation: float = 0.70
    max_avg_correlation: float = 0.55
    correlation_lookback: int = 60
    var_confidence: float = 0.95
    max_var_pct: float = 0.05
    max_cvar_pct: float = 0.08
    vol_regime_short: int = 20
    vol_regime_long: int = 60
    vol_expansion_threshold: float = 1.5
    vol_contraction_threshold: float = 0.5
    high_vol_position_scalar: float = 0.5
    medium_vol_position_scalar: float = 0.75
    low_vol_position_scalar: float = 1.2
    min_diversification_score: float = 0.3


class VolCorrRiskManager(RiskManager):
    """波动率+相关性联合风控管理器"""

    def __init__(self, config: VolCorrRiskConfig = None):
        super().__init__(config or VolCorrRiskConfig())
        self.config: VolCorrRiskConfig = self.config
        self._returns_cache: Dict[str, np.ndarray] = {}
        self._vol_regime: str = "normal"
        self._diversification_score: float = 1.0

    def compute_volatility_regime(
        self, market_data: Dict[str, pd.DataFrame], tickers: List[str]
    ) -> Dict:
        """计算波动率区间"""
        vols = {}
        for ticker in tickers:
            df = market_data.get(ticker)
            if df is None or df.empty or "close" not in df.columns:
                continue
            close = df["close"].values
            if len(close) < self.config.vol_regime_long:
                continue
            rets = np.diff(close) / close[:-1]
            vol_short = float(np.std(rets[-self.config.vol_regime_short:]) * np.sqrt(252))
            vol_long = float(np.std(rets[-self.config.vol_regime_long:]) * np.sqrt(252))
            vols[ticker] = {"vol_short": vol_short, "vol_long": vol_long}

        if not vols:
            return {"regime": "unknown", "adjustments": {}}

        avg_vol_ratio = np.mean([
            v["vol_short"] / max(v["vol_long"], 0.001) for v in vols.values()
        ])

        if avg_vol_ratio > self.config.vol_expansion_threshold:
            regime = "high"
            scalar = self.config.high_vol_position_scalar
        elif avg_vol_ratio < self.config.vol_contraction_threshold:
            regime = "low"
            scalar = self.config.low_vol_position_scalar
        else:
            regime = "normal"
            scalar = self.config.medium_vol_position_scalar

        self._vol_regime = regime

        adjustments = {}
        for ticker, v in vols.items():
            ratio = v["vol_short"] / max(v["vol_long"], 0.001)
            if ratio > self.config.vol_expansion_threshold:
                adj = self.config.high_vol_position_scalar
            elif ratio < self.config.vol_contraction_threshold:
                adj = self.config.low_vol_position_scalar
            else:
                adj = 1.0
            adjustments[ticker] = {
                "vol_short": v["vol_short"],
                "vol_long": v["vol_long"],
                "ratio": ratio,
                "position_scalar": adj,
            }

        return {"regime": regime, "scalar": scalar, "adjustments": adjustments}

    def compute_correlation_matrix(
        self, market_data: Dict[str, pd.DataFrame], tickers: List[str]
    ) -> Tuple[Optional[pd.DataFrame], Dict]:
        """计算相关性矩阵和分散化指标"""
        returns_dict = {}
        for ticker in tickers:
            df = market_data.get(ticker)
            if df is None or df.empty or "close" not in df.columns:
                continue
            close = df["close"].values
            if len(close) < self.config.correlation_lookback:
                continue
            rets = np.diff(close[-self.config.correlation_lookback:]) / close[-self.config.correlation_lookback:-1]
            returns_dict[ticker] = rets

        if len(returns_dict) < 2:
            return None, {"error": "数据不足，无法计算相关性"}

        returns_df = pd.DataFrame(returns_dict)
        corr = returns_df.corr()
        n = len(corr)

        upper_tri = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool))
        values = upper_tri.stack()
        avg_corr = float(values.mean()) if len(values) > 0 else 0.0
        max_corr = float(values.max()) if len(values) > 0 else 0.0

        high_corr_pairs = []
        for i in range(n):
            for j in range(i + 1, n):
                if abs(corr.iloc[i, j]) > self.config.max_pairwise_correlation:
                    high_corr_pairs.append({
                        "pair": (corr.index[i], corr.columns[j]),
                        "correlation": float(corr.iloc[i, j]),
                    })

        diversification_score = max(0.0, 1.0 - avg_corr)
        self._diversification_score = diversification_score

        metrics = {
            "avg_correlation": avg_corr,
            "max_correlation": max_corr,
            "high_corr_pairs": high_corr_pairs,
            "diversification_score": diversification_score,
            "needs_diversification": avg_corr > self.config.max_avg_correlation,
        }

        return corr, metrics

    def compute_var_cvar(
        self, market_data: Dict[str, pd.DataFrame],
        tickers: List[str], weights: Dict[str, float],
        total_value: float
    ) -> Dict:
        """计算组合 VaR 和 CVaR"""
        returns_dict = {}
        for ticker in tickers:
            df = market_data.get(ticker)
            if df is None or df.empty or "close" not in df.columns:
                continue
            close = df["close"].values
            if len(close) < 60:
                continue
            rets = np.diff(close[-60:]) / close[-60:-1]
            returns_dict[ticker] = rets

        if not returns_dict:
            return {"var_95": 0.0, "cvar_95": 0.0, "var_95_pct": 0.0, "cvar_95_pct": 0.0}

        returns_df = pd.DataFrame(returns_dict)
        common_tickers = [t for t in tickers if t in returns_df.columns]
        if not common_tickers:
            return {"var_95": 0.0, "cvar_95": 0.0, "var_95_pct": 0.0, "cvar_95_pct": 0.0}

        w = np.array([weights.get(t, 1.0 / len(common_tickers)) for t in common_tickers])
        w = w / w.sum()

        portfolio_returns = (returns_df[common_tickers] * w).sum(axis=1)
        var_95 = float(np.percentile(portfolio_returns, (1 - self.config.var_confidence) * 100))
        cvar_95 = float(portfolio_returns[portfolio_returns <= var_95].mean())

        return {
            "var_95": float(var_95 * total_value),
            "var_95_pct": float(abs(var_95)),
            "cvar_95": float(cvar_95 * total_value),
            "cvar_95_pct": float(abs(cvar_95)),
            "var_breached": abs(var_95) > self.config.max_var_pct,
            "cvar_breached": abs(cvar_95) > self.config.max_cvar_pct,
        }

    def adjust_position_sizes(
        self, signals: list, vol_adjustments: Dict,
        corr_metrics: Dict, var_metrics: Dict
    ) -> list:
        """波动率+相关性联合调整仓位大小"""
        if not signals:
            return signals

        regime_scalar = vol_adjustments.get("scalar", 1.0)
        adjustments = vol_adjustments.get("adjustments", {})
        div_score = corr_metrics.get("diversification_score", 1.0)

        for s in signals:
            code = getattr(s, "code", None)
            if code is None:
                continue

            ticker_adj = adjustments.get(code, {})
            vol_scalar = ticker_adj.get("position_scalar", 1.0)

            combined_scalar = regime_scalar * vol_scalar

            if div_score < self.config.min_diversification_score:
                combined_scalar *= 0.7

            if var_metrics.get("var_breached") or var_metrics.get("cvar_breached"):
                combined_scalar *= 0.6

            combined_scalar = max(0.1, min(2.0, combined_scalar))

            if hasattr(s, "weight"):
                s.weight *= combined_scalar

        return signals

    def get_risk_budget(
        self, vol_regime: str, diversification_score: float
    ) -> Dict:
        """动态风险预算分配"""
        base_budgets = {
            "high": {"equity": 0.30, "cash": 0.70, "max_positions": 3},
            "normal": {"equity": 0.65, "cash": 0.35, "max_positions": 8},
            "low": {"equity": 0.85, "cash": 0.15, "max_positions": 12},
        }

        budget = base_budgets.get(vol_regime, base_budgets["normal"]).copy()

        if diversification_score < 0.3:
            budget["equity"] *= 0.7
            budget["max_positions"] = max(2, budget["max_positions"] - 2)
        elif diversification_score > 0.7:
            budget["equity"] = min(0.90, budget["equity"] * 1.1)

        budget["cash"] = 1.0 - budget["equity"]

        return budget

    def full_risk_assessment(
        self, market_data: Dict[str, pd.DataFrame],
        tickers: List[str], positions: Dict,
        total_value: float, signals: list = None
    ) -> Dict:
        """完整风控评估 —— 一站式调用"""
        result = {}

        vol_result = self.compute_volatility_regime(market_data, tickers)
        result["volatility"] = vol_result

        corr_matrix, corr_metrics = self.compute_correlation_matrix(market_data, tickers)
        result["correlation"] = corr_metrics

        weights = {}
        for code, pos in positions.items():
            weights[code] = pos.get("weight", 0)
        if not weights and tickers:
            w = 1.0 / len(tickers)
            weights = {t: w for t in tickers}

        var_metrics = self.compute_var_cvar(market_data, tickers, weights, total_value)
        result["var"] = var_metrics

        risk_budget = self.get_risk_budget(
            vol_result.get("regime", "normal"),
            corr_metrics.get("diversification_score", 0.5),
        )
        result["risk_budget"] = risk_budget

        alerts = []
        if vol_result.get("regime") == "high":
            alerts.append(RiskAlert(
                timestamp=datetime.now().isoformat(),
                rule_name="volatility_regime",
                level="WARNING",
                message=f"高波动区间，建议仓位降至 {risk_budget['equity']:.0%}",
                details={"regime": "high", "target_equity": risk_budget["equity"]},
            ))

        if corr_metrics.get("needs_diversification"):
            alerts.append(RiskAlert(
                timestamp=datetime.now().isoformat(),
                rule_name="correlation_risk",
                level="WARNING",
                message=f"平均相关性 {corr_metrics['avg_correlation']:.2f}，分散化不足",
                details={"avg_correlation": corr_metrics["avg_correlation"]},
            ))

        if var_metrics.get("var_breached"):
            alerts.append(RiskAlert(
                timestamp=datetime.now().isoformat(),
                rule_name="var_breach",
                level="CRITICAL",
                message=f"VaR {var_metrics['var_95_pct']:.2%} 超过阈值",
                details={"var_95_pct": var_metrics["var_95_pct"]},
            ))

        result["alerts"] = alerts

        if signals is not None:
            adjusted_signals = self.adjust_position_sizes(
                signals, vol_result, corr_metrics, var_metrics
            )
            result["adjusted_signals"] = adjusted_signals

        return result


# ========== 协方差收缩估计（来自 PyPortfolioOpt） ==========

def covariance_shrinkage(returns_df: pd.DataFrame) -> Optional[pd.DataFrame]:
    """计算收缩协方差矩阵（比样本协方差数值更稳定）

    适用场景：组合超过 20 只股票时，样本协方差噪声大
    使用 Ledoit-Wolf 收缩法，将样本协方差向单位矩阵收缩

    Args:
        returns_df: 收益率 DataFrame，index=date, columns=ticker

    Returns:
        收缩后的协方差矩阵 DataFrame
    """
    if returns_df is None or returns_df.empty:
        return None

    try:
        from sklearn.covariance import LedoitWolf
    except ImportError:
        # 无 sklearn 时用简单替代
        n, p = returns_df.shape
        if n < p:
            # 高维时使用对角矩阵
            return returns_df.cov()

    codes = [c for c in returns_df.columns if returns_df[c].nunique() > 1]
    if len(codes) < 2:
        return returns_df.cov()

    data = returns_df[codes].dropna()
    if data.empty or len(data) < 10:
        return returns_df.cov()

    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf().fit(data.values)
        shrunk = pd.DataFrame(lw.covariance_, index=codes, columns=codes)
        return shrunk
    except Exception:
        return returns_df.cov()