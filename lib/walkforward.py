"""
Walk-Forward Backtest + Factor Audit —— 推进式回测 + 因子时间戳审计
参考 cryptoquant-ai 的 walkForwardBacktest + backtestValidation 设计
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ValidationStatus(str, Enum):
    STABLE = "stable"
    FRAGILE = "fragile"
    INSUFFICIENT_TRADES = "insufficient_trades"
    NO_VALIDATION_TRADES = "no_validation_trades"


class FactorAuditStatus(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    LATEST_REVISION_BLOCKED = "latest_revision_blocked"


@dataclass
class WalkForwardWindow:
    index: int
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    warmup_start: int
    warmup_bars: int
    train_start_date: str = ""
    train_end_date: str = ""
    validation_start_date: str = ""
    validation_end_date: str = ""


@dataclass
class FactorAuditItem:
    factor: str
    label: str
    status: FactorAuditStatus
    used_in_backtest: bool
    required_timestamp: str
    message: str


@dataclass
class WalkForwardRound:
    window_index: int
    strategy: str
    symbol: str = ""
    validation_status: ValidationStatus = ValidationStatus.NO_VALIDATION_TRADES
    train_metrics: Dict = field(default_factory=dict)
    validation_metrics: Dict = field(default_factory=dict)
    perturbation: Dict = field(default_factory=dict)
    diagnostics: Dict = field(default_factory=dict)


@dataclass
class WalkForwardConfig:
    train_days: int = 180
    validation_days: int = 30
    step_days: int = 30
    warmup_bars: int = 80
    min_train_trades: int = 5
    min_validation_trades: int = 3
    risk_per_trade_pct: float = 0.5
    strict_factor_audit: bool = True


class WalkForwardBacktest:
    """推进式回测引擎 —— 滑动窗口训练+验证，杜绝过拟合"""

    def __init__(self, config: WalkForwardConfig = None):
        self.config = config or WalkForwardConfig()

    def create_windows(
        self,
        dates: List,
        ohlcv_data: Dict[str, pd.DataFrame] = None,
    ) -> List[WalkForwardWindow]:
        """创建推进式窗口"""
        n = len(dates)
        train_bars = self.config.train_days
        val_bars = self.config.validation_days
        step_bars = self.config.step_days

        if n < train_bars + val_bars:
            logger.warning(f"数据不足: {n} < {train_bars + val_bars}")
            return []

        windows = []
        for start in range(0, n - train_bars - val_bars + 1, step_bars):
            train_start = start
            train_end = start + train_bars - 1
            val_start = train_end + 1
            val_end = min(val_start + val_bars - 1, n - 1)
            warmup_start = max(train_start, val_start - self.config.warmup_bars)

            windows.append(WalkForwardWindow(
                index=len(windows),
                train_start=train_start,
                train_end=train_end,
                validation_start=val_start,
                validation_end=val_end,
                warmup_start=warmup_start,
                warmup_bars=val_start - warmup_start,
                train_start_date=str(dates[train_start]) if train_start < len(dates) else "",
                train_end_date=str(dates[train_end]) if train_end < len(dates) else "",
                validation_start_date=str(dates[val_start]) if val_start < len(dates) else "",
                validation_end_date=str(dates[val_end]) if val_end < len(dates) else "",
            ))

        return windows

    def run(
        self,
        strategy_fn: Callable,
        market_data: Dict[str, pd.DataFrame],
        factor_data: pd.DataFrame = None,
        dates: List = None,
        symbols: List[str] = None,
        strategy_name: str = "default",
    ) -> List[WalkForwardRound]:
        """执行推进式回测"""
        if dates is None:
            all_dates = set()
            for df in market_data.values():
                if "trade_date" in df.columns:
                    all_dates.update(df["trade_date"].tolist())
            dates = sorted(all_dates)

        windows = self.create_windows(dates)
        if not windows:
            logger.warning("无法创建推进式窗口")
            return []

        rounds = []
        for window in windows:
            train_data = self._slice_data(market_data, dates, window.train_start, window.train_end)
            val_data = self._slice_data(market_data, dates, window.validation_start, window.validation_end)

            try:
                train_result = strategy_fn(
                    market_data=train_data,
                    factor_data=factor_data,
                    dates=dates[window.train_start:window.train_end + 1],
                    symbols=symbols,
                )
            except Exception as e:
                logger.error(f"窗口 {window.index} 训练失败: {e}")
                train_result = {}

            try:
                val_result = strategy_fn(
                    market_data=val_data,
                    factor_data=factor_data,
                    dates=dates[window.validation_start:window.validation_end + 1],
                    symbols=symbols,
                )
            except Exception as e:
                logger.error(f"窗口 {window.index} 验证失败: {e}")
                val_result = {}

            train_trades = train_result.get("trades", []) if isinstance(train_result, dict) else []
            val_trades = val_result.get("trades", []) if isinstance(val_result, dict) else []

            status = self._classify_status(
                train_trades=len(train_trades),
                validation_trades=len(val_trades),
                fragile=False,
            )

            rounds.append(WalkForwardRound(
                window_index=window.index,
                strategy=strategy_name,
                validation_status=status,
                train_metrics=self._extract_metrics(train_result),
                validation_metrics=self._extract_metrics(val_result),
                diagnostics=self._build_diagnostics(train_result, val_result),
            ))

        return rounds

    def _slice_data(
        self,
        market_data: Dict[str, pd.DataFrame],
        dates: List,
        start: int,
        end: int,
    ) -> Dict[str, pd.DataFrame]:
        sliced = {}
        date_slice = set(dates[start:end + 1])
        for code, df in market_data.items():
            if df is None or df.empty:
                continue
            if "trade_date" in df.columns:
                mask = df["trade_date"].isin(date_slice)
                sub = df[mask].copy()
            else:
                sub = df.iloc[start:end + 1].copy() if len(df) > end else df.iloc[start:].copy()
            if not sub.empty:
                sliced[code] = sub
        return sliced

    def _classify_status(
        self,
        train_trades: int,
        validation_trades: int,
        fragile: bool,
    ) -> ValidationStatus:
        if train_trades < self.config.min_train_trades:
            return ValidationStatus.INSUFFICIENT_TRADES
        if validation_trades <= 0:
            return ValidationStatus.NO_VALIDATION_TRADES
        if fragile:
            return ValidationStatus.FRAGILE
        return ValidationStatus.STABLE

    def _extract_metrics(self, result) -> Dict:
        if not isinstance(result, dict):
            return {}
        return {
            "total_return": result.get("total_return", 0),
            "max_drawdown": result.get("max_drawdown", 0),
            "sharpe_ratio": result.get("sharpe_ratio", 0),
            "win_rate": result.get("win_rate", 0),
            "profit_factor": result.get("profit_factor", 0),
            "total_trades": result.get("total_trades", 0),
            "expectancy": result.get("expectancy", 0),
        }

    def _build_diagnostics(self, train_result, val_result) -> Dict:
        diag = {
            "no_entry_reasons": {},
            "stop_loss_count": 0,
            "take_profit_count": 0,
            "opposite_signal_count": 0,
            "end_exit_count": 0,
            "total_fees": 0,
        }
        if isinstance(train_result, dict):
            exits = train_result.get("exit_reasons", {})
            diag["stop_loss_count"] = exits.get("stop_loss", 0)
            diag["take_profit_count"] = exits.get("take_profit", 0)
            diag["total_fees"] = train_result.get("total_fees", 0)
        return diag


class FactorAudit:
    """因子审计系统 —— 确保回测无未来函数"""

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self._audit_items: List[FactorAuditItem] = []
        self._build_default_audit()

    def _build_default_audit(self):
        self._audit_items = [
            FactorAuditItem(
                factor="price",
                label="OHLCV 行情数据",
                status=FactorAuditStatus.ENABLED,
                used_in_backtest=True,
                required_timestamp="trade_date",
                message="价格 K 线按时间顺序回放；信号只使用当前及更早数据。",
            ),
            FactorAuditItem(
                factor="financial",
                label="财务报表数据",
                status=FactorAuditStatus.LATEST_REVISION_BLOCKED if self.strict_mode else FactorAuditStatus.ENABLED,
                used_in_backtest=not self.strict_mode,
                required_timestamp="ann_date 或 f_ann_date",
                message="严格模式下需按财报公告日期对齐，避免使用未来财务数据。",
            ),
            FactorAuditItem(
                factor="industry",
                label="行业分类数据",
                status=FactorAuditStatus.ENABLED,
                used_in_backtest=True,
                required_timestamp="纳入日期",
                message="行业分类变更频率低，允许使用。",
            ),
            FactorAuditItem(
                factor="north_flow",
                label="北向资金数据",
                status=FactorAuditStatus.ENABLED,
                used_in_backtest=True,
                required_timestamp="trade_date",
                message="北向资金为日频数据，按交易日对齐。",
            ),
            FactorAuditItem(
                factor="margin",
                label="融资融券数据",
                status=FactorAuditStatus.ENABLED,
                used_in_backtest=True,
                required_timestamp="trade_date",
                message="两融数据为日频，按交易日对齐。",
            ),
            FactorAuditItem(
                factor="analyst",
                label="分析师预测数据",
                status=FactorAuditStatus.LATEST_REVISION_BLOCKED if self.strict_mode else FactorAuditStatus.ENABLED,
                used_in_backtest=not self.strict_mode,
                required_timestamp="research_date",
                message="严格模式下禁用分析师预测，避免使用未来一致预期。",
            ),
            FactorAuditItem(
                factor="index_weight",
                label="指数权重调整",
                status=FactorAuditStatus.ENABLED,
                used_in_backtest=True,
                required_timestamp="effective_date",
                message="指数调整有明确的生效日期。",
            ),
            FactorAuditItem(
                factor="news_sentiment",
                label="新闻情绪数据",
                status=FactorAuditStatus.UNAVAILABLE,
                used_in_backtest=False,
                required_timestamp="publish_time",
                message="当前无按发布时间回放的新闻情绪数据，严格模式下禁用。",
            ),
        ]

    def get_audit(self) -> List[FactorAuditItem]:
        return self._audit_items

    def get_enabled_factors(self) -> List[str]:
        return [item.factor for item in self._audit_items if item.used_in_backtest]

    def get_blocked_factors(self) -> List[str]:
        return [item.factor for item in self._audit_items if not item.used_in_backtest]

    def audit_summary(self) -> Dict:
        enabled = sum(1 for item in self._audit_items if item.used_in_backtest)
        blocked = len(self._audit_items) - enabled
        return {
            "strict_mode": self.strict_mode,
            "total_factors": len(self._audit_items),
            "enabled": enabled,
            "blocked": blocked,
            "enabled_list": self.get_enabled_factors(),
            "blocked_list": self.get_blocked_factors(),
        }

    def validate_timestamp(
        self,
        factor_name: str,
        data_timestamp,
        reference_timestamp,
    ) -> Tuple[bool, str]:
        """验证因子数据时间戳是否在参考时间之前"""
        item = next((i for i in self._audit_items if i.factor == factor_name), None)
        if item is None:
            return True, f"因子 {factor_name} 不在审计列表中"

        if not item.used_in_backtest:
            return False, f"因子 {factor_name} 在严格模式下被禁用: {item.message}"

        try:
            data_ts = pd.Timestamp(data_timestamp)
            ref_ts = pd.Timestamp(reference_timestamp)
            if data_ts > ref_ts:
                return False, f"因子 {factor_name} 时间戳 {data_ts} 晚于参考时间 {ref_ts}，存在未来函数风险"
            return True, "OK"
        except Exception as e:
            return False, f"时间戳验证异常: {e}"


def summarize_rounds(rounds: List[WalkForwardRound]) -> Dict:
    """汇总推进式回测结果"""
    if not rounds:
        return {
            "rounds": 0,
            "message": "无回测轮次",
            "stable_rounds": 0,
            "fragile_rounds": 0,
            "insufficient_rounds": 0,
            "median_return": 0,
            "worst_return": 0,
            "median_sharpe": 0,
            "median_win_rate": 0,
            "worst_drawdown": 0,
            "median_profit_factor": 0,
            "total_trades": 0,
        }

    val_metrics = [r.validation_metrics for r in rounds if r.validation_metrics]

    returns = [m.get("total_return", 0) for m in val_metrics]
    sharpes = [m.get("sharpe_ratio", 0) for m in val_metrics]
    win_rates = [m.get("win_rate", 0) for m in val_metrics]
    drawdowns = [m.get("max_drawdown", 0) for m in val_metrics]
    profit_factors = [m.get("profit_factor", 0) for m in val_metrics]

    def _median(arr):
        if not arr:
            return 0
        s = sorted(arr)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

    return {
        "rounds": len(rounds),
        "stable_rounds": sum(1 for r in rounds if r.validation_status == ValidationStatus.STABLE),
        "fragile_rounds": sum(1 for r in rounds if r.validation_status == ValidationStatus.FRAGILE),
        "insufficient_rounds": sum(1 for r in rounds if r.validation_status == ValidationStatus.INSUFFICIENT_TRADES),
        "median_return": _median(returns),
        "worst_return": min(returns) if returns else 0,
        "median_sharpe": _median(sharpes),
        "median_win_rate": _median(win_rates),
        "worst_drawdown": max(drawdowns) if drawdowns else 0,
        "median_profit_factor": _median(profit_factors),
        "total_trades": sum(m.get("total_trades", 0) for m in val_metrics),
    }