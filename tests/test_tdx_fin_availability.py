"""坑表 P-104 机器判据（2026-11-19）：死源的可用性判定要在**源层**，不是逐股告警。

TDX 本地目录在 ADR-0017 里已删，但财务适配器仍对每只候选股调用 GpcwReader，
读不到就打 `找不到 gpcw*.dat 文件`，一次扫描刷数十条，然后才走 baostock 兜底。
这不是"日志太吵"——是把「源不可用」的判定放到了每个调用点。

修法：
1. `TDX_FIN_PATH` 尊重 `TDX_DATA_PATH`（与 K 线本地源同一口径）；
2. 适配层先判源可用性，不可用直接走兜底，GpcwReader 保留显式调用时的告警。
"""
import importlib
import logging

import pytest

import lib.data.data_tdx_fin as tdx_fin
import lib.scoring.data_tdx_fin_adapter as adapter


def test_fin_path_respects_tdx_data_path(monkeypatch):
    monkeypatch.setenv("TDX_DATA_PATH", "D:/custom-tdx")
    reloaded = importlib.reload(tdx_fin)
    assert str(reloaded.TDX_FIN_PATH).replace("\\", "/").endswith("D:/custom-tdx/cw")
    monkeypatch.delenv("TDX_DATA_PATH")
    importlib.reload(tdx_fin)  # 恢复默认，避免污染其它用例


def test_unavailable_source_skips_reader_and_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(tdx_fin, "TDX_FIN_PATH", tmp_path / "absent")

    called = {"tdx": 0}

    def forbidden(*a, **k):
        called["tdx"] += 1
        raise AssertionError("源不可用时不应调用 GpcwReader")

    monkeypatch.setattr(adapter, "get_tdx_fin_adapter",
                        lambda: type("A", (), {"compute_fundamentals": staticmethod(forbidden)})())
    monkeypatch.setattr(adapter, "_fetch_baostock_fundamentals",
                        lambda code: {"profitability": {"ROE": 0.12}, "solvency": {}, "growth": {}})

    with caplog.at_level(logging.WARNING, logger="lib.scoring.data_tdx_fin_adapter"):
        out = adapter.get_stock_fundamentals("999101")

    assert called["tdx"] == 0
    assert out["_source"] == "baostock"
    assert "找不到 gpcw" not in caplog.text


def test_available_source_still_uses_tdx(tmp_path, monkeypatch):
    (tmp_path / "gpcw20260930.dat").write_bytes(b"")
    monkeypatch.setattr(tdx_fin, "TDX_FIN_PATH", tmp_path)

    monkeypatch.setattr(adapter, "get_tdx_fin_adapter", lambda: type("A", (), {
        "compute_fundamentals": staticmethod(lambda code, period=None: {
            "profitability": {"ROE": 0.15}, "solvency": {}, "growth": {}})
    })())
    monkeypatch.setattr(adapter, "_fetch_baostock_fundamentals",
                        lambda code: pytest.fail("TDX 可用时不应走兜底"))

    out = adapter.get_stock_fundamentals("999102")
    assert out["_source"] == "tdx"


def test_reader_still_available_on_explicit_call(tmp_path):
    """源层跳过 != 删掉读取器：显式调用仍返回 None（由调用方决定怎么处理）。"""
    reader = tdx_fin.GpcwReader(data_path=str(tmp_path / "absent"))
    assert reader._get_latest_file() is None


def test_data_layer_entry_also_short_circuits(tmp_path, monkeypatch):
    """P-104 第二入口：factors.py:1105 -> data_layer.get_stock_financial。

    只修适配层是不够的——真跑追踪显示 gpcw 告警实际来自这一条路径。
    """
    import lib.data.data_layer as data_layer
    monkeypatch.setattr(tdx_fin, "TDX_FIN_PATH", tmp_path / "absent")

    out = data_layer.get_stock_financial("999103")
    assert out.empty
    assert data_layer.get_financial_codes() == []
