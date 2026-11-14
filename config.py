"""配置文件 —— 参考 quant-system 的配置架构"""

import copy
import dataclasses
import json
import logging
import math
import os
import typing
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

# TDX（通达信）数据源配置 —— lib/ 内一律从本模块取值，禁止绕过重复 os.environ.get
TDX_DATA_PATH = os.environ.get("TDX_DATA_PATH", "D:/TDX/vipdoc")
TDX_HOST = os.environ.get("TDX_HOST", "180.153.39.210")
TDX_PORT = int(os.environ.get("TDX_PORT", "7709"))
TDX_REF_PATH = os.environ.get("TDX_REF_PATH", "D:/TDX/vipdoc/ref")

# 告警推送失败降级目录（环境变量可覆盖，便于测试/部署）
ALERT_FALLBACK_DIR = os.environ.get(
    "ALERT_FALLBACK_DIR",
    str(Path(__file__).parent / "logs" / "alert_fallback"),
)

# A 股年化交易天数（原 lib/ 内 4 处重复定义 + 3 处裸 252 年化字面量的单一真相源）
TRADING_DAYS_PER_YEAR = 252


def api_secret_key() -> str:
    """调用时读取（保持 monkeypatch.setenv 语义），env 名归 config 所有。"""
    return os.getenv("API_SECRET_KEY", "")


def tushare_token() -> str:
    """调用时读取（tushare pro api 懒初始化时取 token）。"""
    return os.environ.get("TUSHARE_TOKEN", "")


def stock_font_path_override() -> str:
    """调用时读取；返回字体覆盖路径（空串 = 无覆盖）。"""
    return os.environ.get("STOCK_FONT_PATH", "").strip()


# ---- RF-01 Phase 4：统一评分内核开关 ----------------------------------------
# P7-3（2026-11-12，ADR-0014）：灰度已收口，ScoringKernel 是唯一评分路径，
# FEATURE_UNIFIED_SCORING / unified_scoring_enabled 已删除；
# 数值口径由 L5 评分基线（9 入口 × 285 样本，容差 1e-9）继续锁定。

# ---- RF-01 Phase 6：异步 offload 开关（默认 off，可按消费者灰度）----------
# 取值（与已收口的评分灰度同构）：
#   0/off/false/no/""      -> 全部同步直调（默认，逐字节等价于改造前）
#   1/on/true/yes/all      -> 全部已接入消费者走线程池
#   "backtest,walkforward" -> 仅列出的消费者走线程池
# 回滚：环境变量改回 0 或删除即可，无需回滚部署。
# 判定只允许在 lib/infra/async_offload.py::run_blocking（RULES §1.7 T2）。
FEATURE_ASYNC_OFFLOAD_DEFAULT = "0"


def async_offload_enabled(consumer: str = "default") -> bool:
    """动态读环境变量，便于秒级切换；不依赖模块导入时的快照。"""
    raw = os.environ.get(
        "FEATURE_ASYNC_OFFLOAD", FEATURE_ASYNC_OFFLOAD_DEFAULT
    ).strip().lower()
    if raw in ("1", "true", "yes", "on", "all", "*"):
        return True
    if raw in ("", "0", "false", "no", "off", "none"):
        return False
    return consumer in {part.strip() for part in raw.split(",") if part.strip()}


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
    hist_max_age_hours: float = 24.0  # 原 4.0：日线数据按日更新，4h TTL 导致每日首次扫描冷重拉，放大 5+ 分钟瓶颈；提至 24h
    # 注：原 index_max_age_hours/realtime_max_age_minutes 已删除（未被读取）
    macro_max_age_hours: float = 24.0


@dataclass
class BrokerConfig:
    """Broker / Paper Trading 配置 (2026-06-09 magic number 集中)

    集中以下 magic number:
    - missing_days_threshold: 持仓无行情天数阈值, 触发强制清理 (退市检测)
    - 板块涨跌停幅度: ST / 主板 / 创业板 / 科创板 / 北交所 / 默认
    - 退市清理动作: 强制平仓 / 释放 position_count 槽位 / 记录退市 set
    """
    # 退市检测：连续无行情 N 天 → 强制清理持仓 (P0-3 触发)
    missing_days_threshold: int = 60

    # 板块涨跌停幅度 (A 股规则)
    # 来源: 上交所/深交所交易规则 + 北交所/科创板特定规则
    limit_pct_st: float = 0.05       # ST/*ST ±5%
    limit_pct_main: float = 0.10     # 主板 (60/00 开头) ±10%
    limit_pct_chinext: float = 0.20  # 创业板 (30 开头) ±20%
    limit_pct_star: float = 0.20     # 科创板 (68 开头) ±20%
    limit_pct_bse: float = 0.30     # 北交所 (8/4 开头) ±30%
    limit_pct_default: float = 0.10 # 默认 (兜底)

    # 退市清理动作 (2026-06-09 D3.2 集中)
    force_close_on_delisting: bool = True  # 强制从 holdings 移除 (强平)
    release_position_slot: bool = True    # 释放 position_count 槽位
    record_delisted_set: bool = True      # 记录到 _delisted_codes set
    book_cash_on_delisting: bool = False  # 现金不入账 (无对手盘, P0-3 决策)


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
    # 批次 A / P0-1：PRESETS 是模块级共享单例，直接返回会让任一调用方的修改
    # （含 _apply_overrides 的 setattr）永久污染全局口径，且跨 preset 串联。
    # 深拷贝切断共享状态；数值本身不变，L4/L5 口径不受影响。
    config = copy.deepcopy(PRESETS.get(preset, PRESETS["balanced"]))

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

