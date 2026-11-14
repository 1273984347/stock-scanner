"""批次 B 第一件事：L3 取证 pin 与 L10 前端路径判据的机器判据。

两条门禁今晚判红都不是业务回归，而是判据自身的结构性缺陷：
- L3：`get_market_sentiment` 漏 pin，取证链路读生产缓存（G2 / P-18 / P-19 复发）；
- L10：把路径数量「36」硬编码进探针，而清单由 L7 从源码生成，数字必然漂移。

本文件锁住修复，并证明两条判据**真的会判红**，不是空判据（坑表 P-54）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load(script_name: str):
    """按项目惯例用 importlib 加载 scripts/ 下的门禁工具。"""
    spec = importlib.util.spec_from_file_location(
        f"_gate_{script_name}", ROOT / "scripts" / f"{script_name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_frontend_file(tmp_path, payload) -> pathlib.Path:
    path = tmp_path / "frontend-api-paths.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ L3 / G2

def test_l3_market_sentiment_pin_survives_poisoned_runtime(monkeypatch):
    """把生产情绪函数换成极端负值，取证链路仍必须得到中性门控。

    若 pin 被删掉，risk_checker 会读到 -1.0 → BLOCK_NEW_RISK，
    L3 的 9 个 risk_check 字段立刻漂移（今晚的实际现象）。
    """
    dual = _load("dual_run_diff")
    import lib.data.data_layer as data_layer
    import lib.strategy.strategies as strategies

    monkeypatch.setattr(data_layer, "get_market_sentiment", lambda *a, **k: -1.0)
    monkeypatch.setattr(strategies, "get_market_sentiment", lambda *a, **k: -1.0)

    with dual.pinned_environment():
        with dual._inject_shared_data(["600519", "000001", "000858"]):
            result = dual.normalize(dual.capture_scan())

    gate = result["risk_check"]["macro_gate"]
    assert gate["state"] == "ALLOW_FULL"
    assert gate["regime"] == "NEUTRAL"
    assert gate["sentiment_mult"] == 1.0
    assert result["risk_check"]["risk_budget"]["equity"] > 0


def test_l3_pin_covers_all_three_binding_sites():
    """G2 机器判据：定义方 + 两个模块级 from-import 使用方都必须登记。"""
    source = (ROOT / "scripts" / "dual_run_diff.py").read_text(encoding="utf-8")
    for module in ("data_layer", "strategies", "pipeline"):
        assert f'({module}, "get_market_sentiment"' in source, (
            f"缺少 {module}.get_market_sentiment 的 pin；延迟 import 走定义方、"
            "模块级 from-import 走使用方，漏一处就会读生产状态"
        )


# ------------------------------------------------------------------ L10

def test_l10_detects_registry_drift_vs_source_scan(tmp_path, monkeypatch):
    """登记清单少一条路径，必须判红（证明不是「自己校验自己」）。"""
    probe = _load("concurrency_probe")
    original = json.loads(probe.FRONTEND_FILE.read_text(encoding="utf-8"))
    tampered = dict(original)
    tampered["normalized_paths"] = original["normalized_paths"][:-1]
    monkeypatch.setattr(probe, "FRONTEND_FILE", _fake_frontend_file(tmp_path, tampered))

    failures, _ = probe._frontend_freeze()
    assert any("drift vs source scan" in f for f in failures), failures


def test_l10_detects_count_tampering(tmp_path, monkeypatch):
    """只把 counts 改大、不动成员，同样必须判红。"""
    probe = _load("concurrency_probe")
    original = json.loads(probe.FRONTEND_FILE.read_text(encoding="utf-8"))
    tampered = dict(original)
    tampered["counts"] = dict(original["counts"])
    tampered["counts"]["normalized_paths"] = 999
    monkeypatch.setattr(probe, "FRONTEND_FILE", _fake_frontend_file(tmp_path, tampered))

    failures, _ = probe._frontend_freeze()
    assert any("counts.normalized_paths" in f for f in failures), failures


def test_l10_no_hardcoded_path_count():
    """数量只能来自源码重算，禁止再把数字抄进探针。"""
    source = (ROOT / "scripts" / "concurrency_probe.py").read_text(encoding="utf-8")
    assert "!= 36" not in source
    assert "expected 36" not in source
    assert "capture_frontend_paths" in source


# ------------------------------------------------------------------ L4 / G1

def test_l4_simple_mode_forwards_start_date(monkeypatch):
    """B2：BacktestRequest.start_date 必须真正到达引擎，否则窗口随日历漂移。"""
    from backtest.unified import BacktestRequest, BacktestRunner
    import backtest.engine as engine

    captured = {}

    def fake_run_backtest(**kwargs):
        captured.update(kwargs)
        return {"metrics": {}, "trades": []}

    monkeypatch.setattr(engine, "run_backtest", fake_run_backtest)
    BacktestRunner().run(BacktestRequest(
        mode="simple", symbols=["600519"], start_date="2025-12-22"))
    assert captured.get("start_date") == "2025-12-22"


def test_l4_full_mode_forwards_start_date(monkeypatch):
    """full 经 run_full_backtest 的 **kwargs 透传，同样必须带上窗口锚点。"""
    from backtest.unified import BacktestRequest, BacktestRunner
    import backtest.full as full

    captured = {}

    def fake_full(**kwargs):
        captured.update(kwargs)
        return {"metrics": {}, "trades": []}

    monkeypatch.setattr(full, "run_full_backtest", fake_full)
    BacktestRunner().run(BacktestRequest(
        mode="full", symbols=["600519"], start_date="2025-12-22"))
    assert captured.get("start_date") == "2025-12-22"


def test_l4_baseline_chain_pins_the_window():
    """G1：取证链路必须显式传常量锚定的 start_date，不能依赖 datetime.now()。"""
    source = (ROOT / "scripts" / "backtest_baseline.py").read_text(encoding="utf-8")
    assert "start_date=PIN_START_DATE" in source
    assert 'BASELINE_ANCHOR_DATE = "2026-11-07"' in source
