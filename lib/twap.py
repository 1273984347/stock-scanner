"""TWAP 执行策略 —— 时间加权均价执行

将大单拆分为 N 笔等量子单，按时间均匀执行，
减少市场冲击成本。
"""

import logging
from typing import List, Dict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ChildOrder:
    """子单"""
    index: int
    total_shares: int
    target_time_offset: int  # 相对起始时间的偏移（秒）
    status: str = "pending"  # pending / filled / failed
    fill_price: float = 0
    fill_shares: int = 0


class TWAPExecutor:
    """TWAP 执行器

    将一笔大单拆分为 N 笔等量子单，按时间均匀分布。

    使用方式：
        executor = TWAPExecutor(num_slices=5, interval_seconds=60)
        child_orders = executor.split_order(code="600519", side="buy", total_shares=10000)
    """

    def __init__(self, num_slices: int = 5, interval_seconds: int = 60):
        """
        Args:
            num_slices: 拆分子单数量
            interval_seconds: 子单间隔（秒）
        """
        self.num_slices = num_slices
        self.interval_seconds = interval_seconds

    def split_order(self, code: str, side: str, total_shares: int) -> List[ChildOrder]:
        """拆分订单

        Args:
            code: 股票代码
            side: buy / sell
            total_shares: 总股数

        Returns:
            子单列表
        """
        # A 股整手取整（100 股为一手）
        total_shares = (total_shares // 100) * 100
        if total_shares <= 0:
            return []

        slice_shares = total_shares // self.num_slices
        remainder = total_shares % self.num_slices

        child_orders = []
        for i in range(self.num_slices):
            # 余数分配到前几笔
            shares = slice_shares + (100 if i < remainder // 100 else 0)
            if shares <= 0:
                continue

            child_orders.append(ChildOrder(
                index=i,
                total_shares=shares,
                target_time_offset=i * self.interval_seconds,
            ))

        logger.info(
            f"TWAP 拆分: {code} {side} {total_shares}股 → "
            f"{len(child_orders)} 笔，间隔 {self.interval_seconds}s"
        )
        return child_orders

    def estimate_completion_time(self) -> int:
        """估算执行完成时间（秒）"""
        return (self.num_slices - 1) * self.interval_seconds

    def get_schedule(self, code: str, side: str, total_shares: int) -> List[Dict]:
        """获取执行计划（用于展示）"""
        children = self.split_order(code, side, total_shares)
        return [
            {
                "slice": c.index + 1,
                "shares": c.total_shares,
                "offset_seconds": c.target_time_offset,
                "status": c.status,
            }
            for c in children
        ]
