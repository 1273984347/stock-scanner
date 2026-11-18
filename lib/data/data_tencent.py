"""腾讯财经估值查询（PE/PB/市值）—— 唯一解析在 ``data_realtime``。

数据来源：https://qt.gtimg.cn/q=sh600519

字段来源（已核验，2026-11-18）：
  parts[39]: PE(TTM)
  parts[44]: 总市值(亿元)
  parts[46]: PB

历史（ADR-0024）：本文件原有一份完整的腾讯报文解析（与 data_ashare 各一份），
已删除；批量大小由 10 提到 200（实测 500 码单请求可返 500 行，10 码/批是 20 倍
的请求放大）。这里只做「快照帧 → 估值字典」的投影。

Usage:
  from lib.data.data_tencent import get_tencent_quote
  q = get_tencent_quote("600519")
  print(f"PE={q['pe']}, PB={q['pb']}, 市值={q['market_cap']}亿")
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _project(quote_row) -> dict:
    """快照行 → {pe, pb, market_cap}（缺值/0/负 PE 统一为 None）。"""
    def _pick(v) -> Optional[float]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f != 0 else None

    pe = _pick(quote_row.get("市盈率-动态"))
    return {
        "pe": pe if (pe is not None and pe > 0) else None,
        "pb": _pick(quote_row.get("市净率")),
        "market_cap": _pick(quote_row.get("总市值")),
    }


def get_tencent_quote(code: str) -> dict:
    """获取腾讯财经单只股票估值（含 PE/PB/市值）。

    Args:
        code: 6 位股票代码，如 "600519"

    Returns:
        dict with keys: pe, pb, market_cap (亿元)；未找到返回空 dict
    """
    from lib.data.data_realtime import fetch_tencent_quotes
    df = fetch_tencent_quotes([code])
    if df.empty:
        return {}
    return _project(df.iloc[0])


def get_tencent_quotes_batch(codes: list) -> dict:
    """批量获取腾讯估值（200 码/请求，分片并发）。

    Args:
        codes: 股票代码列表

    Returns:
        dict {code: {pe, pb, market_cap}}
    """
    from lib.data.data_realtime import fetch_tencent_quotes
    df = fetch_tencent_quotes(codes)
    if df.empty:
        return {}
    return {str(row["代码"]): _project(row) for _, row in df.iterrows()}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    for code in ["600519", "000001", "000011"]:
        q = get_tencent_quote(code)
        pe, pb, mc = q.get("pe"), q.get("pb"), q.get("market_cap")
        print(f"{code}: PE={pe}, PB={pb}, 市值={mc}亿" if pe else f"{code}: 无数据")
