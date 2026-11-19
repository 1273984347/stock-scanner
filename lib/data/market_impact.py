"""Broker 冲击成本模型：ImpactConfig / MarketImpactModel"""
import numpy as np
from dataclasses import dataclass
from typing import Optional

from config import MARKET_CAP_LARGE, MARKET_CAP_MEDIUM


@dataclass
class ImpactConfig:
    """冲击成本模型配置

    冲击成本公式（Almgren-Chriss 2005）：
    1. 平方根模型：impact(bps) = η * σ_daily * sqrt(Q/ADV) * 10000
       - η (eta): 系数，默认 0.1。值越大表示冲击成本对交易量的敏感度越高
       - σ_daily: 日波动率
       - Q: 交易股数，ADV: 日均成交量

    2. 线性模型：impact(bps) = λ * (Q/ADV) * 10000
       - λ (lambda_): 系数，默认 5.0

    调参建议：
    - 高流动性大盘股：η=0.05~0.1, λ=3~5
    - 中小盘股：η=0.1~0.2, λ=5~10
    - 极端市场（涨跌停附近）：η=0.2~0.5
    """
    model: str = "square_root"  # square_root / linear / none
    eta: float = 0.1  # 平方根模型系数（bps 基准）
    lambda_: float = 5.0  # 线性模型系数（bps）
    volume_window: int = 20  # 日均成交量计算窗口
    min_volume_ratio: float = 0.01  # 最小参与率（低于此值不计冲击）
    max_impact_bps: float = 50.0  # 最大冲击成本上限（bps）
    use_volatility_scaling: bool = True  # 是否用波动率缩放


def get_impact_config_by_market_cap(market_cap: float) -> ImpactConfig:
    """根据市值获取对应的冲击成本配置

    Args:
        market_cap: 流通市值（元）

    Returns:
        适合该市值分组的 ImpactConfig

    市值分组标准（参考 A 股市场）：
    - 大盘股：流通市值 > 500亿（> 5e10）
    - 中盘股：流通市值 100~500亿（1e10 ~ 5e10）
    - 小盘股：流通市值 < 100亿（< 1e10）
    """
    if market_cap is None or market_cap <= 0:
        # 未知市值使用中档参数
        return ImpactConfig(model="square_root", eta=0.15, lambda_=7.0,
                          volume_window=20, max_impact_bps=50.0)
    elif market_cap > MARKET_CAP_LARGE:  # 大盘股 > 500亿
        return ImpactConfig(model="square_root", eta=0.07, lambda_=4.0,
                          volume_window=20, max_impact_bps=30.0)
    elif market_cap > MARKET_CAP_MEDIUM:  # 中盘股 100-500亿
        return ImpactConfig(model="square_root", eta=0.12, lambda_=6.0,
                          volume_window=20, max_impact_bps=40.0)
    else:  # 小盘股 < 100亿
        return ImpactConfig(model="square_root", eta=0.18, lambda_=9.0,
                          volume_window=20, max_impact_bps=60.0)


class MarketImpactModel:
    """市场冲击成本模型

    支持两种模型：
    1. 平方根模型（Almgren-Chriss 经典）：
       impact(bps) = η * σ_daily * sqrt(Q / ADV)
       其中 σ_daily = 日波动率，Q = 交易量，ADV = 日均成交量

    2. 线性模型：
       impact(bps) = λ * (Q / ADV) * 10000

    参考：Almgren, Thum, Hauptmann, Li (2005)
    """

    def __init__(self, config: ImpactConfig = None):
        self.config = config or ImpactConfig()

    def estimate_impact_bps(self, shares: int, price: float,
                            daily_volume: float, volatility: float = None) -> float:
        """估算冲击成本（bps）

        Args:
            shares: 交易股数
            price: 当前价格
            daily_volume: 日均成交量（股）
            volatility: 日波动率（标准差，可选）

        Returns:
            冲击成本（bps）
        """
        if self.config.model == "none":
            return 0.0

        if daily_volume <= 0 or shares <= 0:
            return 0.0

        participation_rate = shares / daily_volume

        if participation_rate < self.config.min_volume_ratio:
            return 0.0

        if self.config.model == "linear":
            impact = self.config.lambda_ * participation_rate * 10000
        elif self.config.model == "square_root":
            sigma = volatility if (volatility is not None and self.config.use_volatility_scaling) else 0.02
            impact = self.config.eta * sigma * np.sqrt(participation_rate) * 10000
        else:
            return 0.0

        return min(impact, self.config.max_impact_bps)

    def estimate_impact_price(self, shares: int, base_price: float,
                              daily_volume: float, direction: str = "BUY",
                              volatility: float = None) -> float:
        """估算冲击调整后的成交价

        Args:
            shares: 交易股数
            base_price: 基准价格（收盘价）
            daily_volume: 日均成交量
            direction: BUY / SELL
            volatility: 日波动率

        Returns:
            冲击调整后的成交价
        """
        impact_bps = self.estimate_impact_bps(shares, base_price, daily_volume, volatility)
        impact_pct = impact_bps / 10000.0

        if direction == "BUY":
            return base_price * (1 + impact_pct)
        else:
            return base_price * (1 - impact_pct)
