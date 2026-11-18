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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
