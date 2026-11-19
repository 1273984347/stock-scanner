"""交易日历机器判据（架构评审 P0 修复批次，2026-11-20，坑表 P-108）

覆盖：
- 周末短路不触网、不读盘（conftest 默认禁网兜底：任何漏网打网即判红）
- 缓存命中：交易日 True / 合成节假日 False，且不刷网
- 缓存缺失 + 刷取成功：结果正确 + 落盘到隔离后的 tmp cache（P-80 写路径纪律）
- 刷取失败：显式退化为周末判断 + warning 留痕（不静默）+ 冷却期内不重复打网
- 损坏缓存按 miss 处理不崩溃
- 三处消费端（run.py 门 / daily_scan / scheduler）接线守卫：都吃同一真源
- 进程级单例按用例清零（P-100 纪律，autouse fixture）

patch 口径（RULES I4 / G2）：trade_calendar 自身的 ``_fetch_trade_days`` 在定义方 patch；
run.py 顶层绑定 → patch 使用方 ``run.is_trading_day``；
daily_scan / scheduler 函数内延迟 import → patch 定义方 ``lib.data.trade_calendar.is_trading_day``。

日志断言陷阱：tests/conftest.py 的 fileConfig 把 ``lib`` logger 配成 propagate=0
（自带 console handler）→ pytest caplog（挂在 root）**收不到任何 lib.* 记录**。
lib.* 的告警断言必须用直挂目标 logger 的 _ListHandler，caplog 只对根命名空间（如 run）有效。
"""

import inspect
import json
import logging
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.data import trade_calendar


class _ListHandler(logging.Handler):
    """直挂目标 logger 的记录收集器（绕开 conftest fileConfig 的 propagate=0）。"""

    def __init__(self):
        super().__init__()
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture(autouse=True)
def _reset_calendar_singleton():
    """进程级单例按用例清零（P-100）。"""
    trade_calendar.reset_for_tests()
    yield
    trade_calendar.reset_for_tests()


def _cache_path() -> Path:
    from lib.data.data_layer import CACHE_DIR

    return Path(CACHE_DIR) / "calendar" / "trade_days.json"


def _seed_cache(days_iso: list) -> Path:
    """直接种缓存文件（走隔离后的 tmp cache root）。"""
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated": "2026-03-04", "source": "test", "trade_days": days_iso}),
        encoding="utf-8",
    )
    return path


# 合成日历：2026-03-02(一) 与 2026-03-04(三) 为交易日，03-03(二) 合成节假日，
# 覆盖到 03-04 —— 命中/节假日判定都不触网。
_SYNTHETIC_DAYS = ["2026-03-02", "2026-03-04"]
_MON = date(2026, 3, 2)
_SYNTH_HOLIDAY = date(2026, 3, 3)
_WED = date(2026, 3, 4)
_SAT = date(2026, 3, 7)
_SUN = date(2026, 3, 8)


def test_weekend_short_circuits_without_fetch_or_disk():
    fetch = MagicMock(side_effect=AssertionError("周末短路后不得触网"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "_fetch_trade_days", fetch)
        assert trade_calendar.is_trading_day(_SAT) is False
        assert trade_calendar.is_trading_day(_SUN) is False
    fetch.assert_not_called()
    assert not _cache_path().exists()  # 短路路径不产生任何 IO


def test_cached_calendar_hit_trading_and_holiday_without_fetch():
    _seed_cache(_SYNTHETIC_DAYS)
    fetch = MagicMock(side_effect=AssertionError("缓存覆盖当天不得刷网"))
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "_fetch_trade_days", fetch)
        assert trade_calendar.is_trading_day(_MON) is True
        assert trade_calendar.is_trading_day(_SYNTH_HOLIDAY) is False
    fetch.assert_not_called()


