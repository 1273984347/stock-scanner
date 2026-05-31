"""龙虎榜席位分析 -- 区分游资/机构/外资"""

import logging
from typing import Dict, List
from collections import defaultdict

logger = logging.getLogger(__name__)

# 机构席位关键词
INSTITUTIONAL_KEYWORDS = ["机构专用", "机构", "基金", "社保", "保险", "QFII"]

# 外资席位关键词
FOREIGN_KEYWORDS = ["沪股通", "深股通", "QFII", "RQFII", "外资"]

# 知名游资席位（部分）
KNOWN_YOUZI = [
    "华泰证券深圳益田路荣超商务中心",
    "中信证券上海溧阳路",
    "国泰君安上海江苏路",
    "中国银河证券绍兴营业部",
    "东方财富拉萨团结路",
    "东方财富拉萨东环路",
]


def classify_seat(seat_name: str) -> str:
    """分类席位类型

    Args:
        seat_name: 席位名称

    Returns:
        "institutional" / "foreign" / "youzi" / "retail"
    """
    if not seat_name:
        return "retail"

    # 检查外资
    for kw in FOREIGN_KEYWORDS:
        if kw in seat_name:
            return "foreign"

    # 检查机构
    for kw in INSTITUTIONAL_KEYWORDS:
        if kw in seat_name:
            return "institutional"

    # 检查已知游资
    for yz in KNOWN_YOUZI:
        if yz in seat_name:
            return "youzi"

    return "retail"


def analyze_lhb_seats(lhb_data: List[Dict]) -> Dict:
    """分析龙虎榜席位分布

    Args:
        lhb_data: 龙虎榜数据列表，每条需有席位名称和金额字段。
                  支持两种字段命名：
                  - stock_lhb_stock_detail_em: 交易营业部名称 / 买入金额 / 卖出金额
                  - stock_lhb_hyyyb_em: 营业部名称 / 买入总金额 / 卖出总金额

    Returns:
        席位类型分析结果
    """
    by_type = defaultdict(
        lambda: {"count": 0, "buy_amount": 0.0, "sell_amount": 0.0, "seats": []}
    )

    for entry in lhb_data:
        seat_name = (
            entry.get("交易营业部名称")
            or entry.get("营业部名称")
            or entry.get("seat_name")
            or ""
        )
        seat_type = classify_seat(seat_name)

        buy_amount = (
            entry.get("买入金额")
            or entry.get("买入总金额")
            or entry.get("buy_amount")
            or 0
        )
        sell_amount = (
            entry.get("卖出金额")
            or entry.get("卖出总金额")
            or entry.get("sell_amount")
            or 0
        )

        by_type[seat_type]["count"] += 1
        by_type[seat_type]["buy_amount"] += float(buy_amount)
        by_type[seat_type]["sell_amount"] += float(sell_amount)
        by_type[seat_type]["seats"].append(seat_name)

    # 计算净买入
    for seat_type in by_type:
        by_type[seat_type]["net_buy"] = (
            by_type[seat_type]["buy_amount"] - by_type[seat_type]["sell_amount"]
        )

    return dict(by_type)


def format_lhb_analysis(analysis: Dict) -> str:
    """格式化龙虎榜分析结果"""
    if not analysis:
        return "暂无龙虎榜数据"

    type_names = {
        "institutional": "机构",
        "foreign": "外资",
        "youzi": "游资",
        "retail": "散户",
    }

    lines = ["## 龙虎榜席位分析", ""]

    for seat_type in ["institutional", "foreign", "youzi", "retail"]:
        data = analysis.get(seat_type)
        if not data:
            continue
        name = type_names.get(seat_type, seat_type)
        net = data["net_buy"]
        direction = "净买入" if net > 0 else "净卖出"
        lines.append(
            f"- **{name}**: {data['count']} 个席位 | "
            f"{direction} {abs(net)/1e8:.2f}亿 | "
            f"买入 {data['buy_amount']/1e8:.2f}亿 卖出 {data['sell_amount']/1e8:.2f}亿"
        )

    return "\n".join(lines)
