"""ADR-0017 机器判据（2026-11-14）：实时行情源优先级 + hist 源栈去 TDX 本地化。

背景：用户删除本地 TDX 客户端与 vipdoc 目录（D:/TDX 不存在）。
- get_realtime_quotes 旧序 = pytdx 公共服务器（仅 ~28 只样本股）优先 → 市场级策略
  （量价突破/多因子选股）覆盖面不足；akshare 全市场 spot（sina，~5.5k 行含 成交量）
  只是兜底。新序 = akshare 全市场优先，pytdx 降为兜底（pytdx 走公共行情服务器，
  与本地客户端无关，保留）。
- data_source_registry.DATA_SOURCE_PRIORITY 旧序把 tdx_local_qfq / tdx_local 两个
  本地文件源排在头部——目录已删，每次 hist 加载都先撞两下死路径再降级。
  新序 = 摘到尾部（保留注册，env TDX_DATA_PATH 可重定向恢复）。
"""

import sys
import types

import pandas as pd
import pytest

import lib.data.data_layer as data_layer
from lib.data.data_source_registry import (DATA_SOURCE_PRIORITY,
                                            DATA_SOURCE_PRIORITY_ADJUSTED)


class _FakeTdxApi:
    """pytdx 假 API：记录实例化，get_security_quotes 返回可辨识的样本帧。"""

    instances = 0

    def __init__(self):
        _FakeTdxApi.instances += 1

    def connect(self, host, port, time_out=5):
        return True

    def get_security_quotes(self, codes):
        return [
            {"code": "600519", "name": "TDX样本", "price": 99.0, "open": 98.0,
             "high": 100.0, "low": 97.0, "vol": 12345.0, "amount": 6.7e8},
        ]

    def disconnect(self):
        pass


def _install_pytdx_fake(monkeypatch):
    _FakeTdxApi.instances = 0
    fake_hq = types.ModuleType("pytdx.hq")
    fake_hq.TdxHq_API = _FakeTdxApi
    fake_pkg = types.ModuleType("pytdx")
    monkeypatch.setitem(sys.modules, "pytdx", fake_pkg)
    monkeypatch.setitem(sys.modules, "pytdx.hq", fake_hq)


def test_realtime_quotes_prefers_full_market_akshare(monkeypatch):
    """pytdx 可用且成功时，也必须先走 akshare 全市场（覆盖面判据：全市场 > 28 只样本）。"""
    _install_pytdx_fake(monkeypatch)
    df = data_layer.get_realtime_quotes(force=True)
    # conftest autouse 已 mock ak.stock_zh_a_spot（100 行「股票N」帧）
    assert not df.empty and "股票1" in set(df["名称"]), "应返回 akshare 全市场帧"
    assert _FakeTdxApi.instances == 0, "akshare 成功时不得触碰 pytdx"


def test_realtime_quotes_falls_back_to_pytdx_when_akshare_empty(monkeypatch):
    """akshare 返回空 → 回退 pytdx 公共服务器，列契约经 col_map 归一（含 成交量）。"""
    _install_pytdx_fake(monkeypatch)
    monkeypatch.setattr(data_layer.ak, "stock_zh_a_spot", lambda: pd.DataFrame())

    df = data_layer.get_realtime_quotes(force=True)
    assert not df.empty, "pytdx 兜底应返回样本帧"
    assert df.iloc[0]["名称"] == "TDX样本"
    for col in ("代码", "最新价", "成交量", "成交额"):
        assert col in df.columns, f"pytdx 兜底缺列 {col}（col_map 未覆盖）"


def test_registry_priority_demotes_dead_local_tdx_sources():
    """hist 源栈：本地 TDX 文件源（目录已删）摘到尾部；源集合不变（一个不丢）。

    ADR-0027（2026-11-19）更新头部：新增两条网页 K 线网关。原断言
    「baostock 首位」已不成立——baostock 当日进黑名单（10001011），
    且网页网关实测可返最近交易日。尾部约束与原意（摘除≠删除）保持不变。
    """
    assert DATA_SOURCE_PRIORITY[:2] == ["sina_kline", "tencent_kline"], \
        f"默认栈应由网页网关领头（覆盖最广），实际 {DATA_SOURCE_PRIORITY[:2]}"
    for priority in (DATA_SOURCE_PRIORITY, DATA_SOURCE_PRIORITY_ADJUSTED):
        assert priority[-2:] == ["tdx_local_qfq", "tdx_local"], (
            f"本地 TDX 源应在尾部，实际 {priority}"
        )
    # 源集合不变：摘除 ≠ 删除（有 TDX 的环境可经 TDX_DATA_PATH 恢复）
    assert set(DATA_SOURCE_PRIORITY) == {
        "sina_kline", "tencent_kline", "baostock", "tdx",
        "adata", "westock", "tushare", "akshare",
        "tdx_local_qfq", "tdx_local",
    }
    # 回测栈：腾讯（唯一提供前复权）领头；新浪**不得**进入——
    # 不复权价在除权日会造出假跳空，属于隐蔽失真源。
    assert DATA_SOURCE_PRIORITY_ADJUSTED[0] == "tencent_kline"
    assert "sina_kline" not in DATA_SOURCE_PRIORITY_ADJUSTED, \
        "不复权的新浪源不得进回测栈"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
