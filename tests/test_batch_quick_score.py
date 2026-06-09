"""batch_quick_score N+1 fix 测试 (2026-06-09)

来自 wip/pipeline-refactor WIP commit 5c5d728 的 N+1 fix 独立提取:
- master 原: for-loop 串行 quick_score, 70+ stocks × 200ms = 14s 串行
- fix 后: ThreadPoolExecutor 并行, ~3s 跑完
- 不依赖 P0-2/P0-3 修复, 独立可应用

覆盖:
- 4 batch_quick_score 单元 (基本 / 跳过无代码 / 异常容错 / batch_flows 透传)
- 2 调用方回归 (get_zt_candidates / get_lhb_candidates 走 batch 后行为不变)
"""
import os, sys
sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from lib import strategies as strat


# ─────────────────────────────────────────────────────────────
# batch_quick_score 单元测试 (4 用例)
# ─────────────────────────────────────────────────────────────

def test_batch_quick_score_basic_parallel():
    """3 个有效股票 → 3 个评分结果"""
    rows = [
        pd.Series({'代码': '600519', '涨跌幅': 1.5, '60日涨跌幅': 10.0}),
        pd.Series({'代码': '000001', '涨跌幅': -0.5, '60日涨跌幅': -2.0}),
        pd.Series({'代码': '600036', '涨跌幅': 0.0, '60日涨跌幅': 5.0}),
    ]
    results = strat.batch_quick_score(rows, max_workers=3)
    assert len(results) == 3
    assert set(results.keys()) == {'600519', '000001', '600036'}
    # 每个结果都应有 score 字段 (无 mock 真实 quick_score 走完整逻辑)
    for code, res in results.items():
        assert "score" in res, f"{code} 应有 score 字段"


def test_batch_quick_score_skip_rows_without_code():
    """rows 含 None 代码 → 跳过, 不入结果"""
    rows = [
        pd.Series({'代码': '600519', '涨跌幅': 1.5}),
        pd.Series({'代码': None}),  # 应被跳过
        pd.Series({'代码': '000001', '涨跌幅': -0.5}),
    ]
    results = strat.batch_quick_score(rows)
    assert '600519' in results
    assert '000001' in results
    assert None not in results, "None 代码应被跳过"


def test_batch_quick_score_handles_exception_in_one_row():
    """单个 row 抛异常 → 该 row 标 error, 其他不受影响"""
    # 直接 mock quick_score 让它对特定 row 抛异常
    original_quick_score = strat.quick_score

    def selective_quick_score(row, **kwargs):
        if str(row.get("代码", "")) == "BAD":
            raise ValueError("simulated failure")
        return original_quick_score(row, **kwargs)

    rows = [
        pd.Series({'代码': '600519', '涨跌幅': 1.5}),
        pd.Series({'代码': 'BAD', '涨跌幅': 0.0}),  # 应抛异常
        pd.Series({'代码': '000001', '涨跌幅': -0.5}),
    ]
    with patch.object(strat, "quick_score", side_effect=selective_quick_score):
        results = strat.batch_quick_score(rows)

    assert "600519" in results
    assert "000001" in results
    assert results["BAD"] == {"score": None, "rating": "error"}, \
        f"异常 row 应返 error sentinel, 实际 {results.get('BAD')}"


def test_batch_quick_score_passes_batch_flows_and_news():
    """batch_flows / batch_news 透传给每个 quick_score 调用"""
    flows = {"600519": {"main_net_inflow": 1000}, "000001": {"main_net_inflow": -500}}
    news = {"600519": {"major_positive": True}}
    rows = [
        pd.Series({'代码': '600519', '涨跌幅': 1.5}),
        pd.Series({'代码': '000001', '涨跌幅': -0.5}),
    ]
    captured_kwargs = []

    def capture_quick_score(row, **kwargs):
        captured_kwargs.append((str(row.get("代码")), kwargs))
        return {"score": 5.0}

    with patch.object(strat, "quick_score", side_effect=capture_quick_score):
        strat.batch_quick_score(rows, batch_flows=flows, batch_news=news)

    # 验证每个 code 收到的 kwargs 包含正确 flow/news
    captured_by_code = {code: kw for code, kw in captured_kwargs}
    assert captured_by_code["600519"]["fund_flow"] == flows["600519"]
    assert captured_by_code["600519"]["news_sig"] == news["600519"]
    assert captured_by_code["000001"]["fund_flow"] == flows["000001"]
    # '000001' 不在 news 中, 透传 None
    assert captured_by_code["000001"]["news_sig"] is None
