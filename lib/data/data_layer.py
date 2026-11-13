from lib.data.data_utils import to_float

"""数据层：多源数据获取、缓存、限流"""
import os
import json
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (get_config, TDX_DATA_PATH as _CFG_TDX_DATA_PATH,
                    TDX_REF_PATH as _CFG_TDX_REF_PATH)
from lib.data.cache import CircuitBreaker, CircuitOpenError
from lib.data.broker import LRUCache
from lib.infra.utils import _safe_float
from lib.data.data_cache import get_financial_cache
from lib.data.data_source_registry import (
    _normalize_columns, _validate_dataframe, AllDataSourcesFailedError,
    _fetch_from_source, DATA_SOURCE_PRIORITY, _CIRCUIT_BREAKERS,
    get_stock_hist_multi_source,
    get_circuit_breaker_status,
)
from lib.data.news_signal import get_news_signals
from lib.data.market_utils import detect_market

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# ========== 路径配置 ==========
# P7-4：单一真相源归 config.py（默认值在此，env 覆盖在 import 时生效）
TDX_REF_PATH = Path(_CFG_TDX_REF_PATH)

# ========== 实时行情配置 ==========
# P2-config-migration-deferred
TDX_HQ_SERVERS = [
    ("119.147.212.81", 7709),
    ("112.74.214.43", 7709),
]

# (market, code) 列表 —— 0=沪, 1=深
REALTIME_SAMPLE_CODES = [
    (0, "600519"), (0, "601318"), (0, "600036"), (0, "600276"),
    (0, "601166"), (0, "600030"), (0, "601398"), (0, "600900"),
    (0, "601888"), (0, "600809"), (0, "601012"), (0, "600585"),
    (1, "000858"), (1, "000333"), (1, "002714"), (1, "000651"),
    (1, "000001"), (1, "002415"), (1, "300750"), (1, "002594"),
    (0, "601899"), (0, "600050"), (0, "601668"), (0, "600000"),
    (1, "000568"), (1, "002304"), (1, "300059"), (1, "002475"),
]

# ========== 缓存 TTL 配置（小时）==========
# P2-config-migration-deferred
CACHE_TTL = {
    "load_default": 2,          # _load_cache 默认 max_age_hours
    "clean_age_hours": 24,      # clean_cache 默认最大保留时间
    "clean_max_total_mb": 500,  # clean_cache 默认总大小上限
    "financial_default": 168,   # get_stock_financial 默认缓存（7天）
    "fund_flow": 1,             # get_stock_fund_flow 磁盘缓存（1小时）
}

# ========== 网络/数据窗口配置 ==========
# P2-config-migration-deferred
TDX_CONNECT_TIMEOUT_S = 5      # pytdx 行情服务器连接超时
NET_INFLOW_WINDOW_DAYS = 5     # 资金流向近 N 日窗口

# ========== API 限流 ==========
# P2-config-migration-deferred
RATE_LIMIT_CALLS_PER_SEC = 3.0
API_RETRY_MAX_ATTEMPTS = 3
API_RETRY_BASE_WAIT_S = 2.0

class _RateLimiter:
    """线程安全的 API 调用频率限制器"""
    def __init__(self, max_calls_per_second: float = 3.0):
        self._min_interval = 1.0 / max_calls_per_second
        self._last_call_time = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.time()
            wait = self._last_call_time + self._min_interval - now
            self._last_call_time = max(now, self._last_call_time + self._min_interval)
        if wait > 0:
            time.sleep(wait)

_rate_limiter = _RateLimiter(max_calls_per_second=RATE_LIMIT_CALLS_PER_SEC)


def _is_rate_limit_error(exc: Exception) -> bool:
    """检测异常是否由 API 限流引起

    限流特征: API 返 HTML 错误页 (rate-limit warning) 而非 JSON, 导致
    pd.DataFrame() 或 json.loads() 抛 JSONDecodeError,
    错误消息含 "Can not decode value starting with '<'" 或 "Expecting value"。

    区分限流 vs 真实错误: 限流需要更长 backoff (翻倍), 真实错误重试无意义。
    """
    if isinstance(exc, json.JSONDecodeError):
        return True
    msg = str(exc)
    return "Can not decode" in msg or "Expecting value" in msg


def _retry_api_call(func, *args, max_retries: int = API_RETRY_MAX_ATTEMPTS, **kwargs):
    """带重试和限流的 API 调用

    2026-06-09 增强: 区分 rate-limit 错误 (HTML/JSON decode 失败) vs 真实错误。
    - rate-limit: backoff 翻倍 (等待限流窗口过去)
    - 真实错误: 正常指数 backoff
    """
    last_error = None
    for attempt in range(max_retries):
        try:
            _rate_limiter.acquire()
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                # 限流错误 → 翻倍 backoff, 真实错误 → 正常 backoff
                if _is_rate_limit_error(e):
                    wait = API_RETRY_BASE_WAIT_S * (2 ** (attempt + 1))  # 翻倍
                    logger.info(f"API 限流 (尝试 {attempt + 1}/{max_retries}), {wait}s 后重试: {type(e).__name__}")
                else:
                    wait = API_RETRY_BASE_WAIT_S * (attempt + 1)
                    logger.debug(f"API 调用失败 (尝试 {attempt + 1}/{max_retries})，{wait}s 后重试: {e}")
                time.sleep(wait)
    raise last_error


# ========== 数据缓存 ==========

FIN_CACHE_DIR = CACHE_DIR / "fin"
FIN_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(key: str) -> Path:
    """支持多层级子目录的缓存路径，如 fin/600519 或 hsj/sh/600000/180

    安全：禁止包含 .. 或绝对路径，防止路径遍历攻击
    """
    # 防止路径遍历：禁止 .. 或绝对路径分量
    if ".." in key or key.startswith("/") or key.startswith("~"):
        raise ValueError(f"Invalid cache key: {key}")
    if "/" in key:
        parts = key.split("/")
        subdir = parts[0]
        # 再次检查 subdir 不包含 ..
        if ".." in subdir or subdir.startswith("/"):
            raise ValueError(f"Invalid cache key subdir: {subdir}")
        (CACHE_DIR / subdir).mkdir(parents=True, exist_ok=True)
        return CACHE_DIR / (key.replace("/", "_") + ".parquet")
    return CACHE_DIR / f"{key}.parquet"


