"""ADR-0019 机器判据（2026-11-14）：数据源调用超时——挂起计入熔断。

背景：2026-11-14 周六凌晨管线验证轮实测，BaoStock「网络接收错误」后 socket
无超时挂起——量价突破/多因子的逐股 hist 链路 10+ 分钟无新日志，扫描整体卡死
被人工终止。熔断体系缺口：每源熔断只按「抛错次数」计数，挂起（不抛错、不返回）
既不计失败也不切换——快失败挡得住，慢挂起挡不住。

治理：registry 6 个网络源经 `_call_source_with_timeout`（daemon 线程 + join
超时），超时 → `breaker.record_failure()` 计入失败（达阈值开启，后续调用
fail-fast）→ 抛 SourceCallTimeoutError → 多源循环切下一个源。

⚠️ 挂起桩用 threading.Event().wait 而非 time.sleep：conftest 会把
`lib.data.data_layer.time.sleep`（= 全局 time 模块的 sleep）patch 成 no-op，
sleep 型挂起桩在 pytest 下会瞬间返回假成功（本文件首版因此假失败 3 例）。
"""

import threading
import time

import pandas as pd
import pytest

import lib.data.data_baostock
import lib.data.data_source_registry as registry
from lib.data.cache import CircuitBreaker

_HANG_EVENT = threading.Event()


@pytest.fixture(autouse=True)
def _fresh_circuit_breakers(monkeypatch):
    """隔离全局熔断状态（B3 全量验证时暴露的既有测试隔离缺陷）。

    `monkeypatch.setitem` 只恢复字典的**键**，不恢复 breaker 对象内部累积的
    failure_count / state。全量序列里若前面用例对同一个源记过失败，恢复出来的
    breaker 仍可能是 open，导致多源循环全源 fail-fast —— 表现为
    `AllDataSourcesFailedError: baostock/akshare: empty/invalid`，但单独跑本文件
    又是全绿。每个用例前换成全新实例，让「用例自身行为」成为唯一变量。
    """
    fresh = {name: CircuitBreaker(failure_threshold=5, recovery_timeout=60)
             for name in registry.DATA_SOURCE_PRIORITY}
    monkeypatch.setattr(registry, "_CIRCUIT_BREAKERS", fresh)


@pytest.fixture
def fast_timeout(monkeypatch):
    """把超时压到 0.3s，测试秒级完成。"""
    monkeypatch.setattr(registry, "SOURCE_CALL_TIMEOUT_S", 0.3)


def _hanging_kline(*args, **kwargs):
    """模拟 baostock socket 挂起：不抛错、不返回（Event.wait 不受 sleep patch 影响）。"""
    _HANG_EVENT.wait(5)
    return pd.DataFrame()


def _valid_hist_frame():
    """≥10 行（_validate_dataframe min_rows 契约）的合法 hist 帧。"""
    return pd.DataFrame({
        "日期": [f"2026-11-{d:02d}" for d in range(1, 13)],
        "开盘": [10.0 + i * 0.1 for i in range(12)],
        "收盘": [10.5 + i * 0.1 for i in range(12)],
        "最高": [10.8 + i * 0.1 for i in range(12)],
        "最低": [9.9 + i * 0.1 for i in range(12)],
        "成交量": [1000 + i * 10 for i in range(12)],
    })


def test_hanging_source_times_out_and_opens_breaker(monkeypatch, fast_timeout):
    """挂起源：~0.3s 被裁剪 + 计入熔断失败（threshold=1 即开）+ 返回空帧走降级。"""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    monkeypatch.setitem(registry._CIRCUIT_BREAKERS, "baostock", breaker)
    monkeypatch.setattr(lib.data.data_baostock, "get_kline", _hanging_kline)

    t0 = time.time()
    df = registry._fetch_from_source("000001", 30, "baostock")
    elapsed = time.time() - t0

    assert df.empty, "挂起被裁剪后应返回空帧（多源循环据此切下一源）"
    assert elapsed < 2.0, f"超时应 ~0.3s 生效，实际 {elapsed:.1f}s（30s 默认未生效？）"
    assert breaker.state == "open", "挂起必须被 record_failure 计入，达阈值开启熔断"

    # 熔断开启后：同源再调用直接跳过（fail-fast），不再产生新挂起线程
    t1 = time.time()
    df2 = registry._fetch_from_source("000001", 30, "baostock")
    assert df2.empty and (time.time() - t1) < 0.2, "熔断开启后应快速跳过"


def test_source_completing_within_timeout_unaffected(monkeypatch, fast_timeout):
    """正常完成的源（耗时 < 超时）行为零变化，不计失败。"""
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    monkeypatch.setitem(registry._CIRCUIT_BREAKERS, "baostock", breaker)

    def fast_kline(*args, **kwargs):
        _HANG_EVENT.wait(0.05)
        return _valid_hist_frame().rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume"})

    monkeypatch.setattr(lib.data.data_baostock, "get_kline", fast_kline)

    df = registry._fetch_from_source("000001", 30, "baostock")
    assert not df.empty, "超时内完成的调用必须原样返回"
    assert breaker.state == "closed", "正常完成不得计入失败"


def test_timeout_accumulates_like_failures(monkeypatch, fast_timeout):
    """连续挂起与快失败同权重：3 次挂起（threshold=3）→ 熔断开启。"""
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
    monkeypatch.setitem(registry._CIRCUIT_BREAKERS, "baostock", breaker)
    monkeypatch.setattr(lib.data.data_baostock, "get_kline", _hanging_kline)

    for _ in range(3):
        registry._fetch_from_source("000001", 30, "baostock")
    assert breaker.state == "open", "3 次挂起应等价于 3 次失败"


def test_multi_source_loop_falls_through_hang_to_next_source(monkeypatch, fast_timeout):
    """多源循环：baostock 挂起 → 超时裁剪 → 自动切下一源（akshare mock 成功）。"""
    monkeypatch.setattr(lib.data.data_baostock, "get_kline", _hanging_kline)
    monkeypatch.setattr(registry, "DATA_SOURCE_PRIORITY", ["baostock", "akshare"])
    import akshare as ak
    monkeypatch.setattr(ak, "stock_zh_a_hist",
                        lambda **kwargs: _valid_hist_frame())

    df = registry.get_stock_hist_multi_source("000001", days=30, source="auto")

    assert not df.empty, "baostock 挂起被裁剪后应落到下一源并成功"
    assert set(["date", "open", "close"]).issubset(df.columns), "统一列名契约不变"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
