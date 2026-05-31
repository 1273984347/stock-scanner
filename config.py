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

# TDX（通达信）数据源配置
TDX_DATA_PATH = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")
TDX_HOST = os.environ.get("TDX_HOST", "180.153.39.210")
TDX_PORT = int(os.environ.get("TDX_PORT", "7709"))


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

    # 数据因子开关（2026-05-30 新增）
    use_block: bool = True
    use_valuation: bool = True
    use_fund_hold: bool = True
    use_dividend: bool = True

    # 因子参数
    momentum_periods: List[int] = field(default_factory=lambda: [5, 10, 20])
    reversal_period: int = 5
    volatility_window: int = 20
    volume_short: int = 5
    volume_long: int = 20
    trend_short: int = 5
    trend_long: int = 20
    rsi_period: int = 14

    # Alpha 合成权重（基于 IC 评价 2026-05-30）
    # IC: volume_ratio(0.152) > adx(0.092) > williams_r(0.070) > cci(0.065) > rsi(0.061)
    factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "volume_ratio": 0.25,     # IC=0.152, 最强因子
        "momentum_5": 0.05,       # IC=0.031
        "momentum_20": 0.10,      # IC=0.052
        "reversal_5": 0.00,       # 无 IC 数据
        "volatility_20": 0.08,    # IC=0.048
        "trend_strength": 0.09,   # IC=0.055
        "rsi_14": 0.10,           # IC=0.061
        "adx": 0.15,              # IC=0.092
        "williams_r": 0.12,       # IC=0.070
        "cci": 0.06,              # IC=0.065
    })

    # 多策略权重（基于 IC 评价 2026-05-30）
    strategy_weights: Dict[str, float] = field(default_factory=lambda: {
        "trend": 0.15,
        "mean_reversion": 0.10,
        "momentum": 0.20,
        "volatility": 0.15,
        "stat_arb": 0.10,
        "trend_breakout": 0.30,
    })

    # IC 加权因子权重（基于 2026-05-30 IC 评价，30 只股票 × 500 天）
    ic_factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "volume_ratio": 0.22,
        "adx": 0.14,
        "williams_r": 0.11,
        "cci": 0.09,
        "rsi_14": 0.09,
        "trend_strength": 0.08,
        "kdj_j": 0.08,
        "kdj_resonance_score": 0.08,
        "momentum_20": 0.07,
        "volatility_20": 0.04,
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

    # 分批止盈
    partial_exit_enabled: bool = True
    partial_exit_levels: List[float] = field(default_factory=lambda: [0.10, 0.20])  # +10%, +20%
    partial_exit_ratios: List[float] = field(default_factory=lambda: [0.33, 0.33])  # reduce 1/3 each
    use_fixed_take_profit: bool = False  # disable fixed take-profit by default

    # 风险预警阈值
    max_single_weight: float = 0.20  # 单票最大仓位
    max_drawdown: float = 0.15       # 最大回撤预警（15%）
    daily_loss_limit: float = 0.03   # 单日亏损限制（3%）
    weekly_loss_limit: float = 0.08  # 周亏损限制（8%）


@dataclass
class MLConfig:
    """机器学习配置"""
    use_ml: bool = True
    ml_weight: float = 0.20
    enable_xgb: bool = True
    enable_lgbm: bool = True
    enable_sentiment: bool = True
    weekly_retrain_day: str = "Mon"
    model_dir: str = "cache/models/"


@dataclass
class LLMConfig:
    """LLM API 配置"""
    # 自定义 OpenAI 兼容端点（通过环境变量 OLLM_API_BASE_URL 设置）
    api_base_url: str = os.environ.get("OLLAMA_API_BASE_URL", os.environ.get("OLLAMA_BASE_URL", ""))
    # 各 provider API Key（优先读环境变量）
    deepseek_api_key: str = os.environ.get("DEEPSEEK_API_KEY", "")
    kimi_api_key: str = os.environ.get("KIMI_API_KEY", "")
    mimo_api_key: str = os.environ.get("MIMO_API_KEY", "")
    # 默认模型
    deepseek_model: str = "deepseek-v4-flash"
    kimi_model: str = "kimi-k2.6"
    mimo_model: str = "mimo-v2.5"
    # API 端点（可覆盖）
    deepseek_endpoint: str = "https://api.deepseek.com/v1/chat/completions"
    kimi_endpoint: str = "https://api.moonshot.cn/v1/chat/completions"
    mimo_endpoint: str = "https://api.xiaomimimo.com/v1/chat/completions"
    # 通用参数
    temperature: float = 0.3
    max_tokens: int = 500
    timeout: int = 30


