"""RF-01 Phase 4 特征测试 —— 统一评分契约的硬守卫。

按 `docs/plans/2026-11-07-refactor-plan.md` §三 Phase 4「验证方式 / 特征测试」：
四维分映射、等级边界、权重缺失时的默认值；另加三条**机器判据**：
  ① 适配器必须是纯转发（AST 禁算术）—— 防止以后有人把权重计算塞进适配层；
  ② `raw` 必须是旧实现返回值的同一对象 —— 对拍才有取证意义；
  ③ 权重定义只能有一个落点 —— 防止「收口」之后又长回模块内硬编码。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from lib.scoring.scoring_adapters import build_kernel
from lib.scoring.scoring_contract import (
    FOUR_DIMENSIONS,
    PRIMARY_4D_SOURCE,
    AdapterSpec,
    ScoringKernel,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------- 四维映射 / 等级

def test_primary_source_maps_four_dimensions():
    k = build_kernel()
    r = k.score("scorer_4d", {"fundamental": 8, "capital_flow": 6,
                              "news": 5, "technical": 7})
    out = r.to_dict()
    assert set(out) == set(FOUR_DIMENSIONS) | {"total", "level"}
    assert out["fundamental"] == 8
    assert out["capital_flow"] == 6
    assert out["total"] == 6.6
    assert r.authoritative_4d is True


@pytest.mark.parametrize("value,expected", [
    (10.0, "强烈买入"), (8.000001, "强烈买入"), (7.999999, "买入"),
    (6.000001, "买入"), (5.999999, "持有"),
    (4.000001, "持有"), (3.999999, "谨慎"),
    (2.000001, "谨慎"), (1.999999, "回避"), (0.0, "回避"),
])
def test_rating_boundaries(value, expected):
    """等级边界冻结：阈值来自 scorer.SCORE_4D；用阈值 ±1e-6 避开浮点临界。"""
    k = build_kernel()
    r = k.score("scorer_4d", {"fundamental": value, "capital_flow": value,
                              "news": value, "technical": value})
    assert r.level == expected
    assert r.to_dict()["level"] == expected


def test_exact_threshold_keeps_legacy_float_behavior():
    """锁既有口径：恰好 8.0/4.0/2.0 会因加权浮点误差落到**下一档**。

    实测 `weighted = 8*0.35 + 8*0.35 + 8*0.20 + 8*0.10 = 7.999999999999999`。
    这是现存行为，Phase 4「不改变任何评分数值」要求它**保持原样**；
    若将来有人「顺手修正」浮点比较，本测试必须判红并走 ADR。
    """
    k = build_kernel()
    for value, expected in ((8.0, "买入"), (4.0, "谨慎"), (2.0, "回避")):
        r = k.score("scorer_4d", {"fundamental": value, "capital_flow": value,
                                  "news": value, "technical": value})
        assert r.level == expected, f"{value} boundary changed"


def test_additional_sources_never_mix_into_four_dimensions():
    """评委团 / Agent / 机会分是各自量纲的附加维度，不得混算进四维总分。"""
    k = build_kernel()
    r = k.score("opportunity_scorer", {
        "regime": "TREND_UP", "regime_confidence": 0.8,
        "alpha_posterior": 0.7, "sector_score": 65.0,
        "trend_strength": 4.0, "volume_ratio": 1.4,
    })
    out = r.to_dict()
    assert r.authoritative_4d is False
    assert all(out[d] is None for d in FOUR_DIMENSIONS)
    assert isinstance(out["total"], (int, float))


def test_raw_is_the_legacy_return_object():
    """B4 同构：`raw` 必须是旧实现那次调用的同一对象引用，而非拷贝/重算。"""
    import lib.scoring.scorer as scorer

    sentinel = {"score": 1.25, "rating": "哨兵", "breakdown": {}}
    orig = scorer.comprehensive_score
    scorer.comprehensive_score = lambda *a, **k: sentinel
    try:
        r = build_kernel().score("scorer_4d", {
            "fundamental": 1, "capital_flow": 1, "news": 1, "technical": 1,
        })
    finally:
        scorer.comprehensive_score = orig
    assert r.raw is sentinel
    assert r.total == 1.25
    assert r.level == "哨兵"


def test_kernel_matches_legacy_for_pure_sources():
    """不经基线文件，当场逐字段比对（纯函数入口）。"""
    from lib.scoring.opportunity_scorer import compute_opportunity_score
    from lib.scoring.scorer import comprehensive_score, six_factor_score

    k = build_kernel()
    i4 = {"fundamental": 7.3, "capital_flow": 4.1, "news": 6.2, "technical": 8.4}
    assert k.score("scorer_4d", i4).raw == comprehensive_score(**i4)

    i6 = {"factors": {"trend_strength": 3.2, "momentum_5": 1.1,
                      "volume_ratio": 1.4, "volatility_20": 2.0},
          "sector_score": 61.0}
    assert k.score("scorer_6f", i6).raw == six_factor_score(**i6)

    io = {"regime": "TREND_UP", "regime_confidence": 0.8, "alpha_posterior": 0.7,
          "sector_score": 65.0, "trend_strength": 4.0, "volume_ratio": 1.4}
    assert k.score("opportunity_scorer", io).raw == compute_opportunity_score(**io)


# ---------------------------------------------------------------- 注册表

def test_registry_integrity():
    k = build_kernel()
    assert len(k.sources) == 9
    assert sum(k.spec(s).authoritative_4d for s in k.sources) == 1
    assert k.spec(PRIMARY_4D_SOURCE).authoritative_4d is True
    with pytest.raises(KeyError):
        k.score("no_such_source", {})
    with pytest.raises(ValueError, match="duplicate"):
        k.register(AdapterSpec(name="scorer_4d", call=lambda i: None))
    with pytest.raises(ValueError, match="authoritative_4d"):
        ScoringKernel().register(AdapterSpec(
            name="impostor", call=lambda i: None, authoritative_4d=True,
        ))


# ---------------------------------------------------------------- 权重单一落点

def test_weights_have_single_definition_point():
    import lib.scoring.scorer as scorer
    import lib.scoring.scoring_weights as sw

    assert scorer.WEIGHTS_4D is sw.WEIGHTS_4D
    assert scorer.SIX_FACTOR_WEIGHTS is sw.SIX_FACTOR_WEIGHTS
    assert sum(sw.WEIGHTS_4D.values()) == pytest.approx(1.0)
    assert sum(sw.SIX_FACTOR_WEIGHTS.values()) == pytest.approx(1.0)

    tree = ast.parse((ROOT / "lib/scoring/scorer.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            assert not (names & {"WEIGHTS_4D", "SIX_FACTOR_WEIGHTS"}), (
                "权重定义必须只在 lib/scoring/scoring_weights.py；scorer.py 仅 re-export"
            )


def test_all_weight_sources_are_registered():
    from lib.scoring.scoring_weights import list_weight_sources
    src = list_weight_sources()
    assert len(src) >= 6
    assert src["scorer.weights_4d"].endswith("WEIGHTS_4D")
    assert src["scorer.six_factor_weights"].endswith("SIX_FACTOR_WEIGHTS")
    assert "scorecard.layer_weights" in src
    assert "judge_panel.groups" in src


# ---------------------------------------------------------------- 开关（P7-3 已收口，flag 删除；历史见 ADR-0014）


# ---------------------------------------------------------------- 适配器纯转发（AST 硬守卫）

def test_adapters_are_pure_forwarders():
    """适配层一旦出现算术，权重就可能被算两次 —— 必须机器判红。"""
    tree = ast.parse((ROOT / "lib/scoring/scoring_adapters.py").read_text(encoding="utf-8"))
    calls = [n for n in tree.body
             if isinstance(n, ast.FunctionDef) and n.name.startswith("_call_")]
    assert len(calls) == 9, f"expected 9 adapters, got {len(calls)}"
    for fn in calls:
        assert not any(isinstance(n, ast.BinOp) for n in ast.walk(fn)), (
            f"{fn.name} contains arithmetic; adapters must be pure forwarders"
        )
        assert any(isinstance(n, ast.Return) for n in fn.body)
        # 只解包 + 一次旧实现调用：不允许 if/for/while 等控制流
        assert not any(isinstance(n, (ast.If, ast.For, ast.While, ast.Try))
                       for n in ast.walk(fn)), f"{fn.name} has control flow"