def _load_cache(key: str, max_age_hours: int = CACHE_TTL["load_default"]) -> pd.DataFrame:
    path = _cache_path(key)
    if not path.exists():
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        path.unlink(missing_ok=True)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except PermissionError as e:
        # P1-2 修复（2026-06-05）：权限错误不删文件（保留待人工诊断）
        logger.warning(f"[data_layer] 缓存文件权限错误 [{key}]: {e}，保留文件")
        return pd.DataFrame()
    except IsADirectoryError as e:
        # 路径是目录（不应该）
        logger.warning(f"[data_layer] 缓存路径是目录 [{key}]: {e}，保留路径")
        return pd.DataFrame()
    except (OSError, IOError) as e:
        # 其他 OSError（文件损坏：pyarrow 解码失败通常抛 OSError）
        logger.warning(f"[data_layer] 缓存文件损坏 [{key}]: {e}，删除并重试")
        path.unlink(missing_ok=True)
        return pd.DataFrame()
    except Exception as e:
        # P1-2 修复：未知错误（如 KeyError）不删文件
        # 可能是代码 bug 而非文件损坏，保留文件供诊断
        logger.warning(
            f"[data_layer] 缓存读取失败 [{key}] 但保留文件: {type(e).__name__}: {e}"
        )
        return pd.DataFrame()


def _save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


def clean_cache(max_age_hours: int = CACHE_TTL["clean_age_hours"], max_total_mb: int = CACHE_TTL["clean_max_total_mb"]):
    """清理过期缓存 + LRU 淘汰"""
    if not CACHE_DIR.exists():
        return 0
    now = datetime.now()
    deleted = 0
    total_size = 0
    alive_files = []
    for path in CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix not in (".parquet", ".json"):
            continue
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime)
            # P1-1 修复（2026-06-07）：LRU 应按 atime 排序
            # Linux/macOS 默认 atime 在读文件时更新，Windows 也支持
            # 某些挂载点 (noatime) atime 不会更新 → 用 max(atime, mtime) 兜底
            atime = datetime.fromtimestamp(max(stat.st_atime, stat.st_mtime))
            size = stat.st_size
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours > max_age_hours:
                path.unlink(missing_ok=True)
                deleted += 1
            else:
                total_size += size
                alive_files.append((path, atime, size))
        except Exception as e:
            logger.debug(f"[data_layer] 缓存文件处理失败: {e}")
            continue
    max_bytes = max_total_mb * 1024 * 1024
    if total_size > max_bytes:
        # 按 atime（访问时间）升序排序，删最久未访问的
        alive_files.sort(key=lambda x: x[1])
        for path, _, size in alive_files:
            path.unlink(missing_ok=True)
            total_size -= size
            deleted += 1
            if total_size <= max_bytes:
                break
    if deleted > 0:
        logger.info(f"缓存清理: 删除 {deleted} 个文件")
    return deleted


# ========== 基本面数据获取 ==========

_financial_cache = get_financial_cache()  # 统一缓存实例

# 资金流向进程内缓存：扫描时 5 个策略 + ML 富集循环会对同一只股票重复调用
# get_stock_fund_flow，原函数无任何缓存，被全局 3/sec 限流放大成 5+ 分钟瓶颈。
# 这里做进程内字典缓存（单次扫描内去重）+ 磁盘缓存（跨扫描复用）。
_fundflow_cache: Dict[str, dict] = {}
_fundflow_cache_lock = threading.Lock()

# ADR-0016（2026-11-14）：资金流上游全局熔断器。
# 上游（东方财富/腾讯估算/akshare 兜底）全源失败连续达阈值后，短 TTL 内直接返回
# 零默认值，不再逐股烧重试退避——源拒连时单次未缓存调用实测 ~13.2s（重试
# 2s/4s backoff × 连接尝试），300 股批量场景按股票数放大即复现「5+ 分钟」级瓶颈。
# 语义边界：
# - 返回值形状与降级语义不变：熔断期间返回的零默认值与既有「所有源失败」路径一致，
#   不新增任何「假数据」；熔断只裁剪重复的失败尝试（成本），不改成功路径。
# - 失败信号 = _fetch_fund_flow 显式抛 FundFlowUnavailableError（全源异常）；
#   真实取到的零值（停牌/零流）正常返回、不计失败、不触发熔断。
# - 不写任何缓存（进程内/磁盘均不落「熔断标记」），避免把「上游不可达」持久化
#   成缓存内容（P-80 教训：测试基础设施都不该写生产缓存）。
# - 到期后半开：recovery_timeout 后下一次调用真实探测，成功即闭合、失败重开。
_FUND_FLOW_BREAKER = CircuitBreaker(failure_threshold=3, recovery_timeout=300)


class FundFlowUnavailableError(Exception):
    """资金流全部数据源获取失败（东财/腾讯估算 + akshare 兜底均异常）。"""


def _to_float(v) -> Optional[float]:
    """将任意值转为 float，自动剥离千分位逗号和中文字符（亿/万）。

Returns:
    转换成功返回 float，失败或输入为 None 返回 None。"""
    return to_float(v, strip_grouping=True, strip_cn_units=True)


