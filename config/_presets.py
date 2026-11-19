"""预设域 —— 三套策略模板（config 拆包 C11，2026-11-20，自 config.py :396-450 逐字搬迁）"""
from config._types import FactorConfig, RiskConfig, ScanConfig, SystemConfig, _sum_one

# 预设策略模板
PRESETS = {
    "balanced": SystemConfig(),

    "aggressive": SystemConfig(
        factor=FactorConfig(
            factor_weights=_sum_one({
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
            })
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
            factor_weights=_sum_one({
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
            })
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
