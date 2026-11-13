"""get_stock_fund_flow 缓存去重 + 熔断行为（H1 门禁 + ADR-0016）

背景：扫描时 5 个策略 + ML 富集循环会对同一只股票重复调用
get_stock_fund_flow，而原函数无任何缓存，被全局 3/sec 限流放大成 5+ 分钟瓶颈。

本测试把「缓存命中 / 去重 / 熔断」逻辑变成机器判据（与 RF-01 基线 pin 机制同构）：
- 进程内字典缓存：同 code 重复调用只触发 1 次底层 fetch（去重，解除瓶颈）
- 磁盘缓存：底层 fetch 失败/被替换时，从磁盘缓存复用（跨扫描复用）
- 仅缓存成功取数（非全零），不缓存「所有源失败」的默认值，避免掩盖瞬时故障
- 全局熔断（ADR-0016）：全源失败连续 3 次 → 熔断 300s 直接返回零默认值，
  源拒连时单次未缓存调用实测 ~13.2s，300 股批量场景按股票数放大复现瓶颈，
  熔断把它压到约 3 次尝试的成本；真实零值（停牌/零流）不计失败。

全程使用 hermetic fake，不触网、不落真盘。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
import pytest

import lib.data.data_layer as data_layer
from lib.data.data_layer import FundFlowUnavailableError


@pytest.fixture(autouse=True)
def _reset_fundflow_cache():
    """每个用例前后清空进程内缓存、熔断器与市场批量失败标记，避免跨用例污染。"""
    data_layer._fundflow_cache.clear()
    data_layer._FUND_FLOW_BREAKER.reset()
    data_layer._fundflow_market_unavailable_until = 0.0
    yield
    data_layer._fundflow_cache.clear()
    data_layer._FUND_FLOW_BREAKER.reset()
    data_layer._fundflow_market_unavailable_until = 0.0


def _install_fakes(monkeypatch, disk_store, value_fn=None):
    """安装 hermetic fakes：fetch 计数 + 磁盘缓存 in-memory。

    value_fn(code) -> dict 控制每次 fetch 的返回值；缺省对所有 code 返回同一固定值。
    """
    calls = {"n": 0}

    def fake_fetch(code):
        calls["n"] += 1
        if value_fn is not None:
            return value_fn(code)
        return {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}

    def fake_load(key, max_age_hours=2):
        df = disk_store.get(key)
        return df if df is not None else pd.DataFrame()

    def fake_save(key, df):
        disk_store[key] = df

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", fake_fetch)
    monkeypatch.setattr(data_layer, "_load_cache", fake_load)
    monkeypatch.setattr(data_layer, "_save_cache", fake_save)
    return calls


def test_in_process_dedup_only_one_fetch(monkeypatch):
    """同一 code 重复调用只触发 1 次底层 fetch（去重，解除 5+ 分钟瓶颈）。"""
    disk_store = {}
    calls = _install_fakes(monkeypatch, disk_store)
    code = "600519"
    expected = {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}

    r1 = data_layer.get_stock_fund_flow(code)
    r2 = data_layer.get_stock_fund_flow(code)
    r3 = data_layer.get_stock_fund_flow(code)

    assert calls["n"] == 1, f"预期只 fetch 1 次，实际 {calls['n']} 次"
    assert r1 == r2 == r3 == expected
    # 进程内缓存命中（第二次起不再进 fetch）
    assert data_layer._fundflow_cache.get(code) is not None


def test_distinct_codes_each_fetched_once(monkeypatch):
    """不同 code 各自只 fetch 一次，互不串缓存。"""
    disk_store = {}

    def per_code(code):
        # 用 code 派生确定且互异的值，便于断言「不同 code 互不复用缓存」
        h = int(code[-2:]) if code[-2:].isdigit() else 0
        return {"main_net_ratio": round(0.01 * (h % 10), 4), "net_inflow_5d": float(h) * 1e6}

    calls = _install_fakes(monkeypatch, disk_store, value_fn=per_code)
    a = data_layer.get_stock_fund_flow("600519")
    b = data_layer.get_stock_fund_flow("000001")
    a2 = data_layer.get_stock_fund_flow("600519")

    assert calls["n"] == 2, f"两只不同股票应 fetch 2 次，实际 {calls['n']} 次"
    assert a == a2
    assert a != b


def test_disk_cache_reused_when_fetch_fails(monkeypatch):
    """底层 fetch 抛异常时，磁盘缓存仍能被复用（跨扫描复用）。"""
    disk_store = {}
    code = "000001"
    good = {"main_net_ratio": 0.03, "net_inflow_5d": 5.0e7}
    disk_store[f"fundflow/{code}"] = pd.DataFrame([good])

    def boom(_code):
        raise RuntimeError("network down")

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", boom)
    monkeypatch.setattr(data_layer, "_load_cache",
                        lambda key, max_age_hours=2: disk_store.get(key, pd.DataFrame()))
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)

    res = data_layer.get_stock_fund_flow(code)
    assert res == good, f"磁盘缓存应被复用，实际返回 {res}"


def test_only_nonzero_results_cached(monkeypatch):
    """全零结果（所有源失败）不写入缓存，便于后续重试。"""
    disk_store = {}
    data_layer._fundflow_cache.clear()

    def zero(_code):
        return {"main_net_ratio": 0, "net_inflow_5d": 0}

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", zero)
    monkeypatch.setattr(data_layer, "_load_cache", lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache",
                        lambda key, df: disk_store.__setitem__(key, df))

    res = data_layer.get_stock_fund_flow("600000")
    assert res == {"main_net_ratio": 0, "net_inflow_5d": 0}
    # 不应写入磁盘缓存
    assert "fundflow/600000" not in disk_store
    # 进程内也不应缓存（下次再试仍会 fetch）
    assert data_layer._fundflow_cache.get("600000") is None


# ===========================================================================
# ADR-0016：全局熔断器（全源失败连续 3 次 → 熔断 300s → 零默认值 fail-fast）
# ===========================================================================

def _install_source_down_fakes(monkeypatch, codes_seen=None):
    """安装「上游全挂」fakes：_fetch_fund_flow 抛 FundFlowUnavailableError 并计数。"""
    calls = {"n": 0}

    def failing_fetch(code):
        calls["n"] += 1
        if codes_seen is not None:
            codes_seen.append(code)
        raise FundFlowUnavailableError(f"all sources down for {code}")

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", failing_fetch)
    monkeypatch.setattr(data_layer, "_load_cache", lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)
    return calls


def test_breaker_trips_then_fail_fast(monkeypatch):
    """连续 3 次全源失败 → 熔断开启；之后的调用直接返回零默认值，不再进 fetch。

    成本核算（ADR-0016 依据）：源拒连时单次 fetch ~13.2s（重试 backoff），
    无熔断 = 300 股 × 13.2s ≈ 66 分钟；有熔断 = 3 × 13.2s ≈ 40s 后 fail-fast。
    """
    calls = _install_source_down_fakes(monkeypatch)

    results = [data_layer.get_stock_fund_flow(f"60000{i}") for i in range(6)]

    # 返回值形状与降级语义不变：全零默认值
    assert all(r == {"main_net_ratio": 0, "net_inflow_5d": 0} for r in results)
    # fetch 恰好 3 次（阈值），第 4-6 次由熔断 fail-fast 兜底
    assert calls["n"] == 3, f"熔断后应停止 fetch，实际 fetch {calls['n']} 次"
    assert data_layer._FUND_FLOW_BREAKER.state == "open"


def test_breaker_success_resets_failure_count(monkeypatch):
    """两次失败后一次成功 → 计数归零；重新数满阈值才再次熔断。"""
    calls = {"n": 0}

    def flaky_fetch(code):
        calls["n"] += 1
        # 第 1、2 次失败，第 3 次成功（归零），第 4-6 次再连续失败
        if calls["n"] <= 2 or calls["n"] >= 4:
            raise FundFlowUnavailableError("down")
        return {"main_net_ratio": 0.05, "net_inflow_5d": 2.0e7}

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", flaky_fetch)
    monkeypatch.setattr(data_layer, "_load_cache", lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)

    # 2 次失败（零默认值）+ 1 次成功
    r1 = data_layer.get_stock_fund_flow("600001")
    r2 = data_layer.get_stock_fund_flow("600002")
    r3 = data_layer.get_stock_fund_flow("600003")
    assert r1 == {"main_net_ratio": 0, "net_inflow_5d": 0}
    assert r3 == {"main_net_ratio": 0.05, "net_inflow_5d": 2.0e7}
    assert data_layer._FUND_FLOW_BREAKER.state == "closed"

    # 计数已归零：再失败 2 次不应熔断，第 3 次才开
    data_layer.get_stock_fund_flow("600004")
    data_layer.get_stock_fund_flow("600005")
    assert data_layer._FUND_FLOW_BREAKER.state == "closed"
    data_layer.get_stock_fund_flow("600006")
    assert data_layer._FUND_FLOW_BREAKER.state == "open"
    assert calls["n"] == 6  # 2 + 1 + 3，熔断后无再 fetch


def test_breaker_half_open_probes_after_recovery(monkeypatch):
    """recovery_timeout 过期后半开：下一次调用真实探测；继续失败则重开。"""
    calls = _install_source_down_fakes(monkeypatch)

    for _ in range(3):
        data_layer.get_stock_fund_flow("600007")
    assert data_layer._FUND_FLOW_BREAKER.state == "open"

    # 模拟 recovery_timeout（300s）过期
    breaker = data_layer._FUND_FLOW_BREAKER
    breaker._last_failure_time = time.time() - (breaker.recovery_timeout + 1)

    data_layer.get_stock_fund_flow("600008")
    assert calls["n"] == 4, "半开期应放行一次真实探测"
    # 探测仍失败 → 重开
    assert data_layer._FUND_FLOW_BREAKER.state == "open"
    # 后续继续 fail-fast
    data_layer.get_stock_fund_flow("600009")
    assert calls["n"] == 4


def test_genuine_zero_flow_never_trips_breaker(monkeypatch):
    """真实取到的零值（停牌/零流）不计失败：反复调用不熔断、每次都真实 fetch。"""
    calls = {"n": 0}

    def genuine_zero(code):
        calls["n"] += 1
        return {"main_net_ratio": 0, "net_inflow_5d": 0}

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", genuine_zero)
    monkeypatch.setattr(data_layer, "_load_cache", lambda key, max_age_hours=2: pd.DataFrame())
    monkeypatch.setattr(data_layer, "_save_cache", lambda key, df: None)

    for _ in range(5):
        res = data_layer.get_stock_fund_flow("000003")
        assert res == {"main_net_ratio": 0, "net_inflow_5d": 0}

    assert calls["n"] == 5, "真实零值不得计入失败（全源失败才触发熔断）"
    assert data_layer._FUND_FLOW_BREAKER.state == "closed"


def test_circuit_breaker_reset_clears_named_and_default_state():
    """CircuitBreaker.reset() 存在且清空默认 + 命名状态。

    背景：data_source_registry.reset_circuit_breakers 此前调用不存在的
    `_reset()`（死代码，一调即 AttributeError），2026-11-14 修复为 reset()。
    """
    from lib.data.cache import CircuitBreaker

    def _boom():
        raise RuntimeError("x")

    cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60)
    # 默认断路器：threshold=1，一次失败即开
    with pytest.raises(RuntimeError):
        cb.call(_boom)
    assert cb.state == "open"
    cb.reset()
    assert cb.state == "closed"
    assert cb.call(lambda: "ok") == "ok"

    # 命名断路器：reset 同样清空命名状态（svc_a 重新放行）
    with pytest.raises(RuntimeError):
        cb.call("svc_a", _boom)
    with pytest.raises(Exception):
        cb.call("svc_a", _boom)  # 已 open，直接 CircuitOpenError（不再执行函数）
    cb.reset()
    assert cb.call("svc_a", lambda: "ok") == "ok"

    # 死代码修复判据：reset_circuit_breakers 不再 AttributeError
    from lib.data.data_source_registry import reset_circuit_breakers
    reset_circuit_breakers()


# ===========================================================================
# ADR-0018：市场批量层（同花顺全市场 1 call/小时，取代 4d 评分路径的逐股排队）
# ===========================================================================

def _install_market_fakes(monkeypatch, disk_store, market_map, market_calls=None):
    """安装市场批量层 fakes：_fetch_market_fund_flow 返回 market_map 并计数。"""
    if market_calls is None:
        market_calls = {"n": 0}

    def fake_fetch_market():
        market_calls["n"] += 1
        return dict(market_map)

    monkeypatch.setattr(data_layer, "_fetch_market_fund_flow", fake_fetch_market)
    monkeypatch.setattr(data_layer, "_load_cache",
                        lambda key, max_age_hours=2: disk_store.get(key, pd.DataFrame()))
    monkeypatch.setattr(data_layer, "_save_cache",
                        lambda key, df: disk_store.__setitem__(key, df))
    return market_calls


def test_market_batch_serves_and_skips_per_stock_fetch(monkeypatch):
    """市场批量命中：get_stock_fund_flow 直接返回批量值，不进逐股 fetch（限流排队消除）。"""
    disk_store = {}
    market_map = {"000001": {"main_net_ratio": 0.04, "net_inflow_5d": 3.0e7}}
    market_calls = _install_market_fakes(monkeypatch, disk_store, market_map)

    fetch_calls = {"n": 0}

    def per_stock_fetch(code):
        fetch_calls["n"] += 1
        raise FundFlowUnavailableError("should not be called")

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", per_stock_fetch)

    res = data_layer.get_stock_fund_flow("000001")
    assert res == {"main_net_ratio": 0.04, "net_inflow_5d": 3.0e7}
    assert fetch_calls["n"] == 0, "批量命中不得走逐股 fetch"
    assert market_calls["n"] == 1
    # 批量结果落盘（下一次扫描 1h TTL 内不再拉）
    assert "fundflow/_market" in disk_store


def test_market_batch_one_fetch_per_hour_across_codes(monkeypatch):
    """多只股票共享一次批量拉取（1 call/小时），不再逐股排队。"""
    disk_store = {}
    market_map = {
        "000001": {"main_net_ratio": 0.04, "net_inflow_5d": 3.0e7},
        "600519": {"main_net_ratio": 0.02, "net_inflow_5d": 1.0e7},
    }
    market_calls = _install_market_fakes(monkeypatch, disk_store, market_map)
    monkeypatch.setattr(data_layer, "_fetch_fund_flow",
                        lambda code: (_ for _ in ()).throw(FundFlowUnavailableError(code)))

    r1 = data_layer.get_stock_fund_flow("000001")
    r2 = data_layer.get_stock_fund_flow("600519")

    assert market_calls["n"] == 1, "两只股票应共享一次批量拉取"
    assert r1["net_inflow_5d"] == 3.0e7 and r2["net_inflow_5d"] == 1.0e7


def test_market_batch_failure_degrades_and_marks_unavailable(monkeypatch):
    """批量失败 → 进程内 300s 不可用标记（不落盘）→ 直接走逐股路径；标记期间不再重拉。"""
    disk_store = {}
    market_calls = _install_market_fakes(monkeypatch, disk_store, {})  # 拉取返回空 = 失败

    per_stock = {"000001": {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}}
    fetch_calls = {"n": 0}

    def per_stock_fetch(code):
        fetch_calls["n"] += 1
        return per_stock[code]

    monkeypatch.setattr(data_layer, "_fetch_fund_flow", per_stock_fetch)

    r1 = data_layer.get_stock_fund_flow("000001")
    assert r1 == per_stock["000001"], "批量失败应降级逐股路径"
    assert market_calls["n"] == 1
    assert data_layer._fundflow_market_unavailable_until > time.time(), "应记 300s 不可用标记"

    # 标记期间：第二次调用不再触碰批量层（fetch_market 计数不变），逐股照常
    r2 = data_layer.get_stock_fund_flow("000001")
    assert market_calls["n"] == 1, "不可用标记期间不得重拉批量"
    assert r2 == per_stock["000001"]


def test_per_stock_disk_cache_takes_precedence_over_batch(monkeypatch):
    """精确值优先：逐股东财磁盘缓存存在时，压过市场批量（口径差异，ADR-0018 D2）。"""
    disk_store = {}
    good = {"main_net_ratio": 0.03, "net_inflow_5d": 5.0e7}
    disk_store["fundflow/000001"] = pd.DataFrame([good])
    market_map = {"000001": {"main_net_ratio": 0.99, "net_inflow_5d": 9.9e9}}
    _install_market_fakes(monkeypatch, disk_store, market_map)

    res = data_layer.get_stock_fund_flow("000001")
    assert res == good, "逐股磁盘缓存（东财精确口径）应优先于市场批量"


def test_batch_fund_flow_shares_market_cache(monkeypatch):
    """策略路径 _batch_fund_flow 与评分路径共享同一次市场拉取（整场 1 call）。"""
    disk_store = {}
    market_map = {
        "000001": {"main_net_ratio": 0.04, "net_inflow_5d": 3.0e7},
        "600519": {"main_net_ratio": 0.02, "net_inflow_5d": 1.0e7},
    }
    market_calls = _install_market_fakes(monkeypatch, disk_store, market_map)

    r1 = data_layer._batch_fund_flow(["000001", "600519", "999999"])
    r2 = data_layer.get_stock_fund_flow("600519")

    assert market_calls["n"] == 1, "两条路径共享一次批量拉取"
    assert r1["000001"]["net_inflow_5d"] == 3.0e7
    assert r1["999999"] == {"main_net_ratio": 0, "net_inflow_5d": 0}, "不在榜股票保持零默认值"
    assert r2["net_inflow_5d"] == 1.0e7


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
