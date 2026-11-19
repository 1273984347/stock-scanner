"""坑表 P-105 机器判据（2026-11-19）：策略内 NameError 被 try/except 吞掉 = 静默少一个策略。

现象：真实扫描日志 `量价突破策略出错: name 'date' is not defined`，
该策略本轮产出 0 候选。根因是 P1-3 引入 `as_of_date` 参数时调用点仍写
`ScoreCard.score(..., as_of_date=date)`，`date` 未定义——只在量价突破分支走到时触发。

此类缺陷的共性：策略层 `except Exception: logger.error(...); return empty` 让
功能静默降级，L1 若不覆盖该分支就全绿。判据必须同时钉「参数名正确」与
「该分支真能走通」。
"""
import pathlib

import pandas as pd
import pytest

import lib.strategy.strategies as st
from config import get_config


def test_no_undefined_date_forwarding():
    """静态守卫：全仓不得再有 `as_of_date=date`（P1-3 遗留笔误）。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for p in list((root / "lib").rglob("*.py")) + list((root / "backtest").rglob("*.py")):
        text = p.read_text(encoding="utf-8")
        if "as_of_date=date" in text:
            offenders.append(str(p.relative_to(root)))
    assert offenders == [], f"未定义的 date 被当作 as_of_date 转发: {offenders}"


def test_volume_breakout_reaches_scorecard_with_as_of_date(monkeypatch):
    """行为判据：策略分支必须走到 ScoreCard.score，且 as_of_date 原样透传。"""
    rt = pd.DataFrame({
        "代码": ["600519"], "名称": ["贵州茅台"], "成交量": [1e6],
        "涨跌幅": [5.0], "成交额": [1e9], "最新价": [1400.0],
    })
    hist = pd.DataFrame({
        "date": pd.date_range("2026-06-01", periods=80, freq="B"),
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6,
    })
    seen = {}

    monkeypatch.setattr(st, "get_realtime_quotes", lambda: rt)
    monkeypatch.setattr(st, "get_market_sentiment", lambda: 0.0)
    monkeypatch.setattr(st, "_batch_get_stock_hist",
                        lambda codes, days=180, as_of_date=None: {c: hist.copy() for c in codes})
    monkeypatch.setattr(st, "_batch_get_financial_data", lambda codes: {})
    monkeypatch.setattr(st, "get_index_hist", lambda *a, **k: hist.copy())

    class _Constraint:
        def check_ticker(self, code, market_data=None, financial_data=None, stock_name=""):
            return type("R", (), {"blocked": False})()

    monkeypatch.setattr(st, "_constraints", _Constraint())

    import lib.scoring.scorecard as sc

    def fake_score(code, row, history, as_of_date=None):
        seen["as_of_date"] = as_of_date
        return {"verdict": "中性", "score": 60}

    monkeypatch.setattr(sc.ScoreCard, "score", staticmethod(fake_score))
    monkeypatch.setattr(st, "compute_all_signals", lambda *a, **k: None)

    st.get_volume_breakout_candidates(get_config(), as_of_date="2026-11-18")
    assert seen.get("as_of_date") == "2026-11-18", \
        f"量价突破分支未走到打分卡，或 as_of_date 未透传: {seen}"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
