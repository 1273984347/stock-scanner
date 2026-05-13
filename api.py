"""FastAPI 后端 —— 封装选股系统为 REST API"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="A股盘后选股系统", version="1.0.0")

STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股盘后选股系统 API", "docs": "/docs"}


@app.get("/api/scan")
async def scan(preset: str = "balanced"):
    """选股结果"""
    from scanner import run_all_strategies
    try:
        results = run_all_strategies(preset)
        output = {}
        for name, df in results.items():
            output[name] = df.to_dict(orient="records")
        return {"preset": preset, "results": output, "total": sum(len(v) for v in output.values())}
    except Exception as e:
        logger.error(f"选股失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock/{code}")
async def stock_detail(code: str):
    """单股详情：因子 + 技术信号 + 评分"""
    from scanner import get_stock_hist
    from factors import compute_all_signals
    from lib.technical_signals import calc_composite_verdict

    hist = get_stock_hist(code, days=180)
    if hist.empty:
        raise HTTPException(status_code=404, detail=f"未找到 {code} 数据")

    result = compute_all_signals(hist)
    tech = calc_composite_verdict(hist)

    if not result:
        raise HTTPException(status_code=500, detail="因子计算失败")

    return {
        "code": code,
        "factors": result.get("factors", {}),
        "combined": result.get("combined", {}),
        "regime": result.get("regime", {}),
        "macro_gate": result.get("macro_gate", {}),
        "technical": {
            "verdict": tech.get("verdict"),
            "signals": tech.get("signals"),
            "summary": tech.get("summary"),
        },
    }


@app.get("/api/market")
async def market_overview():
    """市场概况"""
    from scanner import get_market_sentiment, get_realtime_quotes
    from factors import evaluate_macro_gate, calc_market_exposure
    import akshare as ak
    import pandas as pd

    sentiment = get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)

    # 沪深300择时
    exposure = {"state": "neutral", "exposure": 0.70}
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if not idx.empty:
            close = idx["close"].tail(120)
            volume = idx["volume"].tail(120) if "volume" in idx.columns else None
            pct_chg = close.pct_change().fillna(0)
            exposure = calc_market_exposure(close, volume, pct_chg)
    except Exception:
        pass

    return {
        "sentiment": round(sentiment, 3),
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": macro.position_multiplier,
            "reasoning": macro.reasoning,
        },
        "exposure": exposure,
    }


@app.get("/api/positions")
async def positions():
    """持仓概况"""
    from lib.position_manager import PositionManager
    try:
        pm = PositionManager()
        # 用缓存的行情更新价格
        from scanner import get_realtime_quotes
        quotes = get_realtime_quotes()
        prices = {}
        if not quotes.empty:
            for _, row in quotes.iterrows():
                code = str(row.get("代码", ""))
                price = row.get("最新价")
                if code and price:
                    prices[code] = float(price)

        summary = pm.get_portfolio_summary(prices)
        alerts = pm.check_stop_loss(prices)
        return {"summary": summary, "alerts": alerts}
    except Exception as e:
        return {"summary": {"total_positions": 0}, "alerts": [], "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
