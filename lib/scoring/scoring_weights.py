"""机会评分权重状态 —— 从 lib/opportunity_scorer.py 抽出的全局可变状态（RF-01 Phase 1）

背景：`set_opp_score_weights()` 设置后，`compute_opportunity_score()` 必须立即读取到
新权重 ⇒ 这是一份**全局可变状态**。原先埋在 opportunity_scorer.py 里，
而 opportunity_scorer 只是评分器的一个实现 ⇒ 权重状态与具体评分器耦合过紧。

本模块持有**唯一**状态；`lib/opportunity_scorer.py` 通过
`from lib.scoring.scoring_weights import get_opp_score_weights, ...` 引用同一份状态
（函数级引用 ⇒ 运行时始终一致）。

兼容：`from lib.scoring.opportunity_scorer import set_opp_score_weights` 等旧 import 仍可用
（opportunity_scorer 侧 re-export）。
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# 默认权重（与历史硬编码一致）：regime 0.30 / alpha 0.25 / sector 0.20 / trend 0.15 / volume 0.10
_DEFAULT_OPP_SCORE_WEIGHTS: Dict[str, float] = {
    "regime": 0.30,
    "alpha": 0.25,
    "sector": 0.20,
    "trend": 0.15,
    "volume": 0.10,
}

# 模块级缓存：set_opp_score_weights() 设置后立即生效；
# 设为 None 表示「下次计算时从 config 拉取最新值」
_active_weights: Optional[Dict[str, float]] = None


def get_opp_score_weights(source_config=None) -> Dict[str, float]:
    """获取当前生效的机会评分权重。

    优先级：
    1. 模块级缓存 `_active_weights`（被 set_opp_score_weights 设置过）
    2. source_config.opp_score_weights（调用方传入的 FactorConfig）
    3. config.FactorConfig().opp_score_weights（默认 FactorConfig 的默认值）
    4. _DEFAULT_OPP_SCORE_WEIGHTS（兜底）
    """
    if _active_weights is not None:
        return dict(_active_weights)

    if source_config is not None and hasattr(source_config, 'opp_score_weights'):
        return dict(source_config.opp_score_weights)

    # 尝试从 config 模块拉取默认值（避免循环导入：失败时兜底）
    try:
        from config import FactorConfig
        return dict(FactorConfig().opp_score_weights)
    except Exception as e:
        logger.debug(f"从 config 拉取 opp_score_weights 失败，使用兜底: {e}")
        return dict(_DEFAULT_OPP_SCORE_WEIGHTS)


def set_opp_score_weights(weights: Dict[str, float]) -> None:
    """设置机会评分权重（覆盖默认值）。

    Args:
        weights: 5 项权重 dict（regime/alpha/sector/trend/volume）
    """
    global _active_weights
    if not isinstance(weights, dict):
        raise TypeError(f"weights 必须是 dict，实际 {type(weights).__name__}")
    _active_weights = dict(weights)


def reset_opp_score_weights() -> None:
    """重置为从 config 读取（清除 set_opp_score_weights 的覆盖）"""
    global _active_weights
    _active_weights = None


# =====================================================================
# RF-01 Phase 4：评分权重的唯一定义点
# ---------------------------------------------------------------------
# 原先 WEIGHTS_4D / SIX_FACTOR_WEIGHTS 硬编码在 lib/scoring/scorer.py 里，
# 与 opportunity_scorer 的权重状态（本模块上半部分）分处两地。
# 现把两个常量的**定义**搬到这里，scorer.py 改为 re-export（同签名、同值、
# 同一对象引用）⇒ 数值零变更，但「权重定义」此后再无第二个落点。
# ⚠️ 这两个 dict 的值是评分口径，改动必须走 ADR + 对拍基线。
# =====================================================================

WEIGHTS_4D: Dict[str, float] = {
    "fundamental": 0.35,
    "capital_flow": 0.35,
    "news": 0.20,
    "technical": 0.10,
}

SIX_FACTOR_WEIGHTS: Dict[str, float] = {
    "trend": 0.20,       # 趋势分
    "momentum": 0.15,    # 动量分
    "volume": 0.20,      # 量能分
    "sector": 0.20,      # 板块分
    "volatility": 0.10,  # 波动质量分
    "structure": 0.15,   # 结构分
}

# 外部配置源：本身是合法的配置载体（不是模块内硬编码），故**不搬迁**，
# 但必须在此登记，使「权重到底散落在哪」可被一个函数回答，而不是靠人记。
EXTERNAL_WEIGHT_SOURCES = {
    "scorecard.layer_weights": "config.LAYER_WEIGHTS（四层打分卡 0/0.5/1 加权）",
    "judge_panel.groups": "lib/scoring/judge_panel.py::_DEFAULT_JUDGE_GROUPS（代码默认；config/judges.json 已于 RF-02 D3 删除，P0#13 起 6 组禁用、AI量化=1.00）",
    "opportunity_scorer": "config.FactorConfig.opp_score_weights（可由本模块 set/get/reset 覆盖）",
    "factor_weights": "config.FactorConfig.factor_weights（因子层，非评分层）",
}


def list_weight_sources() -> Dict[str, str]:
    """列出全部评分权重来源 —— 供审计与 `tests/features/` 特征测试断言。"""
    return {
        "scorer.weights_4d": "lib.scoring.scoring_weights.WEIGHTS_4D",
        "scorer.six_factor_weights": "lib.scoring.scoring_weights.SIX_FACTOR_WEIGHTS",
        **EXTERNAL_WEIGHT_SOURCES,
    }
