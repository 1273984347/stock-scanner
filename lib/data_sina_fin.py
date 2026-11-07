from lib.data_utils import to_float

"""新浪财经财务报表 API

[状态] 当前网络环境下 Sina API 返回 "Input error" (code=11)，暂不可用。

API: https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022
Method: POST
report_type: lrb(利润表) | fzb(资产负债表) | llb(现金流量表)

返回字段（部分）：
  lrb: revenue(营业收入), operating_cost(营业成本), net_profit(净利润)
  fzb: total_assets(资产总计), total_liabilities(负债合计), equity(股东权益)
  llb: operating_cash_flow(经营现金流), investing_cash_flow(投资现金流), financing_cash_flow(筹资现金流)

单位：万元

未来可用时：
  - 毛利率 = (revenue - operating_cost) / revenue
  - 替代 baostock gpMargin（当前已实现）
  - 经营现金流 = llb operating_cash_flow（需单位转换万元→元）
"""
import requests
import logging
import threading
import time
from typing import Optional, Dict

logger = logging.getLogger(__name__)

SINA_FIN_URL = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"

# 全局限流器
_sina_rate_limiter = threading.Lock()
_last_call_time = 0.0
_MIN_INTERVAL = 0.5  # 最少间隔0.5秒


def _rate_limited():
    global _last_call_time
    with _sina_rate_limiter:
        now = time.time()
        wait = _last_call_time + _MIN_INTERVAL - now
        if wait > 0:
            time.sleep(wait)
        _last_call_time = time.time()


def _to_float(v) -> Optional[float]:
    return to_float(v, strip_grouping=True)


def _post_json(url: str, data: dict, timeout: int = 10) -> dict:
    """POST请求，统一错误处理"""
    _rate_limited()
    try:
        resp = requests.post(url, data=data, timeout=timeout)
        return resp.json()
    except Exception as e:
        logger.debug(f"Sina finance API 请求失败: {e}")
        return {}


def _parse_report(data: dict, report_type: str) -> Optional[Dict]:
    """解析财报响应，提取最新一期数据"""
    try:
        result = data.get("result", {})
        if not result or result.get("status", {}).get("code") != 0:
            return None
        data_list = result.get("data", {})
        if not data_list:
            return None
        # 取最新一期（字典的key是字符串索引）
        latest_key = max(data_list.keys(), key=lambda k: int(k) if k.isdigit() else -1)
        report = data_list[latest_key]
        if report.get("report_type", "").lower() != report_type:
            return None
        return report
    except Exception as e:
        logger.debug(f"[sina_fin] 财报解析异常: {e}")
        return None


def get_income_statement(code: str, year: int = 2025) -> Optional[Dict]:
    """获取利润表（LRB）

    Returns:
        dict with keys: revenue, operating_cost, net_profit (万元)
    """
    symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
    payload = {
        "report_type": "lrb",
        "symbol": symbol,
        "begin_date": f"{year}0101",
        "end_date": f"{year}1231",
        "page": 1,
        "count": 1,
    }
    data = _post_json(SINA_FIN_URL, payload)
    report = _parse_report(data, "lrb")
    if not report:
        return None
    return {
        "revenue": _to_float(report.get("revenue")),
        "operating_cost": _to_float(report.get("operating_cost")),
        "net_profit": _to_float(report.get("net_profit")),
    }


def get_balance_sheet(code: str, year: int = 2025) -> Optional[Dict]:
    """获取资产负债表（FZB）

    Returns:
        dict with keys: total_assets, total_liabilities, equity (万元)
    """
    symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
    payload = {
        "report_type": "fzb",
        "symbol": symbol,
        "begin_date": f"{year}0101",
        "end_date": f"{year}1231",
        "page": 1,
        "count": 1,
    }
    data = _post_json(SINA_FIN_URL, payload)
    report = _parse_report(data, "fzb")
    if not report:
        return None
    return {
        "total_assets": _to_float(report.get("total_assets")),
        "total_liabilities": _to_float(report.get("total_liabilities")),
        "equity": _to_float(report.get("equity")),
    }


def get_cash_flow(code: str, year: int = 2025) -> Optional[Dict]:
    """获取现金流量表（LLB）

    Returns:
        dict with keys: operating_cash_flow, investing_cash_flow, financing_cash_flow (万元)
    """
    symbol = f"sh{code}" if code.startswith("6") else f"sz{code}"
    payload = {
        "report_type": "llb",
        "symbol": symbol,
        "begin_date": f"{year}0101",
        "end_date": f"{year}1231",
        "page": 1,
        "count": 1,
    }
    data = _post_json(SINA_FIN_URL, payload)
    report = _parse_report(data, "llb")
    if not report:
        return None
    return {
        "operating_cash_flow": _to_float(report.get("operating_cash_flow")),
        "investing_cash_flow": _to_float(report.get("investing_cash_flow")),
        "financing_cash_flow": _to_float(report.get("financing_cash_flow")),
    }


def get_gross_margin(code: str, year: int = 2025) -> Optional[float]:
    """计算毛利率 = (营收 - 营业成本) / 营收

    Returns:
        毛利率（0~1之间），失败返回 None
    """
    inc = get_income_statement(code, year)
    if not inc:
        return None
    rev = inc.get("revenue")
    cost = inc.get("operating_cost")
    if rev is None or rev <= 0:
        return None
    cogs = cost if cost is not None else 0
    return (rev - cogs) / rev


def get_financial_summary(code: str, year: int = 2025) -> Optional[Dict]:
    """获取完整财务摘要（用于基本面评分）

    Returns:
        dict with keys:
        - gross_margin: 毛利率 (0~1)
        - net_profit: 净利润 (万元)
        - revenue: 营业收入 (万元)
        - operating_cash_flow: 经营现金流 (万元)
        - total_assets: 资产总计 (万元)
        - total_liabilities: 负债合计 (万元)
    """
    inc = get_income_statement(code, year)
    cfl = get_cash_flow(code, year)
    bse = get_balance_sheet(code, year)

    result = {}
    if inc:
        rev = inc.get("revenue")
        cost = inc.get("operating_cost")
        if rev and rev > 0:
            cogs = cost if cost is not None else 0
            result["gross_margin"] = (rev - cogs) / rev
        result["net_profit"] = inc.get("net_profit")
        result["revenue"] = rev

    if cfl:
        result["operating_cash_flow"] = cfl.get("operating_cash_flow")

    if bse:
        result["total_assets"] = bse.get("total_assets")
        result["total_liabilities"] = bse.get("total_liabilities")

    return result if result else None


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行")]:
        print(f"\n{'='*50}")
        print(f"{code} {name}")
        print(f"{'='*50}")

        summary = get_financial_summary(code, year=2025)
        if summary:
            gm = summary.get("gross_margin")
            print(f"毛利率: {gm:.2%}" if gm else "毛利率: N/A")
            print(f"净利润: {summary.get('net_profit')} 万元")
            print(f"营收: {summary.get('revenue')} 万元")
            print(f"经营现金流: {summary.get('operating_cash_flow')} 万元")
        else:
            print("无数据")
