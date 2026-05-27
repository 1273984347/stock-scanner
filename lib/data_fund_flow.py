"""资金流向估算

数据来源（3层fallback）：
1. 东方财富 push2.eastmoney.com（优先，5日平均，最准确）
2. 腾讯成交量/换手率 + 历史均量（备用，估算资金活跃度）
3. akshare stock_individual_fund_flow（兜底）

资金流向估算逻辑（腾讯备选）：
  - 换手率 > 3% = 活跃交易
  - 历史对比：今日成交量 vs 20日均量
  - 大成交量 + 价格上涨 → 主力净流入
  - 大成交量 + 价格下跌 → 主力净流出

Usage:
  from lib.data_fund_flow import get_fund_flow_estimate
  flow = get_fund_flow_estimate("600519")
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _to_float(v) -> Optional[float]:
    if not v or str(v) in ("", "-", "0"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def get_fund_flow_estimate(code: str) -> dict:
    """获取资金流向估算（多源优先）

    Returns:
        dict with keys: main_net_ratio, net_inflow_5d, source
    """
    # 1. 东方财富（最准确）
    try:
        from lib.data_eastmoney_fund import get_fund_flow
        flow = get_fund_flow(code, days=5)
        if flow and flow.get("main_net_inflow", 0) != 0:
            return {
                "main_net_ratio": flow.get("main_net_ratio", 0),
                "net_inflow_5d": flow.get("main_net_inflow", 0),
                "source": "eastmoney",
            }
    except Exception:
        pass

    # 2. 腾讯 + 历史成交量估算（仅在有明确估算时返回，否则继续akshare）
    tencent_result = None
    try:
        from lib.data_ashare import _get_tencent_quotes
        import pandas as pd

        df = _get_tencent_quotes([code])
        if not df.empty:
            row = df.iloc[0]
            price_chg = _to_float(row.get("涨跌幅"))  # field 32

            # 获取历史成交量
            hist_vol = _get_hist_avg_volume(code)
            today_vol = _to_float(row.get("成交量")) or 0

            if hist_vol and hist_vol > 0 and today_vol > 0:
                vol_ratio = today_vol / hist_vol
            else:
                vol_ratio = 1.0

            # 估算主力净流入占比
            # 假设：成交量放大超过1.5倍时，有主力参与
            # 大单成交方向由涨跌幅判断
            main_net_ratio = 0.0
            if vol_ratio > 1.5:
                if price_chg is not None and price_chg > 0:
                    main_net_ratio = min((vol_ratio - 1.0) * 0.05, 0.15)  # 放量上涨，估算主力流入
                elif price_chg is not None and price_chg < 0:
                    main_net_ratio = -min((vol_ratio - 1.0) * 0.05, 0.15)  # 放量下跌，估算主力流出

            # 仅在有明确估算（非0）时返回；否则继续akshare
            if main_net_ratio != 0.0:
                tencent_result = {
                    "main_net_ratio": main_net_ratio,
                    "net_inflow_5d": 0,
                    "source": "tencent_estimate",
                }
    except Exception:
        pass

    if tencent_result is not None:
        return tencent_result

    # 3. akshare 兜底
    try:
        import akshare as ak
        market = "sh" if code.startswith(("6", "5", "9")) else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is not None and not df.empty:
            # akshare 列名：主力净流入-净额, 主力净流入-净占比
            col_net = [c for c in df.columns if "净额" in str(c)]
            col_ratio = [c for c in df.columns if "净占比" in str(c)]
            if col_ratio:
                ratio_vals = pd.to_numeric(df[col_ratio[0]], errors="coerce").dropna()
                if len(ratio_vals) > 0:
                    ratio = float(ratio_vals.iloc[-1]) / 100  # 转为小数
                    net = 0.0
                    if col_net:
                        net_vals = pd.to_numeric(df[col_net[0]], errors="coerce").dropna()
                        if len(net_vals) > 0:
                            net = float(net_vals.iloc[-1])
                    return {
                        "main_net_ratio": ratio,
                        "net_inflow_5d": net,
                        "source": "akshare",
                    }
    except Exception:
        pass

    return _empty_flow()


def _get_hist_avg_volume(code: str, days: int = 20) -> Optional[float]:
    """从本地TDX获取历史平均成交量"""
    try:
        import pandas as pd
        from lib.data_layer import get_stock_hist
        df = get_stock_hist(code, days=days)
        if not df.empty and "volume" in df.columns:
            return float(df["volume"].iloc[:-1].mean())  # 排除今日
    except Exception:
        pass
    return None


def _empty_flow() -> dict:
    return {"main_net_ratio": 0, "net_inflow_5d": 0, "source": "none"}


def get_fund_flow_simple(code: str) -> dict:
    """兼容接口：返回 main_net_ratio, net_inflow_5d"""
    flow = get_fund_flow_estimate(code)
    return {
        "main_net_ratio": flow.get("main_net_ratio", 0),
        "net_inflow_5d": flow.get("net_inflow_5d", 0),
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行")]:
        flow = get_fund_flow_estimate(code)
        print(f"\n{code} {name}:")
        print(f"  main_net_ratio: {flow['main_net_ratio']*100:.2f}%")
        print(f"  net_inflow_5d: {flow['net_inflow_5d']/1e8:.2f}亿")
        print(f"  source: {flow['source']}")
