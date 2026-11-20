"""数据源注册与熔断管理模块

从 data_layer.py 提取的熔断逻辑，负责：
- 多数据源优先级管理
- 每个数据源的独立熔断器
- 数据源自动切换
"""
import pandas as pd
import logging
import threading
from typing import Dict, Optional
from datetime import datetime, timedelta

from lib.data.cache import CircuitBreaker, CircuitOpenError
from lib.data.market_utils import normalize_code

logger = logging.getLogger(__name__)

# ========== 数据源配置 ==========

# ADR-0017（2026-11-14）：用户已删除本地 TDX 客户端与 vipdoc 目录（D:/TDX 不存在），
# tdx_local_qfq / tdx_local 两个本地文件源从优先级头部摘到尾部——保留注册（有 TDX 的
# 环境可用 env TDX_DATA_PATH 重定向恢复），但默认源栈不再每次加载都先撞两下死路径。
# ADR-0027（2026-11-19）：新增两条网页 K 线网关。实测 8 个既有源当日全 0
# （baostock 黑名单 / push2his 不可达 / tushare 无权限 / adata·westock 缺 Node /
# tdx 已删），而新浪与腾讯网关都能返到最近交易日，故置于头部。
# - 默认栈：新浪优先（覆盖沪/深/创业/科创/北交所/指数，不复权）
# - 回测栈：腾讯优先（唯一提供前复权）。新浪**不得**进回测栈——不复权价在除权日
#   会造出假跳空，属于隐蔽的失真源。
DATA_SOURCE_PRIORITY = ["sina_kline", "tencent_kline", "baostock", "tdx", "adata",
                        "westock", "tushare", "akshare", "tdx_local_qfq", "tdx_local"]
DATA_SOURCE_PRIORITY_ADJUSTED = ["tencent_kline", "baostock", "akshare", "tdx",
                                 "adata", "westock", "tushare", "tdx_local_qfq", "tdx_local"]
# 北交所专用：本地 TDX 已删，改由网页网关承担（新浪覆盖 92xxxx，腾讯仅 1 根作兜底）
BJ_SOURCE_PRIORITY = ["sina_kline", "tencent_kline", "tdx_local"]


def _is_bj_stock(code: str) -> bool:
    """判断是否为北交所股票（43/83/87/88/92）——单一口径走 market_utils。

    旧实现用 ``startswith(("4","8"))``，漏掉 92xxxx（北证新号段），
    会让 920000 这类标的落进沪深源栈（ADR-0027 修正）。
    """
    from lib.data.market_utils import detect_market
    c = str(code).strip()
    for p in ("sh", "sz", "bj"):
        if c.lower().startswith(p):
            c = c[2:]
            break
    return detect_market(c) == "bj"


# ADR-0019（2026-11-14）：数据源调用超时——挂起（不抛错、不返回）计入熔断。
# 2026-11-14 周六凌晨实测：BaoStock「网络接收错误」后 socket 无超时挂起，
# 量价突破/多因子的逐股 hist 链路 10+ 分钟无新日志，扫描整体卡死被人工终止。
# 熔断体系缺口：每源熔断只按「抛错次数」计数，挂起既不计失败也不切换——
# 快失败挡得住，慢挂起挡不住。治理 = daemon 线程 + join(timeout)：超时后
# breaker.record_failure() 计入失败（达到阈值即开启，后续调用直接跳过），
# 并抛 SourceCallTimeoutError 让多源循环切下一个源。
# 挂起线程为 daemon：不阻塞解释器退出，等 socket 自行死亡/进程结束回收。
SOURCE_CALL_TIMEOUT_S = 30.0

# P1-1（2026-11-20）：挂起守卫的泄漏可观测计量——超时后 daemon 线程继续跑
# （ADR-0019 D3 接受），此前无任何计数；_hung_threads = 当前挂起未返回的调用数
# （超时 +1，worker 完成后 -1）。
_hang_lock = threading.Lock()
_hung_threads = 0


