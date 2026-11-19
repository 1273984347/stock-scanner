"""扫描全局 deadline + ML 富集并行化 + 源挂起计量判据（P1-1，批次 C6，2026-11-20）

覆盖：
- ``_scan_deadline_s`` env 解析（默认 480 / 覆盖 / 非法回默认 / 0=关闭）
- ``_deadline_left`` 检查点语义（超预算 → False + warning；0=关闭恒 True）
- **端到端**：SCAN_DEADLINE_S=0.001 的 run_all_strategies 三个昂贵尾相位全部跳过
  → 部分结果（ml_ranking={}、portfolio 最小形状、scan_deadline 标记含 3 相位）
- ML 富集并行化：并发取数（峰值并发 ≥2）+ 全部候选入缓存（含负缓存）
- 源挂起计量：超时 → hung_threads +1 且抛 SourceCallTimeoutError；worker 完成后
  回落 0（P-81 坑：挂起桩必须用 Event.wait，time.sleep 被 conftest patch 成 no-op）

patch 口径（I4/G2）：pipeline 顶层绑定（compute_all_signals/get_stock_hist_multi_source）
→ patch 使用方 pipeline 属性；registry 常量 SOURCE_CALL_TIMEOUT_S → monkeypatch 模块属性。
"""

import threading
import time

import pandas as pd
import pytest

from lib.data import data_source_registry as reg
from lib.strategy import pipeline


# ---------------------------------------------------------------- deadline 助手


def test_scan_deadline_env_parsing(monkeypatch):
    monkeypatch.setenv("SCAN_DEADLINE_S", "300")
    assert pipeline._scan_deadline_s() == 300.0
    monkeypatch.setenv("SCAN_DEADLINE_S", "not-a-number")
    assert pipeline._scan_deadline_s() == 480.0  # 非法回默认
    monkeypatch.setenv("SCAN_DEADLINE_S", "0")
    assert pipeline._scan_deadline_s() == 0.0  # 0 = 关闭
    monkeypatch.delenv("SCAN_DEADLINE_S", raising=False)
    assert pipeline._scan_deadline_s() == 480.0


def test_deadline_left_semantics(caplog):
    t0 = time.monotonic()
    assert pipeline._deadline_left(t0, 480.0, "组合优化") is True
    # 模拟已耗尽：t0 回拨到预算之前
    exhausted_t0 = time.monotonic() - 481.0
    assert pipeline._deadline_left(exhausted_t0, 480.0, "组合优化") is False
    # 0 = 关闭：即使早已超时也恒放行
    assert pipeline._deadline_left(exhausted_t0, 0.0, "组合优化") is True


# ---------------------------------------------------------------- 端到端部分结果


def test_scan_deadline_skips_tail_phases(monkeypatch):
    """预算 0.001s → 三个昂贵尾相位全跳过：部分结果 + scan_deadline 标记。

    网络/外部依赖由 conftest 默认禁网 + akshare mock 兜住（各相位 except 吞掉）；
    降频跟踪器换 fresh 实例且 save 置空（P2-5 残留：save 写生产 strategy_failures.json）。
    """
    monkeypatch.setenv("SCAN_DEADLINE_S", "0.001")
    fresh = pipeline.StrategyFailureTracker(persist=False)
    monkeypatch.setattr(pipeline, "_failure_tracker", fresh)
    monkeypatch.setattr(fresh, "save", lambda: None)
    scan_result = pipeline.run_all_strategies("balanced")
    assert isinstance(scan_result, dict)
    assert "scan_deadline" in scan_result
    skipped = scan_result["scan_deadline"]["skipped"]
    assert set(skipped) == {"主线板块过滤", "组合优化", "ML 富集"}
    assert scan_result["ml_ranking"] == {}
    assert scan_result["portfolio"] == {"weights": {}, "details": []}
    assert "results" in scan_result and "risk_check" in scan_result  # 部分结果核心保留


def test_scan_deadline_normal_path_no_marker(monkeypatch):
    """0=关闭（回滚值）：正常路径不得新增 scan_deadline 键（L2/L9 基线零漂移）。"""
    monkeypatch.setenv("SCAN_DEADLINE_S", "0")
    fresh = pipeline.StrategyFailureTracker(persist=False)
    monkeypatch.setattr(pipeline, "_failure_tracker", fresh)
    monkeypatch.setattr(fresh, "save", lambda: None)
    scan_result = pipeline.run_all_strategies("balanced")
    assert "scan_deadline" not in scan_result


# ---------------------------------------------------------------- ML 富集并行化


