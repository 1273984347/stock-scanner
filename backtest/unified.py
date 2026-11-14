"""统一回测入口 —— 全项目**唯一**对外的回测调用口（RF-01 Phase 3）

提供统一的 BacktestRunner 接口，内部根据 mode 选择引擎：
- simple: 单股信号测试（backtest.engine.run_backtest）
- full: 全市场并行回测（backtest.full.run_full_backtest）
- rebalance: 月频调仓回测（backtest.engine.run_rebalance_backtest）
- factor: 因子回测（backtest.core.run_factor_backtest）
- walk_forward: Walk-forward 验证（backtest.core.run_walk_forward）
- stress: 压力测试（backtest.stress.run_stress_test）

⚠️ **调用方规约（RF-01 RULES.md §1）**：所有外部代码**只经由此模块**调用回测，
不要直接 import backtest.core / backtest.engine / backtest.full / backtest.rebalance。
这样「回测口径」只有一处定义，改动可被回测对拍基线（scripts/backtest_baseline.py）捕获。

使用方式：
    from backtest.unified import BacktestRunner
    runner = BacktestRunner()
    result = runner.run(mode="rebalance", symbols=["600519"], days=300)

兼容：旧路径 ``lib.monitoring.backtest_unified`` 保留为薄壳 re-export（同一对象）。
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

from backtest.types import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class BacktestRequest:
    """回测请求"""
    mode: str = "factor"  # simple / full / rebalance / factor
    symbols: List[str] = None
    days: int = 300
    preset: str = "balanced"
    # 退出参数
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 4.0
    max_hold_days: int = 10
    # 调仓参数
    rebalance_period: int = 20
    disable_loss_breaker: bool = False
    # 全市场参数
    max_workers: int = 4
    # 因子参数
    factor_config: Optional[object] = None
    # --- 以下为 Phase 3 统一入口补齐（2026-11-07）---
    # 补齐原因：api.py 传 confidence_threshold/risk_per_trade_pct，
    # factor_optimizer 传 exit_days_map，multi_market_validator 传 start_date/end_date
    # ⇒ 若统一入口不透传这些参数，**回测口径会静默改变**
    initial_cash: float = 1_000_000
    confidence_threshold: float = 0.3
    risk_per_trade_pct: float = 0.5
    initial_positions: Optional[object] = None
    include_delisted: bool = False
    exit_days_map: Optional[Dict] = None
    start_date: str = ""
    end_date: str = ""
    # 单股信号回测（simple）
    exit_version: str = "v1_atr",
    parallel: bool = False
    multi_signal_entry: bool = False
    sentiment_filter: bool = False
    # walk-forward 参数
    train_days: int = 180
    validation_days: int = 30
    step_days: int = 30
    strategy_name: str = "factor_strategy",
    # stress 参数
    windows: Optional[List] = None


class BacktestRunner:
    """统一回测运行器"""

    def run(self, request: BacktestRequest = None, **kwargs) -> Optional[BacktestResult]:
        """运行回测

        Args:
            request: BacktestRequest 对象
            **kwargs: 直接传参，会覆盖 request 的值

        Returns:
            dict with standardized keys:
            - metrics: {total_return, sharpe, max_drawdown, win_rate}
            - trades: list of trade dicts
            - exit_reasons: dict
            - equity_curve: list of {date, nav} (if available)
        """
        if request is None:
            request = BacktestRequest(**kwargs)
        else:
            for k, v in kwargs.items():
                setattr(request, k, v)

        if not request.symbols:
            logger.error("未指定股票代码")
            return None

        mode = request.mode
        if mode == "simple":
            return self._run_simple(request)
        elif mode == "full":
            return self._run_full(request)
        elif mode == "rebalance":
            return self._run_rebalance(request)
        elif mode == "factor":
            return self._run_factor(request)
        elif mode == "walk_forward":
            return self._run_walk_forward(request)
        elif mode == "stress":
            return self._run_stress(request)
        else:
            logger.error(f"未知回测模式: {mode}")
            return None

    def _run_simple(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """单股信号测试"""
        from backtest.engine import run_backtest
        result = run_backtest(
            # ⚠️ stock_codes 是**必填位置参数**，symbols 只是 backward-compat 别名
            #    （engine.py:635-637）⇒ 只传 symbols 会报 missing 1 required positional argument
            stock_codes=req.symbols,
            symbols=req.symbols,
            days=req.days,
            # B2：BacktestRequest 早有 start_date 字段却没透传，引擎会用
            # datetime.now()-days 推窗口 ⇒ 回测口径逐日漂移（跨日交易数 6→5）。
            # 默认 "" 时引擎仍走原逻辑，生产行为不变；显式传值才锚定窗口。
            start_date=req.start_date,
            exit_version=req.exit_version,
            parallel=req.parallel,
            max_workers=req.max_workers,
            multi_signal_entry=req.multi_signal_entry,
            sentiment_filter=req.sentiment_filter,
            include_delisted=req.include_delisted,
        )
        return self._normalize_result(result, "simple")

    def _run_full(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """全市场并行回测"""
        from backtest.full import run_full_backtest
        result = run_full_backtest(
            symbols=req.symbols,
            days=req.days,
            max_workers=req.max_workers,
            # run_full_backtest 把 **kwargs 透传给 engine.run_backtest（full.py:491）
            start_date=req.start_date,
        )
        return self._normalize_result(result, "full")

    def _run_rebalance(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """月频调仓回测"""
        from backtest.rebalance import run_rebalance_backtest
        result = run_rebalance_backtest(
            symbols=req.symbols,
            days=req.days,
            initial_cash=req.initial_cash,
            rebalance_period=req.rebalance_period,
            stop_loss_atr_mult=req.stop_loss_atr_mult,
            take_profit_atr_mult=req.take_profit_atr_mult,
            max_hold_days=req.max_hold_days,
            confidence_threshold=req.confidence_threshold,
            disable_loss_breaker=req.disable_loss_breaker,
            factor_config=req.factor_config,
            max_workers=req.max_workers,
            exit_days_map=req.exit_days_map,
        )
        return self._normalize_result(result, "rebalance")

    def _run_factor(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """因子回测"""
        from backtest.core import run_factor_backtest
        result = run_factor_backtest(
            req.symbols,
            days=req.days,
            initial_cash=req.initial_cash,
            stop_loss_atr_mult=req.stop_loss_atr_mult,
            take_profit_atr_mult=req.take_profit_atr_mult,
            max_hold_days=req.max_hold_days,
            confidence_threshold=req.confidence_threshold,
            risk_per_trade_pct=req.risk_per_trade_pct,
            preset=req.preset,
            initial_positions=req.initial_positions,
            include_delisted=req.include_delisted,
        )
        return self._normalize_result(result, "factor")

    def _run_walk_forward(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """Walk-forward 验证回测（Phase 3 新增）"""
        from backtest.core import run_walk_forward
        result = run_walk_forward(
            req.symbols,
            days=req.days,
            train_days=req.train_days,
            validation_days=req.validation_days,
            step_days=req.step_days,
            strategy_name=req.strategy_name,
        )
        return self._normalize_result(result, "walk_forward")

    def _run_stress(self, req: BacktestRequest) -> Optional[BacktestResult]:
        """压力测试回测（Phase 3 新增）"""
        from backtest.stress import run_stress_test
        result = run_stress_test(
            req.symbols,
            initial_cash=req.initial_cash,
            windows=req.windows,
        )
        return self._normalize_result(result, "stress")

    def _normalize_result(self, result: Optional[Dict], mode: str) -> Optional[BacktestResult]:
        """标准化结果格式"""
        if result is None:
            return None

        # ⚠️ 非 dict 结果（如 stress 模式返回 StressTestReport **对象**）
        #    ⇒ 直接调 result.get 会 AttributeError。此处统一转成 dict 视图。
        if not isinstance(result, dict):
            if hasattr(result, "__dict__"):
                result = {k: v for k, v in vars(result).items()
                          if not k.startswith("_")}
            else:
                result = {"_raw": result}

        # 提取 metrics
        metrics_raw = result.get("metrics")
        if hasattr(metrics_raw, '__dict__'):
            # PerformanceMetrics 对象
            metrics = {
                "total_return": getattr(metrics_raw, 'total_return', 0),
                "sharpe": getattr(metrics_raw, 'sharpe_ratio', getattr(metrics_raw, 'sharpe', 0)),
                "max_drawdown": getattr(metrics_raw, 'max_drawdown', 0),
                "win_rate": getattr(metrics_raw, 'win_rate', 0),
            }
        elif isinstance(metrics_raw, dict):
            metrics = metrics_raw
        else:
            # 从顶层提取
            metrics = {
                "total_return": result.get("total_return", 0),
                "sharpe": result.get("sharpe", result.get("final_sharpe", 0)),
                "max_drawdown": result.get("max_drawdown", result.get("final_drawdown", 0)),
                "win_rate": result.get("win_rate", 0),
            }

        return BacktestResult(
            mode=mode,
            metrics=metrics,
            trade_count=result.get("trade_count", 0),
            trades=result.get("trades", result.get("trade_log", [])),
            exit_reasons=result.get("exit_reasons", {}),
            equity_curve=result.get("equity_curve", []),
            benchmark=result.get("benchmark", {}),
            config=result.get("config", {}),
            # ⚠️ 保留**原始结果 dict** —— 归一化会丢 cost_summary / perf_df 等字段，
            #    而 api.py 的 /api/backtest 响应依赖它们 ⇒ 丢了就改变对外契约
            raw=result,
        )
