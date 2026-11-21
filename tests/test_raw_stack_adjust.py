"""Δ5/N2 判据（2026-11-22）：源 × 复权口径 × 仓 三元组收口。

评审依据：docs/audit/architecture-review-delta-2026-11-22.md Δ5（v2 扩容）——
raw 栈里 tencent 无条件 adjust="qfq"、westock 默认 qfq、ADJUSTED 栈里排着
raw-only 的 baostock/akshare——三个同根因的口径污染洞（写仓层零口径校验）。
修复 = SOURCE_ADJUST_CAPABILITY 能力表 + 栈过滤（宁缺毋假）+ 双口径源按意图
传参。G3 实测（600519 跨 4 次除权 500 日窗）：tencent adjust="" 与 westock
fq="bfq" 均返真不复权且逐值对齐。
"""
import pandas as pd
import pytest

import lib.data.data_source_registry as reg


def test_capability_table_pins_dual_mode_sources():
    """双口径源与 raw-only 源的能力钉死——改表必须过本判据。"""
    assert reg.SOURCE_ADJUST_CAPABILITY["tencent_kline"] == {"raw": True, "qfq": True}
    assert reg.SOURCE_ADJUST_CAPABILITY["westock"] == {"raw": True, "qfq": True}
    assert reg.SOURCE_ADJUST_CAPABILITY["sina_kline"] == {"raw": True, "qfq": False}
    assert reg.SOURCE_ADJUST_CAPABILITY["akshare"] == {"raw": False, "qfq": True}
    assert reg.SOURCE_ADJUST_CAPABILITY["tdx_local_qfq"] == {"raw": False, "qfq": True}


def test_raw_stack_excludes_qfq_only_sources():
    """raw 栈不得含 qfq-only 源（tdx_local_qfq 曾排在尾部——反向污染洞）。"""
    raw = reg._stack_for(False)
    assert "sina_kline" in raw and "tencent_kline" in raw and "westock" in raw
    assert "tdx_local_qfq" not in raw, "qfq-only 源进 raw 栈 = kline_raw 污染"


def test_adjusted_stack_excludes_raw_only_sources():
    """qfq 栈不得含 raw-only 源（baostock/tushare/tdx/adata/tdx_local 曾在栈内
    ——腾讯失败时 raw 价直接写进 kline 仓）。akshare 分支固定 adjust="qfq"，
    属 qfq-capable，保留在栈内。"""
    adj = reg._stack_for(True)
    assert adj[0] == "tencent_kline"
    for raw_only in ("baostock", "tushare", "tdx", "adata", "tdx_local"):
        assert raw_only not in adj, f"raw-only 源 {raw_only} 不得进前复权栈"
    assert "westock" in adj and "akshare" in adj and "tdx_local_qfq" in adj


def _fake_ohlcv(**kwargs):
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "open": [1.0, 1.0], "high": [1.0, 1.0],
        "low": [1.0, 1.0], "close": [1.0, 1.0], "volume": [100.0, 100.0],
    })


def test_tencent_branch_adjust_by_intent(monkeypatch):
    """tencent 分支：raw 意图 → adjust=""（G3 实测真不复权），qfq 意图 → qfq。"""
    captured = {}

    def fake(code, days=250, adjust="qfq", as_of_date=None):
        captured["adjust"] = adjust
        return _fake_ohlcv()

    monkeypatch.setattr("lib.data.data_kline_web.get_tencent_kline", fake)
    reg._fetch_from_source("600519", 30, "tencent_kline", prefer_adjusted=False)
    assert captured["adjust"] == "", "raw 栈 tencent 必须 adjust=''（Δ5 主洞）"
    reg._fetch_from_source("600519", 30, "tencent_kline", prefer_adjusted=True)
    assert captured["adjust"] == "qfq", "回测栈 tencent 保持 qfq（T7-e）"


def test_westock_branch_fq_by_intent(monkeypatch):
    """westock 分支：raw 意图 → fq="bfq"（CLI 文档口径=不复权），qfq → qfq。"""
    captured = {}

    def fake(code, period="day", limit=200, fq="qfq", as_of_date=None):
        captured["fq"] = fq
        return pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01"]),
            "open": ["1.0"], "last": ["1.0"], "high": ["1.0"],
            "low": ["1.0"], "volume": ["100.0"],
        })

    monkeypatch.setattr("lib.data.data_westock.get_kline", fake)
    reg._fetch_from_source("600519", 30, "westock", prefer_adjusted=False)
    assert captured["fq"] == "bfq", "raw 栈 westock 必须 fq='bfq'（非空串，G3 实测口径）"
    reg._fetch_from_source("600519", 30, "westock", prefer_adjusted=True)
    assert captured["fq"] == "qfq"


def test_explicit_source_capability_guard(monkeypatch):
    """显式 source 组合的能力守卫：raw-only 源被要求 qfq 时取数前拒绝（不触网）。"""
    called = {}

    def fake(*a, **k):
        called["hit"] = True
        return _fake_ohlcv()

    monkeypatch.setattr("lib.data.data_baostock.get_kline", fake)
    out = reg._fetch_from_source("600519", 30, "baostock", prefer_adjusted=True)
    assert out.empty and not called, "baostock 不具备 qfq 能力，必须在取数前拒绝"
    out2 = reg._fetch_from_source("600519", 30, "baostock", prefer_adjusted=False)
    assert not out2.empty, "raw 语义下 baostock 正常可用"
