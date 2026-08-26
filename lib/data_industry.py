"""行业财务序列聚合 —— 板块成分股财报 → 板块级季度 yoy 序列

数据源：Tushare（lib/data_tushare 的 get_balancesheet/get_income/get_cashflow）
聚合口径：板块内最多 max_stocks_per_block 只股票，逐季同比增速取中位数
缓存：cache/industry/{block}.parquet，TTL = CycleWarningConfig.cache_ttl_days 天

已知特性（v1 接受，见计划 Task 1.1）：ECDF 分位在低位平台上，若窗口内大量
观测精确等于最新值（指标长期横盘），分位会偏高；由 TopWarning 层的价格破位
二次过滤兜底。

使用方式：
    from lib.data_industry import fetch_block_financial_series
    df = fetch_block_financial_series("白酒")
"""
import logging
import time
from pathlib import Path
from typing import List, Optional

import pandas as pd

from config import CycleWarningConfig
from lib.data_layer import get_all_blocks

logger = logging.getLogger(__name__)

CACHE_DIR = Path("cache") / "industry"
_TTL_SECONDS = CycleWarningConfig().cache_ttl_days * 24 * 3600
_TABLES_CACHE_DIR = CACHE_DIR / "tables"   # 逐股财报表磁盘缓存（不同板块成分股重叠，跨板块复用）
_BLOCK_SERIES_MEMO: dict = {}              # 进程内板块序列备忘录（含 None 负结果，避免日循环反复重建）


def _to_ts_code(code: str) -> str:
    """6 位股票代码 → Tushare ts_code（0/3 开头 .SZ，6 开头 .SH，4/8/92 开头北交所 .BJ）；已是 ts_code 则原样返回"""
    code = str(code).split(".")[0].zfill(6)
    if code[:2] == "92" or code[0] in ("4", "8"):   # 北交所：4/8 开头 + 920 段新代码
        suffix = "BJ"
    elif code[0] in ("0", "3"):
        suffix = "SZ"
    else:
        suffix = "SH"
    return f"{code}.{suffix}"


def _block_codes(block: str) -> List[str]:
    """板块 → 股票代码列表（TDX 板块文件）"""
    return sorted(get_all_blocks().get(block, []))


def _yoy_by_quarter(ts: Optional[pd.DataFrame], value_col: str) -> pd.DataFrame:
    """单表季度同比（v_t / v_{t-4Q} - 1），end_date 升序去重后按 4 期错位对齐

    Returns:
        DataFrame 列 ["date", value_col]；输入空/无列时返回仅含列名的空表
    """
    if ts is None or ts.empty or value_col not in ts.columns:
        return pd.DataFrame(columns=["date", value_col])
    q = (ts[["end_date", value_col]]
         .sort_values("end_date")
         .drop_duplicates(subset="end_date", keep="last"))
    prev = q[value_col].shift(4)
    result = pd.DataFrame({"date": q["end_date"].astype(str)})
    result[value_col] = (q[value_col] / prev - 1).where(prev.notna() & (prev != 0))
    return result.dropna(subset=[value_col])


def _fetch_stock_yoy_series(code: str) -> pd.DataFrame:
    """单只股票的 4 指标季度 yoy 序列（列: date, inventory_yoy, revenue_yoy, capex_yoy, debt_yoy）"""
    bs, inc, cf = _fetch_raw_tables(code)

    if bs is None or bs.empty:
        return pd.DataFrame()

    debt_src = bs.copy()
    debt_src["_debt"] = (
        debt_src["st_borr"].fillna(0)
        + debt_src["lt_borr"].fillna(0)
        + debt_src["bond_payable"].fillna(0)
    )

    frames = {
        "inventory_yoy": _yoy_by_quarter(bs, "inventory"),
        "revenue_yoy": _yoy_by_quarter(inc, "revenue"),
        "capex_yoy": _yoy_by_quarter(cf, "c_pay_acq_const_fiolta"),
        "debt_yoy": _yoy_by_quarter(debt_src, "_debt"),
    }
    renamed = {
        key: df.rename(columns={df.columns[1]: key}) if len(df.columns) >= 2 else df
        for key, df in frames.items()
    }
    merged = renamed["inventory_yoy"]
    for key in ("revenue_yoy", "capex_yoy", "debt_yoy"):
        merged = merged.merge(renamed[key], on="date", how="outer")
    return merged.sort_values("date")


# akshare 东财财报接口 → Tushare 同款列名映射（跨源聚合要求 end_date 归一化为 YYYYMMDD）
_AK_BS_MAP = {"REPORT_DATE": "end_date", "INVENTORY": "inventory",
              "SHORT_LOAN": "st_borr", "LONG_LOAN": "lt_borr", "BOND_PAYABLE": "bond_payable"}
_AK_INC_MAP = {"REPORT_DATE": "end_date", "TOTAL_OPERATE_INCOME": "revenue"}
_AK_CF_MAP = {"REPORT_DATE": "end_date", "CONSTRUCT_LONG_ASSET": "c_pay_acq_const_fiolta"}


