"""保守动态调权

FACTOR_WEIGHT_BOUNDS: weight 范围 [preset × 0.20, preset × 1.50]

compute_dynamic_weights(
    preset_weights: Dict[str, float],
    ic_stats: Dict[str, Dict],   # from ic_history
    current_weights: Dict[str, float],  # 当前权重（上一期结果）
) -> {
    "weights": Dict[str, float],
    "changes": Dict[str, str],  # "up" | "down" | "emergency" | "unchanged"
    "reasons": Dict[str, str],
    "mode": "dynamic" | "fixed",
    "fallback_reason": str | None,
}

is_ic_sufficient(ic_stats: Dict) -> bool:
    有效 IC 日期 ≥ 20 天则动态调权，否则降级固定权重

maybe_restore_dynamic_weights(ic_stats: Dict) -> Dict | None:
    检查是否应从固定权重切回动态权重
    条件：当前 mode == "fixed" AND is_ic_sufficient(ic_stats) == True

_apply_emergency_adjustment(weights, factor, ic):
    IC < -0.10 → 立即降权 50%，不等待周期

常规调权规则（每周一触发）：
    IF IC_mean < 0 AND win_rate < 50% FOR 连续 5 天:
        weight = weight × 0.80          # 降20%
    IF 连续 10 天仍未恢复:
        weight = preset × 0.20         # 降至下限，不再降
    IF IC_mean > 0.02 AND IR > 0.5:
        weight = weight × 1.20          # 上浮20%，有上限

权重范围：[preset × 0.20, preset × 1.50]
"""

import json
import numpy as np
from pathlib import Path
from typing import Dict
import logging

logger = logging.getLogger(__name__)

WEIGHTS_PATH = Path("cache/ic/factor_weights.json")

# 常量
WEIGHT_MIN_RATIO = 0.20   # 最低 preset × 20%
WEIGHT_MAX_RATIO = 1.50   # 最高 preset × 150%
IC_MIN_DAYS = 20          # 动态调权最低 IC 天数
IC_EMERGENCY_THRESHOLD = -0.10  # 紧急阈值
IC_DECAY_CONSECUTIVE = 5   # 连续负 IC 天数（触发降权）
IC_ELIMINATE_CONSECUTIVE = 10  # 连续负 IC 天数（触发降至下限）
IC_UP_THRESHOLD = 0.02     # 上浮阈值
IC_IR_THRESHOLD = 0.5      # IR 阈值

def is_ic_sufficient(ic_stats: Dict) -> bool:
    """有效 IC 日期 ≥ 20 天则动态调权"""
    total_days = 0
    for factor_data in ic_stats.values():
        n = factor_data.get("n_valid_days", 0)
        total_days = max(total_days, n)
    return total_days >= IC_MIN_DAYS

def maybe_restore_dynamic_weights(ic_stats: Dict) -> Dict:
    """检查是否应从固定权重切回动态权重

    条件：当前 mode == "fixed" AND is_ic_sufficient(ic_stats) == True
    """
    weights_data = load_weights()
    if not weights_data:
        return None
    if weights_data.get("mode") != "fixed":
        return None
    if not is_ic_sufficient(ic_stats):
        return None

    # 恢复动态权重
    restored = dict(weights_data)
    restored["mode"] = "dynamic"
    restored["fallback_reason"] = None
    restored["recovery_reason"] = f"IC样本恢复，{IC_MIN_DAYS} 天有效 IC，满足动态调权条件"
    save_weights(restored)
    logger.info("[weight_adjuster] 从固定权重恢复为动态权重")
    return restored

def _apply_emergency_adjustment(
    weights: Dict[str, float],
    factor: str,
    ic_value: float
) -> Dict[str, float]:
    """IC < -0.10 立即降权 50%"""
    new_weights = dict(weights)
    preset = new_weights.get(f"{factor}_preset", new_weights.get(factor, 0.05))
    new_weights[factor] = max(preset * WEIGHT_MIN_RATIO, new_weights[factor] * 0.50)
    new_weights.setdefault("reasons", {})[factor] = f"紧急: IC={ic_value:.4f} < -0.10，降权50%"
    new_weights.setdefault("changes", {})[factor] = "emergency"
    return new_weights

def compute_dynamic_weights(
    preset_weights: Dict[str, float],
    ic_stats: Dict[str, Dict],
    current_weights: Dict[str, float] = None,
) -> Dict:
    """计算动态因子权重"""
    if current_weights is None:
        current_weights = dict(preset_weights)

    new_weights = {}
    changes = {}
    reasons = {}

    for factor, preset in preset_weights.items():
        upper = preset * WEIGHT_MAX_RATIO
        lower = preset * WEIGHT_MIN_RATIO
        current = current_weights.get(factor, preset)

        ic_data = ic_stats.get(factor, {})
        ic_mean = ic_data.get("ic_mean", 0)
        win_rate = ic_data.get("win_rate", 0.5)
        ir = ic_data.get("ir", 0)
        n_days = ic_data.get("n_valid_days", 0)

        new_weight = current

        # 连续 10 天仍未恢复 → 降至下限，不再降
        if ic_mean < 0 and win_rate < 0.50 and n_days >= IC_ELIMINATE_CONSECUTIVE:
            new_weight = lower
            changes[factor] = "down"
            reasons[factor] = (f"IC连续负 {n_days} 天: mean={ic_mean:.4f}, "
                               f"win_rate={win_rate:.1%}, 降至下限 preset×20%")
        # 连续 5 天 IC<0 且胜率<50% → 降20%
        elif ic_mean < 0 and win_rate < 0.50 and n_days >= IC_DECAY_CONSECUTIVE:
            new_weight = current * 0.80  # 降20%
            changes[factor] = "down"
            reasons[factor] = (f"IC连续负: mean={ic_mean:.4f}, "
                               f"win_rate={win_rate:.1%}, 降权20%")
        # IC 表现良好 → 上浮20%，有上限
        elif ic_mean > IC_UP_THRESHOLD and ir > IC_IR_THRESHOLD:
            new_weight = min(current * 1.20, upper)
            changes[factor] = "up"
            reasons[factor] = f"IC表现良好: mean={ic_mean:.4f}, IR={ir:.2f}，升权20%"
        else:
            changes[factor] = "unchanged"
            reasons[factor] = f"IC正常: mean={ic_mean:.4f}，权重不变"

        # 限制范围
        new_weights[factor] = max(lower, min(upper, new_weight))

    result = {
        "weights": new_weights,
        "changes": changes,
        "reasons": reasons,
        "mode": "dynamic",
        "fallback_reason": None,
    }
    save_weights(result)
    return result

def load_weights() -> Dict:
    """读取当前动态权重"""
    if not WEIGHTS_PATH.exists():
        return None
    with open(WEIGHTS_PATH, encoding="utf-8") as f:
        return json.load(f)

def save_weights(weights_result: Dict) -> None:
    """保存权重结果"""
    WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WEIGHTS_PATH, "w", encoding="utf-8") as f:
        json.dump(weights_result, f, ensure_ascii=False, indent=2)