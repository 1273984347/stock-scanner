"""RF-01 Phase 5 域路由：backtest

Phase 6 第 2 批：三个重计算端点的阻塞段原样搬成同步函数，
端点改经 `lib/infra/async_offload.py::run_blocking(consumer, fn, ...)`。
**只改调度方式，不改可观测结果**：响应字段/顺序、异常映射、async 签名全部保留。
flag 默认 off 时是同步直调，与改造前逐字节等价（RULES §1.7 T1–T4）。
"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()


def _compute_factor_backtest(symbols, days, stop_loss_atr_mult,
                             take_profit_atr_mult, max_hold_days,
                             confidence_threshold, risk_per_trade_pct, preset):
    """原 `POST /api/backtest` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    from backtest.unified import BacktestRunner
    sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]
    if len(sym_list) > 50:
        raise HTTPException(status_code=400, detail="最多支持50只股票同时回测")
    # 统一入口（RF-01 Phase 3）：所有回测调用只经 backtest.unified
    unified = BacktestRunner().run(
        mode="factor",
        symbols=sym_list, days=days,
        stop_loss_atr_mult=stop_loss_atr_mult,
        take_profit_atr_mult=take_profit_atr_mult,
        max_hold_days=max_hold_days,
        confidence_threshold=confidence_threshold,
        risk_per_trade_pct=risk_per_trade_pct,
        preset=preset,
    )
    if not unified:
        return {"error": "回测无结果"}
    # ⚠️ 取 raw（原始 dict）而非归一化结果 —— 本端点响应依赖
    #    cost_summary / perf_df 等非归一化字段，取归一化结果会丢字段 = 改契约
    result = unified.raw if unified.raw is not None else unified.to_dict()
    equity_curve = _extract_equity_curve(result)

    return {
        "metrics": result.get("metrics"),
        "benchmark": result.get("benchmark"),
        "trade_count": result.get("trade_count"),
        "exit_reasons": result.get("exit_reasons"),
        "cost_summary": result.get("cost_summary"),
        "equity_curve": equity_curve,
        "trades": result.get("trades", [])[:200],  # 限制交易记录数
    }


def _compute_compare(symbols, days):
    """原 `POST /api/backtest/compare` try 块内容，逐字搬迁。"""
    from backtest.core import run_factor_backtest
    sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]
    if len(sym_list) > 20:
        raise HTTPException(status_code=400, detail="最多支持20只股票")

    presets = ["balanced", "aggressive", "conservative"]
    results = {}
    for preset in presets:
        try:
            result = run_factor_backtest(sym_list, days=days, preset=preset)
            if result:
                results[preset] = {
                    "metrics": result.get("metrics", {}),
                    "equity_curve": _extract_equity_curve(result),
                    "trade_count": result.get("trade_count", 0),
                }
        except Exception as e:
            logger.warning(f"策略 {preset} 回测失败: {e}")

    return {"results": results, "symbols": sym_list, "days": days}


