"""成交率预测 —— 基于历史数据预测限价单成交概率

输入：限价与当前价的距离、波动率、时段
输出：成交概率 (0-1)

模型：基于 A 股统计的简化逻辑模型
- 距离越近，成交率越高
- 波动率越高，成交率越高（价格更可能触及限价）
- 开盘/尾盘成交率高于午盘
"""

import logging
import math
from datetime import datetime
from typing import Optional

from lib.slippage_model import get_slippage_multiplier

logger = logging.getLogger(__name__)


def predict_fill_rate(
    limit_price: float,
    current_price: float,
    volatility: float = 0.02,
    side: str = "buy",
    time: Optional[datetime] = None,
) -> float:
    """预测限价单成交概率

    Args:
        limit_price: 限价
        current_price: 当前价
        volatility: 日波动率（默认 2%）
        side: buy / sell
        time: 下单时间（默认当前时间）

    Returns:
        成交概率 (0-1)
    """
    if current_price <= 0:
        return 0.0

    # 计算价格距离（百分比）
    if side == "buy":
        # 买入：限价高于当前价更容易成交
        distance_pct = (limit_price - current_price) / current_price
    else:
        # 卖出：限价低于当前价更容易成交
        distance_pct = (current_price - limit_price) / current_price

    # 基础成交率：距离为 0 时约 50%，每偏离 1% 下降约 20%
    # 使用 sigmoid-like 函数
    base_rate = 1.0 / (1.0 + math.exp(distance_pct / max(volatility, 0.001) * 3))

    # 波动率调整：高波动率提高成交率
    vol_factor = min(1.5, 1.0 + volatility * 10)

    # 时段调整
    time_factor = 1.0 / max(get_slippage_multiplier(time), 0.5)  # 反向：滑点高时成交率低

    fill_rate = base_rate * vol_factor * time_factor
    return max(0.0, min(1.0, fill_rate))


def estimate_fill_time(
    limit_price: float,
    current_price: float,
    volatility: float = 0.02,
    side: str = "buy",
) -> int:
    """估算成交所需时间（分钟）

    Args:
        limit_price: 限价
        current_price: 当前价
        volatility: 日波动率
        side: buy / sell

    Returns:
        预估成交时间（分钟），-1 表示难以成交
    """
    fill_rate = predict_fill_rate(limit_price, current_price, volatility, side)

    if fill_rate > 0.8:
        return 1  # 几乎立即成交
    elif fill_rate > 0.5:
        return 5  # 5 分钟内
    elif fill_rate > 0.2:
        return 30  # 30 分钟
    elif fill_rate > 0.05:
        return 120  # 2 小时
    else:
        return -1  # 难以成交
