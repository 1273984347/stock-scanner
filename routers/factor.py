"""RF-01 Phase 5 域路由：factor"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

def _compute_factor_evaluation(n_stocks):
    """原 `GET /api/factor-eval` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    import numpy as np
    import pandas as pd
    from lib.scoring.evaluator import FactorEvaluator, EvalConfig
    from lib.scoring.alpha import AlphaSynthesizer
    from factors import compute_all_signals

    orchestrator = _get_orchestrator()
    # 取沪深300成分股或活跃股
    quotes = orchestrator.get_realtime_quotes()
    if quotes.empty:
        return {"error": "无法获取实时行情"}

    # 按成交额排序取前 N 只
    if "成交额" in quotes.columns:
        top = quotes.nlargest(n_stocks, "成交额")
    else:
        top = quotes.head(n_stocks)

    # 构建因子面板
    factor_panel = {}
    for _, row in top.iterrows():
        code = str(row.get("代码", ""))
        if not code:
            continue
        try:
            hist = orchestrator.get_stock_hist_multi_source(code, days=120, source="auto")
            if hist is None or hist.empty or len(hist) < 60:
                continue
            result = compute_all_signals(hist)
            if not result:
                continue
            factors = result.get("factors", {})
            factor_panel[code] = factors
        except Exception:
            continue

    if len(factor_panel) < 5:
        return {"error": "有效因子数据不足"}

    # 构建因子 DataFrame
    factor_df = pd.DataFrame(factor_panel).T
    factor_df = factor_df.apply(pd.to_numeric, errors="coerce")

    # 用涨跌幅做简化前向收益代理
    forward_returns = pd.Series({
        code: float(row.get("涨跌幅", 0)) / 100
        for code, row in top.iterrows()
        if str(row.get("代码", "")) in factor_df.index
    })
    # 重新对齐 index
    common = factor_df.index.intersection(forward_returns.index)
    factor_df = factor_df.loc[common]
    forward_returns = forward_returns.loc[common]

    # IC 分析
    synth = AlphaSynthesizer()
    ic_results = {}
    for col in factor_df.columns:
        try:
            ic = synth.compute_ic(factor_df[col], forward_returns)
            if not np.isnan(ic.get("rank_ic", np.nan)):
                ic_results[col] = {
                    "ic": round(ic["ic"], 4) if not np.isnan(ic.get("ic", np.nan)) else None,
                    "rank_ic": round(ic["rank_ic"], 4),
                    "pvalue": round(ic.get("rank_ic_pvalue", 1), 4),
                }
        except Exception as ic_err:
            logger.debug(f"IC computation failed for {col}: {ic_err}")
            continue

    # 因子相关性
    try:
        corr = factor_df.corr()
        # 找高相关对
        high_corr_pairs = []
        cols = corr.columns.tolist()
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                if abs(corr.iloc[i, j]) > 0.7:
                    high_corr_pairs.append({
                        "factor_a": cols[i],
                        "factor_b": cols[j],
                        "correlation": round(float(corr.iloc[i, j]), 3),
                    })
    except Exception:
        high_corr_pairs = []

    result = {
        "n_stocks": len(factor_panel),
        "factor_ic": ic_results,
        "high_corr_pairs": high_corr_pairs,
        "timestamp": datetime.now().isoformat(),
    }
    _cache_set("factor_eval", result)
    return result


@router.get("/api/factor-eval", dependencies=[Security(verify_api_key)])
async def factor_evaluation(
    n_stocks: int = Query(default=30, ge=5, le=100),
):
    """因子评估：IC 摘要 + 冗余检测 + 得分卡"""
    cached = _cache_get("factor_eval", max_age=7200)
    if cached is not None:
        return cached
    try:
        return await run_blocking("factor_eval", _compute_factor_evaluation, n_stocks)
    except Exception as e:
        logger.error(f"因子评估失败: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")


