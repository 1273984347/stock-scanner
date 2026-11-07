"""北向资金板块轮动关联分析

将北向资金净买入股票与板块表现关联，识别板块轮动信号。

数据来源：
- 北向资金: lib.data.data_northbound_rt.NorthboundMonitor.get_top_buy_stocks()
- 板块表现: factors.get_sector_performance()
- 股票→板块映射: lib.data.data_layer.get_stock_blocks()

用法：
    from lib.strategy.northbound_sector import analyze_northbound_sector_flow, format_nb_sector_analysis
    result = analyze_northbound_sector_flow()
    print(format_nb_sector_analysis(result))
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


def _get_stock_sector(code: str) -> Optional[str]:
    """从 infoharbor_block 获取股票所属板块"""
    try:
        from lib.data.data_layer import get_stock_blocks
        blocks = get_stock_blocks(code)
        if blocks:
            return blocks[0]  # 取第一个板块
    except Exception:
        pass
    return None


def analyze_northbound_sector_flow(
    northbound_stocks: List[Dict] = None,
    sector_performance: List[Dict] = None,
) -> Dict:
    """分析北向资金流入与板块表现的关联

    Args:
        northbound_stocks: 北向资金买入股票列表 [{"code", "name", "net_buy", "holding"}]
                          若为 None，自动从 NorthboundMonitor 获取
        sector_performance: 板块表现列表 [{"block", "stock_count", "avg_return", ...}]
                          若为 None，自动从 get_sector_performance 获取

    Returns:
        关联分析结果，包含：
        - correlations: 各板块的北向资金+表现关联
        - signal: 整体信号 (bullish/bearish/neutral)
        - bullish_sectors / bearish_sectors: 看多/看空板块数
        - top_rotation: 资金流入最大且表现最强的板块（轮动信号）
    """
    # 自动获取数据
    if northbound_stocks is None:
        try:
            from lib.data.data_northbound_rt import NorthboundMonitor
            monitor = NorthboundMonitor()
            northbound_stocks = monitor.get_top_buy_stocks(top_n=30)
        except Exception as e:
            logger.debug(f"获取北向资金数据失败: {e}")
            northbound_stocks = []

    if sector_performance is None:
        try:
            from factors import get_sector_performance
            sector_performance = get_sector_performance(top_n=20, days=20)
        except Exception as e:
            logger.debug(f"获取板块表现数据失败: {e}")
            sector_performance = []

    if not northbound_stocks or not sector_performance:
        return {"correlations": [], "signal": "neutral", "top_rotation": []}

    # 板块表现索引
    sector_perf_map: Dict[str, float] = {}
    for item in sector_performance:
        sector_perf_map[item["block"]] = item["avg_return"]

    # 按板块分组北向资金买入
    sector_nb_flow: Dict[str, List[Dict]] = defaultdict(list)
    unmapped = 0

    for stock in northbound_stocks:
        code = stock.get("code", "")
        # 尝试从 stock 中获取板块信息，否则查映射
        sector = stock.get("sector", stock.get("industry"))
        if not sector:
            sector = _get_stock_sector(code)
        if sector:
            sector_nb_flow[sector].append(stock)
        else:
            unmapped += 1

    # 关联分析
    correlations = []
    for sector, stocks in sector_nb_flow.items():
        perf = sector_perf_map.get(sector, 0)
        count = len(stocks)
        total_net_buy = sum(s.get("net_buy", 0) for s in stocks)

        if count >= 3 and perf > 0.5:
            signal = "bullish"
        elif count >= 3 and perf < -0.5:
            signal = "bearish"
        elif count >= 2 and perf > 1.0:
            signal = "bullish"
        elif count >= 2 and perf < -1.0:
            signal = "bearish"
        else:
            signal = "neutral"

        correlations.append({
            "sector": sector,
            "nb_stock_count": count,
            "total_net_buy": round(total_net_buy, 2),
            "sector_return": round(perf, 2),
            "signal": signal,
            "stocks": [s.get("name", s.get("code", "")) for s in stocks[:5]],
        })

    # 排序：按北向资金买入数量
    correlations.sort(key=lambda x: x["nb_stock_count"], reverse=True)

    # 整体信号
    bullish_count = sum(1 for c in correlations if c["signal"] == "bullish")
    bearish_count = sum(1 for c in correlations if c["signal"] == "bearish")

    if bullish_count > bearish_count + 1:
        signal = "bullish"
    elif bearish_count > bullish_count + 1:
        signal = "bearish"
    else:
        signal = "neutral"

    # 轮动信号：北向资金集中流入 + 板块表现强
    top_rotation = [
        c for c in correlations
        if c["nb_stock_count"] >= 3 and c["sector_return"] > 0
    ][:5]

    return {
        "correlations": correlations[:10],
        "signal": signal,
        "bullish_sectors": bullish_count,
        "bearish_sectors": bearish_count,
        "unmapped_stocks": unmapped,
        "top_rotation": top_rotation,
        "timestamp": datetime.now().isoformat(),
    }


def format_nb_sector_analysis(result: Dict) -> str:
    """格式化北向资金板块分析为可读文本"""
    if not result.get("correlations"):
        return "暂无北向资金板块数据"

    signal = result.get("signal", "neutral")
    emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "🟡"}.get(signal, "🟡")

    lines = [
        f"## {emoji} 北向资金板块轮动分析",
        "",
        f"整体信号: {signal}  |  看多板块: {result.get('bullish_sectors', 0)}  |  看空板块: {result.get('bearish_sectors', 0)}",
        "",
        "| 板块 | 北向买入股数 | 板块涨跌% | 信号 |",
        "|------|-------------|-----------|------|",
    ]

    for c in result["correlations"][:5]:
        sig_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(c["signal"], "➡️")
        lines.append(
            f"| {c['sector']} | {c['nb_stock_count']} | {c['sector_return']:.2f}% | {sig_emoji} |"
        )

    # 轮动信号
    rotation = result.get("top_rotation", [])
    if rotation:
        lines.append("")
        lines.append("### 轮动热点（北向集中流入 + 板块上涨）")
        for r in rotation:
            stocks_str = ", ".join(r.get("stocks", [])[:3])
            lines.append(f"- **{r['sector']}**: {r['nb_stock_count']}股流入, 涨{r['sector_return']:.2f}% ({stocks_str})")

    unmapped = result.get("unmapped_stocks", 0)
    if unmapped > 0:
        lines.append(f"\n> {unmapped} 只股票未映射到板块")

    return "\n".join(lines)
