"""新浪快失败上抛 + westock 复活判据（架构评审 P0-2 修复批次 C3，2026-11-20）

新浪侧（评审 P0-2）：
- 旧形态：``get_sina_kline`` catch-all 返空帧 + registry 用 ``call_with_health``
  包整函数 → 新浪 HTTP 失败到不了任何计数器，``note_success`` 反而清零
  （host_health 永闭合、源级熔断永不计数，整链静默滑向腾讯单源）。
- 修后：HTTP 错误/超时与「200 但报文不可解析」（WAF 挑战页形态）**上抛**；
  「真无数据」（``[]``/``null``/空串，新股/停牌）仍返空帧不计失败
  （复核 B2：200+空数组解析得空帧无异常，不会误熔断）。
- 降级链保住判据：``_fetch_from_source`` 的外层 except 仍把异常兜成空帧，
  多源循环照常切下一源（新浪失败 ≠ 整链失败）。

westock 侧（实测发现的复活三层）：
- P-111：Windows ``subprocess.run(list)`` 不解析 npx.cmd shim → FileNotFoundError
  →「缺 Node」误诊，源全灭数月；修 = ``shutil.which`` 解析。
- P-112：CLI 1.0.4 英文表头（date/open/**last**/...）vs 旧版中文 → col_map 静默失配；
  修 = 双表头映射（last→close）。
- 成交量单位 = 手（实测 sh600519 westock 42692 vs 新浪 4269153 股，比值恰 100.0）
  → registry 分支就地 ×100（放分支内，勿放共享 _normalize_columns）。
"""

import shutil

import pandas as pd
import pytest
import requests

from lib.data import data_kline_web as kw
from lib.data import data_westock
from lib.data.data_source_registry import (
    _fetch_from_source,
    get_circuit_breaker_status,
    reset_circuit_breakers,
)
from lib.data.host_health import HOST_SINA_KLINE, call_with_health, health_snapshot


# ---------------------------------------------------------------- 新浪快失败


def _raise_http(*args, **kwargs):
    raise requests.ConnectionError("sina 503 / WAF")


def test_sina_http_error_propagates(monkeypatch):
    """HTTP 失败必须上抛（旧实现返空帧 = note_success 清零计数）。"""
    monkeypatch.setattr(kw, "_http_get", _raise_http)
    with pytest.raises(requests.ConnectionError):
        kw.get_sina_kline("600519", days=30)


def test_sina_waf_html_payload_raises(monkeypatch):
    """200 但报文是 HTML/挑战页（WAF 形态）→ SinaKlinePayloadError 上抛。"""
    monkeypatch.setattr(kw, "_http_get", lambda *a, **k: "<html>js challenge</html>")
    with pytest.raises(kw.SinaKlinePayloadError):
        kw.get_sina_kline("600519", days=30)


@pytest.mark.parametrize("payload", ["", "null", "[]"])
def test_sina_legit_empty_payloads_stay_empty(monkeypatch, payload):
    """「真无数据」（新股/停牌/空串）返空帧不抛——不误熔断（复核 B2）。"""
    monkeypatch.setattr(kw, "_http_get", lambda *a, **k: payload)
    df = kw.get_sina_kline("600519", days=30)
    assert df.empty


def test_sina_parse_contract_unchanged():
    """纯解析函数契约不变（回归守卫）：垃圾报文 → 空帧（钉在 parse 层）。"""
    assert kw.parse_sina_kline("not json").empty


def test_host_health_counts_sina_failures(monkeypatch):
    """复核判定标准：注入新浪失败 → HOST_SINA_KLINE 3 次内 open（评审 P0-2 验证指标）。"""
    monkeypatch.setattr(kw, "_http_get", _raise_http)
    for _ in range(3):
        with pytest.raises(requests.ConnectionError):
            call_with_health(HOST_SINA_KLINE, kw.get_sina_kline, "600519", days=30)
    snap = health_snapshot()
    assert HOST_SINA_KLINE in snap
    assert snap[HOST_SINA_KLINE]["failures"] >= 3
    assert snap[HOST_SINA_KLINE]["state"] == "open"


