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
import requests
from lib.cache import retry_api_call

_EM_URL = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew"


def _to_float(v) -> Optional[float]:
    """安全转 float"""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_em_financial(code: str) -> Optional[Dict]:
    """从东方财富获取财务报表数据（最新一期）"""
    # 转换代码格式: 600519 -> SH600519
    prefix = "SH" if code.startswith(("6", "9")) else ("SZ" if code.startswith(("0", "3", "2")) else code)
    bs_code = prefix + code if not code.startswith(("SH", "SZ")) else code

    def _fetch() -> Dict:
        resp = requests.get(
            _EM_URL,
            params={"type": "0", "code": bs_code},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    data = retry_api_call(_fetch, max_retries=2)

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
