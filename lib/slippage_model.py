"""滑点模型 —— 基于时段的动态滑点率"""

from datetime import datetime
from typing import Optional


# A 股交易时段滑点系数（基于历史统计）
# 开盘前30分钟和收盘前30分钟滑点较高，午盘较低
TIME_SLIPLAGE_MULTIPLIERS = {
    # (start_hour, start_min, end_hour, end_min): multiplier
    (9, 30, 10, 0): 1.8,    # 开盘前30分钟：高波动
    (10, 0, 11, 30): 1.0,   # 上午正常
    (11, 30, 13, 0): 0.6,   # 午盘：低流动性但低波动
    (13, 0, 14, 30): 1.0,   # 下午正常
    (14, 30, 15, 0): 1.5,   # 尾盘：高波动
}


def get_slippage_multiplier(time: Optional[datetime] = None) -> float:
    """获取当前时段的滑点倍数

    Args:
        time: 指定时间，默认使用当前时间

    Returns:
        滑点倍数（1.0 = 基准）
    """
    if time is None:
        time = datetime.now()

    hour, minute = time.hour, time.minute

    for (sh, sm, eh, em), multiplier in TIME_SLIPLAGE_MULTIPLIERS.items():
        if (hour > sh or (hour == sh and minute >= sm)) and \
           (hour < eh or (hour == eh and minute < em)):
            return multiplier

    return 1.0  # 非交易时段返回基准


def compute_dynamic_slippage(base_slippage: float, time: Optional[datetime] = None,
                              volatility: float = 0.0) -> float:
    """计算动态滑点率

    Args:
        base_slippage: 基础滑点率（如 0.002 = 0.2%）
        time: 指定时间
        volatility: 当前波动率（可选，高波动时增加滑点）

    Returns:
        调整后的滑点率
    """
    multiplier = get_slippage_multiplier(time)

    # 波动率调整：波动率每增加1%，滑点增加10%
    vol_adjustment = 1.0 + volatility * 10

    return base_slippage * multiplier * vol_adjustment