# ---------------------------------------------------------------------------
# 批次 A / P0-1 根因修：热更新白名单不再手工维护。
#
# 旧实现的手写 _CONFIG_SCHEMA 只覆盖 4 个类 / 11 个字段（其中 MLConfig.min_train_samples
# 与 CacheConfig.ttl_minutes 在 dataclass 里根本不存在），而 _apply_overrides 对
# 「字段存在但没登记进 schema」的情况仍然无条件 setattr ⇒ 任意 dict 可改写
# 风控 / 因子权重 / LLM 参数，并因 PRESETS 共享单例而永久污染。
# 根因不是「漏登记两个字段」，而是「白名单靠人同步」：新增字段必然再次漏登记。
# 现在白名单 = dataclass 字段集合（由类型标注自动推导），新增字段自动进入校验，
# 删除字段自动失去写入权；范围约束单独登记且由机器判据校验键必须存在。
# ---------------------------------------------------------------------------

# 需要限制取值范围的字段；(min, max) 为 None 表示不限。
_RANGE_CONSTRAINTS: dict = {
    ("RiskConfig", "stop_loss_pct"): (0.5, 20.0),
    ("RiskConfig", "take_profit_pct"): (1.0, 50.0),
    ("RiskConfig", "trailing_pct"): (0.1, 10.0),
    ("RiskConfig", "max_hold_days"): (1, 30),
    ("RiskConfig", "max_single_weight"): (0.01, 1.0),
    ("ScanConfig", "min_zt_days"): (0, 30),
    ("ScanConfig", "price_change_min"): (-10.0, 10.0),
    ("ScanConfig", "price_change_max"): (-10.0, 20.0),
}

# 凭据 / 请求端点类字段一律禁止热更新。按后缀匹配，新增同类字段自动被拒，
# 不需要有人记得把它加进黑名单。
_SENSITIVE_SUFFIXES = ("_api_key", "_secret", "_token", "_endpoint", "_base_url")

# 容器类值的上界，防止用超大结构撑爆内存或写盘。
_MAX_CONTAINER_ITEMS = 256
_MAX_STRING_LEN = 4096
_MAX_CONTAINER_DEPTH = 3


def _field_kind(annotation) -> str:
    """把类型标注映射成校验用的 kind 字符串。"""
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"
    origin = typing.get_origin(annotation)
    if annotation is list or origin is list:
        return "list"
    if annotation is dict or origin is dict:
        args = typing.get_args(annotation)
        # Dict[str, float] 是因子权重类字段：值必须是有限数值。
        # 旧漏洞正是 {"factor": {"factor_weights": {"x": "abc"}}} 让
        # sum(weights.values()) 抛 TypeError → 所有调用 get_config() 的端点 500。
        if len(args) == 2 and args[0] is str and args[1] is float:
            return "float_dict"
        return "dict"
    if isinstance(annotation, type) and dataclasses.is_dataclass(annotation):
        return "dataclass"
    return "any"


def _nested_dataclass(cls, field_name: str):
    """取嵌套 dataclass 字段的真实类型（供写入侧递归校验）。"""
    return typing.get_type_hints(cls).get(field_name)


def _build_config_schema(root_cls=SystemConfig) -> dict:
    """由 dataclass 字段集合生成 {类名: {字段名: (kind, min, max, desc)}}。"""
    schema: dict = {}

    def walk(cls) -> None:
        if cls.__name__ in schema:
            return
        hints = typing.get_type_hints(cls)
        fields: dict = {}
        for f in dataclasses.fields(cls):
            if f.name.endswith(_SENSITIVE_SUFFIXES):
                continue
            kind = _field_kind(hints.get(f.name, f.type))
            lo, hi = _RANGE_CONSTRAINTS.get((cls.__name__, f.name), (None, None))
            fields[f.name] = (kind, lo, hi, f.name)
            if kind == "dataclass":
                nested = _nested_dataclass(cls, f.name)
                if nested is not None:
                    walk(nested)
        schema[cls.__name__] = fields

    walk(root_cls)
    return schema


_CONFIG_SCHEMA = _build_config_schema()


def _validate_container(value, expected, depth: int = 0) -> bool:
    """递归校验 dict/list 的结构与规模，禁止 NaN/Inf 与超深嵌套。"""
    if depth > _MAX_CONTAINER_DEPTH:
        return False
    if expected is dict:
        if not isinstance(value, dict) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(
            isinstance(k, str) and len(k) <= 128 and _validate_container(v, None, depth + 1)
            for k, v in value.items()
        )
    if expected is list:
        if not isinstance(value, list) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(_validate_container(v, None, depth + 1) for v in value)
    if isinstance(value, dict):
        return _validate_container(value, dict, depth)
    if isinstance(value, list):
        return _validate_container(value, list, depth)
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, str) and len(value) > _MAX_STRING_LEN:
            return False
        if isinstance(value, float) and not math.isfinite(value):
            return False
        return True
    return False


