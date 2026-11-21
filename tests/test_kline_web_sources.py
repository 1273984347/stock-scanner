"""ADR-0027 机器判据（2026-11-19）：网页网关日线源（新浪 / 腾讯）。

背景：2026-11-19 00:15 实测个股日线 8 个已注册源全 0（baostock 黑名单 /
push2his 不可达 / tushare 无权限 / adata·westock 缺 Node / tdx 已删），
而两条未注册的网页 K 线网关都返到最近交易日。本文件钉住四件事：

1. 报文解析与单位（腾讯 volume 是手 → ×100）；
2. as_of_date 的语义（腾讯用日期区间，新浪取足够根数再截断）；
3. 源栈分工（默认栈新浪领头、回测栈腾讯领头且**不含**不复权源）；
4. 北交所路由（旧实现硬编码 tdx_local 等于判死，92xxxx 还漏判）。
"""
import pandas as pd
import pytest

import lib.data.data_kline_web as kw
from lib.data.data_source_registry import (_CIRCUIT_BREAKERS, BJ_SOURCE_PRIORITY,
                                          DATA_SOURCE_PRIORITY,
                                          DATA_SOURCE_PRIORITY_ADJUSTED,
                                          _is_bj_stock,
                                          get_stock_hist_multi_source)

SINA_PAYLOAD = """[
 {"day":"2026-11-17","open":"1458.880","high":"1462.000","low":"1440.000","close":"1457.700","volume":"3500000"},
 {"day":"2026-11-18","open":"1450.000","high":"1456.000","low":"1431.700","close":"1442.660","volume":"2984695"}
]"""

TENCENT_PAYLOAD = ('{"code":0,"msg":"","data":{"sh600519":{"qfqday":['
                   '["2026-11-17","1458.880","1457.700","1462.000","1440.000","35000.000"],'
                   '["2026-11-18","1450.000","1442.660","1456.000","1431.700","29847.000"]'
                   ']}}}')


# ========== 解析与单位 ==========

def test_sina_parse_contract():
    df = kw.parse_sina_kline(SINA_PAYLOAD)
    assert list(df.columns) == kw.COLUMNS
    assert len(df) == 2
    assert df["date"].dtype.kind == "M"
    assert df.iloc[-1]["close"] == pytest.approx(1442.66)
    assert df.iloc[-1]["volume"] == pytest.approx(2984695), "新浪 volume 已是股，不得缩放"


def test_tencent_parse_converts_lot_to_shares():
    df = kw.parse_tencent_kline(TENCENT_PAYLOAD, "sh600519", "qfq")
    assert len(df) == 2
    assert df.iloc[-1]["volume"] == pytest.approx(29847 * 100), "腾讯 volume 是手，须 ×100"
    # 腾讯 rows 顺序是 日期/开/收/高/低/量 —— 高低位错读会把 K 线画反
    assert df.iloc[-1]["open"] == pytest.approx(1450.00)
    assert df.iloc[-1]["close"] == pytest.approx(1442.66)
    assert df.iloc[-1]["high"] == pytest.approx(1456.00)
    assert df.iloc[-1]["low"] == pytest.approx(1431.70)


TENCENT_PAYLOAD_688 = ('{"code":0,"msg":"","data":{"sh688001":{"qfqday":['
                       '["2026-11-17","33.370","33.770","34.500","32.580","8722449.000"],'
                       '["2026-11-18","33.600","33.300","34.190","32.900","6493272.000"]'
                       ']}}}')


def test_tencent_star_market_volume_already_in_shares():
    """P-122（ADR-0036 D1）：科创板（688/689）腾讯报文 volume 已是股，×100 是单位错。

    2026-11-21 Wind 对照实锤：sh688001 报文 10276186 与 Wind 股数同值，
    sh600519=67229=Wind 股数÷100——同一接口单位按板块不同，一刀切 ×100
    曾让 610 只科创板 kline volume 虚增 100 倍且不报错。
    """
    df = kw.parse_tencent_kline(TENCENT_PAYLOAD_688, "sh688001", "qfq")
    assert df.iloc[-1]["volume"] == pytest.approx(6493272), "科创板报文已是股，不得 ×100"
    assert kw._tencent_volume_scale("sh688001") == 1.0
    assert kw._tencent_volume_scale("sh689009") == 1.0
    assert kw._tencent_volume_scale("sh600519") == 100.0
    assert kw._tencent_volume_scale("sz300750") == 100.0
    assert kw._tencent_volume_scale("bj830799") == 100.0


def test_parse_garbage_returns_empty():
    assert kw.parse_sina_kline("not json").empty
    assert kw.parse_tencent_kline("{}", "sh600519").empty
    assert kw.parse_tencent_kline("not json", "sh600519").empty


def test_slice_filters_by_as_of_date():
    df = kw.parse_sina_kline(SINA_PAYLOAD)
    out = kw._slice(df, days=10, as_of_date="2026-11-17")
    assert len(out) == 1 and out.iloc[0]["date"].day == 17


# ========== 请求构造（不打网络） ==========

