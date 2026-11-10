"""RF-01 Phase 5 域路由：portfolio"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403

router = APIRouter()

@router.get("/api/position/{code}", dependencies=[Security(verify_api_key)])
async def position_detail(code: str):
    """单只持仓详情"""
    code = _validate_stock_code(code)
    try:
        from lib.risk.position_manager import PositionManager
        from datetime import datetime as _dt

        pm = PositionManager()
        pos = pm.positions.get(code)
        if not pos:
            raise HTTPException(status_code=404, detail=f"未持有 {code}")

        # 获取实时行情
        orchestrator = _get_orchestrator()
        quotes = orchestrator.get_realtime_quotes()
        current_price = 0.0
        if not quotes.empty:
            row = quotes[quotes["代码"].astype(str).str.zfill(6) == code]
            if not row.empty:
                current_price = float(row.iloc[0].get("最新价", 0))

        # 计算盈亏
        cost = pos.buy_price
        shares = pos.shares
        if current_price > 0 and cost > 0:
            pnl_pct = (current_price - cost) / cost
            pnl_amount = (current_price - cost) * shares
            market_value = current_price * shares
        else:
            pnl_pct = 0
            pnl_amount = 0
            market_value = 0

        # 持有天数
        hold_days = 0
        try:
            buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
            hold_days = (_dt.now() - buy_dt).days
        except Exception:
            pass

        return {
            "code": code,
            "name": pos.name,
            "cost": cost,
            "shares": shares,
            "current_price": round(current_price, 2),
            "pnl_pct": round(pnl_pct, 4),
            "pnl_amount": round(pnl_amount, 2),
            "market_value": round(market_value, 2),
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "entry_date": pos.buy_date,
            "hold_days": hold_days,
            "notes": pos.notes,
            "atr": pos.atr,
            "atr_mult": pos.atr_mult,
            "atr_stop_price": pos.atr_stop_price,
            "peak_price": pos.peak_price,
            "trailing_stop_price": pos.trailing_stop_price,
            "atr_take_profit_price": pos.atr_take_profit_price,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"持仓详情查询失败 {code}: {e}")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@router.get("/api/positions")
async def positions(_=Security(verify_api_key)):
    """持仓概况"""
    from lib.risk.position_manager import PositionManager
    try:
        pm = PositionManager()
        orchestrator = _get_orchestrator()
        quotes = orchestrator.get_realtime_quotes()
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
        return {"summary": {"total_positions": 0}, "alerts": [], "error": "服务暂时不可用"}


@router.get("/api/position-distribution", dependencies=[Security(verify_api_key)])
async def position_distribution():
    """持仓分布（按行业分组）"""
    try:
        from lib.risk.position_manager import PositionManager
        from lib.data.data_layer import get_stock_blocks

        pm = PositionManager()
        positions = pm.positions

        if not positions:
            return {"by_sector": {}, "total_positions": 0}

        # 按行业分组
        sector_weights = {}
        total_value = 0

        for code, pos in positions.items():
            weight = pos.shares * pos.buy_price
            # 获取行业
            try:
                blocks = get_stock_blocks(code)
                sector = blocks[0] if blocks else "其他"
            except Exception:
                sector = "其他"

            sector_weights[sector] = sector_weights.get(sector, 0) + weight
            total_value += weight

        # 归一化
        if total_value > 0:
            for s in sector_weights:
                sector_weights[s] = round(sector_weights[s] / total_value, 4)

        return {
            "by_sector": sector_weights,
            "total_positions": len(positions),
        }
    except Exception as e:
        logger.error(f"持仓分布查询失败: {e}")
        return {"by_sector": {}, "total_positions": 0}

