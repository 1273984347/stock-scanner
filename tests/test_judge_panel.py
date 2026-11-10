"""judge_panel.py 的零覆盖补齐（RF-01 Phase 4 验证方式 / 覆盖率层）。

范围说明：`JudgePanel.evaluate_all()` 自 P0#13 起不再调用 6 组硬编码规则
（其 `weight=0`，属**生产不可达的保留代码**）。本文件覆盖公开 API 与仍在生产
链路上的 ML 投票 / 汇总 / 权重解析分支，不为凑覆盖率去测已禁用的死规则。
"""

from __future__ import annotations

import pytest

from lib.scoring.judge_panel import (
    JUDGE_GROUPS,
    Judge,
    JudgePanel,
    VoteResult,
    _load_judge_groups,
)


def _vote(action="BUY", confidence=0.8, judge="UnknownJudge", group="测试组"):
    return VoteResult(judge=judge, group=group, action=action,
                      confidence=confidence, reason="test")


# ---------------------------------------------------------------- 数据结构 / 配置

def test_dataclasses_defaults():
    j = Judge(name="A", group="G", weight=1.0)
    assert (j.name, j.group, j.weight) == ("A", "G", 1.0)
    v = VoteResult(judge="A", group="G", action="HOLD", confidence=0.5)
    assert v.reason == ""


def test_load_judge_groups_shape():
    groups = _load_judge_groups()
    assert isinstance(groups, dict) and groups
    for name, cfg in groups.items():
        assert "weight" in cfg and "judges" in cfg, f"{name} missing keys"
        assert isinstance(cfg["judges"], list)
    assert JUDGE_GROUPS == groups


def test_panel_defaults():
    p = JudgePanel()
    assert p.factors == {} and p.fundamentals == {}
    assert p.market_data is None and p.results == []
    p2 = JudgePanel(factors={"a": 1}, fundamentals={"roe": 0.2}, market_data="md")
    assert p2.factors == {"a": 1} and p2.market_data == "md"


# ---------------------------------------------------------------- 无票与门控

def test_evaluate_all_returns_empty_summary():
    p = JudgePanel(factors={"a": 1})
    p.results = [_vote()]  # 先污染，evaluate_all 必须清空
    assert p.evaluate_all() == {
        "consensus": "HOLD", "score": 0, "bullish_pct": 0, "total_votes": 0,
    }
    assert p.results == []


@pytest.mark.parametrize("prediction", [None, {}, {"model_available": False}])
def test_evaluate_with_ml_gated(prediction):
    r = JudgePanel().evaluate_with_ml(prediction)
    assert r["total_votes"] == 0 and r["consensus"] == "HOLD"


# ---------------------------------------------------------------- ML 投票

@pytest.mark.parametrize("consensus,action", [
    ("BUY", "BUY"), ("SELL", "SELL"), ("HOLD", "HOLD"), ("whatever", "HOLD"),
])
def test_evaluate_ml_action_mapping(consensus, action):
    votes = JudgePanel()._evaluate_ml({
        "consensus": consensus, "confidence": 0.7, "score": 42.5, "top3_factors": [],
    })
    assert len(votes) == 3
    assert all(v.action == action for v in votes)
    assert all(v.group == "AI 量化" for v in votes)
    assert {v.judge for v in votes} == {"XGBoost-5d", "LightGBM-20d", "Ensemble"}


def test_evaluate_ml_confidence_cap_and_reason():
    votes = JudgePanel()._evaluate_ml({
        "consensus": "BUY", "confidence": 0.99, "score": 12.3456,
        "top3_factors": [{"name": "trend", "value": 3.2}, {"name": "vol", "value": 1.1}],
    })
    assert all(v.confidence <= 1.0 for v in votes)
    assert "trend=3.2 | vol=1.1" in votes[0].reason
    assert "12.346" in votes[0].reason
    assert "多模型融合" in votes[2].reason


def test_evaluate_with_ml_adds_votes():
    r = JudgePanel().evaluate_with_ml({
        "model_available": True, "consensus": "BUY", "confidence": 0.9,
        "score": 80.0, "top3_factors": [],
    })
    assert r["total_votes"] == 3
    assert r["consensus"] == "BUY"
    assert r["bullish_pct"] == 100.0
    assert r["score"] > 0.2


# ---------------------------------------------------------------- 汇总

@pytest.mark.parametrize("action,expected_consensus", [
    ("BUY", "BUY"), ("SELL", "SELL"), ("HOLD", "HOLD"),
])
def test_get_summary_consensus_branches(action, expected_consensus):
    p = JudgePanel()
    p.results = [_vote(action=action, confidence=0.9)]
    out = p.get_summary()
    assert out["consensus"] == expected_consensus
    assert out["total_votes"] == 1
    assert "group_scores" in out and "测试组" in out["group_scores"]


def test_get_summary_abstain_only():
    p = JudgePanel()
    p.results = [_vote(action="ABSTAIN"), _vote(action="ABSTAIN")]
    out = p.get_summary()
    assert out["consensus"] == "HOLD"
    assert out["total_votes"] == 0
    assert out["abstained"] == 2
    assert out["bullish_pct"] == 0


def test_get_summary_mixed_votes_and_details():
    p = JudgePanel()
    p.results = [_vote("BUY", 0.9, judge="B1"), _vote("SELL", 0.1, judge="S1")]
    out = p.get_summary()
    assert out["total_votes"] == 2
    assert out["bullish_count"] == 1
    assert out["bullish_pct"] == 50.0
    assert len(out["details"]) == 2
    assert out["details"][0]["judge"] == "B1"


# ---------------------------------------------------------------- 权重 / 基本面

def test_get_judge_weight_unknown_fallback():
    assert JudgePanel()._get_judge_weight("NoSuchJudge") == 0.01


def test_get_judge_weight_known_uses_group_average():
    p = JudgePanel()
    for name, cfg in JUDGE_GROUPS.items():
        if cfg["judges"]:
            assert p._get_judge_weight(cfg["judges"][0]) == pytest.approx(
                cfg["weight"] / len(cfg["judges"])
            )
            break


@pytest.mark.parametrize("fund,expected", [
    (None, True), ({}, True),
    ({"profitability": {"ROE": 0.1}}, False),
    ({"solvency": {"debt_to_asset": 0.4}}, False),
    ({"profitability": {}, "solvency": {}}, True),
])
def test_fund_data_missing(fund, expected):
    assert JudgePanel(fundamentals=fund)._fund_data_missing() is expected
