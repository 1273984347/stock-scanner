"""7 个评分模块 / 9 个入口的**纯转发适配器** —— RF-01 Phase 4 阶段 A。

每个 `_call_*` 只做三件事：解包 inputs → 调用一次旧实现 → 原样 return。
**不做任何算术、不设默认值填充、不改返回结构**。
`ScoringResult.raw` 保存的就是旧实现返回的同一对象，对拍基线据此证明「数值零变更」。

回归口径：旧实现仍是唯一算法来源；本文件被删掉也不会影响任何评分结果。
新代码要接评分，经 `ScoringKernel.score(source, inputs)`，**不要**直接调 `_call_*`。
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from lib.scoring.scoring_contract import (
    PRIMARY_4D_SOURCE,
    AdapterSpec,
    ScoringKernel,
)


# ---------------------------------------------------------------- 纯转发层
# ⚠️ AST 守卫（tests/features/test_scoring_contract.py）禁止这些函数体出现 BinOp。
#    权重/阈值计算只允许发生在旧实现里；适配器一算，对拍就失去意义。

def _call_scorer_4d(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.scorer import comprehensive_score
    return comprehensive_score(
        inputs["fundamental"], inputs["capital_flow"],
        inputs["news"], inputs["technical"],
    )


def _call_scorer_6f(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.scorer import six_factor_score
    return six_factor_score(inputs["factors"], inputs.get("sector_score", 50))


def _call_scorecard(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.scorecard import ScoreCard
    return ScoreCard.score(
        inputs["code"], inputs["row"], inputs.get("hist"),
        inputs.get("market_brief"), inputs.get("industry_ret"),
        inputs.get("sector_data"), inputs.get("as_of_date"),
    )


def _call_judge_panel(inputs: Mapping[str, Any]) -> Any:
    # 走 evaluate_with_ml：evaluate_all() 自 P0#13 起不再产生任何投票（恒 HOLD/0），
    # 用它会让对拍基线退化成「空 vs 空」，锁不住口径。生产链路亦以 ML 路径为主。
    from lib.scoring.judge_panel import JudgePanel
    return JudgePanel(
        inputs.get("factors"), inputs.get("fundamentals"), inputs.get("market_data"),
    ).evaluate_with_ml(inputs.get("ml_prediction"))


def _call_investor_agents(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.investor_agents import AgentPanel
    return AgentPanel().vote(
        inputs["hist_df"], code=inputs.get("code"),
        data_factors=inputs.get("data_factors"), as_of_date=inputs.get("as_of_date"),
    )


def _call_panel_decision(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.panel_decision import get_panel_decision
    return get_panel_decision(
        inputs["code"], inputs["hist_df"],
        inputs.get("data_factors"), inputs.get("as_of_date"),
    )


def _call_opportunity_scorer(inputs: Mapping[str, Any]) -> Any:
    from lib.scoring.opportunity_scorer import compute_opportunity_score
    return compute_opportunity_score(
        inputs["regime"], inputs["regime_confidence"], inputs["alpha_posterior"],
        inputs["sector_score"], inputs["trend_strength"], inputs["volume_ratio"],
    )


def _call_strategies_quick(inputs: Mapping[str, Any]) -> Any:
    from lib.strategy.strategies import quick_score
    return quick_score(inputs["row"], inputs.get("fund_flow"), inputs.get("news_sig"))


def _call_strategies_full(inputs: Mapping[str, Any]) -> Any:
    from lib.strategy.strategies import full_score
    return full_score(inputs["code"], inputs["hist"], inputs["factors"], inputs["row"])


# ---------------------------------------------------------------- 注册表

def default_specs() -> Tuple[AdapterSpec, ...]:
    """9 个入口的声明式注册表。`dimension_keys` 只描述「从哪读」，不改值。"""
    return (
        AdapterSpec(
            name=PRIMARY_4D_SOURCE, call=_call_scorer_4d,
            total_key="score", level_key="rating", dimension_keys=("breakdown",),
            authoritative_4d=True,
        ),
        AdapterSpec(
            name="scorer_6f", call=_call_scorer_6f,
            dimension_keys=("breakdown", "weights"),
        ),
        AdapterSpec(
            name="scorecard", call=_call_scorecard,
            total_key="score", level_key="verdict", dimension_keys=("layers",),
        ),
        AdapterSpec(
            name="judge_panel", call=_call_judge_panel,
            total_key="score", level_key="consensus",
            dimension_keys=("bullish_pct", "total_votes"),
        ),
        AdapterSpec(
            name="investor_agents", call=_call_investor_agents,
            total_key="confidence", level_key="signal",
            dimension_keys=("bullish_pct", "bearish_pct", "consensus"),
        ),
        AdapterSpec(
            name="panel_decision", call=_call_panel_decision,
            total_key="confidence", level_key="signal",
            dimension_keys=("bullish_pct", "bearish_pct", "consensus"),
        ),
        AdapterSpec(
            name="opportunity_scorer", call=_call_opportunity_scorer,
            dimension_keys=("breakdown", "weights"),
        ),
        AdapterSpec(
            name="strategies_quick", call=_call_strategies_quick,
            dimension_keys=("breakdown", "weights"),
        ),
        AdapterSpec(
            name="strategies_full", call=_call_strategies_full,
            dimension_keys=("breakdown", "weights"),
        ),
    )


def build_kernel() -> ScoringKernel:
    """构造并注册全部适配器，返回开箱即用的内核。"""
    kernel = ScoringKernel()
    for spec in default_specs():
        kernel.register(spec)
    return kernel
