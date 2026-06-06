"""腾讯财经行情 API（PE/PB/市值）

数据来源：https://qt.gtimg.cn/q=sh600519

字段索引（已验证）：
  parts[39]: PE(TTM)
  parts[44]: 总市值(亿元)
  parts[46]: PB

Usage:
  from lib.data_tencent import get_tencent_quote
  q = get_tencent_quote("600519")
  print(f"PE={q['pe']}, PB={q['pb']}, 市值={q['market_cap']}亿")
"""
import requests
import logging
from typing import Optional

logger = logging.getLogger(__name__)

TENCENT_URL = "https://qt.gtimg.cn/q={sym}"


def _sym(code: str) -> str:
    """将 6 位代码转为腾讯格式"""
    c = code.strip().replace("sh.", "").replace("sz.", "").replace("bj.", "")
    # 用 helper 替代手写市场判断（修复 bj 静默丢失）
    from lib.market_utils import detect_market
    market = detect_market(c)
    if market == "bj":
        # 腾讯财经对 bj 股票用 sh 前缀（同深圳 0 编号）
        return f"sh{c}"
    if market in ("sh", "sz"):
        return f"{market}{c}"
    return f"sz{c}"  # 默认深圳（保持向后兼容）


def get_tencent_quote(code: str) -> dict:
    """获取腾讯财经单只股票行情（含 PE/PB/市值）

    Args:
        code: 6 位股票代码，如 "600519"

    Returns:
        dict with keys: pe, pb, market_cap (亿元)
        未找到返回空 dict
    """
    sym = _sym(code)
    url = TENCENT_URL.format(sym=sym)
    try:
        resp = requests.get(url, timeout=5)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        if not parts or len(parts) < 47:
            return {}
    except Exception as e:
        logger.debug(f"腾讯 API 请求失败 [{code}]: {e}")
        return {}

    def _v(s: str) -> Optional[float]:
        if s in ("", "-", "0", "None"):
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    return {
        "pe": _v(parts[39]),
        "pb": _v(parts[46]),
        "market_cap": _v(parts[44]),  # 亿元
    }


def get_tencent_quotes_batch(codes: list) -> dict:
    """批量获取腾讯行情（拼 URL，10 个代码一批）

    Args:
        codes: 股票代码列表

    Returns:
        dict {code: quote_dict}
    """
    result = {}
    batch_size = 10
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        syms = ",".join(_sym(c) for c in batch)
        url = TENCENT_URL.format(sym=syms)
        try:
            resp = requests.get(url, timeout=5)
            resp.encoding = "gbk"
            rows = resp.text.strip().split("\n")
            for row in rows:
                parts = row.split("~")
                if len(parts) < 47:
                    continue
                # 从 sym 提取代码
                raw_sym = parts[0].split("=")[-1] if "=" in parts[0] else ""
                code = raw_sym[2:] if raw_sym.startswith(("sh", "sz")) else ""
                if not code:
                    continue

                def _v(s: str) -> Optional[float]:
                    if s in ("", "-", "0", "None"):
                        return None
                    try:
                        return float(s)
                    except (ValueError, TypeError):
                        return None

                result[code] = {
                    "pe": _v(parts[39]),
                    "pb": _v(parts[46]),
                    "market_cap": _v(parts[44]),
                }
        except Exception as e:
            logger.debug(f"腾讯批量 API 请求失败 [batch {i}]: {e}")
    return result


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code in ["600519", "000001", "000011"]:
        q = get_tencent_quote(code)
        pe = q.get("pe")
        pb = q.get("pb")
        mc = q.get("market_cap")
        print(f"{code}: PE={pe}, PB={pb}, 市值={mc}亿" if pe else f"{code}: 无数据")