def _get_financial_data(code: str, as_of_date: Optional[str] = None) -> Optional[pd.Series]:
    """获取个股基本面数据（PE/PB/市值/质押率等）

    数据源优先级：
    1. FinancialCache（统一缓存，内存5min + 磁盘7天）
    2. 腾讯财经（PE/PB/市值）—— 直连，不依赖第三方
    3. akshare（PE/PB/市值）—— 备用
    4. 东方财富财报（毛利率/经营现金流/负债率/增长率）

    缓存：5分钟内存LRU + 7天 parquet 持久化（通过 FinancialCache 统一管理）

    §4.2 历史集成 TODO (2026-06-08, lib-incremental-audit §4.1):
    - 当前 4 数据源 (腾讯/akshare/东方财富/baostock) 都是实时拉取
    - backtest 走此路径 = 前视偏差 (Item 12 P0 #1 raise NotImplementedError 防误用)
    - 真正历史快照需 akshare/baostock 历史 API (multi-day 重构, ~1-2 周)
    - 集成完成后此函数可在 as_of_date != today 时自动走历史 API

    Args:

    Args:
        code: 股票代码（如 "600519"）
        as_of_date: 历史快照日期（YYYY-MM-DD）。**backtest 必须传入**
                   防止 look-ahead bias（拉取的是 T 日实时数据，但用作 T-1 日训练特征）

    P0 #1 修复 (2026-06-08, lib-incremental-audit §2.1):
    - 旧实现只 warning as_of_date 缺失, 实际仍拉 T 日实时数据
    - backtest 走此路径 = 前视偏差（P1-2 修复"待实现"未实现）
    - 修复: as_of_date 必传, 否则 raise NotImplementedError (强制 caller 传参)
    - 实时扫描路径 (scanner.py) 不传 as_of_date → caller 改用 get_stock_fundamentals_lite()
    - 真正历史快照实现需 akshare 历史接口, 本次仅"stamp"防止误用
    """
    if as_of_date is None:
        # P0 #1 修复 (2026-06-08): 强制 raise 替代 warning
        # 旧 warning 被忽略导致 backtest 误用, 现在硬阻断
        # 实时扫描改用 get_stock_fundamentals_lite() (无 as_of_date 走实时)
        # backtest 必传 as_of_date='YYYY-MM-DD'
        raise NotImplementedError(
            f"_get_financial_data({code}) 必须传 as_of_date, "
            f"防止 backtest 前视偏差。实时扫描请改用 get_stock_fundamentals_lite() (走实时, 无 as_of_date)"
        )
    # 1. 缓存检查（优先复用 get_stock_fundamentals 的结果）
    cached = _financial_cache.get(code)
    if cached is not None:
        # 从统一 dict 格式转换为 _get_financial_data 约定的 pd.Series
        prof = cached.get("profitability") or {}
        solv = cached.get("solvency") or {}
        growth = cached.get("growth") or {}
        return pd.Series({
            "pe": None, "pb": None, "market_cap": None,
            "gross_margin": prof.get("gross_margin"),
            "revenue_growth": growth.get("revenue_growth"),
            "net_income_growth": growth.get("net_income_growth"),
            "operating_cash_flow": None,
            "_roe": prof.get("ROE"),
            "_debt_to_asset": solv.get("debt_to_asset"),
            "_source": cached.get("_source"),
        })

    # 2. 腾讯 PE/PB/市值（直连，不走缓存）
    data = pd.Series({
        "pe": None, "pb": None, "market_cap": None,
        "pledge_ratio": None, "net_assets": None,
        "revenue_growth": None, "prev_revenue_growth": None,
        "gross_margin": None, "operating_cash_flow": None,
    })

    try:
        from lib.data.data_ashare import get_tencent_pe_pb
        tencent = get_tencent_pe_pb(code)
        if tencent.get("pe"):
            data["pe"] = tencent["pe"]
        if tencent.get("pb"):
            data["pb"] = tencent["pb"]
        if tencent.get("market_cap"):
            data["market_cap"] = tencent["market_cap"]
    except Exception as e:
        logger.debug(f"[data_layer] 腾讯 PE/PB 获取失败 {code}: {e}")

    # 3. akshare 兜底（PE/PB/市值）
    try:
        info_df = _retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is not None and not info_df.empty and "item" in info_df.columns and "value" in info_df.columns:
            info = dict(zip(info_df["item"].astype(str), info_df["value"]))
            if data.get("pe") is None:
                data["pe"] = _to_float(info.get("市盈率-动态"))
            if data.get("pb") is None:
                data["pb"] = _to_float(info.get("市净率"))
            if data.get("market_cap") is None:
                data["market_cap"] = _to_float(info.get("总市值"))
    except Exception as e:
        logger.debug(f"[data_layer] akshare 基本面获取失败 {code}: {e}")

    # 4. 东方财富财报（毛利率 + 经营现金流 + 负债率 + 增长率）
    try:
        from lib.data.data_eastmoney_fin import get_em_financial
        em_data = get_em_financial(code)
        if em_data:
            if data.get("gross_margin") is None and em_data.get("gross_margin") is not None:
                data["gross_margin"] = em_data["gross_margin"]
            ocf = em_data.get("ocf_per_share")
            if ocf is not None:
                data["operating_cash_flow"] = ocf  # 每股经营现金流（元），用于 ocf_healthy 判断
            if em_data.get("revenue_growth") is not None:
                data["revenue_growth"] = em_data["revenue_growth"]
    except Exception as e:
        logger.debug(f"[data_layer] 东方财富财报获取失败 {code}: {e}")

    return data


# ========== 日线数据获取 ==========

def _fetch_from_tdx_local(code: str, days: int) -> pd.DataFrame:
    """从本地 TDX .day 文件读取日线（前复权优先，不复权兜底，取最新）

    前复权文件：sh/lday_4/, sz/lday_4/
    不复权文件：sh/lday/, sz/lday/

    当前复权数据过期时，自动 fallback 到不复权数据（取更新更晚的那个）。
    """
    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    code = code.strip().zfill(6)
    # 用 helper 替代手写市场判断（修复 bj 静默丢失：旧代码 ("0","3","2","4","8") 把 4/8 开头都归 sz）
    market_str = detect_market(code)
    if market_str == "unknown":
        return pd.DataFrame()

    # 调用时重读 env（market_utils 同款约定，保测试 monkeypatch 语义）
    tdx_path = os.environ.get("TDX_DATA_PATH", _CFG_TDX_DATA_PATH)
    reader = TdxDailyBarReader()

    def _read_tdx_file(file_path: str) -> pd.DataFrame:
        """读取单个 .day 文件并标准化"""
        df = reader.get_df(file_path)
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        col_map = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["date", "open", "high", "low", "close", "volume"]].copy()

    # 读取两个数据源
    qfq_path = Path(tdx_path) / market_str / "lday_4" / f"{market_str}{code}.day"
    hfq_path = Path(tdx_path) / market_str / "lday" / f"{market_str}{code}.day"

    df_qfq = pd.DataFrame()
    df_hfq = pd.DataFrame()

    if qfq_path.exists():
        try:
            df_qfq = _read_tdx_file(str(qfq_path))
        except Exception as e:
            logger.debug(f"[data_layer] TDX 前复权文件读取失败 [{code}]: {e}")

    if hfq_path.exists():
        try:
            df_hfq = _read_tdx_file(str(hfq_path))
        except Exception as e:
            logger.debug(f"[data_layer] TDX 不复权文件读取失败 [{code}]: {e}")

    # 选择更新更晚的数据源（前复权优先，但不复权更新更快时 fallback）
    if df_qfq.empty and df_hfq.empty:
        return pd.DataFrame()

    if df_qfq.empty:
        df = df_hfq
    elif df_hfq.empty:
        df = df_qfq
    else:
        qfq_date = df_qfq["date"].max()
        hfq_date = df_hfq["date"].max()
        # 前复权数据足够新（>= 不复权）时优先使用前复权
        if qfq_date >= hfq_date:
            df = df_qfq
        else:
            # 不复权数据更新，fallback
            logger.info(f"[data_layer] TDX 前复权过期 [{code}]: qfq={qfq_date} < hfq={hfq_date}，使用不复权数据")
            df = df_hfq

    if len(df) > days:
        df = df.tail(days)
    return df


