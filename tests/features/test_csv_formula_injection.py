"""批次 L9 / P2-10：CSV 与 Excel 公式注入防护的行为判据。

`_compute_export_trades` / `_compute_export_trades_excel` 已因本修复登记到
`MIGRATION_DIVERGENCES`（不再逐字等价于历史快照），因此**必须**有独立行为
测试证明修复真的生效——否则分歧登记就成了放宽门禁的空壳（坑表 P-56）。

覆盖四个写入点：两个 CSV + 两个 Excel；其中前两者中只有 trades 系列
在 L9 冻结清单里，positions 系列可直接改，但判据一视同仁。
"""

import csv
import io

import pytest
from openpyxl import load_workbook

from routers import export as export_router


DANGEROUS = "=HYPERLINK(\"http://evil.example\",\"click\")"


@pytest.mark.parametrize("raw", [
    "=1+1", "+cmd|'/c calc'!A0", "-1+1", "@SUM(A1:A9)", "\t=1+1", "\r=1+1",
])
def test_csv_safe_neutralizes_formula_prefix(raw):
    assert export_router._csv_safe(raw) == "'" + raw


@pytest.mark.parametrize("raw", [
    "000001", "正常名称", 1.5, 0, None, "",
])
def test_csv_safe_keeps_benign_values_unchanged(raw):
    assert export_router._csv_safe(raw) == raw


class _FakePosition:
    name = DANGEROUS
    shares = 100
    buy_price = 10.0
    stop_loss = 9.0
    take_profit = 12.0
    buy_date = "2026-01-02"
    notes = "+cmd|'/c calc'!A0"


class _FakePositionManager:
    positions = {"000001": _FakePosition()}


class _FakeTradeLogger:
    def get_recent_trades(self, days):
        return [{
            "timestamp": "2026-01-02 10:00:00",
            "code": "000001",
            "action": "买入",
            "price": 10.0,
            "shares": 100,
            "pnl_pct": 0.05,
            "hold_days": 3,
            "exit_reason": DANGEROUS,
        }]


async def _body_bytes(response) -> bytes:
    parts = [chunk async for chunk in response.body_iterator]
    # CSV 端点传的是 iter([str])，Excel 端点传 BytesIO；两种 chunk 都要能收。
    return b"".join(p.encode("utf-8") if isinstance(p, str) else p for p in parts)


def _csv_rows(payload: bytes) -> list[list[str]]:
    """按 CSV 语义解析后再断言——csv.writer 会把字段内引号双写转义。"""
    return list(csv.reader(io.StringIO(payload.decode("utf-8"))))


@pytest.mark.asyncio
async def test_positions_csv_neutralizes_formulas(monkeypatch):
    monkeypatch.setattr("lib.risk.position_manager.PositionManager", _FakePositionManager)
    rows = _csv_rows(await _body_bytes(await export_router.export_positions()))
    assert rows[1][1] == "'" + DANGEROUS
    assert rows[1][8] == "'+cmd|'/c calc'!A0"


@pytest.mark.asyncio
async def test_trades_csv_neutralizes_formulas(monkeypatch):
    monkeypatch.setattr("lib.monitoring.trade_logger.TradeLogger", _FakeTradeLogger)
    rows = _csv_rows(await _body_bytes(await export_router.export_trades(days=30)))
    assert rows[1][7] == "'" + DANGEROUS


@pytest.mark.asyncio
async def test_positions_excel_forces_text_cells(monkeypatch):
    monkeypatch.setattr("lib.risk.position_manager.PositionManager", _FakePositionManager)
    wb = load_workbook(io.BytesIO(await _body_bytes(
        await export_router.export_positions_excel())))
    name_cell = wb.active.cell(row=2, column=2)
    notes_cell = wb.active.cell(row=2, column=9)
    assert name_cell.data_type == "s"
    assert name_cell.value == "'" + DANGEROUS
    assert notes_cell.data_type == "s"


@pytest.mark.asyncio
async def test_trades_excel_forces_text_cells(monkeypatch):
    monkeypatch.setattr("lib.monitoring.trade_logger.TradeLogger", _FakeTradeLogger)
    wb = load_workbook(io.BytesIO(await _body_bytes(
        await export_router.export_trades_excel(days=30))))
    reason_cell = wb.active.cell(row=2, column=8)
    assert reason_cell.data_type == "s"
    assert reason_cell.value == "'" + DANGEROUS
