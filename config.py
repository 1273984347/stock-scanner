"""配置文件 —— 参考 quant-system 的配置架构"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict


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

    # 注：原 momentum_periods/reversal_period/volatility_window 等参数字段已删除
    # 原因：factors.py 中硬编码了相同默认值，从未读取 config 字段

    # Alpha 合成权重（等权，避免数据窥探）
    # 之前基于历史 IC 分配权重，属于样本内优化。改为等权。
    factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "volume_ratio": 0.10,
        "momentum_5": 0.10,
        "momentum_20": 0.10,
        "reversal_5": 0.10,
        "volatility_20": 0.10,
        "trend_strength": 0.10,
        "rsi_14": 0.10,
        "adx": 0.10,
        "williams_r": 0.10,
        "cci": 0.10,
    })

    # 多策略权重（等权，避免数据窥探）
    strategy_weights: Dict[str, float] = field(default_factory=lambda: {
        "trend": 0.167,
        "mean_reversion": 0.167,
        "momentum": 0.167,
        "volatility": 0.167,
        "stat_arb": 0.167,
        "trend_breakout": 0.167,
    })

    # IC 加权因子权重（等权，避免数据窥探）
    # 之前基于历史 IC 分配权重（volume_ratio=0.22 等）属于样本内优化，
    # 导致回测结果不可信。改为等权 0.10，消除数据窥探偏差。
    ic_factor_weights: Dict[str, float] = field(default_factory=lambda: {
        "volume_ratio": 0.10,
        "adx": 0.10,
        "williams_r": 0.10,
        "cci": 0.10,
        "rsi_14": 0.10,
        "trend_strength": 0.10,
        "kdj_j": 0.10,
        "kdj_resonance_score": 0.10,
        "momentum_20": 0.10,
        "volatility_20": 0.10,
    })

    # 机会评分权重（P2-1：从 lib/opportunity_scorer.py 提到 config.py，便于调参）
    # 公式：score = regime×30% + alpha×25% + sector×20% + trend×15% + volume×10%
    opp_score_weights: Dict[str, float] = field(default_factory=lambda: {
        "regime": 0.30,
        "alpha": 0.25,
        "sector": 0.20,
        "trend": 0.15,
        "volume": 0.10,
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
    # 注：原 min_amount 已删除（未被任何模块读取）

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

    # 分仓止盈级别：(profit_threshold, sell_ratio, description)
    partial_exit_enabled: bool = True
    partial_exit_levels: list = field(default_factory=lambda: [
        {"profit_threshold": 0.60, "sell_ratio": 0.33, "description": "第一档：+60% 减 1/3"},
        {"profit_threshold": 0.30, "sell_ratio": 0.33, "description": "第二档：+30% 减 1/3（中线持有后）"},
        {"profit_threshold": 0.00, "sell_ratio": 1.00, "description": "第三档：回到成本价 全部退出"},
    ])
    use_fixed_take_profit: bool = False  # disable fixed take-profit by default

    # 风险预警阈值
    max_single_weight: float = 0.20  # 单票最大仓位
    # 注：原 max_drawdown/daily_loss_limit/weekly_loss_limit 已删除
    # 原因：lib/risk_manager.py 有同名 RiskConfig 类的本地字段，config.py 的副本被遮蔽

    # === Deferred config migration (P2-config-migration-deferred) ===
    # 以下常量原在 lib/dynamic_exit.py / lib/partial_exit.py 模块顶层，
    # 2026-06-08 §4.2 A3 迁移到 config 统一管理。
    consec_loss_stop: int = 5           # 连亏熔断触发次数 (原 DEFAULT_CONSEC_LOSS_STOP)
    consec_loss_cooldown_days: int = 20 # 连亏冷却天数 (原 DEFAULT_CONSEC_LOSS_COOLDOWN_DAYS)
    forced_threshold: float = 0.5       # "勉强单"阈值 (原 FORCED_THRESHOLD)
    exit_days_map: dict = field(default_factory=lambda: {
        "TREND_UP": 20,         # 让利润奔跑
        "MEAN_REVERSION": 10,   # 快进快出
        "HIGH_VOLATILITY": 5,   # 缩短周期
        "LOW_VOLATILITY": 15,   # 耐心等待
        "TREND_DOWN": 0,        # 不开仓
        "RISK_OFF": 0,          # 禁止一切开仓
    })  # 原 DEFAULT_EXIT_DAYS_MAP

    # === Bayesian updater 阈值 (P2-config-migration-deferred → §4.2 A3 迁移) ===
    # 原 lib/bayesian_updater.py 模块级常量
    prior_win_rate: float = 0.5          # 无数据时先验胜率
    bayes_min_samples: int = 5           # 贝叶斯平滑收缩强度 (k)
    high_win_threshold: float = 0.6      # 权重上调起点（高胜率分界）
    low_win_threshold: float = 0.4       # 权重下调起点（低胜率分界）
    weight_slope: float = 2.5            # 权重随胜率变化斜率
    weight_ceiling: float = 1.5          # 权重上限
    weight_floor: float = 0.5            # 权重下限
    min_display_samples: int = 3         # 摘要展示的最小样本数


@dataclass
class MLConfig:
    """机器学习配置（当前为占位符）"""
    # 注：原 use_ml/ml_weight/enable_xgb/enable_lgbm/enable_sentiment/weekly_retrain_day/model_dir
    # 7 个字段已删除。ML 模块（ml_trainer.py/ml_predictor.py）使用本地参数，
    # 未消费 config.MLConfig 的任何字段。
    # === Deferred config migration (P2-config-migration-deferred) ===
    default_xgb_params: dict = field(default_factory=lambda: {
        "objective": "rank:ndcg",
        "eval_metric": "ndcg@5",
        "learning_rate": 0.1,
        "max_depth": 6,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "lambda": 1.0,
        "seed": 42,
    })  # 原 ml_trainer.DEFAULT_XGB_PARAMS


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
    # 注：原 rebalance_period 已删除（RebalanceConfig 有自己的本地副本）
    min_daily_volume: float = 5e6
    max_impact_ratio: float = 0.05


@dataclass
class MonitorConfig:
    """监控配置"""
    # 注：原 ic_window/ic_alert_threshold/model_ttl_days/rollback_ic_ratio 已删除
    # 原因：未被主流程消费（ml_monitor.py/factor_strategy.py 有本地副本）
    ic_alert_consecutive: int = 5
    factor_ic_threshold: float = 0.02
    factor_corr_threshold: float = 0.95
    # === Deferred config migration (P2-config-migration-deferred) ===
    min_nb_samples: int = 5  # 北向资金最小样本数 (原 alert_monitor.MIN_NB_SAMPLES)


@dataclass
class CacheConfig:
    """缓存 TTL 配置"""
    hist_max_age_hours: float = 4.0
    # 注：原 index_max_age_hours/realtime_max_age_minutes 已删除（未被读取）
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
    """获取配置（带权重和验证，支持 JSON 文件覆盖）"""
    logger = logging.getLogger(__name__)
    if preset not in PRESETS:
        logger.warning(f"未知preset: {preset}, 使用balanced")
    config = PRESETS.get(preset, PRESETS["balanced"])

    # 应用 JSON 覆盖（热更新：文件变化时自动重新加载）
    overrides = _load_json_overrides()
    if overrides:
        _apply_overrides(config, overrides)

    # 验证因子权重和
    total = sum(config.factor.factor_weights.values())
    if abs(total - 1.0) > 0.01:
        logger.warning(f"preset '{preset}' 因子权重和 = {total:.4f}（应为 1.0），自动归一化")
        config.factor.factor_weights = {k: v / total for k, v in config.factor.factor_weights.items()}

    return config


# ============================================================================
# JSON 配置文件热更新
# ============================================================================

_CONFIG_FILE = Path("config.json")
_config_mtime: float = 0
_cached_overrides: dict = {}

# P0-1 修复（2026-06-05）：配置热更新 schema/type/range 验证
# 防止 {"stop_loss_pct": "5.0"}（字符串）等错误类型污染风控字段
# 格式: {ClassName: {field_name: (type, min, max, description)}}
# type: bool | int | float | str
# min/max: 范围限制（None 表示无限制）
# key 用 type(cfg).__name__（类名）匹配
_CONFIG_SCHEMA = {
    "RiskConfig": {
        "stop_loss_pct": (float, 0.5, 20.0, "止损百分比"),
        "take_profit_pct": (float, 1.0, 50.0, "止盈百分比"),
        "trailing_stop": (bool, None, None, "是否启用移动止损"),
        "trailing_pct": (float, 0.1, 10.0, "移动止损百分比"),
        "max_hold_days": (int, 1, 30, "最大持有天数"),
        "max_single_weight": (float, 0.01, 1.0, "单票最大仓位"),
        "use_fixed_take_profit": (bool, None, None, "是否使用固定止盈"),
    },
    "ScanConfig": {
        "min_zt_days": (int, 0, 30, "最小涨停天数"),
        "price_change_min": (float, -10.0, 10.0, "最小涨跌幅(%)"),
        "price_change_max": (float, -10.0, 20.0, "最大涨跌幅(%)"),
    },
    "MLConfig": {
        "min_train_samples": (int, 50, 10000, "最小训练样本数"),
    },
    "CacheConfig": {
        "ttl_minutes": (int, 1, 1440, "缓存 TTL（分钟）"),
    },
}


def _validate_field_value(field_name: str, expected_type: type, value, min_val, max_val) -> bool:
    """验证字段值是否符合 schema 定义的类型和范围

    Returns:
        True  - 值合法
        False - 值非法（类型错或范围外）
    """
    # bool 是 int 的子类，要先检查 bool 避免误判
    if expected_type is bool:
        if not isinstance(value, bool):
            return False
        return True
    if expected_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif expected_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    elif expected_type is str:
        if not isinstance(value, str):
            return False
    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def _load_json_overrides() -> dict:
    """从 config.json 加载配置覆盖（带 mtime 缓存）"""
    global _config_mtime, _cached_overrides

    if not _CONFIG_FILE.exists():
        return {}

    try:
        mtime = _CONFIG_FILE.stat().st_mtime
        if mtime == _config_mtime:
            return _cached_overrides

        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            _cached_overrides = json.load(f)
        _config_mtime = mtime
        logging.getLogger(__name__).info(f"已加载 config.json（{_CONFIG_FILE}）")
        return _cached_overrides
    except Exception as e:
        logging.getLogger(__name__).warning(f"加载 config.json 失败: {e}")
        return {}


def _apply_overrides(cfg: SystemConfig, overrides: dict) -> None:
    """递归应用配置覆盖

    P0-1 修复（2026-06-05）：在应用前对值做 schema/type/range 验证
    - 字段在 _CONFIG_SCHEMA 中 → 按 (type, min, max) 验证
    - 字段不在 _CONFIG_SCHEMA 中 → 跳过（避免误改）
    - 类型错或范围外 → 打 warning，**保持原值**（不静默 setattr）
    """
    schema_root = _CONFIG_SCHEMA.get(type(cfg).__name__, {})

    for key, value in overrides.items():
        if not hasattr(cfg, key):
            logging.getLogger(__name__).warning(f"config.json 中的未知字段: {key}")
            continue
        attr = getattr(cfg, key)
        if isinstance(value, dict) and hasattr(attr, '__dataclass_fields__'):
            # 递归到子 config
            _apply_overrides(attr, value)
        else:
            # P0-1 修复：先校验再 setattr
            if key in schema_root:
                expected_type, min_val, max_val, desc = schema_root[key]
                if not _validate_field_value(key, expected_type, value, min_val, max_val):
                    logging.getLogger(__name__).warning(
                        f"config.json 字段 {type(cfg).__name__}.{key} 值 {value!r} 不符合 schema "
                        f"(类型={expected_type.__name__}, 范围=[{min_val}, {max_val}])，"
                        f"保持原值"
                    )
                    continue
            setattr(cfg, key, value)


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
STAMP_DUTY_RATE = 0.0005  # A股印花税 0.05%（万分之五），仅卖出收取
                             # 2023-08-28 起从 0.1% 降至 0.05% (财政部公告)
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
