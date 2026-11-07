"""数据层公共工具 —— 统一各数据源重复的数值转换内核（RF-01 Phase 2）

背景：`_to_float` 在 6 个数据源模块里各有一份、`_safe_div` 在 3 个模块里各有一份。
**它们并非等价副本**：例如字符串 "0" 在 data_eastmoney_fund / data_fund_flow 里返回 None，
而在 data_layer / data_sina_fin 里返回 0.0；千分位逗号只有部分实现会剥离。
盲目合并成同一个函数 = **改变财务数据语义**（会静默影响评分与回测）。

因此本模块提供**参数化的公共内核**，各源保留 3 行薄包装并显式声明自己的策略：
- `strip_grouping`   : 是否剥离千分位逗号
- `strip_cn_units`   : 是否剥离中文单位（亿/万）
- `zero_is_none`     : 数值 0 / 字符串 "0" 是否视为缺失
- `dash_is_none`     : "" 与 "-" 是否视为缺失

⇒ 重复的**转换逻辑**只剩一份（在本模块），各源的**差异被显式化**，
行为与搬迁前**逐位等价**（由 tests/test_data_utils_equivalence.py 逐值验证）。
"""

from typing import Optional


def to_float(
    v,
    *,
    strip_grouping: bool = False,
    strip_cn_units: bool = False,
    zero_is_none: bool = False,
    zero_string_is_none: bool = False,
    dash_is_none: bool = False,
    prefer_raw_float: bool = False,
) -> Optional[float]:
    """安全转 float（公共内核）。

    Args:
        v: 任意值
        strip_grouping: 剥离千分位逗号
        strip_cn_units: 剥离中文单位（亿/万）
        zero_is_none: **数值** 0（含 False）视为缺失
        zero_string_is_none: **字符串** "0" 视为缺失（与数值 0 是两回事！）
        dash_is_none: "" 与 "-" 视为缺失
        prefer_raw_float: 直接对原值 float()（保留 bool->1.0/0.0 语义），
            否则走 str 路径（float("True") 会失败 -> None）

    Returns:
        转换成功返回 float；缺失或无法转换返回 None。
    """
    if v is None:
        return None
    if zero_is_none and v == 0:
        # 数值 0（Python 中 False == 0）算缺失；注意字符串 "0" 不在此列
        return None
    if zero_string_is_none and str(v).strip() == "0":
        return None
    if dash_is_none:
        s = str(v).strip()
        if s in ("", "-"):
            return None
    if prefer_raw_float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    s = str(v)
    if strip_grouping:
        s = s.replace(",", "")
    if strip_cn_units:
        s = s.replace("亿", "").replace("万", "")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def safe_div(num, den):
    """安全除法：分母为 0 或任一操作数为 None 时返回 None，否则相除。"""
    if num is None or den is None or den == 0:
        return None
    return num / den