def test_cache_miss_fetch_success_writes_isolated_cache(tmp_path):
    calls = []

    def _fake_fetch():
        calls.append(1)
        return {date(2026, 3, 2), date(2026, 3, 3)}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "_fetch_trade_days", _fake_fetch)
        assert trade_calendar.is_trading_day(_MON) is True
        # 落盘必须落在 conftest 隔离后的 tmp root（P-80 纪律）
        path = _cache_path()
        assert path.exists() and path.parent.is_relative_to(tmp_path)
        # 第二次调用走进程单例，不再刷网
        assert trade_calendar.is_trading_day(date(2026, 3, 3)) is True
    assert len(calls) == 1


def test_fetch_failure_degrades_to_weekday_only_with_warning():
    handler = _ListHandler()
    trade_calendar.logger.addHandler(handler)

    def _boom():
        raise ConnectionError("calendar source down")

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(trade_calendar, "_fetch_trade_days", _boom)
            # 工作日：显式退化放行（与历史兜底方向一致）
            assert trade_calendar.is_trading_day(_WED) is True
            # 周末：仍拒绝
            assert trade_calendar.is_trading_day(_SAT) is False
    finally:
        trade_calendar.logger.removeHandler(handler)
    assert any("退化为周末判断" in r.getMessage() for r in handler.records)


def test_refresh_cooldown_no_refetch_after_failure(caplog):
    calls = []

    def _boom():
        calls.append(1)
        raise ConnectionError("calendar source down")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "_fetch_trade_days", _boom)
        with caplog.at_level(logging.WARNING, logger="lib.data.trade_calendar"):
            assert trade_calendar.is_trading_day(_WED) is True
            assert trade_calendar.is_trading_day(_MON) is True
    assert len(calls) == 1  # 冷却期内第二次调用不再打网


def test_corrupt_cache_treated_as_miss():
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")

    handler = _ListHandler()
    trade_calendar.logger.addHandler(handler)

    def _boom():
        raise ConnectionError("calendar source down")

    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(trade_calendar, "_fetch_trade_days", _boom)
            assert trade_calendar.is_trading_day(_WED) is True
    finally:
        trade_calendar.logger.removeHandler(handler)
    assert any("缓存读取失败按 miss 处理" in r.getMessage() for r in handler.records)


def test_calendar_health_readonly():
    _seed_cache(_SYNTHETIC_DAYS)
    health = trade_calendar.calendar_health()
    assert health["cached_days"] == 2
    assert health["max_date"] == "2026-03-04"
    assert "calendar" in health["cache_file"]


# ---------------------------------------------------------------------------
# 消费端接线守卫：三处判定必须吃同一真源
# ---------------------------------------------------------------------------


def test_daily_scan_delegates_to_calendar():
    import scripts.daily_scan as daily_scan

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "is_trading_day", lambda: False)
        assert daily_scan._is_trading_day() is False
        mp.setattr(trade_calendar, "is_trading_day", lambda: True)
        assert daily_scan._is_trading_day() is True


def test_scheduler_delegates_to_calendar():
    from reporter import scheduler

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(trade_calendar, "is_trading_day", lambda: False)
        assert scheduler.is_trading_day() is False


def test_run_gate_blocks_on_non_trading_day(caplog):
    import run as run_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_module, "is_trading_day", lambda: False)
        with caplog.at_level(logging.WARNING, logger="run"):
            assert run_module._trading_day_gate(False) is False
            assert any("跳过盘后扫描" in r.message for r in caplog.records)


def test_run_gate_passes_on_trading_day():
    import run as run_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_module, "is_trading_day", lambda: True)
        assert run_module._trading_day_gate(False) is True


def test_run_gate_explicit_bypass_wins():
    import run as run_module

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(run_module, "is_trading_day", lambda: False)
        assert run_module._trading_day_gate(True) is True


def test_run_main_wires_gate_and_flag():
    """接线守卫：main() 必须消费交易日门与 --allow-non-trading-day（防未来漂移）。"""
    import run as run_module

    main_src = inspect.getsource(run_module.main)
    assert "_trading_day_gate(args.allow_non_trading_day)" in main_src
    assert "--allow-non-trading-day" in main_src
