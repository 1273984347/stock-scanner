"""数据层统一协议 —— 跨数据源的列契约与规范化（RF-01 Phase 2）

背景：项目里 `get_kline` / `get_realtime_quotes` / `get_stock_financial` 有多个同名实现，
**签名与返回列各不相同**（有的用中文列名，有的用英文；参数顺序也不同）。
直接合并这些实现 = 改变数据语义（实测：6 个 `_to_float` 对 "0" 的处理就分成两派）。
因此本模块提供**协议层**而非"唯一实现"：

- `KLINE_COLUMNS`：K 线的规范列契约（顺序 + dtype）
- `normalize_kline()`：把任意源的 K 线规范到该契约（列名映射 + 列序 + dtype）
- `validate_kline()`：按契约校验，返回问题清单（空列表 = 通过）

**纯新增 API，不改变任何现有数据源的返回** ⇒ 现有调用方零影响。
新代码应优先用协议层取数，而不是直接依赖某个源的实现细节。
"""

from typing import List

import pandas as pd

# ========== K 线列契约 ==========

KLINE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

KLINE_DTYPES = {
    "date": "datetime64[ns]",
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
}

# 常见别名 -> 规范列名（覆盖本项目各数据源实际使用的列名）
COLUMN_ALIASES = {
    "日期": "date", "date": "date", "trade_date": "date", "时间": "date",
    "开盘": "open", "open": "open", "开盘价": "open",
    "最高": "high", "high": "high", "最高价": "high",
    "最低": "low", "low": "low", "最低价": "low",
    "收盘": "close", "close": "close", "收盘价": "close",
    "成交量": "volume", "volume": "volume", "vol": "volume",
}


def normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    """把任意源的 K 线规范到 `KLINE_COLUMNS` 契约。

    · 列名按 `COLUMN_ALIASES` 映射（中文/别名 -> 规范名）
    · 缺失的契约列补 NaN
    · 多余列丢弃
    · 列序固定为 `KLINE_COLUMNS`
    · date -> datetime64[ns]，价格/量 -> float64

    空 DataFrame 原样返回（不制造空列）。
    """
    if df is None:
        return pd.DataFrame(columns=KLINE_COLUMNS)
    if df.empty:
        return df.copy()

    out = df.rename(columns={c: COLUMN_ALIASES[c] for c in df.columns if c in COLUMN_ALIASES})

    # 同一列可能被映射两次（如同时有 "close" 与 "收盘"）=> 去重保留首个
    out = out.loc[:, ~out.columns.duplicated()]

    for col in KLINE_COLUMNS:
        if col not in out.columns:
            out[col] = pd.NA

    out = out[KLINE_COLUMNS].copy()
    if "date" in out.columns:
        out["date"] = pd.to_datetime(out["date"], errors="coerce")
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def truncate_to_date(df: pd.DataFrame, as_of_date=None) -> pd.DataFrame:
    """按 as_of_date 截断（回测防前视偏差）。

    ⚠️ 调用方必须在 `tail(days)` **之前**调用：
    否则"最近 N 天"窗口里可能根本不含该日期 ⇒ 取不到应有数据
    （前视偏差的另一种表现形式 —— 不是多取，而是少取）。

    as_of_date 为 None 时原样返回（实时模式行为不变）。
    """
    if as_of_date is None or df is None or df.empty:
        return df
    if "date" not in df.columns:
        return df
    cutoff = pd.Timestamp(as_of_date)
    return df[df["date"] <= cutoff].copy()


def validate_kline(df: pd.DataFrame) -> List[str]:
    """按 K 线契约校验，返回**问题清单**（空列表 = 通过）。

    检查项：
    · 必需列是否齐全
    · 列序是否与契约一致
    · date 是否可解析为 datetime
    · 数值列是否为数值型
    · 是否存在重复日期
    """
    problems: List[str] = []
    if df is None or df.empty:
        return ["empty DataFrame"]

    missing = [c for c in KLINE_COLUMNS if c not in df.columns]
    if missing:
        problems.append(f"missing columns: {missing}")
        return problems

    expected = [c for c in KLINE_COLUMNS if c in df.columns]
    actual = [c for c in df.columns if c in set(KLINE_COLUMNS)]
    if actual != expected:
        problems.append(f"column order mismatch: expected {expected}, got {actual}")

    if not pd.api.types.is_datetime64_any_dtype(df["date"]):
        problems.append("date column is not datetime dtype")

    for col in ("open", "high", "low", "close", "volume"):
        if not pd.api.types.is_numeric_dtype(df[col]):
            problems.append(f"column '{col}' is not numeric")

    if df["date"].duplicated().any():
        problems.append(f"duplicated dates: {int(df['date'].duplicated().sum())}")

    return problems


def is_valid_kline(df: pd.DataFrame) -> bool:
    """按 K 线契约校验是否通过。"""
    return not validate_kline(df)
