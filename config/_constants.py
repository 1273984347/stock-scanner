"""常量域 —— 交易成本/组合/打分卡/宏观门控等散常量
（config 拆包 C11，2026-11-20，自 config.py :772-815 逐字搬迁）"""

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