def _compute_walkforward(symbols, train_days, validation_days, step_days):
    """原 `POST /api/walkforward` try 块内容，逐字搬迁。"""
    from lib.strategy.walkforward import WalkForwardBacktest, WalkForwardConfig, summarize_rounds
    from backtest.core import run_factor_backtest

    orchestrator = _get_orchestrator()
    sym_list = [_validate_stock_code(s.strip()) for s in symbols.split(",") if s.strip()]

    # 获取市场数据（并行批量获取，避免 N+1）
    market_data = _batch_get_stock_hist(sym_list, days=train_days + validation_days + 60, source="auto")
    all_dates = set()
    for hist in market_data.values():
        if "date" in hist.columns:
            all_dates.update(hist["date"].tolist())
        elif hist.index.dtype == object:
            all_dates.update(hist.index.tolist())

    if not market_data:
        return {"error": "无法获取市场数据"}

    dates = sorted(all_dates)

    # 简化策略函数：对市场数据跑回测
    def strategy_fn(market_data, factor_data=None, dates=None, symbols=None):
        try:
            syms = symbols or list(market_data.keys())
            result = run_factor_backtest(
                syms,
                days=min(validation_days, 60),
            )
            if not result:
                return {"trades": []}
            # 转换为 walkforward 期望的格式
            metrics = result.get("metrics", {})
            return {
                "trades": [{"pnl": 1}],  # 占位
                "total_return": metrics.get("total_return", 0),
                "sharpe_ratio": metrics.get("sharpe", 0),
                "max_drawdown": metrics.get("max_drawdown", 0),
                "win_rate": metrics.get("win_rate", 0),
            }
        except Exception:
            return {"trades": []}

    # 运行 walk-forward
    wf_config = WalkForwardConfig(
        train_days=train_days,
        validation_days=validation_days,
        step_days=step_days,
    )
    wf = WalkForwardBacktest(wf_config)
    rounds = wf.run(
        strategy_fn=strategy_fn,
        market_data=market_data,
        dates=dates,
        symbols=sym_list,
        strategy_name="factor_backtest",
    )

    summary = summarize_rounds(rounds)

    # 序列化轮次结果
    rounds_data = []
    for r in rounds:
        rounds_data.append({
            "window": r.window_index,
            "strategy": r.strategy,
            "status": r.validation_status.value if hasattr(r.validation_status, "value") else str(r.validation_status),
            "train_metrics": r.train_metrics,
            "validation_metrics": r.validation_metrics,
        })

    return {
        "summary": summary,
        "rounds": rounds_data,
        "config": {
            "train_days": train_days,
            "validation_days": validation_days,
            "step_days": step_days,
            "symbols": sym_list,
        },
    }


@router.post("/api/backtest")
async def run_backtest(
    symbols: str = Query(default="000001,600519,000858"),
    days: int = Query(default=250, ge=60, le=1000),
    stop_loss_atr_mult: float = Query(default=2.0, alias="stop_loss_atr"),
    take_profit_atr_mult: float = Query(default=4.0, alias="take_profit_atr"),
    max_hold_days: int = Query(default=10, ge=1, le=60),
    confidence_threshold: float = Query(default=0.3, ge=0.0, le=1.0),
    risk_per_trade_pct: float = Query(default=0.5, ge=0.1, le=5.0),
    preset: str = Query(default="balanced"),
    _=Security(verify_api_key),
):
    """运行回测"""
    async with _backtest_semaphore:
        try:
            return await run_blocking(
                "backtest", _compute_factor_backtest,
                symbols, days, stop_loss_atr_mult, take_profit_atr_mult,
                max_hold_days, confidence_threshold, risk_per_trade_pct, preset,
            )
        except Exception as e:
            logger.error(f"回测失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")


@router.post("/api/backtest/compare")
async def compare_strategies(
    symbols: str = Query(default="000001,600519"),
    days: int = Query(default=250, ge=60, le=1000),
    _=Security(verify_api_key),
):
    """多策略对比回测"""
    async with _backtest_semaphore:
        try:
            return await run_blocking("backtest_compare", _compute_compare, symbols, days)
        except Exception as e:
            logger.error(f"多策略对比回测失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")


@router.post("/api/walkforward")
async def run_walkforward(
    symbols: str = Query(default="000001,600519"),
    train_days: int = Query(default=120, ge=60, le=500),
    validation_days: int = Query(default=30, ge=10, le=120),
    step_days: int = Query(default=30, ge=10, le=120),
    _=Security(verify_api_key),
):
    """运行 Walk-Forward 滚动前推验证"""
    async with _wf_semaphore:
        try:
            return await run_blocking(
                "walkforward", _compute_walkforward,
                symbols, train_days, validation_days, step_days,
            )
        except Exception as e:
            logger.error(f"Walk-Forward 验证失败: {e}")
            raise HTTPException(status_code=500, detail="内部服务器错误")
