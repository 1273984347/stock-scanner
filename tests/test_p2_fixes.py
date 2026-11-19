"""P2 修复判据（批次 C9，2026-11-20）

覆盖：
- P2-2a：`_get_financial_data(live=True)` 实时入口——批量财务路径不再撞防前视
  tripwire（财务约束静默失效 6 个月的修复）；backtest 守卫回归（默认仍 raise）
- P2-2c：scanner.get_realtime_signals 死方法（含 force 参数地雷）已删
- P2-2b：data_quality 死模块已删（零消费零测试）
- 批量路径端到端：缓存命中下 _batch_get_financial_data 返回真实数据

patch 口径（I4）：`data_layer._financial_cache` 实例属性 → 缓存命中短路，
判据全程不触网。
"""

import inspect
from pathlib import Path

import pandas as pd
import pytest

from lib.data import data_layer
from lib.strategy import strategies


@pytest.fixture()
def _warm_financial_cache(monkeypatch):
    """FinancialCache 命中桩：缓存命中分支在腾讯/akshare/EM 网络之前返回。"""
    payload = {
        "profitability": {"gross_margin": 0.91, "ROE": 0.30},
        "solvency": {"debt_to_asset": 0.20},
        "growth": {"revenue_growth": 0.15, "net_income_growth": 0.12},
        "_source": "test",
    }
    monkeypatch.setattr(data_layer._financial_cache, "get", lambda code: payload)


def test_default_still_raises_without_as_of_date():
    """backtest 守卫回归：默认参数（无 as_of_date、live=False）仍硬 raise。"""
    with pytest.raises(NotImplementedError, match="as_of_date"):
        data_layer._get_financial_data("600519")


def test_live_true_bypasses_raise_and_reads_cache(_warm_financial_cache):
    """live=True 实时入口：不再 raise，缓存命中直接返回（不触网）。"""
    series = data_layer._get_financial_data("600519", live=True)
    assert series is not None
    assert series["gross_margin"] == pytest.approx(0.91)
    assert series["revenue_growth"] == pytest.approx(0.15)


def test_batch_financial_returns_real_data(_warm_financial_cache):
    """端到端：批量路径（量价 :413 / 多因子 :691 的调用点）拿到真实数据。"""
    out = strategies._batch_get_financial_data(["600519", "000858"])
    assert out["600519"] is not None, "批量财务路径不得再恒 None"
    assert float(out["600519"]["gross_margin"]) == pytest.approx(0.91)
    assert out["000858"] is not None


def test_signature_still_has_as_of_date():
    """既有 P1-2 pin 保持：as_of_date 参数必须存在（backtest 契约）。"""
    sig = inspect.signature(data_layer._get_financial_data)
    assert "as_of_date" in sig.parameters
    assert "live" in sig.parameters


def test_scanner_dead_method_removed():
    """get_realtime_signals 零调用方 + force 参数地雷——死方法连地雷一起删。"""
    scanner_src = (Path(__file__).resolve().parent.parent / "scanner.py").read_text(
        encoding="utf-8")
    assert "get_realtime_signals" not in scanner_src


def test_data_quality_dead_module_deleted():
    dq = Path(__file__).resolve().parent.parent / "lib" / "data" / "data_quality.py"
    assert not dq.exists(), "DataQualityChecker 零消费零测试——死模块已删（P2-2b）"
