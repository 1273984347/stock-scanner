"""P1 机器判据（ADR-0025，2026-11-18）：host 级健康度与短路。

背景：2026-11-18 实测东财只有 push2 / push2his 不可用，push2ex（涨停池/龙虎榜）
正常。旧熔断只有「数据源」一维，没有 host 维度——要么连带停掉正常的 host，
要么完全不计数、每股先付 3s 限流 + 8s 超时再降级。
"""
import pathlib

import pandas as pd
import pytest

from lib.data import host_health as hh
from lib.data.data_eastmoney_fund import get_fund_flow

LIB_DATA = pathlib.Path(__file__).resolve().parent.parent / "lib" / "data"


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def health():
    c = _Clock()
    return hh.HostHealth(failure_threshold=3, cooldown_s=300.0, clock=c), c


@pytest.fixture(autouse=True)
def _reset_global():
    hh.reset_host_health()
    yield
    hh.reset_host_health()


# ========== 状态机 ==========

def test_closed_by_default(health):
    h, _ = health
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True
    assert h.snapshot().get(hh.HOST_EASTMONEY_PUSH2, {}).get("state", "closed") == "closed"


def test_opens_after_threshold(health):
    h, _ = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2, RuntimeError("reset"))
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False
    assert h.snapshot()[hh.HOST_EASTMONEY_PUSH2]["state"] == "open"


def test_success_resets_failure_count(health):
    h, _ = health
    h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    h.note_success(hh.HOST_EASTMONEY_PUSH2)
    h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    st = h.snapshot()[hh.HOST_EASTMONEY_PUSH2]
    assert st["failures"] == 1 and st["state"] == "closed", "成功未清零失败计数"


def test_cooldown_allows_single_half_open_probe(health):
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True, "冷却到期应放行探测"
    assert h.snapshot()[hh.HOST_EASTMONEY_PUSH2]["state"] == "half_open"


def test_half_open_failure_reopens_immediately(health):
    h, c = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    c.advance(301)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is True
    h.note_failure(hh.HOST_EASTMONEY_PUSH2, RuntimeError("still down"))
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False
    assert h.snapshot()[hh.HOST_EASTMONEY_PUSH2]["state"] == "open"


def test_hosts_are_independent(health):
    """push2 开断不得影响 push2ex（实测前者死、后者活）。"""
    h, _ = health
    for _ in range(3):
        h.note_failure(hh.HOST_EASTMONEY_PUSH2)
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2) is False
    assert h.is_available(hh.HOST_EASTMONEY_PUSH2EX) is True
    assert h.is_available(hh.HOST_TENCENT_QUOTE) is True


def test_snapshot_carries_last_error(health):
    h, _ = health
    h.note_failure(hh.HOST_EASTMONEY_PUSH2, RuntimeError("connection reset"))
    assert "connection reset" in h.snapshot()[hh.HOST_EASTMONEY_PUSH2]["last_error"]


# ========== 资金流接入（短路 + 计数） ==========

@pytest.fixture
def no_sleep(monkeypatch):
    import lib.data.data_eastmoney_fund as m
    monkeypatch.setattr(m, "_rate_limited", lambda: None)
    return m


def test_fund_flow_short_circuits_when_host_open(monkeypatch, no_sleep):
    for _ in range(3):
        hh.note_failure(hh.HOST_EASTMONEY_PUSH2)
    calls = []
    monkeypatch.setattr(no_sleep.requests, "get", lambda *a, **k: calls.append(1))

    assert get_fund_flow("600519") == {}
    assert calls == [], "host 已开断仍发请求——限流+超时白付"


def test_fund_flow_opens_breaker_on_request_error(monkeypatch, no_sleep):
    def boom(*a, **k):
        raise ConnectionError("push2 dead")

    monkeypatch.setattr(no_sleep.requests, "get", boom)
    for _ in range(3):
        assert get_fund_flow("600519") == {}
    st = hh.health_snapshot()[hh.HOST_EASTMONEY_PUSH2]
    assert st["state"] == "open" and st["failures"] >= 3


def test_fund_flow_notes_success(monkeypatch, no_sleep):
    hh.note_failure(hh.HOST_EASTMONEY_PUSH2)
    hh.note_failure(hh.HOST_EASTMONEY_PUSH2)

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"klines": []}}

    monkeypatch.setattr(no_sleep.requests, "get", lambda *a, **k: _Resp())
    get_fund_flow("600519")
    st = hh.health_snapshot()[hh.HOST_EASTMONEY_PUSH2]
    assert st["state"] == "closed" and st["failures"] == 0


def test_eastmoney_fin_short_circuits(monkeypatch):
    import lib.data.data_eastmoney_fin as m
    for _ in range(3):
        hh.note_failure(hh.HOST_EASTMONEY_EMWEB)
    monkeypatch.setattr(m.requests, "get", lambda *a, **k: pytest.fail("不应发请求"))
    assert m._fetch_em_financial("600519") is None


# ========== 静态守卫 ==========

def test_host_check_precedes_rate_limit():
    """短路判据必须早于限流等待——否则「省掉 3s 限流」的目标落空。"""
    src = (LIB_DATA / "data_eastmoney_fund.py").read_text(encoding="utf-8")
    body = src.split("def get_fund_flow", 1)[1]
    check_at = body.index("if not is_available(HOST_EASTMONEY_PUSH2)")
    # 只认真正的调用行（注释里提到 _rate_limited() 不算位置）
    call_at = body.index("\n    _rate_limited()")
    assert check_at < call_at, "is_available 必须在 _rate_limited() 之前"


