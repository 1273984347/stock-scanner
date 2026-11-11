"""RF-01 Phase 5 域路由：export"""

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from lib.infra.async_offload import run_blocking

router = APIRouter()

@router.get("/api/export/positions", dependencies=[Security(verify_api_key)])
async def export_positions():
    """导出持仓为 CSV"""
    try:
        from lib.risk.position_manager import PositionManager
        pm = PositionManager()
        positions = pm.positions

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["代码", "名称", "股数", "成本", "止损", "止盈", "入场日期", "持有天数", "备注"])

        from datetime import datetime as _dt
        for code, pos in positions.items():
            hold_days = 0
            try:
                buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
                hold_days = (_dt.now() - buy_dt).days
            except Exception:
                pass
            writer.writerow([
                code,
                pos.name,
                pos.shares,
                pos.buy_price,
                pos.stop_loss,
                pos.take_profit,
                pos.buy_date,
                hold_days,
                pos.notes,
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=positions.csv"}
        )
    except Exception as e:
        logger.error(f"导出持仓 CSV 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/export/trades", dependencies=[Security(verify_api_key)])
async def export_trades(days: int = Query(default=30, ge=1, le=365)):
    """导出交易历史为 CSV"""
    try:
        from lib.monitoring.trade_logger import TradeLogger
        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["时间", "代码", "操作", "价格", "股数", "盈亏%", "持有天数", "原因"])

        for t in trades:
            writer.writerow([
                t.get("timestamp", ""),
                t.get("code", ""),
                t.get("action", ""),
                t.get("price", 0),
                t.get("shares", 0),
                round(t.get("pnl_pct", 0) * 100, 2),
                t.get("hold_days", 0),
                t.get("exit_reason", ""),
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=trades_{days}d.csv"}
        )
    except Exception as e:
        logger.error(f"导出交易 CSV 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/export/positions/excel", dependencies=[Security(verify_api_key)])
async def export_positions_excel():
    """导出持仓为 Excel"""
    try:
        from lib.risk.position_manager import PositionManager
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        pm = PositionManager()
        positions = pm.positions

        wb = Workbook()
        ws = wb.active
        ws.title = "持仓"

        # Header style
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        headers = ["代码", "名称", "股数", "成本", "止损", "止盈", "入场日期", "持有天数", "备注"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Data rows
        from datetime import datetime as _dt
        row_idx = 2
        for code, pos in positions.items():
            hold_days = 0
            try:
                buy_dt = _dt.strptime(pos.buy_date, "%Y-%m-%d")
                hold_days = (_dt.now() - buy_dt).days
            except Exception:
                pass
            row_data = [
                code, pos.name, pos.shares, pos.buy_price,
                pos.stop_loss, pos.take_profit, pos.buy_date,
                hold_days, pos.notes,
            ]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.border = thin_border
                if col in (3, 4, 5, 6, 8):  # numeric columns
                    cell.alignment = Alignment(horizontal="right")
            row_idx += 1

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=positions.xlsx"},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="需要安装 openpyxl: pip install openpyxl")
    except Exception as e:
        logger.error(f"导出持仓 Excel 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/export/trades/excel", dependencies=[Security(verify_api_key)])
async def export_trades_excel(days: int = Query(default=30, ge=1, le=365)):
    """导出交易历史为 Excel"""
    try:
        from lib.monitoring.trade_logger import TradeLogger
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

        tl = TradeLogger()
        trades = tl.get_recent_trades(days=days)

        wb = Workbook()
        ws = wb.active
        ws.title = "交易历史"

        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_align = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"), right=Side(style="thin"),
            top=Side(style="thin"), bottom=Side(style="thin"),
        )

        headers = ["时间", "代码", "操作", "价格", "股数", "盈亏%", "持有天数", "原因"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align
            cell.border = thin_border

        # Conditional fill for positive/negative PnL
        green_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
        red_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")

        for row_idx, t in enumerate(trades, 2):
            pnl_pct = round(t.get("pnl_pct", 0) * 100, 2)
            row_data = [
                t.get("timestamp", ""),
                t.get("code", ""),
                t.get("action", ""),
                t.get("price", 0),
                t.get("shares", 0),
                pnl_pct,
                t.get("hold_days", 0),
                t.get("exit_reason", ""),
            ]
            for col, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col, value=val)
                cell.border = thin_border
                if col in (4, 5, 6, 7):
                    cell.alignment = Alignment(horizontal="right")
                # Color PnL column
                if col == 6:
                    cell.fill = green_fill if val >= 0 else red_fill
                    cell.number_format = '0.00"%"'

        # Auto-fit column widths
        for col in ws.columns:
            max_len = max((len(str(cell.value or "")) for cell in col), default=8)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 30)

        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename=trades_{days}d.xlsx"},
        )
    except ImportError:
        raise HTTPException(status_code=500, detail="需要安装 openpyxl: pip install openpyxl")
    except Exception as e:
        logger.error(f"导出交易 Excel 失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


def _compute_export_config(preset):
    """原 `GET /api/export/config` try 块内容，逐字搬迁（禁止顺手优化，ADR-0003 D5）。"""
    import dataclasses
    from config import get_config

    cfg = get_config(preset)

    def to_dict(obj):
        if dataclasses.is_dataclass(obj):
            return {k: to_dict(v) for k, v in dataclasses.asdict(obj).items()}
        elif isinstance(obj, (list, tuple)):
            return [to_dict(item) for item in obj]
        elif isinstance(obj, dict):
            return {k: to_dict(v) for k, v in obj.items()}
        return obj

    config_dict = to_dict(cfg)
    config_dict["_preset"] = preset
    config_dict["_exported_at"] = datetime.now().isoformat()

    return JSONResponse(
        content=config_dict,
        headers={"Content-Disposition": f"attachment; filename=config_{preset}.json"}
    )


@router.get("/api/export/config", dependencies=[Security(verify_api_key)])
async def export_config(preset: str = Query("balanced")):
    """导出当前配置为 JSON"""
    if preset not in ALLOWED_PRESETS:
        raise HTTPException(status_code=400, detail=f"无效的preset: {preset}")
    try:
        return await run_blocking("export_config", _compute_export_config, preset)
    except Exception as e:
        logger.error(f"配置导出失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/import/config")
async def import_config(request: Request, _=Security(verify_api_key)):
    """导入配置 JSON（写入 config.json 触发热更新）"""
    try:
        data = await request.json()

        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="配置必须是 JSON 对象")

        # 移除导出时附加的元数据字段
        for meta_key in ("_preset", "_exported_at"):
            data.pop(meta_key, None)

        config_file = Path("config.json")
        config_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

        return {"status": "ok", "message": "配置已导入，将在下次请求时生效"}
    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 格式")
    except Exception as e:
        logger.error(f"配置导入失败: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/api/export/pdf", dependencies=[Security(verify_api_key)])
