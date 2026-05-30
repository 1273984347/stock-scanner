"""统一回测接口 —— 渐进合并三套引擎

提供统一的 BacktestRunner 接口，内部根据需求选择引擎：
- simple: 单股信号测试（backtest_engine.py）
- full: 全市场并行回测（backtest_full.py）
- rebalance: 月频调仓回测（backtest_rebalance.py）
- factor: 因子回测（backtest.py run_factor_backtest）

使用方式：
    from lib.backtest_unified import BacktestRunner
    runner = BacktestRunner()
    result = runner.run(mode="rebalance", symbols=["600519"], days=300)
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

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
    factor_config = None


class BacktestRunner:
    """统一回测运行器"""

    def run(self, request: BacktestRequest = None, **kwargs) -> Optional[Dict]:
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
        else:
            logger.error(f"未知回测模式: {mode}")
            return None

    def _run_simple(self, req: BacktestRequest) -> Optional[Dict]:
        """单股信号测试"""
        from backtest.engine import run_backtest
        result = run_backtest(
            symbols=req.symbols,
            days=req.days,
        )
        return self._normalize_result(result, "simple")

    def _run_full(self, req: BacktestRequest) -> Optional[Dict]:
        """全市场并行回测"""
        from backtest.full import run_full_backtest
        result = run_full_backtest(
            symbols=req.symbols,
            days=req.days,
            max_workers=req.max_workers,
        )
        return self._normalize_result(result, "full")

    def _run_rebalance(self, req: BacktestRequest) -> Optional[Dict]:
        """月频调仓回测"""
        from backtest.rebalance import run_rebalance_backtest
        result = run_rebalance_backtest(
            symbols=req.symbols,
            days=req.days,
            rebalance_period=req.rebalance_period,
            stop_loss_atr_mult=req.stop_loss_atr_mult,
            max_hold_days=req.max_hold_days,
            disable_loss_breaker=req.disable_loss_breaker,
            factor_config=req.factor_config,
        )
        return self._normalize_result(result, "rebalance")

    def _run_factor(self, req: BacktestRequest) -> Optional[Dict]:
        """因子回测"""
        from backtest.core import run_factor_backtest
        result = run_factor_backtest(
            req.symbols,
            days=req.days,
            stop_loss_atr_mult=req.stop_loss_atr_mult,
            take_profit_atr_mult=req.take_profit_atr_mult,
            max_hold_days=req.max_hold_days,
            preset=req.preset,
        )
        return self._normalize_result(result, "factor")

    def _normalize_result(self, result: Optional[Dict], mode: str) -> Optional[Dict]:
        """标准化结果格式"""
        if result is None:
            return None

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

        return {
            "mode": mode,
            "metrics": metrics,
            "trade_count": result.get("trade_count", result.get("trade_count", 0)),
            "trades": result.get("trades", result.get("trade_log", [])),
            "exit_reasons": result.get("exit_reasons", {}),
            "equity_curve": result.get("equity_curve", []),
            "benchmark": result.get("benchmark", {}),
            "report": result.get("report", ""),
        }
