"""P1 修复测试：alert_monitor.push_alerts 失败时降级到本地文件 + 计数器 + 日志

P1-1 修复：失败时必须降级，不能仅 warning 后 return False
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class _LogCapture(logging.Handler):
    """绕过 logging.conf propagate=0 的自定义 handler"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def lib_logger_capture():
    """attach 到 lib.* logger，返回 records 列表"""
    log = logging.getLogger("lib.monitoring.alert_monitor")
    cap = _LogCapture()
    log.addHandler(cap)
    # 移除 propagate 屏蔽（handler 直接抓 records）
    old_propagate = log.propagate
    log.setLevel(logging.DEBUG)
    yield cap
    log.removeHandler(cap)
    log.propagate = old_propagate


class TestAlertPushFallback:
    """P1-1: push_alerts 失败时降级测试"""

    def _make_alert(self):
        """制造一个 alert 触发"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = AlertMonitor()
        am.check_drawdown([100, 110, 90], threshold=0.03)  # 触发 18% 回撤
        return am

    def _patch_feishu_pusher_failure(self, exc=ConnectionError("push failed")):
        """返回 context manager: 让 reporter.feishu_pusher.push_report 抛异常"""
        fake_module = MagicMock()
        fake_module.push_report = MagicMock(side_effect=exc)
        return patch.dict("sys.modules", {"reporter.feishu_pusher": fake_module})

    def test_push_failure_writes_local_file(self, lib_logger_capture):
        """推送失败时必须落本地文件 + 计数器 + ERROR 日志"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = self._make_alert()
        assert len(am.alerts) >= 1

        with self._patch_feishu_pusher_failure():
            result = am.push_alerts()

        # 1. 必须返回 False
        assert result is False

        # 2. 必须有 ERROR 级日志（不是 warning）
        error_logs = [r for r in lib_logger_capture.records if r.levelno >= logging.ERROR]
        assert len(error_logs) >= 1, "失败时必须打 ERROR 日志（不能仅 warning）"

        # 3. 失败计数必须存在且 +1
        assert hasattr(am, "_push_failure_count"), "必须维护 _push_failure_count 计数器"
        assert am._push_failure_count == 1

        # 4. 降级文件必须存在
        assert hasattr(am, "_fallback_path"), "必须配置 fallback_path"
        assert am._fallback_path, "fallback_path 必须非空"
        fallback = Path(am._fallback_path)
        assert fallback.exists(), f"降级文件 {fallback} 必须存在"

        # 5. 降级文件内容含 alert 信息
        content = fallback.read_text(encoding="utf-8")
        assert "drawdown" in content or "回撤" in content

    def test_push_success_does_not_increment_failure_count(self):
        """推送成功时失败计数器不变"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = self._make_alert()

        fake_module = MagicMock()
        fake_module.push_report = MagicMock(return_value=True)
        with patch.dict("sys.modules", {"reporter.feishu_pusher": fake_module}):
            result = am.push_alerts()

        assert result is True
        if hasattr(am, "_push_failure_count"):
            assert am._push_failure_count == 0

    def test_repeated_failures_increment_counter(self):
        """多次失败计数器累加（同一实例多次推送）"""
        from lib.monitoring.alert_monitor import AlertMonitor

        fake_module = MagicMock()
        fake_module.push_report = MagicMock(side_effect=RuntimeError("network down"))

        am = self._make_alert()
        with patch.dict("sys.modules", {"reporter.feishu_pusher": fake_module}):
            for i in range(3):
                # 每次都触发新 alert 再推送
                am.check_drawdown([100, 110, 90], threshold=0.03)
                result = am.push_alerts()
                assert result is False
                assert am._push_failure_count == i + 1

    def test_alert_id_in_log_and_file(self, lib_logger_capture):
        """失败时日志和文件必须含 alert_id 标识（便于人工定位）"""
        from lib.monitoring.alert_monitor import AlertMonitor
        am = self._make_alert()

        with self._patch_feishu_pusher_failure():
            am.push_alerts()

        # 1. 日志必须含 alert_id
        error_text = " ".join(
            r.getMessage() for r in lib_logger_capture.records if r.levelno >= logging.ERROR
        )
        assert "alert_id" in error_text, f"日志必须含 alert_id，实际: {error_text!r}"

        # 2. 降级文件也必须含 alert_id（双重保险）
        content = Path(am._fallback_path).read_text(encoding="utf-8")
        assert "alert_id" in content