async def export_pdf():
    """生成 PDF 报告"""
    try:
        from lib.infra.pdf_report import pdf_generator

        # 收集数据
        dashboard = _cache_get("dashboard", max_age=3600) or {}
        positions = _cache_get("positions", max_age=3600) or []
        signals = _cache_get("signals", max_age=3600) or []
        backtest = _cache_get("backtest", max_age=3600) or {}
        risk = _cache_get("risk", max_age=3600) or {}

        data = {
            "preset": "均衡策略",
            "dashboard": dashboard,
            "positions": positions if isinstance(positions, list) else positions.get("details", []),
            "signals": signals.get("signals", []) if isinstance(signals, dict) else signals,
            "signal_summary": signals.get("summary", {}) if isinstance(signals, dict) else {},
            "backtest": backtest,
            "risk": risk,
        }

        # 生成 PDF
        pdf_bytes = pdf_generator.generate(data)

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=report_{datetime.now().strftime('%Y%m%d')}.pdf"}
        )
    except RuntimeError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error(f"PDF export error: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api/export/pdf/status", dependencies=[Security(verify_api_key)])
async def export_pdf_status():
    """检查 PDF 导出功能是否可用"""
    try:
        from lib.infra.pdf_report import HAS_REPORTLAB, HAS_MATPLOTLIB
        return {
            "available": HAS_REPORTLAB,
            "charts": HAS_MATPLOTLIB,
            "message": "PDF export ready" if HAS_REPORTLAB else "reportlab not installed"
        }
    except Exception as e:
        return {"available": False, "message": str(e)}

