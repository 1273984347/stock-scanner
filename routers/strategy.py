"""RF-01 Phase 5 域路由：strategy"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/strategy/history", dependencies=[Security(verify_api_key)])
async def strategy_history(limit: int = Query(20, ge=1, le=100)):
    """策略回测历史 — 保存的回测结果列表"""
    try:
        history_dir = Path("cache/backtest_history")
        history_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(history_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        results = []
        for f in files:
            try:
                with open(f) as fh:
                    data = json.load(fh)
                    data["id"] = f.stem
                    results.append(data)
            except Exception:
                continue
        return {"history": results}
    except Exception as e:
        logger.error(f"Strategy history error: {e}")
        return {"history": []}


@router.post("/api/strategy/save", dependencies=[Security(verify_api_key)])
async def strategy_save(request: Request):
    """保存回测结果到历史"""
    try:
        data = await request.json()
        history_dir = Path("cache/backtest_history")
        history_dir.mkdir(parents=True, exist_ok=True)
        file_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        with open(history_dir / f"{file_id}.json", "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return {"status": "ok", "id": file_id}
    except Exception as e:
        logger.error(f"Strategy save error: {e}")
        raise HTTPException(500, str(e))


@router.get("/api/strategy/recommend", dependencies=[Security(verify_api_key)])
async def strategy_recommend(symbols: str = Query("", description="股票代码，逗号分隔")):
    """最优参数推荐 — 基于 GA 优化推荐参数组合"""
    try:
        from lib.scoring.factor_optimizer import FactorOptimizer
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else ["600519", "000858"]
        optimizer = FactorOptimizer()
        # 快速评估当前权重
        result = optimizer.evaluate_weights(optimizer._default_weights(), symbols=symbol_list[:5])
        return {
            "recommended": {
                "weights": optimizer._default_weights(),
                "sharpe": result.get("sharpe", 0),
                "max_dd": result.get("max_dd", 0),
                "win_rate": result.get("win_rate", 0),
            },
            "symbols": symbol_list[:5],
            "note": "基于当前因子权重的快速评估。完整优化请运行 factor_optimizer.py",
        }
    except Exception as e:
        logger.error(f"Strategy recommend error: {e}")
        return {"recommended": None, "error": str(e)}


@router.post("/api/ab-test/run", dependencies=[Security(verify_api_key)])
async def ab_test_run(request: Request):
    """运行 A/B 测试"""
    try:
        body = await request.json()
        symbols = body.get("symbols", ["000001", "600519"])
        days = body.get("days", 250)
        group_a = body.get("group_a", {"name": "默认", "stop_loss": 2.0})
        group_b = body.get("group_b", {"name": "优化", "stop_loss": 3.0})
        num_runs = body.get("num_runs", 10)

        from lib.strategy.ab_testing import ab_tester, ABTestConfig
        config = ABTestConfig(
            symbols=symbols,
            days=days,
            group_a=group_a,
            group_b=group_b,
            num_runs=min(num_runs, 20)  # 限制最大次数
        )

        result = ab_tester.run_test(config)

        return {
            "group_a": {
                "name": result.group_a_name,
                "sharpe": result.a_sharpe,
                "return": result.a_return,
                "max_drawdown": result.a_max_drawdown,
                "win_rate": result.a_win_rate,
                "trades": result.a_trades
            },
            "group_b": {
                "name": result.group_b_name,
                "sharpe": result.b_sharpe,
                "return": result.b_return,
                "max_drawdown": result.b_max_drawdown,
                "win_rate": result.b_win_rate,
                "trades": result.b_trades
            },
            "statistics": {
                "sharpe_p_value": result.sharpe_p_value,
                "return_p_value": result.return_p_value,
                "sharpe_diff": result.sharpe_diff,
                "return_diff": result.return_diff,
                "sharpe_ci": [result.sharpe_ci_lower, result.sharpe_ci_upper],
                "return_ci": [result.return_ci_lower, result.return_ci_upper]
            },
            "conclusion": {
                "winner": result.winner,
                "confidence": result.confidence,
                "recommendation": result.recommendation
            }
        }
    except Exception as e:
        logger.error(f"A/B test error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/ab-test/presets", dependencies=[Security(verify_api_key)])
async def ab_test_presets():
    """获取 A/B 测试预设配置"""
    return {
        "presets": [
            {
                "name": "止损对比",
                "group_a": {"name": "紧止损 (2 ATR)", "stop_loss": 2.0, "stop_profit": 4.0},
                "group_b": {"name": "宽止损 (3 ATR)", "stop_loss": 3.0, "stop_profit": 5.0}
            },
            {
                "name": "出场天数对比",
                "group_a": {"name": "短线 (5天)", "exit_days": 5},
                "group_b": {"name": "中线 (10天)", "exit_days": 10}
            },
            {
                "name": "因子权重对比",
                "group_a": {"name": "动量优先", "factor_weights": {"momentum": 0.4, "volume": 0.3, "volatility": 0.3}},
                "group_b": {"name": "均衡权重", "factor_weights": {"momentum": 0.33, "volume": 0.33, "volatility": 0.34}}
            }
        ]
    }


@router.post("/api/strategy/snapshot", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_save(request: Request):
    """保存策略快照"""
    try:
        body = await request.json()
        name = body.get("name", f"快照 {datetime.now().strftime('%H:%M')}")
        description = body.get("description", "")
        params = body.get("params")

        from lib.strategy.strategy_snapshot import snapshot_manager
        snapshot = snapshot_manager.save(name, description, params)

        return {
            "id": snapshot.id,
            "name": snapshot.name,
            "timestamp": snapshot.timestamp
        }
    except Exception as e:
        logger.error(f"Snapshot save error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/strategy/snapshots", dependencies=[Security(verify_api_key)])
async def strategy_snapshots_list():
    """列出所有策略快照"""
    try:
        from lib.strategy.strategy_snapshot import snapshot_manager
        snapshots = snapshot_manager.list()

        return {
            "snapshots": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "timestamp": s.timestamp,
                    "preset": s.preset
                }
                for s in snapshots
            ]
        }
    except Exception as e:
        logger.error(f"Snapshot list error: {e}")
        return {"snapshots": []}


@router.post("/api/strategy/snapshot/{snapshot_id}/apply", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_apply(snapshot_id: str):
    """恢复策略快照"""
    try:
        from lib.strategy.strategy_snapshot import snapshot_manager
        config = snapshot_manager.restore(snapshot_id)
        return {"status": "applied", "config": config}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Snapshot apply error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/api/strategy/snapshot/{snapshot_id}", dependencies=[Security(verify_api_key)])
async def strategy_snapshot_delete(snapshot_id: str):
    """删除策略快照"""
    try:
        from lib.strategy.strategy_snapshot import snapshot_manager
        success = snapshot_manager.delete(snapshot_id)
        if not success:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Snapshot delete error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/strategy/ranking", dependencies=[Security(verify_api_key)])
async def strategy_ranking():
    """策略性能排行"""
    try:
        from lib.strategy.strategy_ranking import strategy_ranker
        ranking = strategy_ranker.get_ranking()
        return {"ranking": ranking}
    except Exception as e:
        logger.error(f"Strategy ranking error: {e}")
        return {"ranking": []}

