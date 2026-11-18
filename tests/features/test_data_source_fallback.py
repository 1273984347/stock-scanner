"""批次 D：数据源可用性根因修复的机器判据。

两个根因（2026-11-18 实测）：
  1. 回测引擎 7 处硬编码 source="tdx_local_qfq"（本地 TDX 目录已被用户删除），
     导致回测永远拿到空数据——不是「没有数据源」，是被钉死在死源上。
  2. get_index_hist 只有「本地 TDX → pytdx 网络」两条路，都不可达时返回空，
     回测在 hs300['close'] 处 KeyError 崩溃。

判据全部用 AST / 静态检查 + 纯函数，不打真实网络（网络可用性由集成实测覆盖）。
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# 这些文件里曾硬编码死源，现必须走 auto + prefer_adjusted。
BACKTEST_FILES = [
    "backtest/engine.py", "backtest/full.py",
    "scripts/backtest_regime_analysis.py", "scripts/backtest_simple.py",
]


def test_no_hardcoded_dead_tdx_source_in_backtest():
    """静态守卫：回测/扫描脚本不得再钉死 tdx_local_qfq。"""
    offenders = []
    for rel in BACKTEST_FILES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        if 'source="tdx_local_qfq"' in text or "source='tdx_local_qfq'" in text:
            offenders.append(rel)
    assert offenders == [], f"hardcoded dead source in: {offenders}"


def test_backtest_uses_auto_with_prefer_adjusted():
    """每处取数都必须 auto + 前复权优先，否则回测口径会退化。"""
    found = 0
    for rel in BACKTEST_FILES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kws = {kw.arg: kw.value for kw in node.keywords}
            if "source" not in kws:
                continue
            src = kws["source"]
            if isinstance(src, ast.Constant) and src.value == "auto":
                found += 1
                assert "prefer_adjusted" in kws, f"{rel}: auto 未配 prefer_adjusted"
                adj = kws["prefer_adjusted"]
                assert isinstance(adj, ast.Constant) and adj.value is True
    assert found >= 7, f"expected >=7 auto+adjusted call sites, found {found}"


def test_index_market_mapping_is_correct():
    """指数代码映射不能复用个股 detect_market（000300 必须是 sh 不是 sz）。"""
    from lib.data.data_baostock import _to_bs_index_code
    assert _to_bs_index_code("000300") == "sh.000300"   # 沪深300
    assert _to_bs_index_code("000905") == "sh.000905"   # 中证500
    assert _to_bs_index_code("399001") == "sz.399001"   # 深证成指
    assert _to_bs_index_code("sh.000300") == "sh.000300"  # 幂等


def test_index_hist_has_baostock_fallback(monkeypatch):
    """指数取数在前两源失败时必须落到 baostock，且列契约完整。"""
    import importlib
    import pandas as pd
    mod = importlib.import_module("lib.data.data_layer")

    # 屏蔽前两条死路：本地 TDX 与 pytdx 网络都返回空。
    monkeypatch.setattr("lib.data.data_tdx.get_index_local", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("lib.data.data_tdx.get_index_daily", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(mod, "_load_cache", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(mod, "_save_cache", lambda *a, **k: None)

    fake = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-02", "2026-01-03"]),
        "open": [1.0, 2.0], "high": [2.0, 3.0], "low": [0.5, 1.5],
        "close": [1.5, 2.5], "volume": [10, 20], "amount": [100, 200],
    })
    monkeypatch.setattr("lib.data.data_baostock.get_index_kline",
                        lambda *a, **k: fake.copy())

    df = mod.get_index_hist("000300", days=60)
    assert not df.empty
    assert "close" in df.columns
    assert len(df) == 2


def test_index_hist_returns_empty_on_all_sources_failing(monkeypatch):
    """所有源都失败时返回空 DataFrame（不抛异常），调用方已有 empty 守卫。"""
    import importlib
    import pandas as pd
    mod = importlib.import_module("lib.data.data_layer")
    monkeypatch.setattr("lib.data.data_tdx.get_index_local", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("lib.data.data_tdx.get_index_daily", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr("lib.data.data_baostock.get_index_kline", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(mod, "_load_cache", lambda *a, **k: pd.DataFrame())
    assert mod.get_index_hist("000300", days=60).empty


def test_baostock_primary_source_is_available():
    """数据源优先级里 baostock 必须在首位（当前环境唯一可用的真实源）。"""
    from lib.data.data_source_registry import (
        DATA_SOURCE_PRIORITY, DATA_SOURCE_PRIORITY_ADJUSTED)
    assert DATA_SOURCE_PRIORITY[0] == "baostock"
    assert DATA_SOURCE_PRIORITY_ADJUSTED[0] == "baostock"
