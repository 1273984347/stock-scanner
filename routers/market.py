"""RF-01 Phase 5 域路由：market"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

def _compute_market():
    """原 `GET /api/market` 缓存守卫之后的函数体，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    from factors import evaluate_macro_gate, calc_market_exposure
    from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig

    orchestrator = _get_orchestrator()
    sentiment = orchestrator.get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)

    exposure = {"state": "neutral", "exposure": 0.70}
    idx_cache = _cache_get("hs300", max_age=7200)
    if idx_cache:
        close_list = idx_cache.get("close", [])
        vol_list = idx_cache.get("volume", [])
        pct_list = idx_cache.get("pct_chg", [])
        if len(close_list) >= 60:
            import pandas as pd
            close = pd.Series(close_list)
            volume = pd.Series(vol_list) if vol_list else None
            pct_chg = pd.Series(pct_list) if pct_list else None
            exposure = calc_market_exposure(close, volume, pct_chg)

    risk_mgr = VolCorrRiskManager(VolCorrRiskConfig())
    risk_budget = risk_mgr.get_risk_budget(
        exposure.get("state", "neutral"),
        0.5,
    )

    result = {
        "sentiment": round(sentiment, 3),
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": macro.position_multiplier,
            "reasoning": macro.reasoning,
        },
        "exposure": exposure,
        "risk_budget": risk_budget,
    }
    _cache_set("market", result)
    return result


@router.get("/api/market")
async def market_overview(_=Security(verify_api_key)):
    """市场概况 + 风控评估"""
    cached = _cache_get("market", max_age=1800)
    if cached is not None:
        return cached
    return await run_blocking("market", _compute_market)


@router.get("/api/regime-history", dependencies=[Security(verify_api_key)])
async def regime_history(days: int = Query(default=60, ge=10, le=365)):
    """市场状态历史（regime 切换时间线）"""
    cached = _cache_get("regime_history", max_age=3600)
    if cached is not None:
        return cached

    try:
        from lib.data.data_layer import get_index_hist
        from lib.monitoring.market_regime import detect_market_regime

        hs300 = get_index_hist("000300", days=days)
        if hs300.empty:
            return {"regimes": [], "message": "暂无数据"}

        # 滑动窗口检测 regime
        regimes = []
        window = 20
        for i in range(window, len(hs300)):
            window_data = hs300.iloc[i - window:i]
            try:
                result = detect_market_regime(window_data)
                regime = result.get("regime", "UNKNOWN") if isinstance(result, dict) else str(result)
                date_val = hs300.iloc[i].get("date", "") if hasattr(hs300.iloc[i], 'get') else ""
                if not date_val and "date" in hs300.columns:
                    date_val = str(hs300.iloc[i]["date"])
                regimes.append({"date": str(date_val), "regime": regime})
            except Exception:
                continue

        # 合并连续相同 regime
        merged = []
        for r in regimes:
            if merged and merged[-1]["regime"] == r["regime"]:
                merged[-1]["end_date"] = r["date"]
            else:
                merged.append({**r, "start_date": r["date"], "end_date": r["date"]})

        result = {"regimes": merged[-30:], "total": len(merged)}
        _cache_set("regime_history", result)
        return result
    except Exception as e:
        logger.error(f"regime 历史查询失败: {e}")
        return {"regimes": []}


@router.get("/api/sector-heatmap", dependencies=[Security(verify_api_key)])
async def sector_heatmap(top_n: int = Query(default=50, ge=10, le=200)):
    """板块热力图数据"""
    cached = _cache_get("sector_heatmap", max_age=1800)
    if cached is not None:
        return cached

    try:
        from lib.strategy.sector_score import compute_sector_scores

        scores = compute_sector_scores(top_n=top_n)

        sectors = []
        for s in scores:
            sectors.append({
                "name": s.block,
                "score": s.total_score,
                "return_pct": s.avg_return_5d,
                "volume_ratio": s.volume_ratio,
                "limit_up_count": s.limit_up_count,
                "leader_return": s.leader_return,
                "leader_code": s.leader_code,
                "stock_count": s.stock_count,
                "score_return": s.score_return,
                "score_volume": s.score_volume,
                "score_limit_up": s.score_limit_up,
                "score_leader": s.score_leader,
            })

        result = {
            "sectors": sectors,
            "total": len(sectors),
            "timestamp": datetime.now().isoformat(),
        }
        _cache_set("sector_heatmap", result)
        return result
    except Exception as e:
        logger.error(f"板块热力图数据失败: {e}")
        return {"sectors": []}