def get_stock_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取个股日线（多源自动切换，带缓存）

    委托 data_source_registry.get_stock_hist_multi_source() 实现多源切换（ADR-0017，
    2026-11-14 本地 TDX 文件源摘到尾部）：
    baostock → tdx(公共服务器) → adata → westock → tushare → akshare
    → tdx_local_qfq → tdx_local
    每个数据源有独立熔断器，失败自动切换下一个。
    """
    cache_key = f"hist/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached

    try:
        df = get_stock_hist_multi_source(code, days=days, source="auto")
        if not df.empty:
            _save_cache(cache_key, df)
        return df
    except AllDataSourcesFailedError as e:
        logger.warning(f"get_stock_hist({code}) 所有数据源失败: {e}")
        return pd.DataFrame()
    except Exception as e:
        logger.debug(f"get_stock_hist({code}) 多源失败: {e}")
        return pd.DataFrame()


# 数据源熔断逻辑已移至 lib/data_source_registry.py（imports 已在文件顶部）


# ========== 指数/板块日线（本地 TDX 优先）==========

def get_index_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取指数/板块日线（本地 TDX 优先，网络兜底）

    Args:
        code: 指数代码，如 "000001"（上证）、"399001"（深证）、"880001"（板块）
        days: 获取天数
    """
    from lib.data.data_tdx import get_index_local
    cache_key = f"index/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    # 本地优先
    df = get_index_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
        return df
    # 网络兜底
    try:
        from lib.data.data_tdx import get_index_daily as _get_index_daily_net
        df = _get_index_daily_net(code, days)
        if not df.empty:
            _save_cache(cache_key, df)
        return df
    except Exception as e:
        logger.debug(f"[data_layer] 指数日线获取失败 [{code}]: {e}")
        return pd.DataFrame()


def get_ggt_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取港股通日线（本地文件）"""
    from lib.data.data_tdx import get_ggt_local
    cache_key = f"ggt/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_ggt_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 沪股通日线（hsj）==========

def get_hsj_hist(code: str, market: str = "sh", days: int = 180) -> pd.DataFrame:
    """获取沪股通日线（本地文件）"""
    from lib.data.data_tdx import get_hsj_local
    cache_key = f"hsj/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_hsj_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_option_hist(code: str, market: str = "sh", days: int = 180) -> pd.DataFrame:
    """获取期权日线（本地文件）"""
    from lib.data.data_tdx import _fetch_option_local
    cache_key = f"option/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_option_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_fund_hist(code: str, market: str = "open", days: int = 180) -> pd.DataFrame:
    """获取基金净值日线（本地文件）"""
    from lib.data.data_tdx import _fetch_fund_local
    cache_key = f"fund/{market}/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_fund_local(code, market, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_macro_hist_local(indicator: str = "CPI", days: int = 30) -> pd.DataFrame:
    """获取宏观指标历史（本地 TDX 文件）

    Args:
        indicator: 指标名，如 "CPI", "GDP", "SHIBOR_1W", "SLF7D"
        days: 最大记录数（宏观数据通常为月度/季度，优先返回最新数据）
    """
    from lib.data.data_tdx import _fetch_macro_local
    cache_key = f"macro/{indicator}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.macro_max_age_hours)
    if not cached.empty:
        return cached
    df = _fetch_macro_local(indicator, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_bj_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取北交所日线（本地文件）"""
    from lib.data.data_tdx import get_bj_local
    cache_key = f"bj/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_bj_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 新三板日线（xsb）==========

