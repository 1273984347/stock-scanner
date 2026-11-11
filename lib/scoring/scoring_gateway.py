"""评分消费者网关 —— ScoringKernel 唯一路径（RF-01 Phase 7 P7-3 收口）。

历史（ADR-0001 / 0003 / 0014）：
- Phase 4 阶段 A：引入 `ScoringKernel` + 9 入口纯转发适配器，旧实现仍是唯一算法来源；
- Phase 4 阶段 B：`FEATURE_UNIFIED_SCORING` 按消费者灰度（GRAY=strategies/pipeline/cli），
  L6 开关对拍证明 off/on 逐字段相等；
- Phase 7 P7-3（2026-11-12）：灰度收口，**kernel 成为唯一路径**，flag 删除
  （`score_4d` 两个分支本就都返回旧实现原对象 `.raw`，收口对消费者不可见）。

设计约束（沿用）：
1. **无模块级可变状态**（Phase 1 已清理过跨端点共享状态）—— 每次构造一个
   轻量的 kernel（9 个 dataclass spec，不触发旧实现 import），线程安全、无缓存污染。
2. **不复制旧逻辑** —— kernel 经纯转发适配器调旧实现，不重写任何评分计算。
3. 数值口径由 L5 评分基线（9 入口 × 285 样本，容差 1e-9）继续锁定。

新消费者接入请调本模块函数，**不要**自己读环境变量（flag 已不存在）。
"""

from __future__ import annotations

from typing import Any, Dict

from lib.scoring.scoring_contract import PRIMARY_4D_SOURCE, ScoringResult


def score_4d(
    fundamental: float,
    capital_flow: float,
    news: float,
    technical: float,
) -> Dict[str, Any]:
    """四维综合评分的接入点（唯一真源 `scorer_4d`，经 ScoringKernel）。

    Returns:
        与 `scorer.comprehensive_score` **完全相同**的 dict（score/rating/breakdown/weights）——
        kernel 分支本就返回 `.raw`（旧实现原对象），P7-3 收口对消费者不可见。
    """
    from lib.scoring.scoring_adapters import build_kernel
    return build_kernel().score(PRIMARY_4D_SOURCE, {
        "fundamental": fundamental,
        "capital_flow": capital_flow,
        "news": news,
        "technical": technical,
    }).raw


#: 纯透传消费者：自身无评分调用点，**不需要也不应该**加网关分支。
#: 它们通过下游已接入的消费者自动受益，只需纳入取证与契约冻结验证。
#: 见 `docs/adr/0003-*.md` D6 / 坑表 P-58。
PASSTHROUGH_CONSUMERS = ("api_score",)

#: 间接消费者：缓存/聚合端点，不直接调评分函数；
#: 其评分经 `run_all_strategies -> strategies` 间接受益。
#: 端点本身实跑取证无鉴别力（缓存命中会让前后返回同一对象）。
#: 见 `docs/adr/0003-*.md` D7 / 坑表 P-59。
INDIRECT_CONSUMERS = ("api_dashboard",)

#: 域外端点：计划里列了，但实测**完全不消费评分结果**（因子权重优化域）。
#: 既不接网关，也不算透传/间接消费者；用 AST 守卫固定这个边界。
OUT_OF_SCOPE_ENDPOINTS = ("api_strategy_recommend",)

# 历史登记（P7-3 收口后不再是运行时概念，全文见 ADR-0014 / MIGRATION-GUIDE BC-10）：
# GRAY_CONSUMERS = ("strategies", "pipeline", "cli") —— 三者已全部走 kernel。