def get_source_hang_stats() -> Dict[str, int]:
    """源调用挂起计量出口：``{"hung_threads": N}``（0 = 无泄漏在飞）。"""
    with _hang_lock:
        return {"hung_threads": _hung_threads}


class SourceCallTimeoutError(Exception):
    """数据源调用超时（挂起检测，ADR-0019）。"""


def _call_source_with_timeout(breaker: CircuitBreaker, fn, *args, **kwargs) -> pd.DataFrame:
    """带超时的数据源调用：超时计入熔断失败并抛 SourceCallTimeoutError。

    P1-1（2026-11-20）：daemon 超时线程此前完全不可观测（泄漏无计量）——加
    ``_hung_threads`` 计数：超时时 +1、worker 最终完成时 -1，经
    ``get_source_hang_stats()`` 出口暴露（每 +10 打一次 warning）。
    """
    result = {}
    timed_out = {"flag": False}

    def _worker():
        try:
            result["df"] = breaker.call(fn, *args, **kwargs)
        except BaseException as e:  # 含 CircuitOpenError / 数据源内部异常，原样回抛
            result["err"] = e
        finally:
            if timed_out["flag"]:
                with _hang_lock:
                    global _hung_threads
                    _hung_threads -= 1

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(SOURCE_CALL_TIMEOUT_S)
    if t.is_alive():
        timed_out["flag"] = True
        with _hang_lock:
            global _hung_threads
            _hung_threads += 1
            if _hung_threads and _hung_threads % 10 == 0:
                logger.warning(f"[source-timeout] 当前挂起未返回的源调用线程 "
                               f"{_hung_threads} 个（daemon，socket 死亡或进程结束回收）")
        breaker.record_failure()
        raise SourceCallTimeoutError(
            f"{getattr(fn, '__name__', fn)} timed out after {SOURCE_CALL_TIMEOUT_S}s (hang)"
        )
    if "err" in result:
        raise result["err"]
    return result.get("df", pd.DataFrame())


# 每个数据源独立的熔断器
_CIRCUIT_BREAKERS: Dict[str, CircuitBreaker] = {
    src: CircuitBreaker(failure_threshold=5, recovery_timeout=60)
    for src in DATA_SOURCE_PRIORITY
}