def get_xsb_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取新三板日线（本地文件）

    注意：新三板流动性较差，仅作数据完整性保留。

    Args:
        code: 股票代码（6位，如 "400001"）
        days: 获取天数
    """
    from lib.data.data_tdx import get_xsb_local
    cache_key = f"xsb/{code}/{days}"
    cached = _load_cache(cache_key, max_age_hours=get_config().cache.hist_max_age_hours)
    if not cached.empty:
        return cached
    df = get_xsb_local(code, days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


# ========== 实时行情 ==========

def get_realtime_quotes(force: bool = False) -> pd.DataFrame:
    """获取实时行情（多源：pytdx 本地 → akshare 兜底）。

    优先 akshare 全市场 spot（sina，一次调用 ~5.5k 行），失败回退 pytdx 公共行情服务器。
    盘中时段缓存 5 分钟，盘后缓存 30 分钟（force=True 跳过缓存）。

    Args:
        force: True = 跳过缓存强制刷新。

    Returns:
        DataFrame，列名含 代码/名称/最新价/开盘价/最高价/最低价/成交量/成交额/涨跌幅。
        失败返回空 DataFrame。

    源优先级说明（ADR-0017，2026-11-14）：akshare 升为主源——① 全市场一次拉齐
    （pytdx 公共行情服务器只返回 ~28 只样本股，量价突破/多因子选股等市场级策略
    覆盖面不足）；② 用户已删除本地 TDX 客户端与 vipdoc 目录，源栈去 TDX 化；
    pytdx 走的是公共行情服务器（TDX_HQ_SERVERS），与本地客户端无关，保留为兜底。
    """
    # 扫描期间用缓存（避免每个策略函数重复获取阻塞 30s+）
    # P1-4 修复（2026-06-05）：盘中缩短缓存到 5 分钟，避免盘中价格过期
    # 判断：当前小时在 9:00-15:00 之间 → 缩短缓存
    from datetime import datetime
    now = datetime.now()
    is_market_hours = 9 <= now.hour < 15  # A 股交易时段 9:30-15:00
    cache_minutes = 5 if is_market_hours else 30  # 盘中 5min，盘后 30min
    if force:
        cache_minutes = 0
    cached = _load_cache("realtime/realtime", max_age_hours=cache_minutes / 60)
    if not cached.empty:
        return cached

    # 1. akshare 全市场 spot（主源，ADR-0017：全市场覆盖 + 成交量列契约满足）
    try:
        df = _retry_api_call(ak.stock_zh_a_spot)
        if not df.empty:
            _save_cache("realtime/realtime", df)
            return df
        logger.warning("akshare 全市场行情返回空，回退 pytdx 公共行情服务器")
    except Exception as e:
        logger.warning(f"akshare 全市场行情获取失败，回退 pytdx 公共行情服务器: {e}")

    # 2. 回退 pytdx 公共行情服务器（TDX_HQ_SERVERS，与本地通达信客户端无关；
    #    仅返回 REALTIME_SAMPLE_CODES ~28 只样本股）
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        servers = TDX_HQ_SERVERS
        connected = False
        # P1-1 修复（2026-06-05）：不再静默吞所有异常
        # 每次连接失败打 warning log，让运维能诊断限流/网络问题
        last_error = None
        for host, port in servers:
            try:
                api.connect(host, port, time_out=TDX_CONNECT_TIMEOUT_S)
                connected = True
                break
            except Exception as e:
                last_error = e
                logger.warning(f"pytdx 连接失败 {host}:{port}: {e}")
                continue
        if not connected and last_error is not None:
            logger.warning(f"pytdx 所有服务器连接失败，最后错误: {last_error}")
        if connected:
            # 用 HS300 成分股 + 已知活跃股票作为样本（避免遍历全部 A 股）
            sample_codes = REALTIME_SAMPLE_CODES
            quotes = api.get_security_quotes(sample_codes)
            api.disconnect()
            if quotes:
                df = pd.DataFrame(quotes)
                col_map = {"code": "代码", "name": "名称", "price": "最新价",
                           "open": "开盘价", "high": "最高价", "low": "最低价",
                           "vol": "成交量", "amount": "成交额"}
                df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
                if "最新价" in df.columns and "开盘价" in df.columns:
                    df["涨跌幅"] = df.apply(
                        lambda r: ((r.get("最新价", 0) - r.get("开盘价", 0)) / r.get("开盘价", 1) * 100)
                        if r.get("开盘价", 0) > 0 else 0, axis=1
                    )
                logger.info(f"[TDX] 实时行情获取成功: {len(df)} 只")
                _save_cache("realtime/realtime", df)
                return df
    except Exception as e:
        logger.debug(f"[TDX] 实时行情获取失败: {e}")

    logger.error("实时行情全部源失败（akshare 全市场 + pytdx 公共服务器）")
    return pd.DataFrame()


def get_market_sentiment() -> Optional[float]:
    """获取市场情绪（涨跌比），失败时返回 None"""
    try:
        df = get_realtime_quotes()
        if df.empty:
            logger.warning("[data_layer] 实时行情为空，市场情绪未知")
            return None
        up_count = (df["涨跌幅"] > 0).sum()
        down_count = (df["涨跌幅"] < 0).sum()
        total = up_count + down_count
        if total == 0:
            logger.warning("[data_layer] 涨跌计数为0，市场情绪未知")
            return None
        ratio = (up_count - down_count) / total
        return max(-1, min(1, ratio))
    except Exception as e:
        logger.warning(f"[data_layer] 市场情绪获取失败: {e}")
        return None


# ========== 资金流向 ==========

def get_stock_fund_flow(code: str) -> dict:
    """获取资金流向数据（带进程内 + 磁盘缓存，跨策略去重）

    数据源优先级：
    1. 东方财富（优先）
    2. 腾讯成交量估算（次级）
    3. akshare（兜底）

    Returns:
        dict with keys: main_net_ratio, net_inflow_5d
        全源失败 / 熔断开启时返回全零默认值（降级语义与「所有源失败」一致），
        本函数不向上抛异常。

    性能说明：扫描时 5 个策略 + ML 富集循环会对同一只股票重复调用本函数，
    而原函数无任何缓存，导致被全局 3/sec 限流放大成 5+ 分钟瓶颈。
    这里加进程内字典缓存（单次扫描内去重）+ 磁盘缓存（跨扫描复用）。

    熔断说明（ADR-0016，2026-11-14）：上游连续 3 次全源失败后熔断 300s，
    期间直接返回零默认值——源拒连时单次未缓存调用实测 ~13.2s（重试 backoff），
    300 股批量场景按股票数放大即复现「5+ 分钟」级瓶颈，熔断把它压到约
    3 次尝试的成本。真实零值（停牌/零流）与全源失败由异常路径区分，
    前者不计失败、不触发熔断。
    """
    # 1) 进程内缓存（同进程内重复调用直接命中，O(1)）
    with _fundflow_cache_lock:
        cached = _fundflow_cache.get(code)
    if cached is not None:
        return cached
    # 2) 磁盘缓存（跨进程/跨扫描复用，限流更友好）
    try:
        disk = _load_cache(f"fundflow/{code}", max_age_hours=CACHE_TTL.get("fund_flow", 1))
        if not disk.empty:
            res = {
                "main_net_ratio": float(disk.iloc[0].get("main_net_ratio", 0) or 0),
                "net_inflow_5d": float(disk.iloc[0].get("net_inflow_5d", 0) or 0),
            }
            with _fundflow_cache_lock:
                _fundflow_cache[code] = res
            return res
    except Exception:
        pass

    result = None
    # 3) 全局熔断（ADR-0016）：开启期间直接走零默认值（与「所有源失败」降级一致），
    #    不再逐股重试；到期后半开探测一次，成功闭合、失败重开。
    try:
        result = _FUND_FLOW_BREAKER.call(_fetch_fund_flow, code)
    except CircuitOpenError:
        logger.debug("[data_layer] 资金流熔断中，返回零默认值（不写缓存）")
        return {"main_net_ratio": 0, "net_inflow_5d": 0}
    except FundFlowUnavailableError as e:
        logger.debug(f"[data_layer] 资金流全源失败 {code}: {e}")
        return {"main_net_ratio": 0, "net_inflow_5d": 0}

    # 仅缓存成功取数（含 legitimately 为零的股票），不缓存「全部源失败」的默认值，
    # 以免掩盖瞬时故障、阻断后续重试。
    if result.get("main_net_ratio", 0) != 0 or result.get("net_inflow_5d", 0) != 0:
        with _fundflow_cache_lock:
            _fundflow_cache[code] = result
        try:
            _save_cache(f"fundflow/{code}", pd.DataFrame([result]))
        except Exception:
            pass
    return result


def _fetch_fund_flow(code: str) -> dict:
    """实际拉取资金流向（不含缓存），见 get_stock_fund_flow。"""
    # 1. 东方财富 + 腾讯估算（统一入口）
    try:
        from lib.data.data_fund_flow import get_fund_flow_simple
        result = get_fund_flow_simple(code)
        # 只要有有效数据就返回
        if result.get("main_net_ratio", 0) != 0 or result.get("net_inflow_5d", 0) != 0:
            return result
    except Exception as e:
        logger.debug(f"[data_layer] 资金流向获取失败 {code}: {e}")

    # 2. akshare 兜底
    try:
        market = detect_market(code) if detect_market(code) in ("sh", "sz") else "sz"
        df = _retry_api_call(ak.stock_individual_fund_flow, stock=code, market=market)
        if not df.empty:
            latest = df.iloc[-1]
            main_net_ratio = 0.0
            for col in df.columns:
                if "主力净流入-净占比" in str(col):
                    main_net_ratio = _safe_float(latest[col]) / 100.0
                    break
            net_inflow_5d = 0.0
            recent = df.tail(NET_INFLOW_WINDOW_DAYS)
            for col in df.columns:
                if "主力净流入-净额" in str(col):
                    net_inflow_5d = recent[col].sum()
                    break
            return {"main_net_ratio": main_net_ratio, "net_inflow_5d": net_inflow_5d}
    except Exception as e:
        logger.debug(f"[data_layer] akshare 资金流向失败 {code}: {e}")

    # ADR-0016：全源失败显式抛错（供熔断器计数），不再静默返回全零——
    # 真实取到的零值（停牌/零流）在上方正常返回，两者由此区分。
    raise FundFlowUnavailableError(
        f"fund_flow all sources failed for {code} (eastmoney/tencent + akshare)"
    )


def _batch_fund_flow(codes: list) -> dict:
    """批量获取资金流向（一次 API 调用，覆盖整个市场）。

    Args:
        codes: 股票代码列表（6 位字符串）。

    Returns:
        {code: {"main_net_ratio": float, "net_inflow_5d": float}, ...}
        未命中的 code 保持默认值 {"main_net_ratio": 0, "net_inflow_5d": 0}。
    """
    result = {c: {"main_net_ratio": 0, "net_inflow_5d": 0} for c in codes}
    try:
        df = _retry_api_call(ak.stock_fund_flow_individual, symbol="即时")
        if df.empty:
            return result
        code_col = None
        for col in df.columns:
            if "代码" in str(col):
                code_col = col
                break
        if code_col is None:
            return result
        net_col = None
        amount_col = None
        for col in df.columns:
            if "净额" in str(col):
                net_col = col
            if "成交额" in str(col):
                amount_col = col
        # 向量化处理，避免逐行iterrows
        df[code_col] = df[code_col].astype(str)
        if net_col and amount_col:
            df[net_col] = pd.to_numeric(df[net_col], errors="coerce").fillna(0)
            df[amount_col] = pd.to_numeric(df[amount_col], errors="coerce").fillna(1)
            # 按股票代码分组求和净额
            grouped = df.groupby(code_col, as_index=False).agg({
                net_col: "sum",
                amount_col: "sum"
            })
            # 向量化处理，避免 iterrows
            flow_map = {}
            if not grouped.empty and code_col in grouped.columns:
                codes = grouped[code_col].astype(str).tolist()
                net_vals = grouped[net_col].tolist()
                amounts = grouped[amount_col].tolist()
                for c, net_val, total_amount in zip(codes, net_vals, amounts):
                    flow_map[c] = {
                        "main_net_ratio": net_val / total_amount if total_amount > 0 else 0,
                        "net_inflow_5d": net_val,
                    }
        for code in codes:
            if code in flow_map:
                result[code] = flow_map[code]
    except Exception as e:
        logger.debug(f"批量资金流向获取失败: {e}")
    return result


def _batch_get_news_signals(codes: List[str]) -> Dict[str, dict]:
    """批量并行获取新闻/事件信号（解决 N+1 查询问题）

    Args:
        codes: 股票代码列表

    Returns:
        Dict[code -> signals dict]，每个 dict 包含 major_positive/earnings_positive/institutional_buy/no_negative
    """
    from lib.data.news_signal import get_news_signals as _get_news_signals
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_get_news_signals, code): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as e:
                logger.debug(f"[data_layer] 新闻信号获取失败 [{code}]: {e}")
                results[code] = {
                    "major_positive": False,
                    "earnings_positive": False,
                    "institutional_buy": False,
                    "no_negative": True,
                }
    return results


# 新闻/业绩信号已移至 lib/signal_fetcher.py（imports 已在文件顶部）


# ========== 专项数据获取（期权/债券/宏观） ==========

def get_options_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取期权日线历史（通过 pytdx）"""
    try:
        from lib.data.data_options import get_options_daily
        return get_options_daily(code, days=days)
    except Exception as e:
        logger.debug(f"获取期权 {code} 失败: {e}")
        return pd.DataFrame()