def test_both_em_call_sites_instrumented():
    fund = (LIB_DATA / "data_eastmoney_fund.py").read_text(encoding="utf-8")
    fin = (LIB_DATA / "data_eastmoney_fin.py").read_text(encoding="utf-8")
    for src, host in ((fund, "HOST_EASTMONEY_PUSH2"), (fin, "HOST_EASTMONEY_EMWEB")):
        assert f"is_available({host})" in src
        assert f"note_failure({host}" in src
        assert f"note_success({host})" in src


def test_host_constants_are_real_hosts():
    """常量值必须是真实 host 字符串，避免拼错后永不命中。"""
    for name in dir(hh):
        if name.startswith("HOST_"):
            val = getattr(hh, name)
            assert isinstance(val, str) and "." in val and " " not in val, f"{name}={val!r}"


# ========== 调用包装（call_with_health / guarded） ==========

def test_call_with_health_short_circuits_when_open():
    for _ in range(3):
        hh.note_failure(hh.HOST_EASTMONEY_PUSH2HIS)
    calls = []
    out = hh.call_with_health(hh.HOST_EASTMONEY_PUSH2HIS, lambda: calls.append(1))
    assert calls == [], "host 已开断仍调用底层函数"
    assert isinstance(out, pd.DataFrame) and out.empty


def test_call_with_health_records_failure_and_reraises():
    """异常必须原样抛出——调用方的重试/切源语义不能在这里被吞掉。"""
    def boom():
        raise ConnectionError("push2his dead")

    with pytest.raises(ConnectionError):
        hh.call_with_health(hh.HOST_EASTMONEY_PUSH2HIS, boom)
    assert hh.health_snapshot()[hh.HOST_EASTMONEY_PUSH2HIS]["failures"] == 1


def test_call_with_health_notes_success():
    out = hh.call_with_health(hh.HOST_EASTMONEY_PUSH2HIS, lambda: "ok")
    assert out == "ok"
    assert hh.health_snapshot()[hh.HOST_EASTMONEY_PUSH2HIS]["state"] == "closed"


def test_guarded_preserves_module_for_rate_limit_bucket():
    """限流按 __module__ 分桶（cache._bucket_for）；包装不得把配额并成单桶。"""
    from lib.data.cache import _bucket_for

    def ak_like():
        return 1
    ak_like.__module__ = "akshare.stock_feature"
    wrapped = hh.guarded(hh.HOST_EASTMONEY_PUSH2EX, ak_like)
    assert wrapped.__module__ == "akshare.stock_feature"
    assert wrapped.__name__ == "ak_like"
    assert _bucket_for(wrapped) == "akshare.stock_feature"


def test_eastmoney_call_sites_are_guarded():
    """四个东财调用点必须接 host 健康（否则状态机收不到 push2his/push2ex 信号）。"""
    repo = pathlib.Path(__file__).resolve().parent.parent
    # 直接断「调用形式」（registry 走超时包装，host 作为位置参数传入，形态不同）
    checks = [
        ("lib/data/data_source_registry.py", "call_with_health, HOST_EASTMONEY_PUSH2HIS"),
        ("lib/data/data_tdx.py", "call_with_health(HOST_EASTMONEY_PUSH2HIS"),
        ("lib/strategy/strategies.py", "guarded(HOST_EASTMONEY_PUSH2EX"),
        ("lib/data/news_signal.py", "guarded(HOST_EASTMONEY_PUSH2EX"),
    ]
    for rel, needle in checks:
        src = (repo / rel).read_text(encoding="utf-8")
        assert needle in src, f"{rel} 未接 host 健康：缺 {needle!r}"

    strat = (repo / "lib/strategy/strategies.py").read_text(encoding="utf-8")
    assert "_retry_api_call(ak." not in strat, "strategies 仍有裸 ak 调用绕过 host 健康"
    news = (repo / "lib/data/news_signal.py").read_text(encoding="utf-8")
    assert "retry_api_call(ak." not in news, "news_signal 仍有裸 ak 调用绕过 host 健康"


# ========== 运维端点 ==========

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("API_SECRET_KEY", "test-secret-key-for-unit-tests")
    from fastapi.testclient import TestClient
    from api import app
    return TestClient(app, raise_server_exceptions=False)


def test_host_health_endpoint_requires_auth(client):
    assert client.get("/api/system/host-health").status_code == 401


def test_host_health_endpoint_reports_state(client):
    hh.note_failure(hh.HOST_EASTMONEY_PUSH2, RuntimeError("boom"))
    resp = client.get("/api/system/host-health",
                      headers={"X-API-Key": "test-secret-key-for-unit-tests"})
    assert resp.status_code == 200
    body = resp.json()
    assert hh.HOST_EASTMONEY_PUSH2 in body["hosts"]
    assert body["hosts"][hh.HOST_EASTMONEY_PUSH2]["failures"] == 1
    assert set(body["summary"]) == {"total", "open", "open_hosts"}


def test_host_health_endpoint_is_non_blocking():
    """端点只读内存：不得出现 requests / sleep / to_thread（L8 的前置源码判据）。"""
    repo = pathlib.Path(__file__).resolve().parent.parent
    src = (repo / "routers/system.py").read_text(encoding="utf-8")
    fn = src.split("async def host_health_status", 1)[1].split("\nasync def ", 1)[0]
    for banned in ("requests.", "time.sleep", "to_thread", "run_in_executor"):
        assert banned not in fn, f"host-health 端点出现阻塞调用: {banned}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
