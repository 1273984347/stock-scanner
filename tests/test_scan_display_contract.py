"""P-79 机器判据（2026-11-14）：CLI 扫描展示/推送的 scan_result 解包契约。

事故：pipeline.run_all_strategies 返回复合 scan_result（results/risk_check/portfolio/
ml_ranking/...），而 run.py 自 2026-05-30（b6d63b2）起把复合 dict 直接传给
print_results / send_feishu_message —— 有候选股即崩：
  print_results:        'dict' object has no attribute 'drop'   (portfolio 键)
  build_message_content: 'str' object has no attribute 'empty' (preset 键)
控制台展示与飞书推送断流 6 个月（cache/reports/push_log.json 末条 2026-05-30 佐证）。

修复：run.py 两处调用改经 unwrap_scan_results 解包（与 routers/common.py 的
Web 路径解包一致）。本文件把「解包 + 裸映射消费」钉成机器判据。
"""

import pandas as pd
import pytest

from reporter.scan_notify import build_message_content, print_results, unwrap_scan_results


def _two_candidate_df():
    return pd.DataFrame({
        "代码": ["000001", "600519"],
        "名称": ["平安银行", "贵州茅台"],
        "综合评分": [80.0, 75.0],
    })


def _composite_scan_result():
    """与 lib/strategy/pipeline.run_all_strategies 的返回结构同形（scan_result 九键）。"""
    return {
        "results": {"热门板块": _two_candidate_df(), "龙虎榜": pd.DataFrame()},
        "risk_check": {"gates": []},
        "factor_health": {},
        "portfolio": {"weights": {}},
        "alpha_analysis": {},
        "ml_ranking": {"data": []},
        "preset": "balanced",
        "macro_filter": {"position_mult": 1.0},
        "index_signal": {"bull_market": True},
    }


# ---------------------------------------------------------------------------
# unwrap_scan_results：三种输入形状
# ---------------------------------------------------------------------------

def test_unwrap_composite_scan_result_extracts_results():
    composite = _composite_scan_result()
    unwrapped = unwrap_scan_results(composite)
    assert set(unwrapped.keys()) == {"热门板块", "龙虎榜"}
    assert list(unwrapped["热门板块"]["代码"]) == ["000001", "600519"]


def test_unwrap_bare_mapping_passthrough():
    """历史调用方直接传裸映射（routers/common.py 解包后再传的场景）→ 原样返回。"""
    bare = {"热门板块": _two_candidate_df()}
    assert unwrap_scan_results(bare) is bare


def test_unwrap_alert_dict_passthrough_no_crash():
    """pipeline.py IC 衰减告警传 {"type": ..., "alerts": [...]}——无 results 键，
    解包器不得崩（内容层适配是独立台账项，此处只保证解包层稳定）。"""
    alert = {"type": "ic_decay_alert", "alerts": [{"severity": "high"}]}
    assert unwrap_scan_results(alert) is alert


def test_unwrap_non_dict_returns_empty():
    assert unwrap_scan_results(None) == {}
    assert unwrap_scan_results("garbage") == {}


# ---------------------------------------------------------------------------
# 裸映射消费层：print_results / build_message_content（解包后的输入形状）
# ---------------------------------------------------------------------------

def test_print_results_with_unwrapped_composite(capsys):
    """解包后传入 print_results：逐策略打印只数 + 汇总，不抛 AttributeError。"""
    print_results(unwrap_scan_results(_composite_scan_result()), "balanced")
    out = capsys.readouterr().out
    assert "【热门板块】2 只" in out
    assert "【龙虎榜】0 只" in out
    assert "共 2 只候选股" in out
    assert "'dict' object" not in out


def test_build_message_content_with_unwrapped_composite():
    """解包后传入消息构造：统计只含 DataFrame 策略的候选数，不抛 AttributeError。"""
    text = build_message_content(unwrap_scan_results(_composite_scan_result()), "balanced")
    assert "热门板块: 2" in text
    # 空策略不计入统计
    assert "龙虎榜" not in text.split("【候选股统计】")[1].split("=")[0]


def test_print_results_bare_mapping_regression(capsys):
    """裸映射直传（unwrap 后的合法形状）回归钉：不因解包修复改变输出语义。"""
    print_results({"热门板块": _two_candidate_df()}, "balanced")
    out = capsys.readouterr().out
    assert "【热门板块】2 只" in out and "共 2 只候选股" in out