def test_sina_request_caps_datalen(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return SINA_PAYLOAD

    monkeypatch.setattr(kw, "_http_get", fake_get)
    kw.get_sina_kline("600519", days=5000)
    assert "datalen=2000" in seen["url"], "新浪单次上限 2000，必须截断"
    assert "symbol=sh600519" in seen["url"]


def test_tencent_request_uses_date_range_when_as_of(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return TENCENT_PAYLOAD

    monkeypatch.setattr(kw, "_http_get", fake_get)
    kw.get_tencent_kline("000001", days=60, as_of_date="2026-11-18")
    assert "sz000001,day,2026-" in seen["url"] and ",2026-11-18,60,qfq" in seen["url"]
    assert "qfq" in seen["url"], "回测必须显式取前复权"


def test_tencent_hosts_rotate_and_are_registered():
    """回归（ADR-0032）：两台腾讯主机在同日交替被 WAF 拦截，不能写死其中一台。"""
    assert set(kw.TENCENT_KLINE_HOSTS) == {"web.ifzq.gtimg.cn", "ifzq.gtimg.cn"}
    from lib.data import host_health as hh
    for key in kw.TENCENT_KLINE_HOST_KEYS.values():
        assert isinstance(getattr(hh, key), str)


def test_tencent_rotation_falls_back_to_second_host(monkeypatch):
    """第一台返回无效报文时，必须继续尝试第二台，而不是整源判死。"""
    seen = []

    def fake_get(url, **kwargs):
        seen.append(url)
        return "challenge-page" if "web.ifzq" in url else TENCENT_PAYLOAD

    monkeypatch.setattr(kw, "_http_get", fake_get)
    df = kw.get_tencent_kline("600519", days=10)
    assert len(df) == 2, "第二台主机有效数据未被采用"
    assert len(seen) == 2, f"应在两台主机间轮换，实际 {seen}"


def test_tencent_rotation_exhausts_both_hosts(monkeypatch):
    """两台都失败才返回空帧（由多源栈继续降级，而不是抛异常）。"""
    calls = []
    monkeypatch.setattr(kw, "_http_get",
                        lambda url, **k: calls.append(url) or "challenge-page")
    df = kw.get_tencent_kline("600519", days=10)
    assert df.empty and len(calls) == 2


def test_index_sym_disambiguates_stock_vs_index():
    """000001 既是平安银行（sz）也是上证指数（sh）——指数入口必须显式判定。"""
    assert kw.index_sym("000300") == "sh000300"
    assert kw.index_sym("000001") == "sh000001"
    assert kw.index_sym("399001") == "sz399001"
    assert kw.index_sym("880001") == ""      # 板块：网页网关不支持，返回空让调用方跳过
    assert kw._sym("000001") == "sz000001"   # 个股路径仍是深市


def test_explicit_prefix_is_preserved():
    """回归：显式 sh000300 曾被 _sym 二次判定翻成 sz000300，指数请求直接空数据。"""
    assert kw._sym("sh000300") == "sh000300"
    assert kw._sym("sz000001") == "sz000001"
    assert kw._sym("bj920000") == "bj920000"


def test_index_hist_prefers_web_before_baostock():
    """ADR-0027：指数链必须是 本地 → pytdx → 网页网关 → baostock（黑名单降为末位）。"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "lib/data/data_layer.py").read_text(encoding="utf-8")
    # ADR-0033 后实现改名为 _get_index_hist_online，本地仓包装层单独存在
    body = src.split("def _get_index_hist_online", 1)[1].split("def get_ggt_hist", 1)[0]
    assert body.index("get_index_kline_web") < body.index("get_index_kline\n"), \
        "网页网关必须排在 baostock 之前"


# ========== 源栈分工 ==========

def test_web_sources_lead_default_stack():
    assert DATA_SOURCE_PRIORITY[:2] == ["sina_kline", "tencent_kline"]


def test_unadjusted_source_is_excluded_from_backtest_stack():
    """新浪不复权：进回测栈会在除权日造出假跳空，且不会报错。"""
    assert DATA_SOURCE_PRIORITY_ADJUSTED[0] == "tencent_kline"
    assert "sina_kline" not in DATA_SOURCE_PRIORITY_ADJUSTED


def test_new_sources_have_circuit_breakers():
    for src in ("sina_kline", "tencent_kline"):
        assert src in _CIRCUIT_BREAKERS, f"{src} 未注册熔断器（失败不会切源）"


# ========== 北交所路由 ==========

def test_bj_detection_covers_92_segment():
    assert _is_bj_stock("920000") is True, "92xxxx 是北证新号段，旧实现 startswith(4/8) 会漏"
    assert _is_bj_stock("430047") is True
    assert _is_bj_stock("830799") is True
    assert _is_bj_stock("600519") is False
    assert _is_bj_stock("000001") is False


def test_bj_routing_tries_web_sources_before_local_tdx(monkeypatch):
    """旧实现把北交所硬编码到 tdx_local（目录已删）= 直接判死。"""
    calls = []

    def fake_fetch(code, days, source, as_of_date=None):
        calls.append(source)
        if source == "sina_kline":
            return pd.DataFrame({
                "date": pd.date_range("2026-09-01", periods=40, freq="B"),
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6,
            })
        return pd.DataFrame()

    monkeypatch.setattr("lib.data.data_source_registry._fetch_from_source", fake_fetch)
    df = get_stock_hist_multi_source("920000", days=40)
    assert len(df) == 40
    assert calls[0] == "sina_kline", f"北交所应先走网页网关，实际 {calls}"
    assert set(BJ_SOURCE_PRIORITY) == {"sina_kline", "tencent_kline", "tdx_local"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