def _validate_field_value(field_name: str, expected_type, value, min_val, max_val) -> bool:
    """按自动生成白名单的 kind 校验值。

    保留历史 5 参数签名；expected_type 现在是 kind 字符串（如 "float"/"dict"），
    兼容旧代码传入的 Python type 对象。
    """
    kind = expected_type
    if isinstance(kind, type):
        kind = {bool: "bool", int: "int", float: "float", str: "str"}.get(kind, "any")

    if kind == "dataclass":
        return isinstance(value, dict)
    if kind == "bool":
        if not isinstance(value, bool):
            return False
    elif kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    elif kind == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(float(value)):
            return False
    elif kind == "str":
        if not isinstance(value, str) or len(value) > _MAX_STRING_LEN:
            return False
    elif kind == "float_dict":
        if not isinstance(value, dict) or len(value) > _MAX_CONTAINER_ITEMS:
            return False
        return all(
            isinstance(k, str) and len(k) <= 128
            and isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v))
            for k, v in value.items()
        )
    elif kind == "dict":
        return _validate_container(value, dict)
    elif kind == "list":
        return _validate_container(value, list)
    elif kind == "any":
        return _validate_container(value, None)
    else:
        return False

    if min_val is not None and value < min_val:
        return False
    if max_val is not None and value > max_val:
        return False
    return True


def validate_config_overrides(data: dict) -> dict:
    """写入侧严格校验：未知字段 / 非法值直接抛 ValueError（含路径），不静默丢弃。

    与读取侧 _apply_overrides 的「跳过非法值」不同：写入必须失败可见，
    否则用户会以为配置已生效。返回清洗后的 dict（只含白名单字段）。
    """
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")

    def walk(cls, obj: dict, path: str) -> dict:
        schema_root = _CONFIG_SCHEMA.get(cls.__name__, {})
        out: dict = {}
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            spec = schema_root.get(key)
            if spec is None:
                raise ValueError(f"字段 {here} 不允许热更新")
            kind, min_val, max_val, _desc = spec
            if kind == "dataclass":
                if not isinstance(value, dict):
                    raise ValueError(f"字段 {here} 必须是对象")
                nested = _nested_dataclass(cls, key)
                if nested is None:
                    raise ValueError(f"字段 {here} 缺少嵌套类型")
                out[key] = walk(nested, value, here)
            else:
                if not _validate_field_value(key, kind, value, min_val, max_val):
                    raise ValueError(f"字段 {here} 值不合法")
                out[key] = value
        return out

    return walk(SystemConfig, data, "")


def public_config_dict(cfg) -> dict:
    """导出侧白名单：与热更新白名单同一份 schema。

    保证 export → import 往返自洽，且不会把 LLM 长期密钥带出进程（P1-9 同源问题）。
    """
    schema_root = _CONFIG_SCHEMA.get(type(cfg).__name__, {})
    out: dict = {}
    for key, (kind, _lo, _hi, _desc) in schema_root.items():
        value = getattr(cfg, key, None)
        out[key] = public_config_dict(value) if kind == "dataclass" else copy.deepcopy(value)
    return out


def write_config_overrides(data: dict) -> dict:
    """配置写盘的唯一入口：严格校验 + 同目录临时文件 + os.replace 原子替换。

    读写共用 _CONFIG_FILE，避免两侧落点分裂；非原子写会让热更新读到半截 JSON。
    """
    validated = validate_config_overrides(data)
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_FILE.with_name(_CONFIG_FILE.name + ".tmp")
    tmp.write_text(json.dumps(validated, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _CONFIG_FILE)
    return validated


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
    """按自动生成白名单递归应用覆盖（读取侧兼容历史文件）。

    与写入侧的严格拒绝不同，这里对未登记字段 / 非法值一律跳过并记 warning，
    保证旧的 config.json 不会让服务起不来；写入侧由 validate_config_overrides 兜底。
    """
    schema_root = _CONFIG_SCHEMA.get(type(cfg).__name__, {})

    for key, value in overrides.items():
        spec = schema_root.get(key)
        if spec is None:
            logging.getLogger(__name__).warning(
                f"config.json 中的字段 {type(cfg).__name__}.{key} 不在热更新白名单，已跳过"
            )
            continue
        kind, min_val, max_val, _desc = spec
        if kind == "dataclass":
            if isinstance(value, dict):
                _apply_overrides(getattr(cfg, key), value)
            else:
                logging.getLogger(__name__).warning(
                    f"config.json 字段 {type(cfg).__name__}.{key} 应为对象，已跳过"
                )
            continue
        if not _validate_field_value(key, kind, value, min_val, max_val):
            logging.getLogger(__name__).warning(
                f"config.json 字段 {type(cfg).__name__}.{key} 值 {value!r} 不符合白名单 "
                f"(类型={kind}, 范围=[{min_val}, {max_val}])，保持原值"
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
