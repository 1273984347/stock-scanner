"""推送心跳哨兵判据（评审 P0-4，批次 C4，2026-11-20）

覆盖：
- push_log 缺失 / 空 / 全失败 / 损坏 → stale=True（最强失联信号）
- 末条成功推送按**交易日**计数判定（复核 B5：节假日按日历跳过，春节不再误报；
  周末 gap 不判失联）——日历用 C1 的 trade_calendar 种缓存法合成（确定性）
- scan_notify 三次退避重试：两败一成 → True + push_log success=True；全败 → False
  + push_log success=False；token 失败也落日志（P-79 教训：推送链路必须有日志）
- run.py main() 推送返回值接线 + 非零退出码守卫；晨报入口心跳自检接线守卫

patch 口径（I4/G2）：
- ``feishu_pusher.PUSH_LOG_PATH`` → tmp（相对路径硬编码不在 conftest 隔离名单，
  P2-5 残留——判据必须自行重定向，绝不写生产 push_log）
- ``scan_notify.requests`` → 整体桩对象（使用方模块属性，I4）
- trade_calendar 进程单例按用例清零（P-100）+ 种缓存（继承 conftest CACHE_DIR 重定向）
"""

import inspect
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from reporter import feishu_pusher, heartbeat, scheduler, scan_notify
from lib.data import trade_calendar


# 合成交易日历：2026-03-02(一) ~ 03-13(五) 全部工作日，周末 07/08 缺席
_SYNTH_WEEKDAYS = ["2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05",
                   "2026-03-06", "2026-03-09", "2026-03-10", "2026-03-11",
                   "2026-03-12", "2026-03-13"]


@pytest.fixture(autouse=True)
def _reset_calendar_singleton():
    trade_calendar.reset_for_tests()
    yield
    trade_calendar.reset_for_tests()


@pytest.fixture()
def push_log_path(tmp_path, monkeypatch):
    """push_log 重定向到 tmp（生产 push_log 不可被测试读写污染）。"""
    p = tmp_path / "reports" / "push_log.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(feishu_pusher, "PUSH_LOG_PATH", p)
    return p


def _seed_calendar():
    from lib.data.data_layer import CACHE_DIR

    path = Path(CACHE_DIR) / "calendar" / "trade_days.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"updated": "2026-03-13", "source": "test",
                    "trade_days": _SYNTH_WEEKDAYS}),
        encoding="utf-8",
    )


def _write_log(path, entries):
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 心跳判定


def test_missing_push_log_is_stale(push_log_path):
    _seed_calendar()
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is True
    assert "不存在" in hb["reason"]


def test_all_failures_is_stale(push_log_path):
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-11T12:00:00+00:00", "type": "scan_notify",
         "success": False, "detail": "boom"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is True
    assert "无成功条目" in hb["reason"]


def test_corrupt_log_is_stale(push_log_path):
    _seed_calendar()
    push_log_path.write_text("{not-json", encoding="utf-8")
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is True


def test_recent_success_not_stale(push_log_path):
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-11T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is False
    assert hb["trading_days_since"] == 1


def test_same_day_success_not_stale(push_log_path):
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-12T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is False
    assert hb["trading_days_since"] == 0


def test_stale_after_two_trading_days(push_log_path):
    """周五成功 → 下周四 = 4 个交易日 > 容忍 2 → 失联（节假日按日历跳过）。"""
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-06T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is True
    assert hb["trading_days_since"] == 4


def test_weekend_gap_not_stale(push_log_path):
    """周五成功 → 下周一：仅 1 个交易日（周末跳过）→ 不失联。"""
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-06T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 9))
    assert hb["stale"] is False
    assert hb["trading_days_since"] == 1


