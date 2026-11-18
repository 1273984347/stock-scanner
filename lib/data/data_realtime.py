"""实时行情唯一实现（P0 收口，2026-11-18）。

背景（坑表 P-99）：
    全市场实时行情曾有 4 套同名 ``get_realtime_quotes``
    （data_layer / data_api 门面 / data_ashare / data_adata）。其中
    ``data_ashare`` 版先取代码列表再只跑前 100 只、``data_adata`` 版无人调用——
    半成品并存，接错一个就静默截断到 100 只。本模块把「取数 + 单位归一 +
    合法性校验 + 降级」收敛到一处，其余同名入口只做委托（不得再各自解析）。

源顺序（SNAPSHOT_SOURCE_ORDER）：
    1) ``sina_full``     —— ``ak.stock_zh_a_spot()``：新浪 Market_Center 全市场
       分页（实测 5579 行 / 12.3s，含北交所）；ADR-0017 起为全市场主源。
    2) ``tencent_batch`` —— ``qt.gtimg.cn`` 批量（200 码/请求，实测 500 码单请求
       可返 500 行）。按「上次成功快照」的代码表重建，无代码表则跳过；腾讯对
       北交所（92xxxx）无稳定覆盖，缺 bj 由校验显式告警。
    3) ``pytdx_sample``  —— 通达信公共行情服务器，仅 ~28 只样本股。最后兜底，
       只为「离线/外网全挂」时不至于返回空帧，覆盖面不足由校验告警。

单位契约（重要，历史缺陷）：
    统一到「股 / 元」。腾讯 ``parts[6]`` 单位是手（×100）、``parts[37]`` 单位是
    万元（×10000）；新浪/akshare 已是股/元。旧 ``data_ashare`` 直接透传腾讯的
    手/万元，导致资金流「今日量 vs 20 日均量（股）」相差 100 倍，
    ``vol_ratio > 1.5`` 几乎永远不成立——腾讯估算分支静默失效。

代码契约（重要，历史缺陷）：
    ``代码`` 统一为 **6 位纯数字**，另加 ``市场`` 列（sh/sz/bj）。
    新浪 ``stock_zh_a_spot`` 返回的是带前缀的 ``sh600519``，而消费方大多按
    6 位精确匹配（``run.py`` 取股票名、``strategy/steps.py`` 取实时行），
    两边不一致时这些精确匹配会静默落空（不报错、只是 enrichment 永远为空）。
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Callable, List, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# 源顺序常量：机器判据与运维文档共用同一真源（不要在别处再写字面量列表）
SNAPSHOT_SOURCE_ORDER = ("sina_full", "tencent_batch", "pytdx_sample")

TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q="
TENCENT_BATCH_SIZE = 200      # 实测 500 码单请求可返 500 行；200 控制 URL 长度与体量
TENCENT_MAX_WORKERS = 6
TENCENT_TIMEOUT_S = 8

SNAPSHOT_CACHE_KEY = "realtime/realtime"   # 沿用旧 key，消费方无需改路径
UNIVERSE_CACHE_KEY = "realtime/universe"   # 腾讯兜底用的代码表（仅代码/名称）
UNIVERSE_TTL_HOURS = 24 * 30               # 代码表 30 天（跨月容忍，代码增删很慢）

# 全市场 A 股（含北交所）常态 5500+ 只；低于此值视为降级快照，
# 不写缓存（避免 28 只样本帧污染后续 30 分钟的扫描），并显式告警。
SNAPSHOT_MIN_ROWS = 4000

# 兼容旧入口的公开列契约（data_layer docstring 承诺 + conftest P-80 对齐）
REQUIRED_COLUMNS = ("代码", "名称", "最新价", "成交量", "成交额")

_TENCENT_VOLUME_LOT = 100.0      # 手 → 股
_TENCENT_AMOUNT_WAN = 10000.0    # 万元 → 元

_TENCENT_LINE_RE = re.compile(r'v_([a-z]{2}\d{6})="(.*)"')


# ========== 代码与市场 ==========

def _norm_code(raw: str) -> str:
    """去掉 sh/sz/bj 前缀，返回 6 位代码。"""
    c = str(raw).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            return c[2:]
    return c


def _tencent_sym(code: str) -> str:
    """6 位代码 → 腾讯代码（sh/sz/bj 前缀）。"""
    from lib.data.market_utils import detect_market
    c = _norm_code(code)
    market = detect_market(c)
    if market in ("sh", "sz", "bj"):
        return f"{market}{c}"
    # 未知市场按深市尝试（保持旧行为，不静默丢代码）
    return f"sz{c}"


def _to_float(s, default: float = 0.0) -> float:
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return default
    return v


# ========== 腾讯解析（唯一实现） ==========

def _tencent_time(raw: str) -> str:
    """腾讯 parts[30]='YYYYMMDDHHMMSS' → 'HH:MM:SS'（与新浪 时间戳 列同形）。"""
    s = str(raw).strip()
    if len(s) >= 14 and s[:14].isdigit():
        return f"{s[8:10]}:{s[10:12]}:{s[12:14]}"
    return ""


def parse_tencent_quotes(text: str) -> pd.DataFrame:
    """解析 ``qt.gtimg.cn`` 返回文本 → 标准快照帧（单位已归一为 股/元）。

    单独抽出便于机器判据用固定报文测试，不依赖网络。
    """
    rows = []
    for line in str(text or "").splitlines():
        m = _TENCENT_LINE_RE.search(line.strip())
        if not m:
            continue
        parts = m.group(2).split("~")
        if len(parts) < 40:
            continue
        code = _norm_code(parts[2] or m.group(1))
        if not code:
            continue
        rows.append({
            "代码": code,
            "名称": parts[1],
            "最新价": _to_float(parts[3]),
            "涨跌额": _to_float(parts[31]),
            "涨跌幅": _to_float(parts[32]),
            "买入": _to_float(parts[9]),
            "卖出": _to_float(parts[19]),
            "昨收": _to_float(parts[4]),
            "今开": _to_float(parts[5]),
            "最高": _to_float(parts[33]),
            "最低": _to_float(parts[34]),
            # 手 → 股
            "成交量": _to_float(parts[6]) * _TENCENT_VOLUME_LOT,
            # 万元 → 元
            "成交额": _to_float(parts[37]) * _TENCENT_AMOUNT_WAN,
            "时间戳": _tencent_time(parts[30]),
            "市盈率-动态": _to_float(parts[39], None) if parts[39] not in ("", "-") else None,
            "市净率": _to_float(parts[46], None) if len(parts) > 46 and parts[46] not in ("", "-") else None,
            "总市值": (_to_float(parts[44], None) if len(parts) > 44 and parts[44] not in ("", "-") else None),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).drop_duplicates(subset=["代码"], keep="last").reset_index(drop=True)


def _fetch_tencent_chunk(syms: List[str]) -> pd.DataFrame:
    url = TENCENT_QUOTE_URL + ",".join(syms)
    try:
        resp = requests.get(url, timeout=TENCENT_TIMEOUT_S)
        resp.encoding = "gbk"
        return parse_tencent_quotes(resp.text)
    except Exception as e:
        logger.warning(f"[realtime] 腾讯批量请求失败（{len(syms)} 码）: {e}")
        return pd.DataFrame()


def fetch_tencent_quotes(codes: List[str], batch_size: int = TENCENT_BATCH_SIZE,
                         max_workers: int = TENCENT_MAX_WORKERS) -> pd.DataFrame:
    """腾讯批量行情（分片并发）。空列表直接返回空帧，不发请求。"""
    if not codes:
        return pd.DataFrame()
    syms = [_tencent_sym(c) for c in codes]
    syms = [s for s in dict.fromkeys(syms) if s]
    chunks = [syms[i:i + batch_size] for i in range(0, len(syms), batch_size)]
    if len(chunks) == 1:
        return _fetch_tencent_chunk(chunks[0])
    frames = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as pool:
        for df in pool.map(_fetch_tencent_chunk, chunks):
            if df is not None and not df.empty:
                frames.append(df)
    if not frames:
        return pd.DataFrame()
    return (pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["代码"], keep="last")
            .reset_index(drop=True))


# ========== 快照归一（代码 + 市场） ==========

def normalize_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """把快照归一到「6 位数字代码 + 市场列」。

    幂等：已归一过的帧再调用不会改变内容。市场优先取原代码前缀，缺前缀时用
    ``detect_market`` 推断（北交所 4/8/92 与沪市 9x 的区分靠它，不能自行猜）。
    """
    if df is None or df.empty or "代码" not in df.columns:
        return df if df is not None else pd.DataFrame()

    out = df.copy()
    raw = out["代码"].astype(str).str.strip().str.lower()
    prefix = raw.str.extract(r"^(sh|sz|bj)", expand=False)
    out["代码"] = raw.str.replace(r"^(sh|sz|bj)", "", regex=True)

    if "市场" not in out.columns:
        out["市场"] = prefix
    missing = out["市场"].isna() | (out["市场"].astype(str).str.strip() == "")
    if missing.any():
        from lib.data.market_utils import detect_market
        out.loc[missing, "市场"] = out.loc[missing, "代码"].map(detect_market)
    return out


# ========== 快照合法性校验（降级显式化） ==========

def _count_bj(df: pd.DataFrame) -> int:
    """北交所行数：优先用 市场 列，否则按代码前缀（bj / 4 / 8 / 92）推断。"""
    if "市场" in df.columns:
        return int((df["市场"].astype(str).str.lower() == "bj").sum())
    if "代码" not in df.columns:
        return 0
    codes = df["代码"].astype(str).str.lower().str.strip()
    return int((codes.str.startswith("bj") | codes.str.match(r"^(4|8|92)")).sum())


def validate_snapshot(df: pd.DataFrame) -> dict:
    """返回 {rows, bj_count, timestamp, degraded, warnings}。

    ``degraded=True`` 的语义：覆盖面不足或列契约缺失，调用方应视为「降级快照」，
    不得当作全市场真值使用（阈值 4000 行；常态 5500+）。
    """
    warnings: List[str] = []
    if df is None or df.empty:
        return {"rows": 0, "bj_count": 0, "timestamp": "",
                "degraded": True, "warnings": ["快照为空"]}

    rows = len(df)
    bj_count = _count_bj(df)
    ts = ""
    if "时间戳" in df.columns and len(df):
        vals = df["时间戳"].dropna().astype(str)
        ts = vals.iloc[0] if len(vals) else ""

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        warnings.append(f"缺列 {missing}（消费方按契约取列会 KeyError）")
    if rows < SNAPSHOT_MIN_ROWS:
        warnings.append(
            f"行数 {rows} < 全市场阈值 {SNAPSHOT_MIN_ROWS}——覆盖面不足，"
            f"市场级策略（涨跌比/量价突破/多因子）结果不可当真")
    if bj_count == 0:
        warnings.append("无北交所（bj）行——北交所标的会整体缺席")
    if not ts:
        warnings.append("时间戳缺失——无法判断快照新鲜度")

    return {
        "rows": rows,
        "bj_count": bj_count,
        "timestamp": ts,
        "degraded": bool(missing) or rows < SNAPSHOT_MIN_ROWS,
        "warnings": warnings,
    }


# ========== 缓存（复用 data_layer 的路径/TTL 语义，避免第二套缓存实现） ==========

def _cache_ttl_hours() -> float:
    now = datetime.now()
    minutes = 5 if 9 <= now.hour < 15 else 30   # 盘中 5min，盘后 30min
    return minutes / 60


def _load(key: str, max_age_hours: float) -> pd.DataFrame:
    from lib.data import data_layer
    return data_layer._load_cache(key, max_age_hours=max_age_hours)


def _save(key: str, df: pd.DataFrame) -> None:
    from lib.data import data_layer
    data_layer._save_cache(key, df)


def _save_universe(df: pd.DataFrame) -> None:
    """落「代码/名称」代码表，供 sina 全市场失败时腾讯批量重建。"""
    if df.empty or "代码" not in df.columns:
        return
    cols = [c for c in ("代码", "名称") if c in df.columns]
    try:
        _save(UNIVERSE_CACHE_KEY, df[cols].copy())
    except Exception as e:   # 代码表不是关键路径，失败只降级腾讯兜底
        logger.warning(f"[realtime] 代码表写入失败（腾讯兜底将不可用）: {e}")


def _load_universe() -> pd.DataFrame:
    try:
        uni = _load(UNIVERSE_CACHE_KEY, max_age_hours=UNIVERSE_TTL_HOURS)
    except Exception as e:
        logger.debug(f"[realtime] 代码表读取失败: {e}")
        return pd.DataFrame()
    return uni if not uni.empty and "代码" in uni.columns else pd.DataFrame()


# ========== pytdx 兜底（最后一级，仅样本股） ==========

def _fetch_pytdx_sample() -> pd.DataFrame:
    # 引用 data_layer 的模块级常量（测试会 monkeypatch 这两个属性）
    from lib.data import data_layer
    try:
        from pytdx.hq import TdxHq_API
    except Exception as e:
        logger.debug(f"[realtime] pytdx 不可用: {e}")
        return pd.DataFrame()

    api = TdxHq_API()
    connected = False
    last_error = None
    for host, port in data_layer.TDX_HQ_SERVERS:
        try:
            api.connect(host, port, time_out=data_layer.TDX_CONNECT_TIMEOUT_S)
            connected = True
            break
        except Exception as e:
            last_error = e
            logger.warning(f"[realtime] pytdx 连接失败 {host}:{port}: {e}")
    if not connected:
        if last_error is not None:
            logger.warning(f"[realtime] pytdx 所有服务器连接失败，最后错误: {last_error}")
        return pd.DataFrame()

    try:
        quotes = api.get_security_quotes(list(data_layer.REALTIME_SAMPLE_CODES))
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
    if not quotes:
        return pd.DataFrame()

    df = pd.DataFrame(quotes)
    col_map = {"code": "代码", "name": "名称", "price": "最新价",
               "open": "今开", "high": "最高", "low": "最低",
               "vol": "成交量", "amount": "成交额"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    if "最新价" in df.columns and "今开" in df.columns:
        df["涨跌幅"] = df.apply(
            lambda r: ((r.get("最新价", 0) - r.get("今开", 0)) / r.get("今开", 1) * 100)
            if r.get("今开", 0) else 0.0, axis=1)
    return df


# ========== 对外唯一入口 ==========

def get_market_snapshot(force: bool = False,
                        primary_fetch: Optional[Callable[[], pd.DataFrame]] = None) -> pd.DataFrame:
    """获取全市场实时快照（主源 → 腾讯批量 → pytdx 样本）。

    Args:
        force: True = 跳过读缓存强制刷新。
        primary_fetch: 主源取数函数（默认 ``ak.stock_zh_a_spot``）。由 data_layer
            注入以复用其重试/限流包装，并让测试可在既有 monkeypatch 点上替换。

    Returns:
        标准列快照；全部源失败返回空 DataFrame（调用方须显式降级）。
        降级快照（< 4000 行或缺列）不写缓存，避免污染后续扫描。
    """
    if not force:
        cached = _load(SNAPSHOT_CACHE_KEY, _cache_ttl_hours())
        if not cached.empty:
            # 旧缓存可能是带市场前缀的格式（ADR-0024 之前写入），读时归一
            return normalize_snapshot(cached)

    if primary_fetch is None:
        import akshare as ak
        primary_fetch = ak.stock_zh_a_spot

    source = ""
    df = pd.DataFrame()
    try:
        df = primary_fetch()
        source = "sina_full"
    except Exception as e:
        logger.warning(f"[realtime] sina 全市场失败，尝试腾讯批量: {e}")
        df = pd.DataFrame()

    if df is None or df.empty:
        universe = _load_universe()
        if universe.empty:
            logger.warning("[realtime] 腾讯兜底跳过：无全市场代码表（需先成功跑一次主源）")
        else:
            df = fetch_tencent_quotes(universe["代码"].tolist())
            source = "tencent_batch"
            if df.empty:
                logger.warning(f"[realtime] 腾讯批量失败（{len(universe)} 码）")

    if df is None or df.empty:
        df = _fetch_pytdx_sample()
        source = "pytdx_sample"

    if df is None or df.empty:
        logger.error(
            "[realtime] 实时行情全部源失败（%s）——返回空帧，调用方须显式降级",
            "/".join(SNAPSHOT_SOURCE_ORDER))
        return pd.DataFrame()

    df = normalize_snapshot(df)
    report = validate_snapshot(df)
    for w in report["warnings"]:
        logger.warning(f"[realtime] {source}: {w}")

    if report["degraded"]:
        # 降级快照不落缓存：28 只样本帧若写进 30 分钟缓存，会让整轮扫描的
        # 涨跌比/情绪分建立在错的分母上。
        logger.error(
            "[realtime] 源=%s 为降级快照（rows=%d bj=%d）——不写缓存",
            source, report["rows"], report["bj_count"])
        return df

    logger.info("[realtime] 源=%s rows=%d bj=%d ts=%s",
                source, report["rows"], report["bj_count"], report["timestamp"])
    _save(SNAPSHOT_CACHE_KEY, df)
    if source == "sina_full":
        _save_universe(df)
    return df
