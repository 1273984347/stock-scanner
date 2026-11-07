"""数据层协议层契约测试（RF-01 Phase 2）

覆盖：
- K 线列契约的规范化（中文/别名 -> 规范列名 + 列序 + dtype）
- 按契约校验（缺列 / 列序错 / 重复日期 / 非数值列）
- as_of_date 截断（回测防前视偏差）
"""

import pandas as pd

from lib.data_protocol import (
    COLUMN_ALIASES,
    KLINE_COLUMNS,
    is_valid_kline,
    normalize_kline,
    truncate_to_date,
    validate_kline,
)


def _sample():
    return pd.DataFrame({
        "日期": pd.date_range("2026-01-01", periods=5, freq="D"),
        "开盘": [10.0, 10.1, 10.2, 10.3, 10.4],
        "最高": [10.5, 10.6, 10.7, 10.8, 10.9],
        "最低": [9.5, 9.6, 9.7, 9.8, 9.9],
        "收盘": [10.2, 10.3, 10.4, 10.5, 10.6],
        "成交量": [1000, 1100, 1200, 1300, 1400],
    })


def test_normalize_maps_chinese_columns():
    df = normalize_kline(_sample())
    assert list(df.columns) == KLINE_COLUMNS
    assert len(df) == 5
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    for c in ("open", "high", "low", "close", "volume"):
        assert pd.api.types.is_numeric_dtype(df[c])


def test_normalize_is_idempotent():
    once = normalize_kline(_sample())
    twice = normalize_kline(once)
    assert list(once.columns) == list(twice.columns)
    assert len(once) == len(twice)


def test_normalize_fills_missing_columns():
    df = pd.DataFrame({"收盘": [1.0, 2.0], "日期": pd.date_range("2026-01-01", periods=2)})
    out = normalize_kline(df)
    assert list(out.columns) == KLINE_COLUMNS
    assert out["open"].isna().all()


def test_normalize_empty_unchanged():
    out = normalize_kline(pd.DataFrame())
    assert out.empty


def test_validate_passes_for_normalized():
    assert is_valid_kline(normalize_kline(_sample()))
    assert validate_kline(normalize_kline(_sample())) == []


def test_validate_detects_missing_columns():
    df = normalize_kline(_sample()).drop(columns=["volume"])
    problems = validate_kline(df)
    assert any("missing columns" in p for p in problems)


def test_validate_detects_column_order():
    df = normalize_kline(_sample())
    df = df[["close", "open", "high", "low", "volume", "date"]]
    problems = validate_kline(df)
    assert any("column order" in p for p in problems)


def test_validate_detects_duplicated_dates():
    df = normalize_kline(_sample())
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)
    problems = validate_kline(df)
    assert any("duplicated dates" in p for p in problems)


def test_aliases_cover_chinese_source_columns():
    for cn in ("日期", "开盘", "最高", "最低", "收盘", "成交量"):
        assert cn in COLUMN_ALIASES
        assert COLUMN_ALIASES[cn] in KLINE_COLUMNS


# ========== as_of_date 截断（防前视偏差）============

def test_truncate_none_keeps_all():
    df = normalize_kline(_sample())
    assert len(truncate_to_date(df, None)) == len(df)


def test_truncate_filters_future_rows():
    df = normalize_kline(_sample())
    out = truncate_to_date(df, "2026-01-03")
    assert len(out) == 3
    assert out["date"].max() == pd.Timestamp("2026-01-03")


def test_truncate_excludes_the_cutoff_day_plus():
    df = normalize_kline(_sample())
    out = truncate_to_date(df, "2026-01-03")
    assert (out["date"] <= pd.Timestamp("2026-01-03")).all()


def test_truncate_before_tail_semantics():
    """截断必须发生在 tail(days) 之前 —— 否则窗口里可能不含目标日期（少取）。

    复现：50 天数据，取最近 10 天；若先 tail 再截断，得到的是"今天往前 10 天"，
    而不是"截止目标日期往前 10 天"。
    """
    df = normalize_kline(pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=50, freq="D"),
        "open": range(50), "high": range(50), "low": range(50),
        "close": range(50), "volume": range(50),
    }))
    cutoff = "2026-01-20"
    truncated_first = truncate_to_date(df, cutoff).tail(10)
    tail_first = truncate_to_date(df.tail(10), cutoff)
    # 先截断后 tail：拿到的是截止日往前 10 天
    assert len(truncated_first) == 10
    assert truncated_first["date"].max() == pd.Timestamp(cutoff)
    # 先 tail 后截断：窗口不含截止日 => 结果更少（这就是必须先截断的原因）
    assert len(tail_first) < len(truncated_first)
