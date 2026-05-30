"""北向资金实时监控 —— 分钟级数据接口

预留接口，待接入实时数据源。

当前实现：基于 TDX 本地 HSJ 文件的日线级北向数据（已在 factors.py）。
增强方向：
- 接入 akshare 北向资金实时接口
- 接入东方财富北向资金实时数据
- 分钟级数据缓存

使用方式：
    from lib.data_northbound_rt import NorthboundMonitor
    monitor = NorthboundMonitor()
    flow = monitor.get_realtime_flow()
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class NorthboundFlow:
    """北向资金流向"""
    timestamp: str
    sh_connect_net: float  # 沪股通净买入（亿元）
    sz_connect_net: float  # 深股通净买入（亿元）
    total_net: float       # 合计净买入（亿元）
    sh_connect_buy: float = 0
    sh_connect_sell: float = 0
    sz_connect_buy: float = 0
    sz_connect_sell: float = 0


class NorthboundMonitor:
    """北向资金实时监控"""

    def __init__(self, source: str = "auto"):
        self.source = source
        self._cache: Optional[NorthboundFlow] = None
        self._cache_ts: float = 0

    def get_realtime_flow(self) -> Optional[NorthboundFlow]:
        """获取实时北向资金流向

        Returns:
            NorthboundFlow or None
        """
        import time
        # 缓存 1 分钟
        if self._cache and time.time() - self._cache_ts < 60:
            return self._cache

        # 尝试 akshare
        flow = self._fetch_akshare()
        if flow:
            self._cache = flow
            self._cache_ts = time.time()
            return flow

        return None

    def get_daily_flow(self, days: int = 20) -> List[Dict]:
        """获取日线级北向资金流向

        Args:
            days: 获取天数

        Returns:
            list of {date, sh_net, sz_net, total_net}
        """
        try:
            import akshare as ak
            # 北向资金历史数据
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
            if df is not None and not df.empty:
                result = []
                for _, row in df.tail(days).iterrows():
                    result.append({
                        "date": str(row.iloc[0]),
                        "total_net": float(row.iloc[1]) if len(row) > 1 else 0,
                    })
                return result
        except Exception as e:
            logger.debug(f"北向资金历史数据获取失败: {e}")

        return []

    def get_top_buy_stocks(self, top_n: int = 20) -> List[Dict]:
        """获取北向资金净买入排名前 N 的股票

        Returns:
            list of {code, name, net_buy, holding}
        """
        try:
            import akshare as ak
            df = ak.stock_hsgt_hold_stock_em(market="北向")
            if df is not None and not df.empty:
                result = []
                for _, row in df.head(top_n).iterrows():
                    result.append({
                        "code": str(row.iloc[0]) if len(row) > 0 else "",
                        "name": str(row.iloc[1]) if len(row) > 1 else "",
                        "net_buy": float(row.iloc[2]) if len(row) > 2 else 0,
                        "holding": float(row.iloc[3]) if len(row) > 3 else 0,
                    })
                return result
        except Exception as e:
            logger.debug(f"北向持股数据获取失败: {e}")

        return []

    def _fetch_akshare(self) -> Optional[NorthboundFlow]:
        """通过 akshare 获取实时北向数据"""
        try:
            import akshare as ak
            # 实时北向资金
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                return NorthboundFlow(
                    timestamp=str(latest.iloc[0]),
                    sh_connect_net=float(latest.iloc[1]) if len(latest) > 1 else 0,
                    sz_connect_net=0,
                    total_net=float(latest.iloc[1]) if len(latest) > 1 else 0,
                )
        except Exception as e:
            logger.debug(f"北向实时数据获取失败: {e}")

        return None
