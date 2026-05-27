"""TDX 财务数据分析模块

提供财务数据的分析和查询功能。
字段含义基于通达信标准财务模板推断，未完全验证。
"""
import pandas as pd
from typing import Dict, List, Optional
from lib.data_tdx_fin import GpcwReader


# 字段名称映射（基于通达信财务数据标准模板 + 实际数据验证）
# 注意：这些映射是推断的，需要对照实际财报验证
# 更新：部分映射已通过 600519(茅台EPS~65元, field50=70)、600036(招行EPS~5.7元, field6=205) 验证
FIELD_NAMES: Dict[int, str] = {
    # 利润表相关（单位：千元）
    0: "净利润",
    1: "净利润(调整)",
    2: "营业总收入",
    3: "营业收入",
    4: "营业利润",
    5: "利润总额",
    6: "每股收益(元)",       # 600036=205≈2.05元(分单位)；600519=15M 异常大
    7: "每股未分配利润",
    8: "每股净资产",         # 单位可能是元或0.01元
    9: "净资产收益率",
    10: "销售毛利率",
    11: "销售净利率",
    12: "财务费用",

    # 资产负债表相关（单位：千元）
    13: "总资产",
    14: "总负债",
    15: "流动资产",
    16: "固定资产",
    17: "无形资产",
    18: "长期股权投资",
    19: "短期借款",
    20: "长期借款",
    21: "应付债券",

    # 每股指标
    50: "每股收益(摊薄)",   # 600519=70≈65元(实际EPS)，单位：分
    51: "每股净资产",       # 600519=17,847≈178.47元(实际每股净资产)，单位：分
    52: "每股现金流",
    53: "每股经营现金流",

    # 资产指标（单位：千元）
    75: "资产总计",
    76: "负债合计",
    77: "所有者权益",
    78: "归属母公司股东权益",

    # 银行专用
    82: "存款总额",
    83: "贷款总额",

    # 券商专用
    106: "经纪业务收入",
    107: "投行业务收入",

    # 保险专用
    111: "保费收入",
    112: "赔付支出",
}

# 字段分组
FIELD_CATEGORIES = {
    "利润指标": [0, 1, 2, 3, 4, 5],
    "每股指标": [6, 7, 8, 9, 10, 11, 12, 50, 51, 52, 53],
    "资产指标": [13, 14, 15, 16, 17, 18, 19, 20, 21, 75, 76, 77, 78],
    "银行专用": [82, 83],
    "券商专用": [106, 107, 108, 109, 110, 111, 112],
}


def get_stock_financial(code: str) -> pd.DataFrame:
    """获取股票财务数据（原始）

    Args:
        code: 6位股票代码

    Returns:
        DataFrame with columns: field_index, value_main, value_aux, raw_bytes
    """
    reader = GpcwReader()
    return reader.read_stock(code)


def get_stock_financial_named(code: str) -> pd.DataFrame:
    """获取股票财务数据（带字段名称）

    Args:
        code: 6位股票代码

    Returns:
        DataFrame with columns: field_index, field_name, value_main, value_aux, raw_bytes
    """
    df = get_stock_financial(code)
    if df.empty:
        return df

    df = df.copy()
    df["field_name"] = df["field_index"].apply(
        lambda x: FIELD_NAMES.get(x, f"field_{x}")
    )
    return df


def get_financial_summary(code: str) -> Dict:
    """获取财务摘要（主要指标）

    Args:
        code: 6位股票代码

    Returns:
        包含主要财务指标的字典
    """
    df = get_stock_financial(code)
    if df.empty:
        return {}

    result = {"code": code}

    # 提取关键字段
    key_fields = [0, 3, 4, 6, 8, 50]
    for field_idx in key_fields:
        row = df[df["field_index"] == field_idx]
        if not row.empty:
            field_name = FIELD_NAMES.get(field_idx, f"field_{field_idx}")
            result[field_name] = row.iloc[0]["value_main"]

    return result


def print_financial_report(code: str, name: str = ""):
    """打印财务报告

    Args:
        code: 股票代码
        name: 股票名称（可选）
    """
    df = get_stock_financial_named(code)
    if df.empty:
        print(f"{code} 无财务数据")
        return

    print(f"\n{'='*60}")
    print(f"{code} {name} 财务数据".center(54))
    print(f"{'='*60}")

    # 按类别显示
    for category, fields in FIELD_CATEGORIES.items():
        print(f"\n【{category}】")
        category_data = df[df["field_index"].isin(fields)]
        for _, row in category_data.iterrows():
            print(f"  {row['field_name']:20s}: {row['value_main']:>15,}")

    # 显示所有非零字段
    print(f"\n【所有字段】(非零)")
    for _, row in df.iterrows():
        if row["value_main"] > 0:
            print(f"  field {row['field_index']:3d} {row['field_name']:20s}: {row['value_main']:>15,}")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # 测试
    for code, name in [("600519", "贵州茅台"), ("600036", "招商银行"), ("600000", "浦发银行")]:
        print_financial_report(code, name)
