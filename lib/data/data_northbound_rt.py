"""北向资金实时监控 —— akshare 日线级北向数据接口

数据来源：akshare (东方财富)
- stock_hsgt_north_net_flow_in_em: 北向资金净流入
- stock_hsgt_hold_stock_em: 北向持股明细

使用方式：
    from lib.data.data_northbound_rt import NorthboundMonitor
    monitor = NorthboundMonitor()
    flow = monitor.get_realtime_flow()
    daily = monitor.get_daily_flow(days=20)
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

    def get_realtime_flow(self, as_of_date: Optional[str] = None) -> Optional[NorthboundFlow]:
        """获取实时北向资金流向

        P0 #5 修复 (2026-06-08, lib-incremental-audit §2.1):
        - 旧实现 ak.stock_hsgt_north_net_flow_in_em 拉 T 日实时
        - backtest 走此路径 = 前视偏差
        - 修复: as_of_date 截断 (df.iloc[-1] → df[date <= as_of_date].iloc[-1])
        - 缓存 key 不变 (1 分钟 TTL, 实时模式忽略 as_of_date)

        Args:
            as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。None = 实时 (行为不变)

        Returns:
            NorthboundFlow or None
        """
        import time
        # 缓存 1 分钟（仅实时模式有效, 回测模式 as_of_date 不同应不走缓存）
        if as_of_date is None and self._cache and time.time() - self._cache_ts < 60:
            return self._cache

        # 尝试 akshare
        flow = self._fetch_akshare(as_of_date=as_of_date)
        if flow and as_of_date is None:
            self._cache = flow
            self._cache_ts = time.time()
        return flow

    def get_daily_flow(self, days: int = 20, as_of_date: Optional[str] = None) -> List[Dict]:
        """获取日线级北向资金流向

        P0 #5 修复: 透传 as_of_date + 截断

        Args:
            days: 获取天数
            as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。None = 实时 (行为不变)

        Returns:
            list of {date, total_net}
        """
        try:
            import akshare as ak
            import pandas as pd

            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df is None or df.empty:
                return []

            # P0 #5 修复: as_of_date 截断
            if as_of_date is not None:
                cutoff = as_of_date.replace("-", "")
                if "日期" in df.columns:
                    df = df[df["日期"].astype(str).str.replace("-", "") <= cutoff]
                elif "date" in df.columns:
                    df = df[df["date"].astype(str).str.replace("-", "") <= cutoff]
                # 截断后取最后 days 行
                df = df.tail(days)
            else:
                df = df.tail(days)

            cols = df.columns.tolist()
            result = []
            for _, row in df.iterrows():
                date_val = str(row.get("日期", row.get("date", row.iloc[0])))
                net_flow = 0.0
                for key in ("净流入", "value"):
                    if key in cols:
                        net_flow = float(row[key])
                        break
                else:
                    if len(cols) > 1:
                        net_flow = float(row.iloc[1])

                result.append({
                    "date": date_val,
                    "total_net": net_flow,
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
            if df is None or df.empty:
                return []

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

    def _fetch_akshare(self, as_of_date: Optional[str] = None) -> Optional[NorthboundFlow]:
        """从 akshare 获取北向资金数据（最新一条）

        P0 #5 修复 (2026-06-08): as_of_date 截断

        akshare stock_hsgt_north_net_flow_in_em 返回列名可能为：
        - 日期 / date
        - 净流入 / value
        """
        try:
            import akshare as ak

            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df is None or df.empty:
                return None

            # P0 #5 修复: as_of_date 截断 (取 <= as_of_date 的最后一行)
            if as_of_date is not None:
                cutoff = as_of_date.replace("-", "")
                if "日期" in df.columns:
                    df = df[df["日期"].astype(str).str.replace("-", "") <= cutoff]
                elif "date" in df.columns:
                    df = df[df["date"].astype(str).str.replace("-", "") <= cutoff]
                if df.empty:
                    return None

            latest = df.iloc[-1]
            cols = df.columns.tolist()

            # 日期列：优先 "日期"，回退第一列
            date_val = str(latest.get("日期", latest.get("date", latest.iloc[0])))

            # 净流入列：优先 "净流入"，回退 "value"，再回退第二列
            net_flow = 0.0
            for key in ("净流入", "value"):
                if key in cols:
                    net_flow = float(latest[key])
                    break
            else:
                if len(cols) > 1:
                    net_flow = float(latest.iloc[1])

            return NorthboundFlow(
                timestamp=date_val,
                sh_connect_net=0,  # akshare 综合接口不拆分沪/深
                sz_connect_net=0,
                total_net=net_flow,
            )
        except Exception as e:
            logger.debug(f"北向实时数据获取失败: {e}")

        return None