def test_fetch_from_source_swallows_and_counts_breaker(monkeypatch):
    """降级链保住 + 源级熔断计数：_fetch_from_source 返空帧（不抛）且 failure_count=1。"""
    reset_circuit_breakers()
    monkeypatch.setattr(kw, "_http_get", _raise_http)
    df = _fetch_from_source("600519", 30, "sina_kline")
    assert df.empty  # 异常被 _fetch_from_source 兜成空帧 → 多源循环可切下一源
    status = get_circuit_breaker_status()["sina_kline"]
    assert status["failure_count"] == 1


def test_index_fallback_returns_empty_not_raise(monkeypatch):
    """指数兜底路径：腾讯空 + 新浪 HTTP 失败 → 仍返空帧（行为不变），host 已计数。"""
    monkeypatch.setattr(kw, "get_tencent_kline", lambda *a, **k: kw._empty())
    monkeypatch.setattr(kw, "_http_get", _raise_http)
    df = kw.get_index_kline_web("000300", days=30)
    assert df.empty


# ---------------------------------------------------------------- westock 复活


def test_westock_cmd_env_override(monkeypatch):
    monkeypatch.setenv("WESTOCK_CLI", r"C:\custom\westock.exe")
    assert data_westock._resolve_westock_cmd() == [r"C:\custom\westock.exe"]


def test_westock_cmd_resolved_via_shutil_which(monkeypatch):
    """P-111：无环境变量时必须走 shutil.which（Windows 裸 "npx" = FileNotFoundError）。"""
    monkeypatch.delenv("WESTOCK_CLI", raising=False)
    monkeypatch.setattr(data_westock.shutil, "which", lambda name: r"C:\nodejs\npx.CMD")
    cmd = data_westock._resolve_westock_cmd()
    assert cmd[0] == r"C:\nodejs\npx.CMD"
    assert cmd[1:3] == ["-y", "westock-data-clawhub@1.0.4"]


def test_westock_cmd_falls_back_to_bare_npx_when_missing(monkeypatch):
    monkeypatch.delenv("WESTOCK_CLI", raising=False)
    monkeypatch.setattr(data_westock.shutil, "which", lambda name: None)
    assert data_westock._resolve_westock_cmd()[0] == "npx"


def test_westock_module_cmd_resolved_on_hosts_with_node():
    """本机装有 Node 时 import 期解析必须已发生（WESTOCK_CMD[0] ≠ 裸 "npx"）。"""
    if shutil.which("npx") is None:
        pytest.skip("host has no node/npx")
    assert data_westock.WESTOCK_CMD[0] != "npx"


def test_registry_scales_westock_volume_from_lots(monkeypatch):
    """P-112 + 单位：英文表头 last→close 映射 + volume 手×100（实测比值 100.0）。"""
    reset_circuit_breakers()
    raw = pd.DataFrame([{
        "date": "2026-11-19", "open": 1442.0, "last": 1460.21,
        "high": 1464.99, "low": 1432.10, "volume": 42692,  # 手
    }])
    monkeypatch.setattr(data_westock, "get_kline", lambda *a, **k: raw.copy())
    df = _fetch_from_source("600519", 5, "westock")
    assert not df.empty
    assert float(df["close"].iloc[0]) == pytest.approx(1460.21)  # last→close 映射
    assert float(df["volume"].iloc[0]) == pytest.approx(4269200.0)  # 42692 手 × 100


def test_westock_as_of_filter_works_with_english_header(monkeypatch):
    """P-112 前视修：英文表头下 as_of_date 必须生效（旧实现只认「日期」→ 不截断）。"""
    output = (
        "| date | open | last | high | low | volume |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 2026-11-19 | 1442 | 1460.21 | 1464.99 | 1432.10 | 42692 |\n"
        "| 2026-11-10 | 1400 | 1410.00 | 1420.00 | 1390.00 | 30000 |\n"
    )
    monkeypatch.setattr(data_westock, "_run_westock", lambda args, timeout=30: output)
    df = data_westock.get_kline("600519", as_of_date="2026-11-15")
    assert list(df["date"]) == ["2026-11-10"]