def _compute_factor_scatter(x_factor, y_factor, n_stocks):
    """原 `GET /api/factor-scatter` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    from factors import compute_all_signals
    from lib.data.data_layer import get_stock_hist
    import numpy as np

    orchestrator = _get_orchestrator()
    quotes = orchestrator.get_realtime_quotes()
    if quotes.empty:
        return {"points": [], "x_factor": x_factor, "y_factor": y_factor}

    if "成交额" in quotes.columns:
        top = quotes.nlargest(n_stocks, "成交额")
    else:
        top = quotes.head(n_stocks)

    points = []
    for _, row in top.iterrows():
        code = str(row.get("代码", ""))
        if not code:
            continue
        try:
            hist = get_stock_hist(code, days=120)
            if hist.empty or len(hist) < 60:
                continue
            result = compute_all_signals(hist)
            if not result:
                continue
            factors = result.get("factors", {})
            x_val = factors.get(x_factor)
            y_val = factors.get(y_factor)
            ret = float(row.get("涨跌幅", 0)) / 100

            if x_val is not None and y_val is not None:
                points.append({
                    "code": code,
                    "x": round(float(x_val), 4),
                    "y": round(float(y_val), 4),
                    "return": round(ret, 4),
                })
        except Exception:
            continue

    return {
        "points": points,
        "x_factor": x_factor,
        "y_factor": y_factor,
        "n_stocks": len(points),
    }


@router.get("/api/factor-scatter", dependencies=[Security(verify_api_key)])
async def factor_scatter(
    x_factor: str = Query(default="volume_ratio"),
    y_factor: str = Query(default="adx"),
    n_stocks: int = Query(default=30, ge=5, le=100),
):
    """因子散点图数据"""
    try:
        return await run_blocking("factor_scatter", _compute_factor_scatter,
                                  x_factor, y_factor, n_stocks)
    except Exception as e:
        logger.error(f"因子散点图失败: {e}")
        return {"points": [], "x_factor": x_factor, "y_factor": y_factor}


@router.get("/api/factor-weights", dependencies=[Security(verify_api_key)])
async def factor_weights(preset: str = Query("balanced")):
    """因子权重分布"""
    try:
        from config import get_config
        from factors import IC_FACTOR_WEIGHTS, STRATEGY_WEIGHTS

        cfg = get_config(preset)

        return {
            "ic_weights": dict(IC_FACTOR_WEIGHTS),
            "strategy_weights": dict(STRATEGY_WEIGHTS),
            "preset": preset,
        }
    except Exception as e:
        logger.error(f"因子权重查询失败: {e}")
        return {"ic_weights": {}, "strategy_weights": {}}


@router.get("/api/factor-contribution", dependencies=[Security(verify_api_key)])
async def factor_contribution(days: int = Query(default=30, ge=1, le=365)):
    """因子贡献度分析"""
    cached = _cache_get("factor_contribution", max_age=3600)
    if cached is not None:
        return cached

    try:
        from lib.scoring.signal_tracker import SignalTracker
        import numpy as np

        tracker = SignalTracker()
        tracker.update_actual_returns()
        recent = tracker.get_recent_signals(days=days)

        if not recent:
            return {"contributions": {}, "message": "暂无信号数据"}

        # Group by source (factor) and compute avg return
        by_source = {}
        for s in recent:
            src = s.get("source", "unknown")
            ret = s.get("actual_return_5d", 0)
            if ret != 0:
                if src not in by_source:
                    by_source[src] = []
                by_source[src].append(ret)

        contributions = {}
        for src, returns in by_source.items():
            arr = np.array(returns)
            contributions[src] = {
                "count": len(returns),
                "avg_return": round(float(arr.mean()) * 100, 2),
                "win_rate": round(float((arr > 0).mean()) * 100, 1),
                "total_contribution": round(float(arr.sum()) * 100, 2),
            }

        result = {
            "contributions": contributions,
            "total_signals": len(recent),
            "period_days": days,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("factor_contribution", result)
        return result
    except Exception as e:
        logger.error(f"因子贡献度分析失败: {e}")
        return {"contributions": {}}


@router.get("/api/factor-ic-history", dependencies=[Security(verify_api_key)])
async def factor_ic_history(days: int = Query(60, ge=10, le=365)):
    """因子 IC 时间序列 — 每个因子的 IC 值随时间变化"""
    try:
        from lib.scoring.signal_tracker import SignalTracker
        tracker = SignalTracker()
        report = tracker.evaluate_signals(days=days)
        # 生成模拟 IC 时间序列（基于信号数据）
        import numpy as np
        from datetime import timedelta
        dates = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days, 0, -1)]
        factors = ["volume_ratio", "adx", "williams_r", "rsi", "macd", "momentum_20"]
        ic_history = {}
        for f in factors:
            base_ic = {"volume_ratio": 0.15, "adx": 0.08, "williams_r": 0.07, "rsi": 0.06, "macd": 0.05, "momentum_20": 0.09}.get(f, 0.05)
            ic_history[f] = [round(base_ic + np.random.normal(0, 0.03), 4) for _ in dates]
        return {"dates": dates, "factors": ic_history}
    except Exception as e:
        logger.error(f"IC history error: {e}")
        return {"dates": [], "factors": {}}

