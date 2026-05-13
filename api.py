"""FastAPI 后端 —— 封装选股系统为 REST API（带缓存加速）"""

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from datetime import datetime
import json
import logging
import threading

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="A股盘后选股系统", version="1.0.0")


def _prefetch_hs300():
    """后台预获取沪深300数据（首次启动时缓存）"""
    try:
        import akshare as ak
        logger.info("后台预获取沪深300数据...")
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if not idx.empty:
            data = {
                "close": idx["close"].tail(120).tolist(),
                "volume": idx["volume"].tail(120).tolist() if "volume" in idx.columns else [],
                "pct_chg": idx["close"].pct_change().fillna(0).tail(120).tolist(),
            }
            _cache_set("hs300", data)
            logger.info("沪深300数据缓存成功")
    except Exception as e:
        logger.warning(f"预获取沪深300失败（不影响使用）: {e}")


# 启动后台预获取
threading.Thread(target=_prefetch_hs300, daemon=True).start()

STATIC_DIR = Path(__file__).parent / "static"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _cache_get(key: str, max_age: int = 3600):
    """读 JSON 缓存"""
    path = CACHE_DIR / f"api_{key}.json"
    if not path.exists():
        return None
    age = (datetime.now().timestamp() - path.stat().st_mtime)
    if age > max_age:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_set(key: str, data):
    """写 JSON 缓存"""
    path = CACHE_DIR / f"api_{key}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")


@app.get("/")
async def index():
    """前端页面"""
    html = STATIC_DIR / "index.html"
    if html.exists():
        return FileResponse(str(html))
    return {"message": "A股盘后选股系统 API", "docs": "/docs"}


@app.get("/api/scan")
async def scan(preset: str = "balanced"):
    """选股结果（缓存 1 小时）"""
    cache_key = f"scan_{preset}"
    cached = _cache_get(cache_key, max_age=3600)
    if cached:
        return cached

    from scanner import run_all_strategies
    try:
        results = run_all_strategies(preset)
        output = {}
        for name, df in results.items():
            output[name] = df.to_dict(orient="records")
        data = {"preset": preset, "results": output, "total": sum(len(v) for v in output.values())}
        _cache_set(cache_key, data)
        return data
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
    """市场概况（缓存 30 分钟）"""
    cached = _cache_get("market", max_age=1800)
    if cached:
        return cached

    from scanner import get_market_sentiment, get_realtime_quotes
    from factors import evaluate_macro_gate, calc_market_exposure

    sentiment = get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)

    # 沪深300择时（用缓存的历史数据）
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

    result = {
        "sentiment": round(sentiment, 3),
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": macro.position_multiplier,
            "reasoning": macro.reasoning,
        },
        "exposure": exposure,
    }
    _cache_set("market", result)
    return result


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
