"""P-89 / G2 复发判据（2026-11-19）：L3 取证链路不得读生产指数缓存。

现象：同一天 L3 两次漂移，都是 `scan.macro_filter.index_ma20`
（3648.3976 → 3644.3770 → 3644.3773）。根因是 `pipeline.py:662` 的
`get_index_hist` 不在 `_inject_shared_data` 的 pin 名单里——它读
`cache/index/000001/30.parquet`，真实扫描刷新缓存后数值就变。

修法：在**定义方 data_layer 与使用方 pipeline 两处**都 pin 到固定种子的
`synth_ohlcv`（P-18/P-19 的双处 pin 纪律）。基线因源栈切换已重取过一次，
本判据保证此后不再因生产缓存变化而漂移。
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "_dual_run_diff", ROOT / "scripts" / "dual_run_diff.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_index_hist_pinned_at_both_definition_and_usage():
    """静态守卫：漏掉任意一处都会让 L3 重新读活数据（P-89 的直接成因）。"""
    src = (ROOT / "scripts/dual_run_diff.py").read_text(encoding="utf-8")
    assert '(data_layer, "get_index_hist", fake_index_hist)' in src
    assert '(pipeline, "get_index_hist", fake_index_hist)' in src
    assert "synth_ohlcv(code, n=max(30" in src, \
        "必须用固定种子合成序列；常数序列会让回测基准收益恒为 0"


def test_pinned_index_differs_from_live_and_restores(monkeypatch):
    """行为判据：pin 期间指数值与 live 数据不同，退出后原函数恢复。

    live 侧不读任何生产状态（P-89 纪律）：ADR-0033 本地仓收官把
    get_index_hist 改为「本地仓 → 在线」，旧 cache/ 兜底已删除；
    pytest 默认 QUANT_LOCAL_STORE=off + 禁网 ⇒ live 侧原本必然空帧
    （KeyError: 'close'，2026-11-21 定因）。改为在**在线边界**
    data_layer._get_index_hist_online 注入固定 ~3600 帧——只换数据
    来源，断言不变。
    """
    gate = _load_gate()
    import numpy as np
    import pandas as pd

    import lib.data.data_layer as data_layer
    import lib.strategy.pipeline as pipeline

    rng = np.random.RandomState(4242)
    close = 3600.0 + np.cumsum(rng.randn(30) * 5.0)
    live_frame = pd.DataFrame({
        "date": pd.bdate_range("2025-05-01", periods=30),
        "open": close - rng.rand(30) * 3.0,
        "high": close + rng.rand(30) * 5.0,
        "low": close - rng.rand(30) * 5.0,
        "close": close,
        "volume": rng.randint(1_000_000, 9_000_000, 30).astype("int64"),
    })
    monkeypatch.setattr(
        data_layer, "_get_index_hist_online",
        lambda code, days=180: live_frame.copy(),
    )

    original = pipeline.get_index_hist
    # get_index_hist 的 pin 在 _inject_shared_data 里（不是 pinned_environment，
    # 后者只管数据源优先级与 socket）
    with gate._inject_shared_data(["000001"]):
        pinned = pipeline.get_index_hist("000001", days=30)
        assert pinned["close"].mean() < 200, "拿到的仍是生产缓存的真实指数（~3600）"
    live = pipeline.get_index_hist("000001", days=30)
    assert abs(live["close"].mean() - pinned["close"].mean()) > 1000
    assert pipeline.get_index_hist is original, "退出 pin 后未恢复原函数"


def test_gate_reports_no_drift_twice():
    """两次独立 --check 必须同结论——真正常量化后才不会「今天绿、明天红」。"""
    gate = _load_gate()
    assert gate.main(["--check"]) == 0
    assert gate.main(["--check"]) == 0