def test_ml_enrich_parallel_and_negative_cache(monkeypatch):
    """取数+因子计算并行（峰值并发 ≥2）+ 全部候选入缓存（含负缓存）。"""
    from lib.ml import ml_predictor

    codes = [f"9{i:05d}" for i in range(20)]  # 唯一码避开进程级 LRU 残留
    results = {"多因子": pd.DataFrame(
        [{"代码": c, "名称": f"股{i}"} for i, c in enumerate(codes)])}

    lock = threading.Lock()
    state = {"cur": 0, "peak": 0}
    blocker = threading.Event()

    def _fake_hist(code, days=180, source="auto", prefer_adjusted=False):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        blocker.wait(0.05)  # 真实阻塞：Event.wait 不受 P-81 的 time.sleep no-op 影响
        with lock:
            state["cur"] -= 1
        idx = pd.date_range("2026-01-01", periods=180, freq="D")
        return pd.DataFrame({
            "date": idx, "open": 10.0, "high": 11.0, "low": 9.0,
            "close": 10.5, "volume": 1000.0,
        })

    monkeypatch.setattr(pipeline, "get_stock_hist_multi_source", _fake_hist)
    monkeypatch.setattr(pipeline, "compute_all_signals",
                        lambda hist, code=None: {"factors": {
                            "momentum_20": 1.0, "momentum_5": 0.5,
                            "rsi_14": 55.0, "volatility_20": 20.0}})

    class _FakePredictor:
        def predict_single(self, factors):
            return {"score": 0.9, "consensus": "BUY", "confidence": 0.9}

    monkeypatch.setattr(ml_predictor, "get_predictor", lambda: _FakePredictor())

    out = pipeline._enrich_with_ml_scores(results)
    assert out["n_candidates"] == 20
    assert out["data"] is not None and len(out["data"]) == 10  # 既有尾部行为：Top 10 输出
    assert state["peak"] >= 2, "取数必须并行（峰值并发 ≥2）"
    for c in codes:
        assert c in pipeline._ml_factor_cache  # 含负缓存语义的全量入缓存


def test_ml_enrich_negative_cache_skips_refetch(monkeypatch):
    """空历史 → None 入缓存；同码二次调用不再触发取数。"""
    calls = []

    def _fake_hist(code, days=180, source="auto", prefer_adjusted=False):
        calls.append(code)
        return pd.DataFrame()  # 空 → None 负缓存

    monkeypatch.setattr(pipeline, "get_stock_hist_multi_source", _fake_hist)
    results = {"多因子": pd.DataFrame([{"代码": "999998", "名称": "x"}])}
    out1 = pipeline._enrich_with_ml_scores(results)
    assert out1["n_candidates"] == 1 and out1["data"] is None
    pipeline._enrich_with_ml_scores(results)
    assert len(calls) == 1  # 负缓存命中，不再取数


# ---------------------------------------------------------------- 源挂起计量


def test_source_hang_counter_increments_and_recovers(monkeypatch):
    """超时 → hung +1 且抛 SourceCallTimeoutError；worker 完成后回落 0。"""
    monkeypatch.setattr(reg, "SOURCE_CALL_TIMEOUT_S", 0.05)
    reg.reset_circuit_breakers()
    before = reg.get_source_hang_stats()["hung_threads"]

    def _hang():
        threading.Event().wait(0.6)  # P-81：不能用 time.sleep（被 conftest no-op）
        return _ohlcv_row()

    with pytest.raises(reg.SourceCallTimeoutError):
        reg._call_source_with_timeout(reg._CIRCUIT_BREAKERS["sina_kline"], _hang)
    assert reg.get_source_hang_stats()["hung_threads"] == before + 1

    deadline = time.monotonic() + 3.0
    while (time.monotonic() < deadline
           and reg.get_source_hang_stats()["hung_threads"] > before):
        time.sleep(0.02)  # 计量读数轮询；非挂起语义
    assert reg.get_source_hang_stats()["hung_threads"] == before


def test_source_hang_counter_zero_when_fast():
    """未超时的正常调用不产生 hung 计数。"""
    reg.reset_circuit_breakers()
    before = reg.get_source_hang_stats()["hung_threads"]
    df = reg._call_source_with_timeout(
        reg._CIRCUIT_BREAKERS["sina_kline"], lambda: _ohlcv_row())
    assert not df.empty
    assert reg.get_source_hang_stats()["hung_threads"] == before


def _ohlcv_row() -> pd.DataFrame:
    return pd.DataFrame([{
        "date": "2026-11-19", "open": 10.0, "high": 11.0,
        "low": 9.0, "close": 10.5, "volume": 1000.0,
    }])
