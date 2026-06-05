"""组合归因分析 — 收益分解

归因维度：
- 市场贡献（大盘涨跌）
- 行业贡献（板块轮动）
- 个股贡献（选股能力）
- 因子贡献（因子暴露）
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class AttributionResult:
    """归因结果"""
    total_return: float
    market_return: float
    sector_return: float
    stock_return: float
    factor_return: float
    # 细分
    market_contribution: float
    sector_contribution: float
    stock_contribution: float
    factor_contribution: float
    # 详情
    sector_details: Dict[str, float]
    stock_details: Dict[str, float]
    factor_details: Dict[str, float]


class PortfolioAttribution:
    """组合归因分析器"""

    def __init__(self):
        self._benchmark = None

    def set_benchmark(self, benchmark_data: Dict):
        """设置基准数据"""
        self._benchmark = benchmark_data

    def analyze(self, portfolio: Dict, market_data: Dict) -> AttributionResult:
        """执行归因分析"""
        # 计算组合总收益
        total_return = portfolio.get("total_return", 0)

        # 市场贡献（基准收益）
        market_return = self._calculate_market_return(market_data)

        # 行业贡献
        sector_contribution, sector_details = self._calculate_sector_contribution(
            portfolio, market_data
        )

        # 个股贡献
        stock_contribution, stock_details = self._calculate_stock_contribution(
            portfolio, market_data
        )

        # 因子贡献
        factor_contribution, factor_details = self._calculate_factor_contribution(
            portfolio, market_data
        )

        # 分解收益
        market_contribution = market_return
        sector_return = sector_contribution - market_return
        stock_return = stock_contribution - sector_contribution
        factor_return = total_return - factor_contribution

        return AttributionResult(
            total_return=total_return,
            market_return=market_return,
            sector_return=sector_return,
            stock_return=stock_return,
            factor_return=factor_return,
            market_contribution=market_contribution,
            sector_contribution=sector_contribution,
            stock_contribution=stock_contribution,
            factor_contribution=factor_contribution,
            sector_details=sector_details,
            stock_details=stock_details,
            factor_details=factor_details
        )

    def _calculate_market_return(self, market_data: Dict) -> float:
        """计算市场收益"""
        # 使用沪深300作为基准
        benchmark = market_data.get("benchmark", {})
        return benchmark.get("return", 0)

    def _calculate_sector_contribution(self, portfolio: Dict, market_data: Dict) -> Tuple[float, Dict]:
        """计算行业贡献"""
        positions = portfolio.get("positions", [])
        sector_returns = market_data.get("sector_returns", {})

        sector_weights = {}
        sector_contributions = {}

        # 计算行业权重
        for pos in positions:
            sector = pos.get("sector", "未知")
            weight = pos.get("weight", 0)
            if sector not in sector_weights:
                sector_weights[sector] = 0
            sector_weights[sector] += weight

        # 计算行业贡献
        total_contribution = 0
        for sector, weight in sector_weights.items():
            sector_return = sector_returns.get(sector, 0)
            contribution = weight * sector_return
            sector_contributions[sector] = {
                "weight": weight,
                "return": sector_return,
                "contribution": contribution
            }
            total_contribution += contribution

        return total_contribution, sector_contributions

    def _calculate_stock_contribution(self, portfolio: Dict, market_data: Dict) -> Tuple[float, Dict]:
        """计算个股贡献"""
        positions = portfolio.get("positions", [])
        stock_returns = market_data.get("stock_returns", {})

        stock_contributions = {}
        total_contribution = 0

        for pos in positions:
            code = pos.get("code")
            weight = pos.get("weight", 0)
            stock_return = stock_returns.get(code, 0)

            contribution = weight * stock_return
            stock_contributions[code] = {
                "name": pos.get("name", ""),
                "weight": weight,
                "return": stock_return,
                "contribution": contribution
            }
            total_contribution += contribution

        return total_contribution, stock_contributions

    def _calculate_factor_contribution(self, portfolio: Dict, market_data: Dict) -> Tuple[float, Dict]:
        """计算因子贡献"""
        # 因子贡献 = 因子暴露 × 因子收益
        factor_exposures = portfolio.get("factor_exposures", {})
        factor_returns = market_data.get("factor_returns", {})

        factor_contributions = {}
        total_contribution = 0

        for factor, exposure in factor_exposures.items():
            factor_return = factor_returns.get(factor, 0)
            contribution = exposure * factor_return

            factor_contributions[factor] = {
                "exposure": exposure,
                "return": factor_return,
                "contribution": contribution
            }
            total_contribution += contribution

        return total_contribution, factor_contributions

    def format_report(self, result: AttributionResult) -> str:
        """格式化归因报告"""
        lines = [
            "## 组合归因分析",
            "",
            f"**总收益**: {result.total_return:.2%}",
            "",
            "### 收益分解",
            "",
            f"- 市场贡献: {result.market_contribution:.2%}",
            f"- 行业贡献: {result.sector_return:.2%}",
            f"- 个股贡献: {result.stock_return:.2%}",
            f"- 因子贡献: {result.factor_return:.2%}",
            "",
            "### 行业贡献详情",
            "",
        ]

        # 按贡献排序
        sorted_sectors = sorted(
            result.sector_details.items(),
            key=lambda x: x[1]["contribution"],
            reverse=True
        )

        for sector, details in sorted_sectors[:10]:
            lines.append(
                f"- {sector}: 权重 {details['weight']:.1%}, "
                f"收益 {details['return']:.2%}, "
                f"贡献 {details['contribution']:.2%}"
            )

        lines.extend(["", "### 因子贡献详情", ""])

        sorted_factors = sorted(
            result.factor_details.items(),
            key=lambda x: abs(x[1]["contribution"]),
            reverse=True
        )

        for factor, details in sorted_factors[:10]:
            lines.append(
                f"- {factor}: 暴露 {details['exposure']:.2f}, "
                f"收益 {details['return']:.2%}, "
                f"贡献 {details['contribution']:.2%}"
            )

        return "\n".join(lines)


# 单例
attribution_analyzer = PortfolioAttribution()