def _fetch_from_source(code: str, days: int, source: str, as_of_date: Optional[str] = None) -> pd.DataFrame:
    """从指定数据源获取数据

    P0-10 修复 (2026-06-08, lib-incremental-audit §2.1):
    - 旧实现 tushare/akshare 用 datetime.now() 拉**今日**实时数据
    - 回测环境若不传 as_of_date，会用 T 日行情 = 前视偏差
    - 修复: as_of_date 透传到 tushare/akshare 端点 (其他数据源行为待补)
    - 不传 as_of_date 时仍走 datetime.now()（实时模式，行为不变）

    Args:
        code: 股票代码
        days: 历史天数
        source: 数据源名称
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传。

    Returns:
        DataFrame with columns: date/open/high/low/close/volume
    """
    breaker = _CIRCUIT_BREAKERS.get(source)
    if breaker is None:
        logger.warning(f"未知数据源: {source}")
        return pd.DataFrame()

    # P0-10: 透传 as_of_date 端点
    if as_of_date is not None:
        end_dt = pd.to_datetime(as_of_date)
    else:
        end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days)
    end_str = end_dt.strftime("%Y%m%d")
    start_str = start_dt.strftime("%Y%m%d")

    try:
        if source == "sina_kline":
            from lib.data.data_kline_web import get_sina_kline
            from lib.data.host_health import HOST_SINA_KLINE, call_with_health
            return _call_source_with_timeout(
                breaker, call_with_health, HOST_SINA_KLINE,
                get_sina_kline, code, days=days, as_of_date=as_of_date)
        if source == "tencent_kline":
            from lib.data.data_kline_web import get_tencent_kline
            # host 健康在 data_kline_web 内按两台主机分别计数（ADR-0032）
            return _call_source_with_timeout(
                breaker, get_tencent_kline, code, days=days,
                adjust="qfq", as_of_date=as_of_date)
        if source == "tdx_local_qfq":
            from lib.data.data_tdx import _fetch_qfq_local_file
            return _fetch_qfq_local_file(code, days, "daily", as_of_date)
        if source == "tdx_local":
            from lib.data.data_tdx import _fetch_from_local_file
            return _fetch_from_local_file(code, days, "daily", as_of_date)
        if source == "baostock":
            from lib.data.data_baostock import get_kline as baostock_kline
            return _call_source_with_timeout(breaker, baostock_kline, code, days=days, as_of_date=as_of_date)
        if source == "tdx":
            from lib.data.data_tdx import get_kline as tdx_kline
            return _call_source_with_timeout(breaker, tdx_kline, code, days=days, as_of_date=as_of_date)
        if source == "adata":
            from lib.data.data_adata import get_kline as adata_kline
            return _call_source_with_timeout(breaker, adata_kline, code, days=days, as_of_date=as_of_date)
        if source == "westock":
            from lib.data.data_westock import get_kline as westock_kline
            df = _call_source_with_timeout(breaker, westock_kline, code, period="day", limit=min(days, 2000),
                                       as_of_date=as_of_date)
            if df.empty:
                return pd.DataFrame()
            # P-112：CLI 1.0.4 实测英文表头（date/open/**last**/high/low/volume/...），
            # 旧版中文——`last`→`close` 必须显式映射，否则 6 列校验不过 = 整链白取
            col_map = {"日期": "date", "开盘": "open", "收盘": "close", "last": "close",
                       "最高": "high", "最低": "low", "成交量": "volume"}
            df = df.rename(columns=col_map)
            for c in ["open", "close", "high", "low", "volume"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            # 成交量单位 = 手（2026-11-20 实测 sh600519：westock 42692 vs 新浪
            # 4269153 股，比值恰 100.0，收盘价逐日一致）——与腾讯同口径 ×100 归一到股。
            # 顺带发现：westock 默认 fq=qfq，修活后是第三条独立协议源（评审 P0-2 冗余目标）
            if "volume" in df.columns:
                df["volume"] = df["volume"] * 100.0
            return df
        if source == "tushare":
            from lib.data.data_tushare import get_daily, _to_ts_code
            ts_code = _to_ts_code(code)
            # P0-10: 透传 as_of_date (旧用 datetime.now() 前视)
            df = _call_source_with_timeout(breaker, get_daily, ts_code, start_str, end_str)
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "trade_date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "vol": "volume"
            })
            # 成交量单位 = 手（复核 B3 确认 tushare pro.daily vol 口径；P1-9，2026-11-20）
            # ——各源**就地**归一到股；勿放进共享 _normalize_columns，否则新浪/腾讯
            # （已是股）会被重复 ×100 制造新事故
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100.0
            return df
        if source == "akshare":
            import akshare as ak
            from lib.data.host_health import (HOST_EASTMONEY_PUSH2HIS,
                                              call_with_health)
            # ADR-0025：`stock_zh_a_hist` 走东财 push2his，host 开断期直接返回空帧
            # （不发请求、不占超时线程），由多源循环切下一个源。
            df = _call_source_with_timeout(
                breaker, call_with_health, HOST_EASTMONEY_PUSH2HIS,
                ak.stock_zh_a_hist, symbol=code, period="daily",
                start_date=start_str, end_date=end_str, adjust="qfq")
            if df.empty:
                return pd.DataFrame()
            df = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume",
                "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg"
            })
            # 成交量单位 = 手（东财 push2his 口径，复核 B3 确认；P1-9）——就地 ×100
            # 归一到股，与腾讯/westock 同口径；勿进共享归一层（见 tushare 分支注释）
            if "volume" in df.columns:
                df["volume"] = pd.to_numeric(df["volume"], errors="coerce") * 100.0
            return df
    except CircuitOpenError:
        logger.warning(f"[{source}] 熔断器开启，跳过该数据源")
        return pd.DataFrame()
    except Exception as e:
        logger.debug(f"[{source}] 数据源调用失败: {e}")
        return pd.DataFrame()

    logger.warning(f"未知数据源: {source}")
    return pd.DataFrame()


