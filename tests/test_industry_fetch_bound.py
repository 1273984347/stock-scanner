"""坑表 P-106 机器判据（2026-11-19）：第三方 socket 无超时的调用必须有界。

现象：修掉量价突破的 NameError 后，整轮扫描从 1m46s 变成 >10 分钟超时。
faulthandler 抓到卡点：`ScoreCard.score → layer2_sector_strength → _get_industry
→ baostock.query_stock_industry → socketutil.send_msg`。baostock 无 socket 超时，
每只候选股打分都同步等它，单次挂起就能拖死整轮扫描。

修法：daemon 工作线程 + join(timeout) 包裹第三方调用；超时抛 TimeoutError，
`_get_industry` 显式降级为「行业中性」（空缓存 + 60s 短重试 TTL）。
"""
import threading
import time

import pytest

import lib.scoring.scorecard as sc


def test_baostock_industry_fetch_is_bounded(monkeypatch):
    """慢调用必须在给定超时内返回（抛 TimeoutError），不能无限等。"""
    import baostock as bs

    def slow(*a, **k):
        # 不用 time.sleep：conftest 把 time.sleep 全局替换成 no-op 了
        threading.Event().wait(3)

        class _RS:
            error_code = "0"

            def next(self):
                return False

        return _RS()

    monkeypatch.setattr(bs, "login", lambda: type("R", (), {"error_code": "0"})())
    monkeypatch.setattr(bs, "query_stock_industry", slow)

    t = time.time()
    with pytest.raises(TimeoutError):
        sc._fetch_baostock_industry_map(timeout_s=0.2)
    assert time.time() - t < 2.0, "超时保护没有生效"


def test_get_industry_degrades_and_negative_caches(monkeypatch):
    """失败时返回空行业（中性），且 60s 内不重复打同一个慢接口。"""
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise TimeoutError("simulated hang")

    monkeypatch.setattr(sc, "_fetch_baostock_industry_map", boom)
    sc.refresh_industry_cache()
    try:
        assert sc._get_industry("600519") == ""
        assert sc._get_industry("000001") == ""
        assert len(calls) == 1, f"失败后不应逐股重试，实际 {len(calls)} 次"
    finally:
        sc.refresh_industry_cache()


def test_baostock_map_parsing(monkeypatch):
    """正常路径仍能解析 code -> industry 映射。"""
    import baostock as bs

    class _RS:
        error_code = "0"
        rows = [["update", "sh.600519", "白酒", "食品饮料"],
                ["update", "sz.000001", "银行", "金融"]]

        def __init__(self):
            self.i = 0

        def next(self):
            if self.i >= len(self.rows):
                return False
            self.i += 1
            return True

        def get_row_data(self):
            return self.rows[self.i - 1]

    monkeypatch.setattr(bs, "login", lambda: type("R", (), {"error_code": "0"})())
    monkeypatch.setattr(bs, "query_stock_industry", _RS)
    monkeypatch.setattr(bs, "logout", lambda: None)

    out = sc._fetch_baostock_industry_map(timeout_s=2)
    assert out["sh.600519"] == "食品饮料"
    assert out["sz.000001"] == "金融"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