def test_latest_success_entry_wins(push_log_path):
    """多条记录取最后一条成功的（中间夹失败不影响）。"""
    _seed_calendar()
    _write_log(push_log_path, [
        {"timestamp": "2026-03-05T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
        {"timestamp": "2026-03-06T12:00:00+00:00", "type": "scan_notify",
         "success": False, "detail": "boom"},
        {"timestamp": "2026-03-11T12:00:00+00:00", "type": "scan_notify",
         "success": True, "detail": "ok"},
    ])
    hb = heartbeat.check_push_heartbeat(today=date(2026, 3, 12))
    assert hb["stale"] is False
    assert hb["last_success_at"] == "2026-03-11"


# ---------------------------------------------------------------- scan_notify 重试+日志


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeRequests:
    """按序回放的 requests 桩（使用方模块属性整体替换，I4）。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else {"code": 0}
        if isinstance(outcome, Exception):
            raise outcome
        return _FakeResp(outcome)


@pytest.fixture()
def _scan_notify_wired(monkeypatch, push_log_path):
    """send_feishu_message 的外部依赖全部打桩：token/接收者/HTTP/文本构造。"""
    monkeypatch.setattr(scan_notify, "FEISHU_RECEIVE_ID", "rid-test")
    monkeypatch.setattr(scan_notify, "get_tenant_token", lambda: "tok-test")
    monkeypatch.setattr(scan_notify, "build_message_content",
                        lambda results, preset="balanced": "content")
    return push_log_path


_RESULTS = {"多因子": pd.DataFrame([{"代码": "600519", "名称": "贵州茅台"}])}


def test_retry_then_success_logs_once(_scan_notify_wired, monkeypatch):
    fake = _FakeRequests([Exception("boom"), {"code": 999}, {"code": 0}])
    monkeypatch.setattr(scan_notify, "requests", fake)
    ok = scan_notify.send_feishu_message(_RESULTS, "balanced")
    assert ok is True
    assert fake.calls == 3  # 3 次退避重试（P-81：sleep 为 no-op，只数次数）
    entries = json.loads(feishu_pusher.PUSH_LOG_PATH.read_text(encoding="utf-8"))
    assert len(entries) == 1  # 重试只记最终结果一条
    assert entries[0]["type"] == "scan_notify" and entries[0]["success"] is True


def test_retry_exhausted_returns_false(_scan_notify_wired, monkeypatch):
    fake = _FakeRequests([Exception("boom")] * 5)
    monkeypatch.setattr(scan_notify, "requests", fake)
    ok = scan_notify.send_feishu_message(_RESULTS, "balanced")
    assert ok is False
    assert fake.calls == 3
    entries = json.loads(feishu_pusher.PUSH_LOG_PATH.read_text(encoding="utf-8"))
    assert entries[0]["success"] is False


def test_token_failure_still_logged(_scan_notify_wired, monkeypatch):
    """token 失败也写 push_log——推送链路不允许「无日志失败」（P-79 教训）。"""
    monkeypatch.setattr(scan_notify, "get_tenant_token", lambda: "")
    ok = scan_notify.send_feishu_message(_RESULTS, "balanced")
    assert ok is False
    entries = json.loads(feishu_pusher.PUSH_LOG_PATH.read_text(encoding="utf-8"))
    assert entries[0]["success"] is False
    assert "token" in entries[0]["detail"]


# ---------------------------------------------------------------- 接线守卫


def test_run_main_wires_push_result_and_exit_code():
    """run.py main() 必须消费推送返回值并在失败时非零退出（P1-5/P0-4）。"""
    import run as run_module

    src = inspect.getsource(run_module.main)
    assert "push_ok = send_feishu_message" in src
    assert "if not push_ok:" in src and "sys.exit(1)" in src


def test_run_rebalance_wires_push_result():
    """调仓推送点同样接返回值（不设退出码——mark_rebalanced 必须执行）。"""
    import run as run_module

    src = inspect.getsource(run_module.run_rebalance_mode)
    assert "rebalance_push_ok = send_feishu_message" in src


def test_scheduler_morning_wires_heartbeat():
    """晨报入口必须接心跳自检（失联告警并入报告文本）。"""
    src = inspect.getsource(scheduler.send_morning_report)
    assert "check_push_heartbeat" in src
    assert "推送心跳告警" in src


def test_precommit_has_prepush_stage():
    """pre-commit 必须登记 pre-push 快判 hook（CI 停摆期的回归兜底）。"""
    yaml_text = (Path(__file__).resolve().parent.parent
                 / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    assert "l1-quick-before-push" in yaml_text
    assert "stages: [pre-push]" in yaml_text
