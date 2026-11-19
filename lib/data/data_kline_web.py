"""网页网关日线源（新浪 / 腾讯）——ADR-0027。

背景（2026-11-19 00:15 实测）：个股日线 8 个已注册源**全部返回 0 行**——
baostock `10001011 黑名单用户`、akshare 走 `push2his` ProxyError、tushare `daily`
无权限、adata/westock 缺 Node、tdx/tdx_local 本地目录已删。
但两条**未注册**的网页 K 线网关都能返到最近交易日（2026-11-18）：

| 通道 | 沪/深/创业/科创 | 北交所 | 指数 | 复权 |
|:--|:--|:--|:--|:--|
| 新浪 `CN_MarketData.getKLineData` | 10/10 | 10/10 | 10/10 | **不复权** |
| 腾讯 `fqkline/get` | 10/10 | 仅 1 根 | 10/10 | 支持 qfq |

因此分工固定：

- **默认源栈**（实时选股）新浪优先——覆盖面最广，含北交所与指数；
- **回测源栈**（`prefer_adjusted=True`）腾讯优先——只有它提供前复权；
  新浪**不得**进 `DATA_SOURCE_PRIORITY_ADJUSTED`，否则回测会静默吃不复权价
  （除权日会造出假跳空，是隐蔽的前视/失真源）。

单位契约：腾讯 `volume` 是**手**（×100 归一到股）；新浪已是股。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

SINA_KLINE_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                  "CN_MarketData.getKLineData")
# 两台腾讯主机在同日的实测中**交替**被 WAF 拦（501 JS 挑战页）：
# 23:05 web.ifzq 被拦、ifzq 正常；23:10 反过来。只写死其中一台就是追着外部状态跑。
# 正确做法：两台都登记、逐台尝试、各按 host_health 独立计数。
TENCENT_KLINE_HOSTS = ("web.ifzq.gtimg.cn", "ifzq.gtimg.cn")
TENCENT_KLINE_HOST_KEYS = {
    "web.ifzq.gtimg.cn": "HOST_TENCENT_KLINE_WEB",
    "ifzq.gtimg.cn": "HOST_TENCENT_KLINE",
}

SINA_MAX_BARS = 2000      # 实测 datalen=2000 可返 2000 根（2018-08 至今）
TENCENT_MAX_BARS = 500    # 实测 500 根可返 500 根；再大无必要
KLINE_HTTP_TIMEOUT_S = 10

COLUMNS = ["date", "open", "high", "low", "close", "volume"]


class SinaKlinePayloadError(Exception):
    """新浪 200 响应但报文不可解析（WAF 挑战页/HTML 的典型形态）。

    与「真无数据」（``[]``/``null``/空串）必须区分：静默返空会让主源失败
    不可观测——host_health 永闭合、源级熔断永不计数（评审 P0-2，2026-11-20）。
    """

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; stock-scanner)",
    "Referer": "https://finance.sina.com.cn",
}


def _sym(code: str) -> str:
    """6 位代码 → sh/sz/bj 前缀（与 market_utils 单一真源一致）。

    已显式带前缀（8 位）的输入**原样返回**：指数 000300 必须保持 ``sh000300``，
    若剥前缀再走 detect_market 会被判成深市 —— 请求打到 sz000300 直接空数据。
    """
    c = str(code).strip().lower()
    if len(c) == 8 and c[:2] in ("sh", "sz", "bj") and c[2:].isdigit():
        return c
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
            break
    from lib.data.market_utils import detect_market
    market = detect_market(c)
    return f"{market}{c}" if market in ("sh", "sz", "bj") else f"sz{c}"


def _http_get(url: str, timeout: float = KLINE_HTTP_TIMEOUT_S) -> str:
    """统一走 requests（与全项目一致）。

    不要用 urllib：`http.client` 在 import 时就把 `socket.create_connection`
    绑成模块级别名，conftest 的网络封锁（patch `socket.create_connection`）
    对它是无效的——实测会让本模块在"禁网"测试里真的发出请求。
    """
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def _as_frame(rows: list, volume_scale: float = 1.0) -> pd.DataFrame:
    if not rows:
        return _empty()
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["volume"] = df["volume"] * volume_scale
    df = (df.dropna(subset=["date", "close"])
            .drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True))
    return df


def _slice(df: pd.DataFrame, days: int, as_of_date: Optional[str]) -> pd.DataFrame:
    if df.empty:
        return df
    if as_of_date:
        df = df[df["date"] <= pd.to_datetime(as_of_date)]
    return df.tail(days).reset_index(drop=True)


# ========== 新浪（不复权，覆盖最全） ==========

def parse_sina_kline(payload: str) -> pd.DataFrame:
    """解析新浪 `getKLineData` 返回的 JSON 数组（volume 已是股）。"""
    try:
        items = json.loads(payload)
    except (ValueError, TypeError) as e:
        logger.warning(f"[sina_kline] 报文解析失败: {e}")
        return _empty()
    if not isinstance(items, list):
        return _empty()
    rows = [
        [it.get("day"), it.get("open"), it.get("high"), it.get("low"),
         it.get("close"), it.get("volume")]
        for it in items if isinstance(it, dict)
    ]
    return _as_frame(rows)


def get_sina_kline(code: str, days: int = 250,
                   as_of_date: Optional[str] = None) -> pd.DataFrame:
    """新浪日线（不复权）。

    as_of_date 不为空时多取一些再截断（该接口只有 datalen、没有 end 参数）——
    取 ``max(days*3, days+60)`` 根，最多 2000 根，保证跨节假日也够。

    快失败语义（评审 P0-2，2026-11-20）：HTTP 错误/超时与「200 但报文不可解析」
    **上抛**，让 ``call_with_health``（HOST_SINA_KLINE）与源级 CircuitBreaker
    计数——旧实现 catch-all 返空帧，主源失败不仅不计数还 note_success 清零。
    「真无数据」（``[]`` / ``null`` / 空串，新股/停牌）仍返空帧不计失败。
    """
    if days <= 0:
        return _empty()
    if as_of_date:
        want = min(max(days * 3, days + 60), SINA_MAX_BARS)
    else:
        want = min(days, SINA_MAX_BARS)
    url = f"{SINA_KLINE_URL}?symbol={_sym(code)}&scale=240&ma=no&datalen={want}"
    payload = _http_get(url)
    df = parse_sina_kline(payload)
    if df.empty and payload.strip() not in ("", "null", "[]"):
        raise SinaKlinePayloadError(
            f"sina 200 但报文不可解析（len={len(payload)}）: {payload[:80]!r}")
    return _slice(df, days, as_of_date)


# ========== 腾讯（支持前复权，北交所覆盖差） ==========

def parse_tencent_kline(payload: str, sym: str,
                        adjust: str = "qfq") -> pd.DataFrame:
    """解析腾讯 `fqkline/get`（rows 顺序：日期/开/收/高/低/量，量单位=手）。"""
    try:
        data = json.loads(payload).get("data", {})
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning(f"[tencent_kline] 报文解析失败: {e}")
        return _empty()
    node = data.get(sym) or {}
    rows = node.get(f"{adjust}day") or node.get("day") or []
    # 腾讯 rows 顺序是 日期/开/**收**/高/低/量（与 OHLC 习惯不同），必须重排，
    # 否则 high/low 会与 close 错位——K 线画反且指标全错，且不会报错。
    ordered = []
    for r in rows:
        vals = list(r)
        if len(vals) < 6:
            continue
        ordered.append([vals[0], vals[1], vals[3], vals[4], vals[2], vals[5]])
    return _as_frame(ordered, volume_scale=100.0)


def get_tencent_kline(code: str, days: int = 250, adjust: str = "qfq",
                      as_of_date: Optional[str] = None) -> pd.DataFrame:
    """腾讯日线（默认前复权）。支持起止日期，as_of_date 语义精确。"""
    if days <= 0:
        return _empty()
    sym = _sym(code)
    count = min(days, TENCENT_MAX_BARS)
    fq = adjust if adjust in ("qfq", "hfq", "") else "qfq"
    if as_of_date:
        end = str(as_of_date)[:10]
        start_dt = datetime.strptime(end, "%Y-%m-%d") - timedelta(days=count * 2)
        param = f"{sym},day,{start_dt:%Y-%m-%d},{end},{count},{fq}"
    else:
        param = f"{sym},day,,,{count},{fq}"
    from lib.data.host_health import call_with_health

    last_error = None
    for host in TENCENT_KLINE_HOSTS:
        key_name = TENCENT_KLINE_HOST_KEYS[host]
        from lib.data import host_health as _hh
        key = getattr(_hh, key_name)
        url = f"https://{host}/appstock/app/fqkline/get?param={param}"
        try:
            payload = call_with_health(key, _http_get, url)
        except Exception as e:
            last_error = e
            continue
        # host 已开断时 call_with_health 返回空 DataFrame，不是字符串
        if not isinstance(payload, str) or not payload:
            continue
        df = parse_tencent_kline(payload, sym, fq)
        if not df.empty:
            return _slice(df, days, as_of_date)

    logger.warning(f"[tencent_kline] 两台主机均无有效数据 {code}: {last_error}")
    return _empty()


# ========== 指数（显式入口，避免 000001 个股/指数歧义） ==========

def index_sym(code: str) -> str:
    """指数代码 → 市场前缀。

    必须显式区分：``000001`` 既是平安银行（sz000001）也是上证指数（sh000001）。
    个股路径走 ``_sym``（detect_market 判 sz），指数路径走本函数（000xxx → sh）。
    无法确定的（如 880xxx 板块）返回空串，调用方跳过。
    """
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c
    if not c.isdigit() or len(c) != 6:
        return ""
    if c.startswith("399"):
        return f"sz{c}"
    if c.startswith("000"):
        return f"sh{c}"
    return ""


def get_index_kline_web(code: str, days: int = 250,
                        as_of_date: Optional[str] = None) -> pd.DataFrame:
    """指数日线（腾讯优先，失败切新浪）。仅支持 sh/sz 指数（399xxx / 000xxx）。"""
    sym = index_sym(code)
    if not sym or days <= 0:
        return _empty()
    df = get_tencent_kline(sym, days=days, adjust="qfq", as_of_date=as_of_date)
    if not df.empty:
        return df
    want = min(max(days * 3, days + 60), SINA_MAX_BARS) if as_of_date else min(days, SINA_MAX_BARS)
    url = f"{SINA_KLINE_URL}?symbol={sym}&scale=240&ma=no&datalen={want}"
    # 评审 P0-2：指数兜底同样经 HOST_SINA_KLINE 计数（异常捕获保持既有「返空」行为）
    from lib.data.host_health import HOST_SINA_KLINE, call_with_health
    try:
        payload = call_with_health(HOST_SINA_KLINE, _http_get, url)
    except Exception as e:
        logger.warning(f"[index_kline] 新浪请求失败 {code}: {e}")
        return _empty()
    return _slice(parse_sina_kline(payload), days, as_of_date)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for c in ("600519", "000001", "920000"):
        s = get_sina_kline(c, 5)
        t = get_tencent_kline(c, 5)
        print(f"{c}: sina={len(s)} {s['date'].max() if len(s) else '-'} | "
              f"tencent={len(t)} {t['date'].max() if len(t) else '-'}")