@dataclass
class PortfolioConfig:
    """投资组合配置"""
    optimize_method: str = "equal_weight"  # equal_weight / risk_parity / mean_variance
    max_single_weight: float = 0.20
    max_sector_exposure: float = 0.30
    top_n: int = 10
    rebalance_period: int = 5
    min_daily_volume: float = 5e6
    max_impact_ratio: float = 0.05


@dataclass
class MonitorConfig:
    """监控配置"""
    ic_window: int = 20
    ic_alert_threshold: float = 0.0
    ic_alert_consecutive: int = 5
    model_ttl_days: int = 7
    rollback_ic_ratio: float = 0.8
    factor_ic_threshold: float = 0.02
    factor_corr_threshold: float = 0.95


@dataclass
class CacheConfig:
    """缓存 TTL 配置"""
    hist_max_age_hours: float = 4.0
    index_max_age_hours: float = 4.0
    realtime_max_age_minutes: float = 0.5
    macro_max_age_hours: float = 24.0


@dataclass
class SystemConfig:
    """系统全局配置"""
    factor: FactorConfig = field(default_factory=FactorConfig)
    scan: ScanConfig = field(default_factory=ScanConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)


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
    """获取配置（带权重和验证）"""
    import logging
    logger = logging.getLogger(__name__)
    if preset not in PRESETS:
        logger.warning(f"未知preset: {preset}, 使用balanced")
    config = PRESETS.get(preset, PRESETS["balanced"])

    # 验证因子权重和
    total = sum(config.factor.factor_weights.values())
    if abs(total - 1.0) > 0.01:
        logger.warning(f"preset '{preset}' 因子权重和 = {total:.4f}（应为 1.0），自动归一化")
        config.factor.factor_weights = {k: v / total for k, v in config.factor.factor_weights.items()}

    return config


# ============================================================================
# 从 lib/config.py 迁移的全局常量（保持向后兼容）
# ============================================================================

# 缓存配置
CACHE_RATE_LIMIT = 3.0

# 组合配置
DEFAULT_CORRELATION = 0.2
RISK_AVERSION_CALIBRATED = 2.0
RISK_AVERSION_UNCALIBRATED = 4.0
MAX_POSITIONS = 10
TURNOVER_PENALTY = 0.005

# 交易配置
COMMISSION_RATE = 0.00025
STAMP_DUTY_RATE = 0.0005
MIN_COMMISSION = 5.0
SLIPPAGE_RATE = 0.002  # 0.2% 滑点（A股小盘股实际可达0.3%-0.5%，取保守值）

# 市值分组阈值
MARKET_CAP_LARGE = 5e10   # > 500亿
MARKET_CAP_MEDIUM = 1e10  # > 100亿

# 打分卡权重
LAYER_WEIGHTS = {"L1": 0.25, "L2": 0.25, "L3": 0.20, "L4": 0.30}

# 宏观门控阈值（SHIBOR）
SHIBOR_TIGHT_THRESHOLD = 3.5    # 紧缩：禁止开仓
SHIBOR_CAUTION_THRESHOLD = 2.5  # 警戒：仓位减半

# IC 冗余阈值
IC_REDUNDANCY_THRESHOLD = 0.70

# 波动率调整系数
HIGH_VOL_POSITION_SCALAR = 0.5
MEDIUM_VOL_POSITION_SCALAR = 0.75
LOW_VOL_POSITION_SCALAR = 1.2

# 止损止盈默认值
DEFAULT_STOP_LOSS = 0.05
DEFAULT_TAKE_PROFIT = 0.10
