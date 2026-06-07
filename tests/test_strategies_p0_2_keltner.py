"""
P0-2 修复测试: Keltner XOSI MT 策略支持 as_of_date 截断

Bug 描述 (lib/strategies.py:595-755):
    get_keltner_xosi_mt_candidates 的 `index_df` 直接调
    `ak.stock_zh_index_spot_em()`（实时）→ `get_stock_hist_multi_source(code, 60)`，
    且内部 `_batch_get_stock_hist(all_codes, days=60)` 不传 as_of_date。
    回测/复盘时返回"未来"指数/股票数据。

修复：
    策略接收 as_of_date 参数：
    - 传给 _batch_get_stock_hist 限定股票历史窗口
    - 拿到 index_hist 后按 as_of_date 截断（get_stock_hist_multi_source 不接受 cutoff）
    - 与 _safe_signal_date() 协同：默认 None = 实时（向后兼容）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect
import pandas as pd
import pytest
from datetime import datetime


def _build_synthetic_index(dates, base=3000.0):
    """构造伪指数 DataFrame（date/open/high/low/close/volume 列）"""
    n = len(dates)
    df = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": [base] * n,
        "high": [base + 10] * n,
        "low": [base - 10] * n,
        "close": [base] * n,
        "volume": [1_000_000_000] * n,
    })
    return df


class TestKeltnerXOSIAsOfDate:
    """P0-2: get_keltner_xosi_mt_candidates 应支持 as_of_date 截断"""

    def test_function_signature_accepts_as_of_date(self):
        """P0-2: 函数签名应包含 as_of_date 参数

        修复前：函数只有 (config)
        修复后：函数有 (config, as_of_date=None)
        """
        from lib.strategies import get_keltner_xosi_mt_candidates
        sig = inspect.signature(get_keltner_xosi_mt_candidates)
        assert "as_of_date" in sig.parameters, (
            f"P0-2 修复未生效：get_keltner_xosi_mt_candidates 仍不接受 as_of_date。"
            f" 当前参数: {list(sig.parameters.keys())}"
        )

    def test_index_df_truncated_to_as_of_date(self, monkeypatch):
        """P0-2: 传入 as_of_date 时，指数数据应被截断到 as_of_date

        场景：
          - 实时指数数据有 60 天（含今天 2026-06-15）
          - as_of_date = '2026-06-10'（5 天前）
          - 修复后：index_df 应只含 ≤ 2026-06-10 的数据（55 天）
        """
        from lib import strategies

        # 构造指数数据：2026-05-01 ~ 2026-06-15（含 as_of_date 之后 5 天）
        dates = pd.date_range("2026-05-01", "2026-06-15", freq="D").strftime("%Y-%m-%d").tolist()
        full_idx = _build_synthetic_index(dates)

        # mock get_stock_hist_multi_source 返回 full_idx
        monkeypatch.setattr(
            strategies, "get_stock_hist_multi_source",
            lambda code, days, *args, **kwargs: full_idx.copy()
        )
        # mock akshare 实时指数
        import akshare as ak
        idx_spot = pd.DataFrame({"名称": ["上证指数"], "代码": ["000001"]})
        monkeypatch.setattr(ak, "stock_zh_index_spot_em", lambda: idx_spot)

        # 探测：在函数入口处 monkey-patch detect_market_regime，让其不依赖真实数据
        captured = {}
        def fake_detect(df):
            captured["index_df_max_date"] = df["date"].max() if "date" in df.columns else None
            captured["index_df_len"] = len(df)
            return {"regime": "TREND_UP", "confidence": 0.7}
        monkeypatch.setattr(
            "lib.market_regime.detect_market_regime", fake_detect
        )

        # mock get_realtime_quotes 返回非空，避免函数 short-circuit
        monkeypatch.setattr(
            strategies, "get_realtime_quotes",
            lambda: pd.DataFrame({
                "代码": ["600000"], "名称": ["测试"], "成交量": [1],
                "涨跌幅": [1.0], "成交额": [1e9]
            })
        )
        # mock _batch_get_stock_hist 返回空（避免走到个股计算逻辑）
        monkeypatch.setattr(
            strategies, "_batch_get_stock_hist",
            lambda codes, days=180, as_of_date=None: {}
        )

        from config import SystemConfig
        cfg = SystemConfig()
        strategies.get_keltner_xosi_mt_candidates(cfg, as_of_date="2026-06-10")

        # 修复后：detect_market_regime 收到的 df 应只含 ≤ 2026-06-10
        assert captured.get("index_df_max_date") is not None, (
            "P0-2 修复未生效：detect_market_regime 未被调用（可能 short-circuit 在前），"
            " 应在 index_df 截断后再判断 regime。"
        )
        # max date 应为 2026-06-10（as_of_date 当天），不能含 2026-06-11~06-15
        from datetime import date
        max_date = captured["index_df_max_date"]
        if hasattr(max_date, "date"):
            max_date = max_date.date()
        assert max_date == date(2026, 6, 10), (
            f"P0-2 修复未生效：index_df.max date = {captured['index_df_max_date']}，"
            f" 应等于 as_of_date 2026-06-10（不能含 06-11~06-15）"
        )

    def test_batch_get_stock_hist_called_with_as_of_date(self, monkeypatch):
        """P0-2: _batch_get_stock_hist 调用应传入 as_of_date

        修复前：stock_hist_data = _batch_get_stock_hist(all_codes, days=60)
        修复后：stock_hist_data = _batch_get_stock_hist(all_codes, days=60, as_of_date=as_of_date)
        """
        from lib import strategies

        captured = {}
        real_batch = strategies._batch_get_stock_hist

        def spy_batch(codes, days=180, as_of_date=None):
            captured["as_of_date"] = as_of_date
            captured["days"] = days
            return real_batch(codes, days, as_of_date=as_of_date)

        monkeypatch.setattr(strategies, "_batch_get_stock_hist", spy_batch)

        # mock 外部依赖让函数能走到 _batch_get_stock_hist 调用
        monkeypatch.setattr(
            strategies, "get_realtime_quotes",
            lambda: pd.DataFrame({"代码": ["600000"], "名称": ["测试"], "成交量": [1], "涨跌幅": [1.0], "成交额": [1e9]})
        )
        # mock akshare
        import akshare as ak
        monkeypatch.setattr(ak, "stock_zh_index_spot_em", lambda: pd.DataFrame())

        # mock detect_market_regime → TREND_UP
        monkeypatch.setattr(
            "lib.market_regime.detect_market_regime",
            lambda df: {"regime": "TREND_UP", "confidence": 0.7}
        )

        # mock get_stock_hist_multi_source → 空（让 candidates 列表为空，但 batch 已被调用）
        monkeypatch.setattr(
            strategies, "get_stock_hist_multi_source",
            lambda code, days, *args, **kwargs: pd.DataFrame()
        )

        from config import SystemConfig
        cfg = SystemConfig()
        strategies.get_keltner_xosi_mt_candidates(cfg, as_of_date="2026-06-10")

        assert captured.get("as_of_date") == "2026-06-10", (
            f"P0-2 修复未生效：_batch_get_stock_hist 未收到 as_of_date，"
            f" 实际: {captured.get('as_of_date')}"
        )
