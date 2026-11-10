"""评分消费者网关 —— RF-01 Phase 4 阶段 B 的唯一灰度判定点。

**为什么需要这一层**：阶段 B 要「逐消费者切换」，若每个调用点各写一次
`if FEATURE_UNIFIED_SCORING: ... else: ...`，判定逻辑就会散落 N 份，
既无法保证口径一致，也无法回答「到底哪几个消费者切了」。
本模块把判定收敛到 `score_4d(consumer, ...)` 一处：

- flag off（**默认**）：直调旧实现 `comprehensive_score`，行为与阶段 A 之前完全一致；
- flag on：经 `ScoringKernel` 取结果（内核纯转发，数值零变更）。

设计约束：
1. **无模块级可变状态**（Phase 1 已清理过跨端点共享状态）—— 每次构造一个
   轻量的 kernel（9 个 dataclass spec，不触发旧实现 import），线程安全、无缓存污染。
2. **不复制旧逻辑** —— off 分支就是原函数调用，不重写任何评分计算。
3. **consumer 是显式参数** —— 开关按消费者粒度灰度，调用方必须声明自己是谁。

新消费者接入请调本模块函数，**不要**自己读环境变量。
"""

from __future__ import annotations

from typing import Any, Dict

from config import unified_scoring_enabled
from lib.scoring.scoring_contract import PRIMARY_4D_SOURCE, ScoringResult


def score_4d(
    consumer: str,
    fundamental: float,
    capital_flow: float,
    news: float,
    technical: float,
) -> Dict[str, Any]:
    """四维综合评分的接入点（唯一真源 `scorer_4d`）。

    Args:
        consumer: 消费者标识，用于 `FEATURE_UNIFIED_SCORING` 按粒度灰度。当前已接入：
            - `"strategies"`：`lib/strategy/strategies.py::_compute_base_scores`（实时评分路径）
            - `"pipeline"`：`lib/strategy/steps.py::score_without_realtime`（无实时行情路径）
            - `"cli"`：`run.py::run_score_mode`（CLI `--score` 模式）
        fundamental / capital_flow / news / technical: 四个维度分（0–10）。

    Returns:
        与 `scorer.comprehensive_score` **完全相同**的 dict（score/rating/breakdown/weights）。
    """
    if not unified_scoring_enabled(consumer):
        from lib.scoring.scorer import comprehensive_score
        return comprehensive_score(fundamental, capital_flow, news, technical)

    from lib.scoring.scoring_adapters import build_kernel
    return build_kernel().score(PRIMARY_4D_SOURCE, {
        "fundamental": fundamental,
        "capital_flow": capital_flow,
        "news": news,
        "technical": technical,
    }).raw


def score_4d_result(
    consumer: str,
    fundamental: float,
    capital_flow: float,
    news: float,
    technical: float,
) -> ScoringResult:
    """返回统一契约形状（供需要 `ScoringResult` 的新消费者使用）。

    注意：本函数**始终**走内核，不受 flag 影响；仅用于新代码，不参与灰度回滚。
    """
    from lib.scoring.scoring_adapters import build_kernel
    return build_kernel().score(PRIMARY_4D_SOURCE, {
        "fundamental": fundamental,
        "capital_flow": capital_flow,
        "news": news,
        "technical": technical,
    })


#: 已接入灰度的消费者名单 —— 供取证脚本与文档核对，避免「以为切了其实没切」。
GRAY_CONSUMERS = ("strategies", "pipeline", "cli")

#: 纯透传消费者：自身无评分调用点，**不需要也不应该**加灰度分支。
#: 它们通过下游已接入的消费者自动受益，只需纳入取证与契约冻结验证。
#: 见 `docs/adr/0003-*.md` D6 / 坑表 P-58。
PASSTHROUGH_CONSUMERS = ("api_score",)