def get_cb_hist(code: str, days: int = 180) -> pd.DataFrame:
    """获取可转债历史行情"""
    try:
        from lib.data.data_bond import get_cb_history
        return get_cb_history(code)
    except Exception as e:
        logger.debug(f"获取可转债 {code} 失败: {e}")
        return pd.DataFrame()


def get_repo_hist(code: str = "204001", days: int = 30) -> pd.DataFrame:
    """获取交易所回购历史"""
    try:
        from lib.data.data_bond import get_repo_history
        return get_repo_history(code=code, days=days)
    except Exception as e:
        logger.debug(f"获取回购 {code} 失败: {e}")
        return pd.DataFrame()


def get_macro_hist(indicator: str = "shibor", days: int = 30) -> pd.DataFrame:
    """获取宏观指标历史

    Args:
        indicator: shibor | lpr | yield | gold | oil | panel
        days: 天数
    """
    try:
        from lib.data.data_macro import (
            get_shibor, get_lpr, get_china_yield,
            get_gold, get_crude_oil, get_macro_panel,
        )
    except ImportError:
        return pd.DataFrame()

    try:
        if indicator == "shibor":
            return get_shibor(days=days)
        elif indicator == "lpr":
            return get_lpr(days=days)
        elif indicator == "yield":
            return get_china_yield(days=days)
        elif indicator == "gold":
            return get_gold(days=days)
        elif indicator == "oil":
            return get_crude_oil(days=days)
        elif indicator == "panel" or indicator == "all":
            return get_macro_panel(days=days)
        else:
            logger.warning(f"未知宏观指标: {indicator}")
            return pd.DataFrame()
    except Exception as e:
        logger.debug(f"获取宏观指标 {indicator} 失败: {e}")
        return pd.DataFrame()


