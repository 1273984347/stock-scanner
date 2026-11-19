"""推送倒置判据（P1-6 二阶段，批次 C10，2026-11-20）

覆盖：
- push_sink 语义：未注册 → False + warning（可见降级，不静默）；已注册 → 透传
  结果；sink 异常 → False + error（推送失败不炸业务链路）
- 倒置回归守卫：pipeline / paper_trading_runner / alert_monitor 三处不得再
  lazy import reporter（lib 层边界，尺子 EXCEPTIONS 已撤销对应条目）
- builder 下沉：lib/monitoring/alert_content 为真源；scan_notify 兼容 re-export
  与下沉实现是同一对象
- sink 注册：feishu_pusher 被 import 即注册（同一对象透传）

patch 口径（I4）：push_sink._sink → 直接 setattr（模块级单例，测试后复原）；
日志断言直挂 handler（P-108：lib.* propagate=0，caplog 收不到）。
"""

import ast
import inspect
import logging
from pathlib import Path

import pytest

from lib.infra import push_sink
from lib.monitoring import alert_content


class _ListHandler(logging.Handler):
    """直挂目标 logger 的收集器（绕开 conftest fileConfig 的 lib propagate=0）。"""

    def __init__(self):
        super().__init__()
        self.records: list = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture(autouse=True)
def _restore_sink():
    saved = push_sink._sink
    yield
    push_sink._sink = saved


def test_push_text_without_sink_degrades_visibly():
    push_sink._sink = None
    handler = _ListHandler()
    push_sink.logger.addHandler(handler)
    try:
        assert push_sink.push_text("预算超限", title="告警") is False
    finally:
        push_sink.logger.removeHandler(handler)
    assert any("未注册" in r.getMessage() for r in handler.records)


def test_push_text_delegates_to_registered_sink():
    calls = []

    push_sink.set_push_sink(lambda text: (calls.append(text), True)[1])
    assert push_sink.push_text("日报正文") is True
    assert calls == ["日报正文"]


def test_push_text_swallows_sink_exception():
    def _boom(text):
        raise RuntimeError("webhook down")

    push_sink.set_push_sink(_boom)
    handler = _ListHandler()
    push_sink.logger.addHandler(handler)
    try:
        assert push_sink.push_text("x") is False
    finally:
        push_sink.logger.removeHandler(handler)
    assert any("sink 异常" in r.getMessage() for r in handler.records)


def test_three_lib_sites_no_longer_import_reporter():
    """倒置回归守卫：三处 lib 推送点的**代码**（AST import 节点，免疫注释）不得再
    引 reporter（尺子例外已撤销）。"""
    root = Path(__file__).resolve().parent.parent
    for rel in ("lib/strategy/pipeline.py", "lib/strategy/paper_trading_runner.py",
                "lib/monitoring/alert_monitor.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] == "reporter":
                    offenders.append(f"{node.module}: line {node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "reporter":
                        offenders.append(f"{alias.name}: line {node.lineno}")
        assert not offenders, f"{rel} 仍引 reporter: {offenders}"


def test_alert_content_builder_moved_to_lib():
    """builder 真源在 lib；scan_notify re-export 与之同一对象（contract 测试兼容）。"""
    from reporter import scan_notify

    assert scan_notify.build_alert_content is alert_content.build_alert_content
    text = alert_content.build_alert_content(
        "ic_decay_alert", [{"severity": "high", "message": "IC 衰减"}])
    assert "IC 衰减告警" in text and "[high]" in text


def test_feishu_pusher_registers_sink_on_import():
    """reporter 被 import 即注册：sink 是 push_report 的单参包装。"""
    import reporter.feishu_pusher  # noqa: F401
    assert push_sink._sink is not None
    assert push_sink.push_text("注册验证") in (True, False)  # 不抛即契约


def test_exceptions_registry_shrunk():
    """推送倒置后 strategy/monitoring→reporter 例外必须撤销（防回潮）。"""
    mod = _load_dep_check()
    assert ("strategy", "reporter") not in mod.EXCEPTIONS
    assert ("monitoring", "reporter") not in mod.EXCEPTIONS


def _load_dep_check():
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "dep_direction_check", root / "scripts" / "dep_direction_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_entries_register_sink():
    """三个扫描入口显式 import reporter.feishu_pusher（API/CLI/日扫路径）。"""
    root = Path(__file__).resolve().parent.parent
    for rel in ("run.py", "api.py", "scripts/daily_scan.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "import reporter.feishu_pusher" in src, rel
