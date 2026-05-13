"""配置文件 —— 参考 quant-system 的配置架构"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


# 自动加载 .env 文件
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").strip().split("\n"):
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())


# 飞书应用配置（优先读环境变量，未配置则留空）
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_RECEIVE_ID = os.environ.get("FEISHU_RECEIVE_ID", "")
FEISHU_RECEIVE_ID_TYPE = os.environ.get("FEISHU_RECEIVE_ID_TYPE", "open_id")


@dataclass
class FactorConfig:
    """因子配置 —— 定义使用的因子及权重"""
    # 技术因子开关
    use_momentum: bool = True
    use_reversal: bool = True
    use_volatility: bool = True
    use_volume: bool = True
    use_trend: bool = True
    use_rsi: bool = True

    # 因子参数
    momentum_periods: List[int] = field(default_factory=lambda: [5, 10, 20])
    reversal_period: int = 5
    volatility_window: int = 20
    volume_short: int = 5
    volume_long: int = 20
    trend_short: int = 5
    trend_long: int = 20
    rsi_period: int = 14

    # Alpha 合成权重
    factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "momentum_5": 0.15,
        "momentum_10": 0.10,
        "momentum_20": 0.10,
        "reversal_5": 0.05,
        "volatility_20": -0.10,  # 低波动更好
        "volume_ratio": 0.10,
        "trend_strength": 0.15,
        "rsi_14": 0.05,
        "turnover_avg": 0.05,
        "max_drawdown": -0.05,  # 低回撤更好
    })


@dataclass
class ScanConfig:
    """选股参数"""
    # 涨停板策略
    min_zt_days: int = 2

    # 量价突破
    price_change_min: float = 3.0
    price_change_max: float = 9.5

    # 通用过滤
    exclude_st: bool = True
    min_amount: float = 50000  # 最小成交额（万元）

    # 候选数量
    max_candidates: int = 10


@dataclass
class RiskConfig:
    """风控配置 —— 参考 quant-system 的风控规则"""
    # 止损止盈
    stop_loss_pct: float = 5.0
    take_profit_pct: float = 10.0
    trailing_stop: bool = True
    trailing_pct: float = 3.0
    max_hold_days: int = 5

    # 风险预警阈值
    max_single_weight: float = 0.20  # 单票最大仓位
    max_drawdown: float = 0.15  # 最大回撤预警


@dataclass
class SystemConfig:
    """系统全局配置"""
    factor: FactorConfig = field(default_factory=FactorConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


# 预设策略模板
PRESETS = {
    "balanced": SystemConfig(),

    "aggressive": SystemConfig(
        factor=FactorConfig(
            factor_weights={
                "momentum_5": 0.25,
                "momentum_10": 0.20,
                "momentum_20": 0.15,
                "reversal_5": -0.05,
                "volatility_20": -0.05,
                "volume_ratio": 0.15,
                "trend_strength": 0.15,
                "rsi_14": 0.10,
                "turnover_avg": 0.10,
                "max_drawdown": 0.00,
            }
        ),
        scan=ScanConfig(
            min_zt_days=1,
            price_change_min=2.0,
            price_change_max=9.5,
        ),
        risk=RiskConfig(
            stop_loss_pct=8.0,
            take_profit_pct=15.0,
        ),
    ),

    "conservative": SystemConfig(
        factor=FactorConfig(
            factor_weights={
                "momentum_5": 0.05,
                "momentum_10": 0.10,
                "momentum_20": 0.15,
                "reversal_5": 0.10,
                "volatility_20": -0.20,
                "volume_ratio": 0.05,
                "trend_strength": 0.20,
                "rsi_14": 0.05,
                "turnover_avg": 0.00,
                "max_drawdown": -0.10,
            }
        ),
        scan=ScanConfig(
            min_zt_days=3,
            price_change_min=3.0,
            price_change_max=7.0,
        ),
        risk=RiskConfig(
            stop_loss_pct=3.0,
            take_profit_pct=8.0,
        ),
    ),
}


def get_config(preset: str = "balanced") -> SystemConfig:
    """获取配置"""
    return PRESETS.get(preset, PRESETS["balanced"])