# ========== 市场统计数据（ScJyData_zbca）==========

def get_market_stats(days: int = 30) -> pd.DataFrame:
    """获取市场统计面板（ScJyData_zbca）

    包含：期货持仓(IH5/IF6/IC7)、担保品(DBZJ)、龙虎榜(LHB)、涨跌停(ZT)等指标
    """
    from lib.data.data_scjy import get_market_stats as _get
    cache_key = f"scjy/market_stats/{days}"
    cached = _load_cache(cache_key, max_age_hours=24)
    if not cached.empty:
        return cached
    df = _get(days)
    if not df.empty:
        _save_cache(cache_key, df)
    return df


def get_latest_market_stats() -> dict:
    """获取最新市场统计（dict 格式）"""
    from lib.data.data_scjy import get_latest_stats as _get_latest
    cache_key = "scjy/latest"
    cached = _load_cache(cache_key, max_age_hours=24)
    if not cached.empty:
        return {col: cached.iloc[0][col] for col in cached.columns}
    result = _get_latest()
    if result:
        # 缓存 dict 格式（转单行 DataFrame）
        _save_cache(cache_key, pd.DataFrame([result]))
    return result


# ========== 高频Tick数据（tdxgp）==========

def get_gp_data(code: str, market: str = "sz",
                record_types: list = None) -> pd.DataFrame:
    """获取高频Tick数据

    Args:
        code: 股票代码（6位，如 "000001"）
        market: 市场 sh/sz/bj
        record_types: 可选，只解析指定类型（如 [1, 47]）
                     1 = 年报/季报财务（0x01）
                     47 = 最近日线（0x2F）
                     3 = 日线指标A（0x03）
    """
    from lib.data.data_tdx_gp import get_gp_data as _get_gp
    return _get_gp(code, market, record_types=record_types)


# ========== 财务报表（cw/gpcw）==========

def get_stock_financial(code: str) -> pd.DataFrame:
    """获取股票财务数据

    数据来源：TDX 本地 gpcw*.dat 财务报表
    返回：field_index, value_main, value_aux, raw_bytes

    注意：字段含义（收入/利润/EPS等）尚未完全解码。
    具体含义参考 lib/data_tdx_fin.py 头部注释。
    """
    from lib.data.data_tdx_fin import GpcwReader
    cache_key = f"fin/cw/{code}"
    cached = _load_cache(cache_key, max_age_hours=CACHE_TTL["financial_default"])  # 财务报表季度更新，缓存7天
    if not cached.empty:
        return cached

    reader = GpcwReader()
    df = reader.read_stock(code)
    if not df.empty:
        _save_cache(cache_key, df)
    return df

def get_financial_codes() -> list:
    """获取所有有财务数据的股票代码"""
    from lib.data.data_tdx_fin import GpcwReader
    reader = GpcwReader()
    return reader.read_all_codes()

def get_financial_file_info() -> dict:
    """获取当前财务数据文件信息"""
    from lib.data.data_tdx_fin import GpcwReader
    reader = GpcwReader()
    return reader.get_file_info()


# ========== 板块映射（infoharbor_block）==========

_block_cache = None
_stock_to_blocks_cache = None


