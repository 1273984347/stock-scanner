"""配置类型域 —— 10 个域 dataclass + _sum_one（config 拆包 C11，2026-11-20）

自 config.py 下沉（:119-392 原文逐字搬迁，注释零改动）。
LLMConfig 默认值在**类定义期**读 os.environ —— 因此 _env 必须先于本模块加载
（config/__init__.py 的 import 顺序保证）。
"""
import os
from dataclasses import dataclass, field
from typing import Dict


def _sum_one(weights: Dict[str, float]) -> Dict[str, float]:
    """声明即归一：让权重表定义处就满足「和为 1.0」。

    背景（坑表 P-102）：``strategy_weights`` 六个 0.167 = 1.002，conservative 的
    ``factor_weights`` 声明和仅 0.40 —— 全靠 ``get_config`` / ``weighted_signal_combination``
    在运行期反复归一化。那是**补丁**：声明仍然是错的，警告每次扫描都刷屏，
    且外部直接读 PRESETS 的代码拿到的是未归一值。这里在定义处修正，
    运行期兜底保留，仅用于 JSON 热覆盖等外部输入。
    """
    total = sum(weights.values())
    if total <= 0:
        return dict(weights)
    return {k: v / total for k, v in weights.items()}


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
    factor_weights: Dict[str, float] = field(default_factory=lambda: _sum_one({
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
    }))

    # 多策略权重（等权，避免数据窥探）
    strategy_weights: Dict[str, float] = field(default_factory=lambda: _sum_one({
        "trend": 0.167,
        "mean_reversion": 0.167,
        "momentum": 0.167,
        "volatility": 0.167,
        "stat_arb": 0.167,
        "trend_breakout": 0.167,
    }))

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
    # 自定义 OpenAI 兼容端点
    # 批次 C / P2-14：原注释写 `OLLM_API_BASE_URL`、代码读 `OLLAMA_API_BASE_URL`，
    # 按文档配置不生效且 key 仍发往公网默认端点。统一主名为 `LLM_API_BASE_URL`，
    # 保留两条旧名兼容（优先级从高到低）。
    api_base_url: str = (os.environ.get("LLM_API_BASE_URL")
                         or os.environ.get("OLLAMA_API_BASE_URL")
                         or os.environ.get("OLLAMA_BASE_URL", ""))
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