def _compute_kline(code, days):
    """原 `GET /api/kline/{code}` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    import pandas as pd
    from lib.data.data_layer import get_stock_hist
    from lib.scoring.technical_signals import ema
    from backtest.utils import compute_atr

    df = get_stock_hist(code, days=days)
    if df.empty:
        return {"data": [], "code": code}

    # 计算技术指标
    close = df["close"]
    df["ema5"] = ema(close, 5)
    df["ema21"] = ema(close, 21)
    df["ema50"] = ema(close, 50)

    # Keltner Channel (EMA21 ± 1.5 * ATR)
    atr = compute_atr(df)
    df["keltner_upper"] = df["ema21"] + 1.5 * atr
    df["keltner_lower"] = df["ema21"] - 1.5 * atr

    # Donchian Channel (20-day high/low)
    df["donchian_high"] = df["high"].rolling(20).max()
    df["donchian_low"] = df["low"].rolling(20).min()

    kline = []
    for _, row in df.iterrows():
        kline.append({
            "date": str(row.get("date", "")),
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": float(row.get("volume", 0)),
            "ema5": float(row["ema5"]) if pd.notna(row.get("ema5")) else None,
            "ema21": float(row["ema21"]) if pd.notna(row.get("ema21")) else None,
            "ema50": float(row["ema50"]) if pd.notna(row.get("ema50")) else None,
            "keltner_upper": float(row["keltner_upper"]) if pd.notna(row.get("keltner_upper")) else None,
            "keltner_lower": float(row["keltner_lower"]) if pd.notna(row.get("keltner_lower")) else None,
            "donchian_high": float(row["donchian_high"]) if pd.notna(row.get("donchian_high")) else None,
            "donchian_low": float(row["donchian_low"]) if pd.notna(row.get("donchian_low")) else None,
        })

    return {"data": kline, "code": code}


@router.get("/api/kline/{code}", dependencies=[Security(verify_api_key)])
async def kline_data(code: str, days: int = Query(default=120, ge=30, le=500)):
    """K线数据（含技术指标）"""
    code = _validate_stock_code(code)
    try:
        return await run_blocking("kline", _compute_kline, code, days)
    except Exception as e:
        logger.error(f"K线数据获取失败 {code}: {e}")
        return {"data": [], "code": code}


@router.get("/api/trade-history", dependencies=[Security(verify_api_key)])
async def trade_history(
    days: int = Query(default=30, ge=1, le=365),
    code: str = Query(default=""),
    sort_by: str = Query(default="date"),
    limit: int = Query(default=50, ge=1, le=200),
):
    """交易历史"""
    try:
        from lib.monitoring.trade_logger import TradeLogger
        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        # 按股票代码筛选
        if code:
            trades = [t for t in trades if t.get("code") == code]

        # 排序
        if sort_by == "pnl":
            trades.sort(key=lambda t: t.get("pnl_pct", 0), reverse=True)
        elif sort_by == "pnl_asc":
            trades.sort(key=lambda t: t.get("pnl_pct", 0))
        else:
            trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)

        return {
            "trades": trades[:limit],
            "total": len(trades),
        }
    except Exception as e:
        logger.error(f"交易历史查询失败: {e}")
        return {"trades": [], "total": 0}


@router.get("/api/sector-flow", dependencies=[Security(verify_api_key)])
async def sector_flow(top_n: int = Query(20, ge=5, le=50)):
    """板块资金流向 — 桑基图数据"""
    try:
        from lib.data.data_layer import _load_cache
        import numpy as np
        sectors_data = _load_cache("sectors/sector_scores", max_age_hours=24)
        if sectors_data is None or (hasattr(sectors_data, 'empty') and sectors_data.empty):
            return {"nodes": [], "links": []}
        nodes = []
        links = []
        if hasattr(sectors_data, 'iterrows'):
            for _, row in sectors_data.head(top_n).iterrows():
                name = row.get("name", row.get("sector", ""))
                score = row.get("score", 50)
                if name:
                    nodes.append({"name": str(name)})
                    if score > 60:
                        links.append({"source": "资金池", "target": str(name), "value": int(score)})
                    elif score < 40:
                        links.append({"source": str(name), "target": "资金池", "value": int(100 - score)})
        if nodes:
            nodes.insert(0, {"name": "资金池"})
        return {"nodes": nodes[:top_n + 1], "links": links[:top_n * 2]}
    except Exception as e:
        logger.error(f"Sector flow error: {e}")
        return {"nodes": [], "links": []}


@router.get("/api/treemap/sectors", dependencies=[Security(verify_api_key)])
async def treemap_sectors():
    """行业分布树状图数据"""
    try:
        scan = _cache_get("scan", max_age=3600) or {}
        details = scan.get("details", [])

        # 按行业分组
        sector_map = {}
        for d in details:
            sector = d.get("sector", "未知")
            if sector not in sector_map:
                sector_map[sector] = {"name": sector, "value": 0, "change": 0, "count": 0}
            sector_map[sector]["value"] += abs(d.get("weight", 0))
            sector_map[sector]["change"] += d.get("change_pct", 0)
            sector_map[sector]["count"] += 1

        # 计算平均涨跌幅
        for sector in sector_map.values():
            if sector["count"] > 0:
                sector["change"] = round(sector["change"] / sector["count"], 2)

        result = list(sector_map.values())
        result.sort(key=lambda x: x["value"], reverse=True)

        return {"data": result[:30]}
    except Exception as e:
        logger.error(f"Treemap sectors error: {e}")
        return {"data": []}


@router.get("/api/treemap/factors", dependencies=[Security(verify_api_key)])
async def treemap_factors():
    """因子权重树状图数据"""
    try:
        from config import FactorConfig
        weights = FactorConfig.ic_factor_weights or {}

        result = [
            {"name": k, "value": abs(v), "change": v * 100}
            for k, v in weights.items()
        ]
        result.sort(key=lambda x: x["value"], reverse=True)

        return {"data": result}
    except Exception as e:
        logger.error(f"Treemap factors error: {e}")
        return {"data": []}


@router.get("/api/sankey/flow", dependencies=[Security(verify_api_key)])
async def sankey_flow():
    """板块资金流向桑基图数据"""
    try:
        scan = _cache_get("scan", max_age=3600) or {}
        details = scan.get("details", [])

        # 按行业分组计算资金流向
        sector_flow = {}
        for d in details:
            sector = d.get("sector", "未知")
            weight = d.get("weight", 0)
            if sector not in sector_flow:
                sector_flow[sector] = {"inflow": 0, "outflow": 0}
            if weight > 0:
                sector_flow[sector]["inflow"] += weight
            else:
                sector_flow[sector]["outflow"] += abs(weight)

        # 构建桑基图数据
        nodes = [{"name": sector} for sector in sector_flow.keys()]
        links = []

        # 创建流入→流出关系（简化：权重高的行业流向权重低的行业）
        sectors = list(sector_flow.items())
        for i, (source, flow) in enumerate(sectors):
            for j, (target, target_flow) in enumerate(sectors):
                if i != j and flow["inflow"] > 0 and target_flow["outflow"] > 0:
                    value = min(flow["inflow"], target_flow["outflow"]) * 0.1
                    if value > 0.01:
                        links.append({
                            "source": i,
                            "target": j,
                            "value": round(value, 4)
                        })

        return {"nodes": nodes, "links": links[:20]}
    except Exception as e:
        logger.error(f"Sankey flow error: {e}")
        return {"nodes": [], "links": []}