def _load_block_mapping():
    """加载 infoharbor_block.dat 板块映射"""
    global _block_cache, _stock_to_blocks_cache
    if _block_cache is not None:
        return

    from pathlib import Path
    block_file = TDX_REF_PATH / "infoharbor_block.dat"
    if not block_file.exists():
        _block_cache = {}
        _stock_to_blocks_cache = {}
        return

    blocks = {}
    stock_to_blocks = {}
    current_block = None

    with open(block_file, "r", encoding="gbk", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("#"):
                parts = line.split(",")
                current_block = parts[0][1:]  # remove #
                blocks[current_block] = []
            elif current_block and line:
                for item in line.split(","):
                    item = item.strip()
                    if "#" in item:
                        code = item.split("#")[1]
                        blocks[current_block].append(code)
                        if code not in stock_to_blocks:
                            stock_to_blocks[code] = []
                        stock_to_blocks[code].append(current_block)

    _block_cache = blocks
    _stock_to_blocks_cache = stock_to_blocks


def get_stock_blocks(code: str) -> List[str]:
    """获取股票所属板块列表"""
    _load_block_mapping()
    return _stock_to_blocks_cache.get(code, [])


def get_block_stocks(block_name: str) -> List[str]:
    """获取板块内所有股票代码"""
    _load_block_mapping()
    return _block_cache.get(block_name, [])


def get_all_blocks() -> Dict[str, List[str]]:
    """获取所有板块映射"""
    _load_block_mapping()
    return _block_cache


# ========== PE/PB/市值数据（tdxstat.cfg）==========

_tdxstat_cache = None


def _load_tdxstat():
    """加载 tdxstat.cfg + tdxstat2.cfg PE/PB/市值数据"""
    global _tdxstat_cache
    if _tdxstat_cache is not None:
        return

    from pathlib import Path
    stats = {}

    # tdxstat.cfg: [0]=market [1]=code [2]=div_yield [3]=PE [9]=PE_ttm [10]=PB [11]=total_mv
    # 市值单位：raw/100=亿元；股息率：raw/10=元/股
    stat_file = TDX_REF_PATH / "tdxstat.cfg"
    if stat_file.exists():
        with open(stat_file, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 12:
                    continue
                code = fields[1]
                try:
                    pe = float(fields[3]) if fields[3] else None
                    pb = float(fields[10]) if fields[10] else None
                    div_yield = float(fields[2]) / 10 if fields[2] else None  # raw/10=元/股
                except (ValueError, IndexError):
                    pe = pb = div_yield = None
                stats[code] = {"pe": pe, "pb": pb, "div_yield": div_yield}

    # tdxstat2.cfg: [0]=market [1]=code [2]=date [3]=total_mv [5]=circ_mv
    # 市值单位：raw/100=亿元
    stat2_file = TDX_REF_PATH / "tdxstat2.cfg"
    if stat2_file.exists():
        with open(stat2_file, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                fields = line.strip().split("|")
                if len(fields) < 6:
                    continue
                code = fields[1]
                try:
                    total_mv = float(fields[3]) / 100 if fields[3] else None  # raw/100=亿元
                    circ_mv = float(fields[5]) / 100 if fields[5] else None
                except (ValueError, IndexError):
                    total_mv = circ_mv = None
                if code in stats:
                    stats[code]["total_mv"] = total_mv
                    stats[code]["circ_mv"] = circ_mv
                else:
                    stats[code] = {"total_mv": total_mv, "circ_mv": circ_mv}

    _tdxstat_cache = stats


def get_stock_valuation(code: str) -> Dict:
    """获取股票估值数据（PE/PB/市值）"""
    _load_tdxstat()
    return _tdxstat_cache.get(code, {})


# ========== 基金持仓数据（fundstk.dat）==========

_fundstk_cache = None


def _load_fundstk():
    """加载 fundstk.dat 基金持仓明细"""
    global _fundstk_cache
    if _fundstk_cache is not None:
        return

    from pathlib import Path
    stk_file = TDX_REF_PATH / "fundstk.dat"
    if not stk_file.exists():
        _fundstk_cache = {}
        return

    holdings = {}  # code -> {fund_count, total_weight, total_value, date}
    with open(stk_file, "r", encoding="gbk", errors="replace") as f:
        for line in f:
            parts = line.strip().split("|")
            if len(parts) < 4:
                continue
            code = parts[0]
            date = parts[1]
            fund_count = 0
            total_weight = 0.0
            total_value = 0.0
            for p in parts[3:]:
                if not p.strip():
                    continue
                fields = p.split(",")
                if len(fields) >= 7:
                    fund_count += 1
                    try:
                        total_value += float(fields[4]) if fields[4] else 0
                        total_weight += float(fields[6]) if fields[6] else 0
                    except ValueError:
                        pass
            if fund_count > 0:
                holdings[code] = {
                    "fund_count": fund_count,
                    "total_weight": round(total_weight, 2),
                    "total_value": round(total_value, 2),
                    "date": date,
                }

    _fundstk_cache = holdings


def get_fund_holdings(code: str) -> Dict:
    """获取股票的基金持仓数据"""
    _load_fundstk()
    return _fundstk_cache.get(code, {})


def get_fund_holdings_all() -> Dict[str, Dict]:
    """获取所有股票的基金持仓数据"""
    _load_fundstk()
    return _fundstk_cache


# ========== 除权除息数据（gbbq，pytdx 解密）==========

_gbbq_cache = None


def _load_gbbq():
    """加载 gbbq 除权除息数据（使用 pytdx GbbqReader 解密）"""
    global _gbbq_cache
    if _gbbq_cache is not None:
        return

    from pathlib import Path
    gbbq_file = TDX_REF_PATH / "gbbq"
    if not gbbq_file.exists():
        _gbbq_cache = {}
        return

    try:
        from pytdx.reader import GbbqReader
        reader = GbbqReader()
        df = reader.get_df(str(gbbq_file))
        # Group by (market, code) → sorted list of events
        _gbbq_cache = {}
        for _, row in df.iterrows():
            code = row["code"]
            if code not in _gbbq_cache:
                _gbbq_cache[code] = []
            _gbbq_cache[code].append({
                "date": int(row["datetime"]),
                "category": int(row["category"]),
                "hongli": float(row["hongli_panqianliutong"]),
                "peigujia": float(row["peigujia_qianzongguben"]),
                "songzhuangu": float(row["songgu_qianzongguben"]),
                "peigu_hou": float(row["peigu_houzongguben"]),
            })
        # Sort each stock's events by date
        for code in _gbbq_cache:
            _gbbq_cache[code].sort(key=lambda x: x["date"])
    except Exception as e:
        logging.getLogger(__name__).warning(f"gbbq 加载失败: {e}")
        _gbbq_cache = {}


def get_exright_events(code: str, start_date: int = 0) -> List[Dict]:
    """获取股票的除权除息事件列表

    Args:
        code: 股票代码（如 "600519"）
        start_date: 起始日期（如 20200101），0 表示全部

    Returns:
        list of dicts with keys: date, category, hongli, songzhuangu, etc.
    """
    _load_gbbq()
    events = _gbbq_cache.get(code, [])
    if start_date > 0:
        events = [e for e in events if e["date"] >= start_date]
    return events


def get_dividend_amount(code: str, year: int = 0) -> float:
    """获取股票的年度每股分红总额

    Args:
        code: 股票代码
        year: 年份（如 2025），0 表示最近一年

    Returns:
        每股分红总额（元）
    """
    _load_gbbq()
    events = _gbbq_cache.get(code, [])
    if year > 0:
        events = [e for e in events if e["date"] // 10000 == year and e["category"] == 1]
    else:
        events = [e for e in events if e["category"] == 1]
        if events:
            latest_year = max(e["date"] for e in events) // 10000
            events = [e for e in events if e["date"] // 10000 == latest_year]
    return sum(e["hongli"] for e in events) / 10  # raw/10=元/股
