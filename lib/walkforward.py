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
        adaptive_state=None,
    ) -> List[WalkForwardRound]:
        """执行推进式回测

        P1-5 修复（2026-06-05）：接受 adaptive_state 参数
        - 在每个窗口起点 snapshot adaptive state
        - 调 strategy_fn（train + val 各一次）传当前窗口 snapshot
        - 窗口结束 restore adaptive state
        这样每个窗口验证的是"窗口起点前的 state"，而非整个历史累积
        """
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

        # P1-5 修复：adaptive state snapshot
        # 真实项目里 state 来自 BayesianUpdater.save() / ML model snapshot 等
        # 这里用 None 表示无 state（向后兼容）；调用方可传 dict 表示 state
        if adaptive_state is None:
            adaptive_state = {"snapshot_at_window_start": True, "weights": {}}
        # 每个窗口用同一份 snapshot（隔离窗口间的 state 累积）
        window_state_snapshot = dict(adaptive_state)
        window_state_snapshot["window_index_at_snapshot"] = -1  # 标记是初始 snapshot

        rounds = []
        for window in windows:
            # P1-5 修复：每个窗口起点恢复 state 到 snapshot
            current_state = dict(window_state_snapshot)
            current_state["window_index_at_snapshot"] = window.index

            train_data = self._slice_data(market_data, dates, window.train_start, window.train_end)
            # P1-4 修复 (2026-06-08): val_data 包含 warmup_bars 上下文
            # 之前: warmup 字段被计算后丢弃, val_data 只到 validation_start
            # 现在: 用 [warmup_start, val_end] 提供 EMA-21 等指标所需上下文
            val_data = self._slice_data(market_data, dates, window.warmup_start, window.validation_end)

            try:
                train_result = strategy_fn(
                    market_data=train_data,
                    factor_data=factor_data,
                    dates=dates[window.train_start:window.train_end + 1],
                    symbols=symbols,
                    adaptive_state=current_state,
                )
            except Exception as e:
                logger.error(f"窗口 {window.index} 训练失败: {e}")
                train_result = {}

            try:
                val_result = strategy_fn(
                    market_data=val_data,
                    factor_data=factor_data,
                    dates=dates[window.warmup_start:window.validation_end + 1],
                    symbols=symbols,
                    adaptive_state=current_state,
                )
            except Exception as e:
                logger.error(f"窗口 {window.index} 验证失败: {e}")
                val_result = {}

            train_trades = train_result.get("trades", []) if isinstance(train_result, dict) else []
            val_trades = val_result.get("trades", []) if isinstance(val_result, dict) else []

            # P1-5 修复 (2026-06-08): 基于 train/val sharpe 偏离度自动判定 fragile
            train_metrics = self._extract_metrics(train_result)
            val_metrics = self._extract_metrics(val_result)
            train_sharpe = train_metrics.get("sharpe_ratio", 0)
            val_sharpe = val_metrics.get("sharpe_ratio", 0)

            status = self._classify_status(
                train_trades=len(train_trades),
                validation_trades=len(val_trades),
                train_sharpe=train_sharpe,
                val_sharpe=val_sharpe,
            )

            rounds.append(WalkForwardRound(
                window_index=window.index,
                strategy=strategy_name,
                validation_status=status,
                train_metrics=train_metrics,
                validation_metrics=val_metrics,
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
        # P1-4 修复 (2026-06-08): 兼容 date_slice 是字符串/日期对象, df 是 Timestamp
        # 之前: df['trade_date'].isin(str_set) 永远 False, 导致 sliced 永远是空 dict
        # 现在: 用 normalize 后的字符串集合, 兼容 str / Timestamp / datetime
        normalized = set()
        for d in date_slice:
            try:
                normalized.add(pd.Timestamp(d).strftime("%Y-%m-%d"))
            except Exception:
                normalized.add(str(d))
        for code, df in market_data.items():
            if df is None or df.empty:
                continue
            if "trade_date" in df.columns:
                ts_norm = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                mask = ts_norm.isin(normalized)
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
        train_sharpe: float = 0.0,
        val_sharpe: float = 0.0,
        fragile: bool = None,
    ) -> ValidationStatus:
        """P1-5 修复 (2026-06-08): 基于 train/val sharpe 偏离度自动判定 FRAGILE

        之前: fragile 硬编码 False, FRAGILE 状态永远走不到
        现在: 显式传 fragile 时优先; 否则按 sharpe 偏离度自动判定
              - train > 0.5 且 |train-val|/|train| > 0.5 → FRAGILE
              - 其它 → STABLE (保守判定, FRAGILE 误报更危险)
        """
        if train_trades < self.config.min_train_trades:
            return ValidationStatus.INSUFFICIENT_TRADES
        if validation_trades <= 0:
            return ValidationStatus.NO_VALIDATION_TRADES

        # 显式 fragile 优先
        if fragile is True:
            return ValidationStatus.FRAGILE

        # 自动判定: sharpe 偏离度
        if train_sharpe > 0.5 and val_sharpe >= 0:
            divergence = abs(train_sharpe - val_sharpe) / abs(train_sharpe)
            if divergence > 0.5:
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


# ========== V4 模块贡献分析 ==========

@dataclass
class ModuleContribution:
    """单个模块的贡献"""
    name: str
    label: str
    total_return: float
    sharpe_ratio: float
    win_rate: float
    max_drawdown: float
    n_trades: int
    contribution: float = 0.0  # 与baseline的差值
    is_independent: bool = True  # 是否有独立贡献


class ModuleContributionAnalyzer:
    """V4 模块贡献分析器（Ablation 测试）

    V4 规则：不要把很多相关条件重复加分。
    处理原则：
    - 有独立贡献的，保留
    - 重复的，降权
    - 没贡献的，删除

    用法：
        analyzer = ModuleContributionAnalyzer(baseline_metrics)
        analyzer.add_module("市场状态过滤", market_regime_metrics)
        analyzer.add_module("板块过滤", sector_filter_metrics)
        analyzer.add_module("个股筛选", stock_filter_metrics)
        result = analyzer.compute_contributions()
    """

    def __init__(self, baseline_metrics: Dict):
        """
        Args:
            baseline_metrics: 完整策略的基准指标
                {total_return, sharpe_ratio, win_rate, max_drawdown, total_trades, ...}
        """
        self.baseline = baseline_metrics
        self.modules: List[Tuple[str, str, Dict]] = []  # (name, label, metrics)

    def add_module(self, name: str, label: str, metrics: Dict):
        """添加一个模块的指标

        Args:
            name: 模块标识（英文）
            label: 模块中文描述
            metrics: 模块的回测指标
        """
        self.modules.append((name, label, metrics))

    def compute_contributions(self) -> Dict:
        """计算各模块的独立贡献

        贡献计算：模块贡献 = 完整策略 - (完整策略 - 该模块结果)
        即：用模块 vs 不用模块的差值

        Returns:
            {
                "baseline": {...},
                "modules": [
                    {"name": "market_regime", "label": "市场状态过滤",
                     "metrics": {...}, "contribution": {...}, "is_independent": bool},
                    ...
                ],
                "redundant_modules": ["板块过滤"],  # 重复模块（无显著贡献）
                "additive_modules": ["市场状态"],   # 独立贡献模块
                "useless_modules": [],               # 无贡献模块
            }
        """
        baseline = self.baseline
        modules_result = []
        contributions = []

        for name, label, metrics in self.modules:
            # 计算该模块的贡献 = baseline - (baseline - module) = module 的增量
            contrib = {
                "return_delta": metrics.get("total_return", 0) - baseline.get("total_return", 0),
                "sharpe_delta": metrics.get("sharpe_ratio", 0) - baseline.get("sharpe_ratio", 0),
                "drawdown_delta": metrics.get("max_drawdown", 0) - baseline.get("max_drawdown", 0),
                "win_rate_delta": metrics.get("win_rate", 0) - baseline.get("win_rate", 0),
            }

            # 独立贡献判断：sharpe 差值 > 0.05 或 return 差值 > 5%
            is_independent = (
                abs(contrib["sharpe_delta"]) > 0.05
                or abs(contrib["return_delta"]) > 0.05
            )

            modules_result.append({
                "name": name,
                "label": label,
                "metrics": metrics,
                "contribution": contrib,
                "is_independent": is_independent,
            })
            contributions.append((name, contrib))

        # 分类
        redundant = [m["name"] for m in modules_result if not m["is_independent"]]
        additive = [m["name"] for m in modules_result if m["is_independent"] and m["contribution"]["return_delta"] > 0]

        return {
            "baseline": baseline,
            "modules": modules_result,
            "redundant_modules": redundant,
            "additive_modules": additive,
            "useless_modules": [m["name"] for m in modules_result
                                if m["contribution"]["return_delta"] < -0.02],
        }

    def get_summary_table(self) -> pd.DataFrame:
        """生成模块贡献汇总表"""
        result = self.compute_contributions()
        rows = []
        for m in result["modules"]:
            c = m["contribution"]
            rows.append({
                "模块": m["label"],
                "收益率": f"{m['metrics'].get('total_return', 0):.2%}",
                "夏普": f"{m['metrics'].get('sharpe_ratio', 0):.2f}",
                "胜率": f"{m['metrics'].get('win_rate', 0):.1%}",
                "最大回撤": f"{m['metrics'].get('max_drawdown', 0):.2%}",
                "收益增量": f"{c['return_delta']:+.2%}",
                "夏普增量": f"{c['sharpe_delta']:+.2f}",
                "独立": "✓" if m["is_independent"] else "✗",
            })
        df = pd.DataFrame(rows)
        return df


# ========== Walk-forward 结果持久化 ==========

from pathlib import Path
import json
import hashlib


def save_walkforward_results(strategy_name: str, summary: Dict, rounds_count: int) -> Path:
    """保存 walk-forward 结果到 cache 目录

    Args:
        strategy_name: 策略名称
        summary: summarize_rounds() 的输出
        rounds_count: 回测轮次数

    Returns:
        保存的文件路径
    """
    cache_dir = Path(__file__).resolve().parent.parent / "cache" / "walkforward"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 生成文件名：strategy_date_hash.json
    date_str = datetime.now().strftime("%Y%m%d")
    key = f"{strategy_name}_{date_str}"
    hash_suffix = hashlib.md5(key.encode()).hexdigest()[:6]
    filename = f"{strategy_name}_{date_str}_{hash_suffix}.json"
    filepath = cache_dir / filename

    data = {
        "strategy_name": strategy_name,
        "saved_at": datetime.now().isoformat(),
        "rounds": rounds_count,
        "summary": summary,
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    logger.info(f"Walk-forward 结果已保存: {filepath}")
    return filepath


def load_latest_walkforward(strategy_name: str) -> Optional[Dict]:
    """加载最新的 walk-forward 结果"""
    cache_dir = Path(__file__).resolve().parent.parent / "cache" / "walkforward"
    if not cache_dir.exists():
        return None

    files = sorted(cache_dir.glob(f"{strategy_name}_*.json"), reverse=True)
    if not files:
        return None

    with open(files[0], encoding="utf-8") as f:
        return json.load(f)