def _ak_to_ts_format(df: Optional[pd.DataFrame], col_map: dict) -> pd.DataFrame:
    """东财原始表 → 与 Tushare 包装函数同列名、同日期格式的 DataFrame；缺列返回空表"""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.rename(columns=col_map)
    keep = list(col_map.values())
    if any(c not in out.columns for c in keep):
        return pd.DataFrame()
    out = out[keep].copy()
    out["end_date"] = pd.to_datetime(out["end_date"]).dt.strftime("%Y%m%d")
    return out.sort_values("end_date").reset_index(drop=True)


def _fetch_from_akshare(code: str):
    """akshare 东财财报接口（免费无积分门槛）→ (bs, inc, cf) 三元组"""
    ts_code = _to_ts_code(code)
    code6, market = ts_code.split(".")
    symbol = f"{market}{code6}"
    try:
        import akshare as ak
        bs = _ak_to_ts_format(ak.stock_balance_sheet_by_report_em(symbol=symbol), _AK_BS_MAP)
        inc = _ak_to_ts_format(ak.stock_profit_sheet_by_report_em(symbol=symbol), _AK_INC_MAP)
        cf = _ak_to_ts_format(ak.stock_cash_flow_sheet_by_report_em(symbol=symbol), _AK_CF_MAP)
        return bs, inc, cf
    except Exception as e:
        logger.warning(f"akshare 财报获取失败 {code}: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


def _fetch_raw_tables(code: str):
    """双源获取三张财报表（带逐股磁盘缓存，TTL 与板块缓存一致）"""
    import pickle

    _TABLES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = _TABLES_CACHE_DIR / f"{code}.pkl"
    if f.exists() and time.time() - f.stat().st_mtime < _TTL_SECONDS:
        try:
            with open(f, "rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, tuple) and len(cached) == 3:
                return cached
        except Exception as e:
            logger.debug(f"财报表缓存读取失败 {code}: {e}")

    result = _fetch_raw_tables_remote(code)
    try:
        with open(f, "wb") as fh:
            pickle.dump(result, fh)
    except Exception as e:
        logger.debug(f"财报表缓存写入失败 {code}: {e}")
    return result


def _fetch_raw_tables_remote(code: str):
    """双源获取三张财报表：Tushare（权限正常且有数据）→ akshare 东财降级

    降级触发条件：Tushare 财报权限已被拒（is_fin_access_denied）或资产负债表为空。
    """
    from lib import data_tushare as dt

    if not dt.is_fin_access_denied():
        ts_code = _to_ts_code(code)
        bs = dt.get_balancesheet(ts_code)
        inc = dt.get_income(ts_code)
        cf = dt.get_cashflow(ts_code)
        if not bs.empty and "inventory" in bs.columns:
            return bs, inc, cf

    bs, inc, cf = _fetch_from_akshare(code)
    if isinstance(bs, pd.DataFrame) and not bs.empty:
        logger.info(f"财报数据改用 akshare {code}")
    else:
        logger.debug(f"Tushare 与 akshare 均无财报数据 {code}")
    return bs, inc, cf


def fetch_block_financial_series(block: str) -> Optional[pd.DataFrame]:
    """聚合板块成分股财报 → 板块级季度 yoy 序列（含 Parquet 缓存，见模块 docstring）"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{block}.parquet"
    if cache_file.exists() and time.time() - cache_file.stat().st_mtime < _TTL_SECONDS:
        try:
            return pd.read_parquet(cache_file)
        except Exception as e:
            logger.warning(f"行业缓存读取失败 {block}: {e}")

    # 进程内备忘录（含 None 负结果）：日循环中同一板块反复请求时避免重复冷构建
    if block in _BLOCK_SERIES_MEMO:
        return _BLOCK_SERIES_MEMO[block]

    codes = _block_codes(block)
    if not codes:
        _BLOCK_SERIES_MEMO[block] = None
        return None

    max_stocks = CycleWarningConfig().max_stocks_per_block
    frames = []
    for code in codes[:max_stocks]:
        try:
            s = _fetch_stock_yoy_series(code)
            if s is not None and not s.empty:
                frames.append(s)
        except Exception as e:
            logger.debug(f"行业聚合跳过 {code}: {e}")
            continue

    if not frames:
        _BLOCK_SERIES_MEMO[block] = None
        return None

    stacked = pd.concat(frames, ignore_index=True)
    grouped = stacked.groupby("date")
    agg = grouped[["inventory_yoy", "revenue_yoy", "capex_yoy", "debt_yoy"]].median()
    agg["n_stocks"] = grouped.size()
    result = agg.reset_index().sort_values("date")
    _BLOCK_SERIES_MEMO[block] = result

    try:
        result.to_parquet(cache_file)
    except Exception as e:
        logger.warning(f"行业缓存写入失败 {block}: {e}")

    return result
