"""
Tradability Filter —— 可交易性过滤，剔除不可交易股票

Filters:
1. Limit up/down stocks (涨跌幅 >= 9.8%)
2. Low volume stocks (日均成交额 < min_daily_volume)
3. High impact cost (平方根模型估算 > max_impact_ratio)

Impact cost formula: eta * sigma * sqrt(weight * portfolio_value / ADV)
where eta=0.3, sigma=0.30, portfolio_value=1_000_000
"""
import numpy as np
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

# Impact cost 模型参数
ETA = 0.3
SIGMA = 0.30
PORTFOLIO_VALUE = 1_000_000


class TradabilityFilter:
    """可交易性过滤器"""

    def __init__(self, config: Any):
        self.min_daily_volume = getattr(config, "min_daily_volume", 5e6)
        self.max_impact_ratio = getattr(config, "max_impact_ratio", 0.05)

    def filter(self, buy_list: List[Dict], market_data: Dict = None) -> List[Dict]:
        """过滤不可交易股票，返回含 expected_cost 的列表

        Parameters
        ----------
        buy_list : list of dict
            买入清单，每项含 code, name, score, weight, ...
        market_data : dict, optional
            市场数据 {code: {change_pct, avg_amount, ...}}

        Returns
        -------
        list of dict
            过滤后列表，每项附加 expected_cost 字段
        """
        if not buy_list:
            return []

        market_data = market_data or {}
        filtered = []

        for item in buy_list:
            code = item.get("code", "")
            reason = self._check(item, market_data)
            if reason:
                logger.debug("剔除 %s: %s", code, reason)
                continue

            avg_volume = self._get_avg_volume(code, market_data)
            impact_cost = self._calc_impact_cost(
                code, item.get("weight", 0), avg_volume
            )
            item["expected_cost"] = round(impact_cost, 4)
            filtered.append(item)

        logger.info(
            "TradabilityFilter: %d/%d 通过过滤",
            len(filtered), len(buy_list)
        )
        return filtered

    def _check(self, item: Dict, market_data: Dict) -> Optional[str]:
        """检查单个条目是否应被剔除，返回原因或 None"""
        code = item.get("code", "")

        # 1. 涨跌停检查
        if self._is_limit_up_or_down(code, market_data):
            return "涨跌停"

        # 2. 低成交量检查
        avg_volume = self._get_avg_volume(code, market_data)
        if avg_volume < self.min_daily_volume:
            return f"低成交量({avg_volume:.0f}<{self.min_daily_volume:.0f})"

        # 3. 冲击成本检查
        impact = self._calc_impact_cost(code, item.get("weight", 0), avg_volume)
        if impact > self.max_impact_ratio:
            return f"冲击成本过高({impact:.4f}>{self.max_impact_ratio})"

        return None

    def _is_limit_up_or_down(self, code: str, market_data: Dict) -> bool:
        """判断是否涨跌停（涨跌幅 >= 9.8%）"""
        md = market_data.get(code, {})
        change_pct = md.get("change_pct", 0) or 0
        # 处理 dict 格式 {"change_pct": ...} 或直接数值
        if isinstance(change_pct, dict):
            change_pct = change_pct.get("value", 0)
        return abs(change_pct) >= 9.8

    def _get_avg_volume(self, code: str, market_data: Dict) -> float:
        """获取日均成交额"""
        md = market_data.get(code, {})
        avg_amount = md.get("avg_amount", 0) or 0
        if isinstance(avg_amount, dict):
            avg_amount = avg_amount.get("value", 0)
        return float(avg_amount)

    def _calc_impact_cost(self, code: str, weight: float, avg_volume: float) -> float:
        """平方根模型估算冲击成本

        impact = eta * sigma * sqrt(weight * portfolio_value / ADV)

        Parameters
        ----------
        code : str
            股票代码（未使用，保留接口兼容性）
        weight : float
            目标权重 (0~1)
        avg_volume : float
            日均成交额

        Returns
        -------
        float
            估算冲击成本（0~1 之间）
        """
        if avg_volume <= 0:
            return 1.0  # 无成交量 => 不可交易

        trade_value = weight * PORTFOLIO_VALUE
        participation = trade_value / avg_volume
        return float(ETA * SIGMA * np.sqrt(participation))
