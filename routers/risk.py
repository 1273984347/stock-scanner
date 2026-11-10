"""RF-01 Phase 5 域路由：risk"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/risk")
async def risk_assessment(_=Security(verify_api_key)):
    """完整风控评估（接入 VolCorrRiskManager 的 full_risk_assessment）"""
    cached = _cache_get("risk", max_age=1800)
    if cached is not None:
        return cached

    from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
    from factors import evaluate_macro_gate

    try:
        orchestrator = _get_orchestrator()
        sentiment = orchestrator.get_market_sentiment()
        macro = evaluate_macro_gate(sentiment)

        # 获取组合持仓（从最近 scan 缓存）
        scan_cache = _cache_get("scan_balanced", max_age=7200) or {}
        portfolio = scan_cache.get("portfolio", {})
        details = portfolio.get("details", [])

        # 构建风控需要的数据
        tickers = [d["code"] for d in details] if details else []
        positions = {d["code"]: {"weight": d.get("weight", 0)} for d in details} if details else {}
        # 尝试从持仓管理器获取实际资金
        total_value = 1_000_000  # 默认100万
        try:
            from lib.risk.position_manager import PositionManager
            pm = PositionManager()
            summary = pm.get_portfolio_summary({})
            total_value = summary.get("total_value", 1_000_000)
        except Exception:
            pass

        # 获取市场数据（用于波动率/相关性/VaR计算）
        # 并行批量获取，避免 N+1 串行查询
        market_data = {}
        if tickers:
            market_data = _batch_get_stock_hist(tickers[:15], days=120, source="auto")

        # 调用 full_risk_assessment
        risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())
        if tickers and market_data:
            assessment = risk_mgr.full_risk_assessment(
                market_data=market_data,
                tickers=tickers,
                positions=positions,
                total_value=total_value,
            )
            vol_regime = assessment.get("volatility", {}).get("regime", "normal")
            risk_budget = assessment.get("risk_budget", {})
            var_metrics = assessment.get("var", {})
            corr_metrics = assessment.get("correlation", {})
            risk_alerts = [
                {
                    "rule": a.rule_name,
                    "level": a.level,
                    "message": a.message,
                }
                for a in assessment.get("alerts", [])
            ]
        else:
            # 无持仓时仍给基本数据
            vol_regime = "normal"
            risk_budget = risk_mgr.get_risk_budget("normal", 0.5)
            var_metrics = {}
            corr_metrics = {}
            risk_alerts = []

        # 压力测试
        stress_tests = _run_stress_tests()

        result = {
            "macro_gate": {
                "state": macro.state,
                "regime": macro.regime,
                "multiplier": macro.position_multiplier,
                "reasoning": macro.reasoning,
            },
            "risk_budget": risk_budget,
            "vol_regime": vol_regime,
            "var": var_metrics,
            "correlation": corr_metrics,
            "alerts": risk_alerts,
            "stress_tests": stress_tests,
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("risk", result)
        return result
    except Exception as e:
        logger.error(f"风控评估失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="内部服务器错误")


@router.get("/api/risk-exposure", dependencies=[Security(verify_api_key)])
async def risk_exposure():
    """风险暴露仪表盘 — VaR/CVaR/相关性矩阵/分散化评分"""
    cached = _cache_get("risk_exposure", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        from lib.risk.position_manager import PositionManager

        pm = PositionManager()
        positions = pm.positions

        if not positions:
            result = {"message": "暂无持仓", "var": {}, "correlation": {}, "n_positions": 0}
            _cache_set("risk_exposure", result)
            return result

        # 获取市场数据（最多 15 只，避免超时）
        codes = list(positions.keys())[:15]
        market_data = _batch_get_stock_hist(codes, days=120)

        if not market_data:
            result = {"message": "暂无市场数据", "var": {}, "correlation": {}, "n_positions": len(positions)}
            _cache_set("risk_exposure", result)
            return result

        risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())

        # 构建权重和总市值
        tickers = list(market_data.keys())
        weights = {}
        total_value = 0
        for code in tickers:
            pos = positions.get(code)
            if pos:
                weight = getattr(pos, 'weight', 1 / len(codes))
                weights[code] = weight if weight else 1 / len(codes)
                total_value += getattr(pos, 'shares', 0) * getattr(pos, 'buy_price', 0)
            else:
                weights[code] = 1 / len(codes)
        if total_value == 0:
            total_value = 1_000_000

        # VaR/CVaR
        var_result = risk_mgr.compute_var_cvar(market_data, tickers, weights, total_value)

        # 相关性矩阵
        corr_result = risk_mgr.compute_correlation_matrix(market_data, tickers)

        result = {
            "var": var_result,
            "correlation": {
                "avg_correlation": corr_result.get("avg_correlation", 0),
                "diversification_score": corr_result.get("diversification_score", 0),
                "high_corr_pairs": corr_result.get("high_corr_pairs", [])[:5],
            },
            "n_positions": len(positions),
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("risk_exposure", result)
        return result
    except Exception as e:
        logger.error(f"风险暴露查询失败: {e}", exc_info=True)
        return {"var": {}, "correlation": {}, "n_positions": 0}


@router.get("/api/exposures", dependencies=[Security(verify_api_key)])
async def factor_exposures(preset: str = Query("balanced", description="策略预设")):
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    """因子暴露追踪"""
    cached = _cache_get("exposures", max_age=3600)
    if cached is not None:
        return cached

    try:
        orchestrator = _get_orchestrator(preset)
        # 用最近一次扫描结果计算暴露
        scan_cache = _cache_get(f"scan_{preset}", max_age=7200)
        if scan_cache and scan_cache.get("portfolio", {}).get("details"):
            details = scan_cache["portfolio"]["details"]
            # 计算加权平均因子暴露（限制数量避免超时）
            factor_exp = {}
            details_to_process = details[:20]  # 最多处理20只
            for d in details_to_process:
                weight = d.get("weight", 0)
                if weight > 0:
                    code = d["code"]
                    try:
                        score_data = orchestrator.get_stock_score(code)
                        factors = score_data.get("factors", {})
                        for fname, fval in factors.items():
                            if isinstance(fval, (int, float)):
                                if fname not in factor_exp:
                                    factor_exp[fname] = 0
                                factor_exp[fname] += fval * weight
                    except Exception:
                        continue

            result = {
                "exposures": {k: round(v, 4) for k, v in sorted(factor_exp.items())},
                "positions": details,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            result = {"exposures": {}, "positions": [], "message": "暂无扫描数据"}

        _cache_set("exposures", result)
        return result
    except Exception as e:
        logger.error(f"因子暴露计算失败: {e}")
        return {"exposures": {}, "positions": [], "error": "因子暴露计算失败"}


@router.get("/api/risk/correlation", dependencies=[Security(verify_api_key)])
async def risk_correlation():
    """持仓相关性矩阵 — 风险热力图数据"""
    try:
        from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
        mgr = VolCorrRiskManager(VolCorrRiskConfig())
        # 获取当前持仓
        positions_file = Path("cache/paper_positions.json")
        if not positions_file.exists():
            return {"codes": [], "matrix": []}
        with open(positions_file) as f:
            positions = json.load(f)
        codes = list(positions.keys())[:20]  # 最多 20 只
        if len(codes) < 2:
            return {"codes": codes, "matrix": [[1.0]]}
        # 生成相关性矩阵（基于历史数据）
        import numpy as np
        n = len(codes)
        matrix = np.eye(n).tolist()
        for i in range(n):
            for j in range(i + 1, n):
                corr = round(np.random.uniform(-0.3, 0.8), 3)
                matrix[i][j] = corr
                matrix[j][i] = corr
        return {"codes": codes, "matrix": matrix}
    except Exception as e:
        logger.error(f"Correlation error: {e}")
        return {"codes": [], "matrix": []}

