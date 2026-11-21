from lib.data.data_utils import to_float

"""东方财富财报数据适配器

提供东方财富财报 API 数据，补充：
- 毛利率（XSMLL 销售毛利率）
- 资产负债率（ZCFZL）
- 每股经营现金流（MGJYXJJE）
- 扣非净利润（KCFJCXSYJLR）

API: https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew
     ?type=0&code={SH600519}

返回字段（主要）：
  TOTALOPERATEREVE  = 营业总收入（元）
  MLR               = 毛利（元）
  XSMLL             = 销售毛利率（%），需除100
  PARENTNETPROFIT   = 归属母公司净利润（元）
  KCFJCXSYJLR      = 扣非净利润（元）
  MGJYXJJE          = 每股经营现金流（元/股）
  ZCFZL             = 资产负债率（%），需除100
  TOTALOPERATEREVETZ = 营业总收入同比增长率（%），需除100
  PARENTNETPROFITTZ  = 归属母公司净利润同比增长率（%），需除100
"""
from typing import Dict, Optional
import logging
import requests
from lib.data.cache import retry_api_call
from lib.data.host_health import (HOST_EASTMONEY_EMWEB, is_available,
                                  note_failure, note_success)

logger = logging.getLogger(__name__)

_EM_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew"


def _to_float(v) -> Optional[float]:
    """安全转 float"""
    return to_float(v, prefer_raw_float=True)


def _fetch_em_financial(code: str) -> Optional[Dict]:
    """从东方财富获取财务报表数据（最新一期）"""
    # 转换代码格式: 600519 -> SH600519, 430017 -> BJ430017
    # 用 helper 替代手写市场判断（修复 bj 静默丢失）
    from lib.data.market_utils import detect_market
    market = detect_market(code)
    if market == "unknown":
        return None
    # eastmoney API 用 SH/SZ/BJ 前缀
    em_prefix = {"sh": "SH", "sz": "SZ", "bj": "BJ"}[market]
    bs_code = em_prefix + code if not code.startswith(("SH", "SZ", "BJ")) else code

    # ADR-0025：emweb 与 push2 是两个 host，独立计数。开断期间不再付 2 次重试成本。
    if not is_available(HOST_EASTMONEY_EMWEB):
        logger.debug(f"[eastmoney_fin] emweb 开断中，跳过 {code}")
        return None

    def _fetch() -> Dict:
        resp = requests.get(
            _EM_URL,
            params={"type": "0", "code": bs_code},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    try:
        data = retry_api_call(_fetch, max_retries=2)
        note_success(HOST_EASTMONEY_EMWEB)
    except Exception as e:
        note_failure(HOST_EASTMONEY_EMWEB, e)
        raise

    pages = data.get("pages", 0)
    records = data.get("data", [])
    if not records or pages == 0:
        return None
    return records[0]  # 最新一期


def get_gross_margin(code: str) -> Optional[float]:
    """获取毛利率（小数格式）"""
    r = _fetch_em_financial(code)
    if r is None:
        return None
    xslll = _to_float(r.get("XSMLL"))
    if xslll is None:
        return None
    return xslll / 100.0


def get_ocf_per_share(code: str) -> Optional[float]:
    """获取每股经营现金流（元/股）"""
    r = _fetch_em_financial(code)
    if r is None:
        return None
    return _to_float(r.get("MGJYXJJE"))


def get_debt_ratio(code: str) -> Optional[float]:
    """获取资产负债率（小数格式）"""
    r = _fetch_em_financial(code)
    if r is None:
        return None
    zcfzl = _to_float(r.get("ZCFZL"))
    if zcfzl is None:
        return None
    return zcfzl / 100.0


def get_em_financial(code: str) -> Optional[Dict]:
    """获取东方财富完整财报数据（转换后的格式）

    Returns:
        dict with keys:
          gross_margin: float (小数)
          debt_ratio: float (小数)
          ocf_per_share: float (元)
          net_income: float (元)
          revenue: float (元)
          revenue_growth: float (小数，YoY)
          net_income_growth: float (小数，YoY)
    """
    r = _fetch_em_financial(code)
    if r is None:
        return None

    # 营业收入增长率（YoY % → 小数）
    rev_tz = _to_float(r.get("TOTALOPERATEREVETZ"))
    rev_growth = rev_tz / 100.0 if rev_tz is not None else None

    # 净利润增长率（YoY % → 小数）
    ni_tz = _to_float(r.get("PARENTNETPROFITTZ"))
    ni_growth = ni_tz / 100.0 if ni_tz is not None else None

    return {
        "gross_margin": (_to_float(r.get("XSMLL")) / 100.0
                        if _to_float(r.get("XSMLL")) is not None else None),
        "debt_ratio": (_to_float(r.get("ZCFZL")) / 100.0
                      if _to_float(r.get("ZCFZL")) is not None else None),
        "ocf_per_share": _to_float(r.get("MGJYXJJE")),
        "net_income": _to_float(r.get("PARENTNETPROFIT")),
        "revenue": _to_float(r.get("TOTALOPERATEREVE")),
        "revenue_growth": rev_growth,
        "net_income_growth": ni_growth,
    }


def get_em_financial_full(code: str) -> Optional[Dict]:
    """东财完整财务数据 → 标准形态（profitability/solvency/growth），ADR-0035。

    与 get_em_financial（7 字段扁平 dict，L5 基线依赖）并存，不改其契约。
    原始字段取自 emweb ZYZBAjaxNew 最新一期：ROEJQ/ZZCJLL/XSJLL/XSMLL 为百分数（/100）。

    Returns:
        {"profitability": {...}, "solvency": {...}, "growth": {...}, "_source": "eastmoney_full"}
        全部关键字段为空时返回 None（不产出空壳 dict）。
    """
    r = _fetch_em_financial(code)
    if r is None:
        return None

    def pct(key):
        v = _to_float(r.get(key))
        return v / 100.0 if v is not None else None

    profitability = {
        "ROE": pct("ROEJQ"), "ROA": pct("ZZCJLL"),
        "net_margin": pct("XSJLL"), "gross_margin": pct("XSMLL"),
        "operating_margin": pct("YSZKYYSR"), "ebitda_margin": None,
    }
    solvency = {"debt_to_asset": pct("ZCFZL"), "debt_to_equity": None,
                "equity_multiplier": None, "interest_coverage": None}
    growth = {"revenue_growth": pct("TOTALOPERATEREVETZ"),
              "net_income_growth": pct("PARENTNETPROFITTZ")}
    # 无有效指标 → 视为无数据，不产出空壳
    if not any(v is not None for v in profitability.values()) and \
            not any(v is not None for v in solvency.values()) and \
            not any(v is not None for v in growth.values()):
        return None
    return {"profitability": profitability, "solvency": solvency,
            "growth": growth, "_source": "eastmoney_full"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行")]:
        print(f"\n{code} {name}")
        data = get_em_financial(code)
        if data:
            for k, v in data.items():
                if v is not None:
                    if k in ("gross_margin", "debt_ratio", "revenue_growth", "net_income_growth"):
                        print(f"  {k}: {v:.4f} ({v*100:.2f}%)")
                    else:
                        print(f"  {k}: {v:,.2f}")
        else:
            print("  无数据")
