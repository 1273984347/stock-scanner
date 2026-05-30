"""券商 Level-2 数据接口 —— 十档行情 + 逐笔成交 + 委托队列

预留接口，待接入实际数据源（如 pytdx Level-2、通达信付费版、第三方 API）。

使用方式：
    from lib.data_level2 import Level2DataProvider
    provider = Level2DataProvider()
    if provider.is_available():
        quotes = provider.get_depth("600519")
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DepthQuote:
    """十档行情"""
    code: str
    # 买盘（5-10 档）
    bid_prices: List[float]
    bid_volumes: List[int]
    # 卖盘（5-10 档）
    ask_prices: List[float]
    ask_volumes: List[int]
    # 逐笔成交
    last_price: float = 0
    last_volume: int = 0
    last_amount: float = 0
    # 委托队列
    bid_queue: List[int] = None  # 买一委托明细
    ask_queue: List[int] = None  # 卖一委托明细


@dataclass
class TickData:
    """逐笔成交"""
    time: str
    price: float
    volume: int
    amount: float
    direction: str  # "B" / "S" / "N"


class Level2DataProvider:
    """Level-2 数据提供者

    支持多种数据源：
    - pytdx Level-2（需通达信 Level-2 权限）
    - 第三方 API（如聚宽、Tushare Pro）
    - 本地缓存
    """

    def __init__(self, source: str = "auto"):
        self.source = source
        self._provider = None
        self._init_provider()

    def _init_provider(self):
        """初始化数据源"""
        # 尝试 pytdx Level-2
        try:
            from pytdx.hq import TdxHq_API
            self._provider = "pytdx"
            return
        except ImportError:
            pass

        # 尝试其他数据源
        self._provider = None

    def is_available(self) -> bool:
        """检查 Level-2 数据是否可用"""
        return self._provider is not None

    def get_depth(self, code: str) -> Optional[DepthQuote]:
        """获取十档行情

        Args:
            code: 股票代码，如 "600519"

        Returns:
            DepthQuote or None
        """
        if not self.is_available():
            return None

        try:
            if self._provider == "pytdx":
                return self._get_depth_pytdx(code)
        except Exception as e:
            logger.debug(f"Level-2 获取失败: {e}")

        return None

    def get_ticks(self, code: str, count: int = 100) -> List[TickData]:
        """获取逐笔成交

        Args:
            code: 股票代码
            count: 获取条数

        Returns:
            list of TickData
        """
        if not self.is_available():
            return []

        try:
            if self._provider == "pytdx":
                return self._get_ticks_pytdx(code, count)
        except Exception as e:
            logger.debug(f"逐笔成交获取失败: {e}")

        return []

    def get_order_queue(self, code: str) -> Optional[Dict]:
        """获取委托队列（买一/卖一明细）

        Returns:
            dict with keys: bid_queue, ask_queue
        """
        if not self.is_available():
            return None

        return None

    def _get_depth_pytdx(self, code: str) -> Optional[DepthQuote]:
        """通过 pytdx 获取十档行情"""
        # TODO: 接入 pytdx Level-2 API
        # api = TdxHq_API()
        # api.connect('127.0.0.1', 7219)
        # data = api.get_security_quotes([(0, '600519')])
        return None

    def _get_ticks_pytdx(self, code: str, count: int) -> List[TickData]:
        """通过 pytdx 获取逐笔成交"""
        # TODO: 接入 pytdx Level-2 API
        return []
