from lib.data.data_utils import to_float

"""东方财富资金流向 API

API: https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
Method: GET
Params:
  secid: 市场代码.股票代码（1.600519=上证, 0.000001=深证）
  lmt: 返回数量（默认5）
  klt:  Kline类型（101=日K）
  fields1/fields2: 返回字段

字段映射（fields2）：
  f51=主力净流入(元), f52=主力净流入占比,
  f53=超大单净流入, f54=超大单净流入占比,
  f55=大单净流入, f56=大单净流入占比,
  f57=中单净流入, f58=小单净流入

Usage:
  from lib.data.data_eastmoney_fund import get_fund_flow
  flow = get_fund_flow("600519")  # 返回近5日主力净流入均值
"""
import requests
import logging
import threading
import time
from typing import Optional

from lib.data.host_health import (HOST_EASTMONEY_PUSH2, is_available,
                                  note_failure, note_success)

logger = logging.getLogger(__name__)

EASTMONEY_FLOW_URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"

# 全局限流（东财 push2 有连接数限制，严格限流）
_lock = threading.Lock()
_last_call_time = 0.0
_MIN_INTERVAL = 3.0  # 至少间隔3秒，避免连接被阻断


def _rate_limited():
    global _last_call_time
    with _lock:
        now = time.time()
        wait = _last_call_time + _MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.time()


def _secid(code: str) -> str:
    """股票代码转 EastMoney secid"""
    # 用 helper 替代手写市场判断（修复 bj 静默丢失）
    from lib.data.market_utils import detect_market
    market = detect_market(code)
    # eastmoney secid: 1=沪, 0=深, 北证按 0 处理（eastmoney API 限制）
    market_id = "1" if market == "sh" else "0"
    return f"{market_id}.{code}"


def _to_float(v) -> Optional[float]:
    return to_float(v, zero_is_none=True, dash_is_none=True, prefer_raw_float=True)


def get_fund_flow(code: str, days: int = 5, as_of_date: Optional[str] = None) -> dict:
    """获取资金流向数据

    P0 #3 修复 (2026-06-08, lib-incremental-audit §2.1):
    - 旧实现 days=5 是回溯窗口, 但回溯的是"当前日"的最近 5 天
    - backtest 走此路径 = 拉 T 日实时数据, 用作历史日特征 = 前视偏差
    - 修复: as_of_date 截断 klines_raw (过滤 <= as_of_date 的行)
    - 3 秒限流硬编码保留, URL/fields 不变 (东方财富 push2 API 不直接支持历史日期)

    Args:
        code: 6位股票代码
        days: 获取最近N日数据（默认5）
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。None = 实时模式 (scanner 兼容)

    Returns:
        dict with keys:
        - main_net_inflow: 主力净流入（元）
        - main_net_ratio: 主力净流入占比（小数，如 0.05=5%）
        - super_large_inflow: 超大单净流入（元）
        - large_inflow: 大单净流入（元）
        - mid_inflow: 中单净流入（元）
        - small_inflow: 小单净流入（元）
        - inflow_5d_avg: 近5日主力净流入均值（元）
    """
    # ADR-0025：host 开断期间直接短路——否则每股先付 3s 限流 + 8s 超时再降级
    # （300 股 = 数十次无意义等待）。这里是「发请求前」判据，必须早于 _rate_limited()。
    if not is_available(HOST_EASTMONEY_PUSH2):
        logger.debug(f"[eastmoney_fund] push2 开断中，跳过 {code}（走下游兜底源）")
        return {}

    _rate_limited()
    secid = _secid(code)
    params = {
        "lmt": days,
        "klt": 101,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "secid": secid,
        "iscca": 1,
    }
    try:
        resp = requests.get(EASTMONEY_FLOW_URL, params=params, timeout=8)
        resp.raise_for_status()
        json_data = resp.json()
        note_success(HOST_EASTMONEY_PUSH2)
    except Exception as e:
        logger.debug(f"[eastmoney_fund] 请求失败 {code}: {e}")
        note_failure(HOST_EASTMONEY_PUSH2, e)
        return {}

    try:
        klines_raw = json_data.get("data", {}).get("klines", [])
        if not klines_raw:
            return {}

        # P0 #3 修复: as_of_date 截断
        if as_of_date is not None:
            cutoff = as_of_date.replace("-", "")  # YYYYMMDD 格式
            klines_raw = [line for line in klines_raw if line.split(",")[0] <= cutoff]
            if not klines_raw:
                return {}

        result = {
            "main_net_inflow": 0.0,
            "main_net_ratio": 0.0,
            "super_large_inflow": 0.0,
            "large_inflow": 0.0,
            "mid_inflow": 0.0,
            "small_inflow": 0.0,
            "inflow_5d_avg": 0.0,
        }

        main_net_sum = 0.0
        for line in klines_raw:
            parts = line.split(",")
            if len(parts) < 8:
                continue
            # parts[0]=date, [1]=主力净流入, [2]=超大单, [3]=大单, [4]=中单, [5]=小单
            main_net = _to_float(parts[1]) or 0.0
            super_large = _to_float(parts[2]) or 0.0
            large = _to_float(parts[3]) or 0.0
            mid = _to_float(parts[4]) or 0.0
            small = _to_float(parts[5]) or 0.0

            result["main_net_inflow"] += main_net
            result["super_large_inflow"] += super_large
            result["large_inflow"] += large
            result["mid_inflow"] += mid
            result["small_inflow"] += small
            main_net_sum += main_net

        result["inflow_5d_avg"] = main_net_sum / len(klines_raw) if klines_raw else 0.0
        # 取最新一日的净流入占比
        latest = klines_raw[-1].split(",")
        if len(latest) >= 6:
            total_net = sum(_to_float(latest[i]) or 0.0 for i in range(1, 6))
            if total_net != 0:
                result["main_net_ratio"] = (_to_float(latest[1]) or 0.0) / abs(total_net) if total_net > 0 else 0.0

        return result
    except Exception as e:
        logger.debug(f"[eastmoney_fund] 解析失败 {code}: {e}")
        return {}


def get_fund_flow_simple(code: str, as_of_date: Optional[str] = None) -> dict:
    """简化版资金流向（兼容现有 score_capital_flow 接口）

    P0 #3 修复: 透传 as_of_date
    """
    flow = get_fund_flow(code, days=5, as_of_date=as_of_date)
    if not flow:
        return {"main_net_ratio": 0, "net_inflow_5d": 0}
    return {
        "main_net_ratio": flow.get("main_net_ratio", 0),
        "net_inflow_5d": flow.get("main_net_inflow", 0),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行"), ("000001", "平安银行")]:
        flow = get_fund_flow(code, days=5)
        if flow:
            print(f"\n{code} {name}:")
            print(f"  主力净流入: {flow['main_net_inflow']/1e8:.2f}亿")
            print(f"  净流入占比: {flow['main_net_ratio']*100:.2f}%")
            print(f"  5日均值: {flow['inflow_5d_avg']/1e8:.2f}亿/日")
        else:
            print(f"\n{code} {name}: 无数据")
