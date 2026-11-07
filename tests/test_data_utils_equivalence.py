"""数据层工具归一的**特征测试**（characterization test，RF-01 Phase 2）

目的：证明 `_to_float` / `_safe_div` 改为委托 `lib/data_utils.py` 公共内核后，
**行为与搬迁前逐位等价**。

做法：内嵌**搬迁前**的 6 个 `_to_float` 实现与 3 个 `_safe_div` 实现作为参考版，
对一批边界值逐一比较「参考版 vs 现行实现」的结果。

⚠️ 为什么必须逐值比对而不是抽样：这 6 个 `_to_float` **同名但语义不同**
（例如字符串 "0" 在 data_eastmoney_fund / data_fund_flow 返回 None，
在 data_layer / data_sina_fin 返回 0.0）⇒ 任何一处不等价都会静默改变财务数据语义，
进而影响评分与回测。本测试就是防止那种"看起来等价"的合并。
"""

import pytest

from lib.data_utils import safe_div, to_float


# ========== 参考实现（搬迁前各模块的原文）============

def ref_to_float_eastmoney_fin(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ref_to_float_eastmoney_fund(v):
    if not v or v == '-' or v == '':
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def ref_to_float_fund_flow(v):
    if not v or str(v) in ("", "-", "0"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def ref_to_float_data_layer(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("亿", "").replace("万", ""))
    except (ValueError, TypeError):
        return None


def ref_to_float_sina_fin(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def ref_to_float_tdx_fin_adapter(v):
    if v is None or str(v) in ("", "-"):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def ref_safe_div(a, b):
    if b is None or a is None or b == 0:
        return None
    return a / b


# ========== 边界值集（覆盖各实现差异点）============

EDGE_VALUES = [
    None, 0, 0.0, "0", 1, -1, 1.5, -1.5, 123.45,
    "", " ", "-", "--", "1,234", "1,234.56", "-1,234",
    "1.5亿", "2万", "1,234.5亿", "abc", "1e3", "  12  ",
    True, False, [], {}, (), float("nan"),
]


# ========== 现行实现（各源的薄包装，参数策略不同）============

def _cur_eastmoney_fin(v):
    return to_float(v, prefer_raw_float=True)


def _cur_eastmoney_fund(v):
    return to_float(v, zero_is_none=True, dash_is_none=True, prefer_raw_float=True)


def _cur_fund_flow(v):
    return to_float(v, zero_is_none=True, zero_string_is_none=True, dash_is_none=True, strip_grouping=True)


def _cur_data_layer(v):
    return to_float(v, strip_grouping=True, strip_cn_units=True)


def _cur_sina_fin(v):
    return to_float(v, strip_grouping=True)


def _cur_tdx_fin_adapter(v):
    return to_float(v, strip_grouping=True, dash_is_none=True)


PAIRS = [
    ("data_eastmoney_fin", ref_to_float_eastmoney_fin, _cur_eastmoney_fin),
    ("data_eastmoney_fund", ref_to_float_eastmoney_fund, _cur_eastmoney_fund),
    ("data_fund_flow", ref_to_float_fund_flow, _cur_fund_flow),
    ("data_layer", ref_to_float_data_layer, _cur_data_layer),
    ("data_sina_fin", ref_to_float_sina_fin, _cur_sina_fin),
    ("data_tdx_fin_adapter", ref_to_float_tdx_fin_adapter, _cur_tdx_fin_adapter),
]


@pytest.mark.parametrize("name,ref,cur", PAIRS, ids=[p[0] for p in PAIRS])
def test_to_float_equivalent_to_pre_refactor(name, ref, cur):
    """每个源的 _to_float 必须与搬迁前逐值等价。"""
    for v in EDGE_VALUES:
        expected = ref(v)
        actual = cur(v)
        # NaN != NaN，单独按"都是 NaN"处理
        if expected != expected and actual != actual:
            continue
        assert actual == expected, (
            f"{name}._to_float({v!r}) 期望 {expected!r}，实际 {actual!r}"
        )


def test_safe_div_equivalent_to_pre_refactor():
    """safe_div 与搬迁前 3 份实现逐值等价。"""
    cases = [
        (1, 2), (1, 0), (0, 0), (None, 2), (2, None), (None, None),
        (-1, 2), (1.5, 0.5), (10, 4),
    ]
    for num, den in cases:
        expected = ref_safe_div(num, den)
        actual = safe_div(num, den)
        assert actual == expected, f"safe_div({num!r}, {den!r}) 期望 {expected!r}，实际 {actual!r}"


def test_modules_expose_same_behaviour():
    """各数据源模块暴露的 _to_float / _safe_div 必须与参考版一致。"""
    from lib import anomaly_detector, data_eastmoney_fin, data_eastmoney_fund
    from lib import data_fund_flow, data_layer, data_sina_fin, data_tdx_fin_adapter
    from lib import fundamental

    live = [
        ("data_eastmoney_fin", data_eastmoney_fin._to_float, ref_to_float_eastmoney_fin),
        ("data_eastmoney_fund", data_eastmoney_fund._to_float, ref_to_float_eastmoney_fund),
        ("data_fund_flow", data_fund_flow._to_float, ref_to_float_fund_flow),
        ("data_layer", data_layer._to_float, ref_to_float_data_layer),
        ("data_sina_fin", data_sina_fin._to_float, ref_to_float_sina_fin),
        ("data_tdx_fin_adapter", data_tdx_fin_adapter._to_float, ref_to_float_tdx_fin_adapter),
    ]
    for name, fn, ref in live:
        for v in EDGE_VALUES:
            e, a = ref(v), fn(v)
            if e != e and a != a:
                continue
            assert a == e, f"{name}._to_float({v!r}) 期望 {e!r}，实际 {a!r}"

    for mod in (anomaly_detector, data_tdx_fin_adapter, fundamental):
        for num, den in [(1, 2), (1, 0), (None, 2), (2, None), (10, 4)]:
            assert mod._safe_div(num, den) == ref_safe_div(num, den)