# 列名标准化映射表
COLUMN_MAPPINGS = {
    # 日期列
    "trade_date": "date",
    "tradeDate": "date",
    "日期": "date",
    # OHLCV 核心列
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "vol": "volume",
    # 扩展列
    "成交额": "amount",
    "turnover": "amount",
    "换手率": "turnover",
    "turnoverRate": "turnover",
    "涨跌幅": "pct_chg",
    "changePct": "pct_chg",
    "pctChg": "pct_chg",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名为标准列名"""
    if df.empty:
        return df

    # 1. 应用所有映射
    rename_map = {}
    for orig, standard in COLUMN_MAPPINGS.items():
        if orig in df.columns and standard not in df.columns:
            rename_map[orig] = standard
    if rename_map:
        df = df.rename(columns=rename_map)

    # 2. 日期格式统一
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    # 3. 数值列类型统一
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover", "pct_chg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


def _validate_dataframe(df: pd.DataFrame, min_rows: int = 10, required_cols: list = None) -> bool:
    """数据完整性校验"""
    if df.empty or len(df) < min_rows:
        return False
    required_cols = required_cols or ["date", "open", "high", "low", "close", "volume"]
    return all(c in df.columns for c in required_cols)


class AllDataSourcesFailedError(Exception):
    """所有数据源均无法获取数据时抛出"""
    def __init__(self, code: str, errors: list):
        self.code = code
        self.errors = errors
        super().__init__(f"所有数据源均无法获取 {code}: {errors}")


def _get_stock_hist_multi_source_online(
    code: str,
    days: int = 180,
    source: str = "auto",
    prefer_adjusted: bool = False,
    as_of_date: Optional[str] = None,
) -> pd.DataFrame:
    """在线多数据源取数（内部实现；对外请用 get_stock_hist_multi_source）。

    统一返回列名: date/open/high/low/close/vol
    所有数据源均失败时抛出 AllDataSourcesFailedError

    P0-10 修复 (2026-06-08): 新增 as_of_date 透传到各数据源 (防回测前视)

    Args:
        code: 股票代码
        days: 历史天数
        source: 数据源，"auto" 表示自动切换
        prefer_adjusted: 是否优先使用前复权数据（回测时应设为 True）
        as_of_date: 时间截断 (YYYY-MM-DD)。回测必传, 实时可省略。
            不传时走 datetime.now() (旧行为)

    Returns:
        DataFrame with columns: date/open/high/low/close/volume
    """
    if source != "auto":
        df = _fetch_from_source(code, days, source, as_of_date=as_of_date)
        df = _normalize_columns(df)
        if not _validate_dataframe(df):
            return pd.DataFrame()
        return df

    # 北交所股票：按专用优先级（新浪 → 腾讯 → 本地 TDX）。
    # ADR-0027 起本地 TDX 已删，网页网关是主力；旧实现硬编码 tdx_local 等于直接判死。
    if _is_bj_stock(code):
        bj_errors = []
        for src in BJ_SOURCE_PRIORITY:
            df = _fetch_from_source(code, days, src, as_of_date=as_of_date)
            if not df.empty:
                df = _normalize_columns(df)
                if _validate_dataframe(df):
                    logger.debug(f"使用数据源 [{src}] 获取北交所 {code}")
                    return df
            bj_errors.append(f"{src}: empty/invalid")
        raise AllDataSourcesFailedError(
            code, bj_errors + ["北交所：本地 TDX 已删且网页网关未取到"])

    errors = []
    # 选择数据源优先级：prefer_adjusted=True 时前复权数据源优先
    priority = DATA_SOURCE_PRIORITY_ADJUSTED if prefer_adjusted else DATA_SOURCE_PRIORITY
    for src in priority:
        df = _fetch_from_source(code, days, src, as_of_date=as_of_date)
        if not df.empty:
            df = _normalize_columns(df)
            if _validate_dataframe(df):
                if src != "akshare":
                    logger.debug(f"使用数据源 [{src}] 获取 {code}")
                return df
        errors.append(f"{src}: empty/invalid")
    raise AllDataSourcesFailedError(code, errors)


def _store_key(code: str) -> str:
    """统一落盘键：纯 6 位数字（去掉 sh/sz/bj 前缀）。

    口径单一真源 = \u0060lib.data.market_utils.normalize_code\u0060。
    P0（2026-11-20）：资金流批量层与本键口径曾漂移（'858' vs '000858'），
    导致 0 开头代码批量层 100% MISSING ⇒ 缺失被伪装成全零参与四维加权。
    """
    return normalize_code(code)


def _merge_write(dataset: str, key: str, df: pd.DataFrame, keep_rows: int = 1500) -> None:
    """与本地已有数据合并后写回（只增不减，避免越取越短）。"""
    from lib.data import local_store
    if df is None or df.empty:
        return
    try:
        old = local_store.read_frame(dataset, key)
        if not old.empty and "date" in old.columns:
            merged = (pd.concat([old, df], ignore_index=True)
                      .drop_duplicates(subset=["date"], keep="last")
                      .sort_values("date"))
        else:
            merged = df
        local_store.write_frame(dataset, key, merged.tail(keep_rows).reset_index(drop=True))
    except Exception as e:  # 落盘失败不影响取数
        logger.warning(f"[local_store] 写回失败 {dataset}/{key}: {e}")


def get_stock_hist_multi_source(
    code: str,
    days: int = 180,
    source: str = "auto",
    prefer_adjusted: bool = False,
    as_of_date: Optional[str] = None,
) -> pd.DataFrame:
    """个股日线：本地仓优先，在线取数成功后回填（ADR-0033）。

    本地仓按复权口径分两份：``kline``（前复权，回测用）与 ``kline_raw``（不复权）。
    绝不混用——不复权价在除权日会造假跳空，且不会报错。

    仅当 ``source="auto"`` 且无 ``as_of_date`` 时走本地；显式指定源或历史快照
    仍走在线链路（回测的 as_of_date 语义不能被本地“最新数据”替换）。
    """
    from lib.data import local_store

    key = _store_key(code)
    dataset = "kline" if prefer_adjusted else "kline_raw"
    use_local = (source == "auto" and as_of_date is None and local_store.enabled())

    if use_local:
        cached = local_store.read_frame(dataset, key)
        if not cached.empty and len(cached) >= min(days, 20) and \
                local_store.is_fresh(dataset, key):
            logger.debug(f"[local_store] 命中 {dataset}/{key} rows={len(cached)}")
            return cached.tail(days).reset_index(drop=True)

    if local_store.enabled() and local_store.offline():
        raise AllDataSourcesFailedError(
            code, [f"offline: 本地仓无可用 {dataset}/{key}（{local_store.STORE_ROOT}）"])

    df = _get_stock_hist_multi_source_online(
        code, days, source, prefer_adjusted, as_of_date)
    if use_local and not df.empty:
        _merge_write(dataset, key, df)
    return df


def get_circuit_breaker_status() -> Dict[str, dict]:
    """获取所有数据源熔断器状态"""
    return {
        src: {
            "failure_count": cb._failures,
            "last_failure_time": cb._last_failure_time,
            "state": cb.state,
        }
        for src, cb in _CIRCUIT_BREAKERS.items()
    }


def reset_circuit_breakers():
    """重置所有熔断器（用于测试）

    2026-11-14 修复：此前调用不存在的 `cb._reset()`（一调即 AttributeError 的死代码），
    改为 CircuitBreaker.reset() 公共方法。
    """
    for cb in _CIRCUIT_BREAKERS.values():
        cb.reset()
