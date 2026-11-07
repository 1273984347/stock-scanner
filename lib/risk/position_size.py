"""
Risk-Sized Position Sizing —— 基于风险预算的仓位计算
参考 cryptoquant-ai 的 calculateRiskSizedQuantity 设计
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class PositionSizeConfig:
    risk_per_trade_pct: float = 0.5
    max_position_pct: float = 0.20
    max_total_exposure: float = 0.95
    max_single_notional: float = float("inf")
    partial_fill_ratio: float = 1.0
    atr_multiplier: float = 2.0
    atr_period: int = 14
    use_atr_stop: bool = True
    min_position_pct: float = 0.01
    round_lot: int = 100


@dataclass
class PositionSizeResult:
    qty: int
    notional: float
    weight_pct: float
    risk_budget: float
    stop_distance: float
    stop_price: float
    capped_by_cash: bool
    capped_by_max_position: bool
    capped_by_round_lot: bool
    reason: str = ""


class RiskSizedPosition:
    """风险预算仓位计算器"""

    def __init__(self, config: PositionSizeConfig = None):
        self.config = config or PositionSizeConfig()

    def calculate(
        self,
        equity: float,
        entry_price: float,
        stop_price: float = None,
        atr: float = None,
        cash_available: float = None,
        current_exposure: float = 0,
    ) -> PositionSizeResult:
        """计算风险预算仓位"""
        cfg = self.config
        equity = max(0, equity)
        entry_price = max(0.01, entry_price)

        if stop_price is None and cfg.use_atr_stop and atr is not None:
            stop_price = entry_price - atr * cfg.atr_multiplier
        elif stop_price is None:
            stop_price = entry_price * 0.95

        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            stop_distance = entry_price * 0.01

        risk_budget = equity * (cfg.risk_per_trade_pct / 100)

        if risk_budget <= 0 or stop_distance <= 0:
            return PositionSizeResult(
                qty=0, notional=0, weight_pct=0,
                risk_budget=risk_budget, stop_distance=stop_distance,
                stop_price=stop_price,
                capped_by_cash=True, capped_by_max_position=False,
                capped_by_round_lot=False,
                reason="风险预算或止损距离为零",
            )

        risk_qty = risk_budget / stop_distance
        risk_notional = risk_qty * entry_price

        max_position_notional = equity * cfg.max_position_pct
        available_cash = (cash_available if cash_available is not None else equity) * (1 - current_exposure)
        available_cash = max(0, available_cash)

        capped_by_cash = False
        capped_by_max_position = False

        notional = risk_notional
        if notional > max_position_notional:
            notional = max_position_notional
            capped_by_max_position = True
        if notional > available_cash:
            notional = available_cash
            capped_by_cash = True

        raw_qty = notional / entry_price
        qty = int(raw_qty / cfg.round_lot) * cfg.round_lot
        capped_by_round_lot = qty == 0 and raw_qty > 0

        # 不再强制最少一手：如果资金不足一手就返回 0
        # 旧逻辑 if qty == 0 and raw_qty > 0: qty = cfg.round_lot
        # 会导致超出可用资金

        final_notional = qty * entry_price
        weight_pct = (final_notional / equity * 100) if equity > 0 else 0

        if weight_pct < cfg.min_position_pct * 100 and not capped_by_max_position:
            qty = 0
            final_notional = 0
            weight_pct = 0

        reasons = []
        if capped_by_cash:
            reasons.append("现金不足")
        if capped_by_max_position:
            reasons.append("触及单票上限")
        if capped_by_round_lot:
            reasons.append("不足一手")

        return PositionSizeResult(
            qty=qty,
            notional=final_notional,
            weight_pct=weight_pct,
            risk_budget=risk_budget,
            stop_distance=stop_distance,
            stop_price=stop_price,
            capped_by_cash=capped_by_cash,
            capped_by_max_position=capped_by_max_position,
            capped_by_round_lot=capped_by_round_lot,
            reason="; ".join(reasons) if reasons else "正常",
        )

    def calculate_batch(
        self,
        equity: float,
        signals: List[Dict],
        prices: Dict[str, float],
        stops: Dict[str, float] = None,
        atrs: Dict[str, float] = None,
        cash_available: float = None,
    ) -> Dict[str, PositionSizeResult]:
        """批量计算多标的仓位"""
        results = {}
        remaining_exposure = 0
        sorted_signals = sorted(signals, key=lambda s: s.get("score", 0), reverse=True)

        for sig in sorted_signals:
            code = sig.get("code", "")
            entry = prices.get(code, 0)
            if entry <= 0:
                continue

            stop = (stops or {}).get(code)
            atr = (atrs or {}).get(code)

            result = self.calculate(
                equity=equity,
                entry_price=entry,
                stop_price=stop,
                atr=atr,
                cash_available=cash_available,
                current_exposure=remaining_exposure,
            )

            if result.qty > 0:
                remaining_exposure += result.weight_pct / 100
                if remaining_exposure > self.config.max_total_exposure:
                    break

            results[code] = result

        return results

    def calculate_atr(
        self,
        df: pd.DataFrame,
        period: int = None,
    ) -> Optional[float]:
        """计算 ATR"""
        period = period or self.config.atr_period
        if df is None or df.empty or len(df) < period + 1:
            return None

        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = []
        for i in range(1, len(high)):
            h_l = high[i] - low[i]
            h_pc = abs(high[i] - close[i - 1])
            l_pc = abs(low[i] - close[i - 1])
            tr.append(max(h_l, h_pc, l_pc))

        if len(tr) < period:
            return float(np.mean(tr))

        return float(np.mean(tr[-period:]))

    def calculate_atr_batch(
        self,
        market_data: Dict[str, pd.DataFrame],
        period: int = None,
    ) -> Dict[str, float]:
        """批量计算 ATR"""
        atrs = {}
        for code, df in market_data.items():
            atr = self.calculate_atr(df, period)
            if atr is not None:
                atrs[code] = atr
        return atrs


class KellyPosition:
    """凯利公式仓位优化"""

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """凯利比例（带输入验证）"""
        # 输入验证
        if not (0 <= win_rate <= 1):
            return 0
        if avg_win < 0 or avg_loss <= 0:
            return 0
        b = avg_win / avg_loss
        f = (b * win_rate - (1 - win_rate)) / b
        return max(0, min(0.25, f))

    @staticmethod
    def half_kelly(win_rate: float, avg_win: float, avg_loss: float) -> float:
        """半凯利 —— 更保守"""
        return KellyPosition.kelly_fraction(win_rate, avg_win, avg_loss) / 2

    @staticmethod
    def optimal_f(
        trades: List[float],
        max_f: float = 0.25,
    ) -> float:
        """最优 f —— 基于历史交易序列"""
        if not trades:
            return 0.01
        arr = np.array(trades)
        win_rate = np.mean(arr > 0)
        avg_win = np.mean(arr[arr > 0]) if np.any(arr > 0) else 0
        avg_loss = abs(np.mean(arr[arr < 0])) if np.any(arr < 0) else 0.01
        return KellyPosition.kelly_fraction(win_rate, avg_win, avg_loss)


class VolTargetPosition:
    """波动率目标仓位 —— 根据目标波动率调整总仓位"""

    @staticmethod
    def calculate(
        target_vol: float,
        current_vol: float,
        max_leverage: float = 1.0,
    ) -> float:
        """计算波动率目标仓位比例"""
        if current_vol <= 0:
            return max_leverage
        ratio = target_vol / current_vol
        return min(ratio, max_leverage)

    @staticmethod
    def calculate_from_returns(
        returns: np.ndarray,
        target_vol: float = 0.15,
        max_leverage: float = 1.0,
        lookback: int = 60,
    ) -> float:
        """从收益率序列计算"""
        if len(returns) < lookback:
            return 0.5
        recent = returns[-lookback:]
        current_vol = float(np.std(recent) * np.sqrt(252))
        return VolTargetPosition.calculate(target_vol, current_vol, max_leverage)


# ========== ATR 风险仓位（来自 QKA 快量化） ==========

def atr_risk_size(cash: float, price: float, atr_value: float,
                  risk_ratio: float = 0.01, multiplier: float = 2.0,
                  lot_size: int = 100) -> int:
    """基于 ATR 的风险仓位计算

    风险预算 = cash * risk_ratio
    每股风险 = atr_value * multiplier
    可买股数 = 风险预算 / 每股风险（向下取整到整手）

    Args:
        cash: 可用资金
        price: 当前价格
        atr_value: ATR 值
        risk_ratio: 单笔风险比例（默认 1%）
        multiplier: 止损倍数（默认 2 倍 ATR）
        lot_size: 最小交易单位（A 股 100）

    Returns:
        可买股数（整手）
    """
    if atr_value <= 0 or price <= 0 or cash <= 0:
        return 0
    risk_per_share = atr_value * multiplier
    max_risk = cash * risk_ratio
    shares = int(max_risk / risk_per_share / lot_size) * lot_size
    return shares if shares >= lot_size else 0


# ========== 离散分配（来自 PyPortfolioOpt） ==========

def discrete_allocation(weights: Dict[str, float],
                        prices: Dict[str, float],
                        total_value: float,
                        lot_size: int = 100) -> Dict[str, int]:
    """连续权重 → A 股整手买入指令

    将最优权重（如 {'000001': 0.3, '600519': 0.7}）
    转为实际买入股数（如 {'000001': 2400, '600519': 100}）

    Args:
        weights: {code: target_weight} 权重和为 1
        prices: {code: current_price}
        total_value: 总资金
        lot_size: 最小交易单位（A 股 100）

    Returns:
        {code: shares_to_buy}
    """
    if not weights or not prices or total_value <= 0:
        return {}

    # 优先分配给权重高的股票
    sorted_codes = sorted(weights.keys(), key=lambda c: weights[c], reverse=True)

    remaining_cash = total_value
    allocation = {}
    leftover_value = 0

    for code in sorted_codes:
        price = prices.get(code)
        weight = weights.get(code, 0)
        if price is None or price <= 0 or weight <= 0:
            continue

        target_value = total_value * weight
        target_shares = int(target_value / price / lot_size) * lot_size

        # 确保不超出剩余资金
        cost = target_shares * price
        if cost > remaining_cash:
            target_shares = int(remaining_cash / price / lot_size) * lot_size
            cost = target_shares * price

        if target_shares >= lot_size:
            allocation[code] = target_shares
            remaining_cash -= cost

        leftover_value += weight * total_value - cost

    return allocation


# ========== 市场状态联动仓位（参考 因子.txt） ==========

def apply_regime_position_scalar(
    base_weight: float,
    regime: str,
    confidence: float = 0.5,
) -> float:
    """根据市场状态调整仓位

    参考：因子.txt 第八节 - 仓位规则
    - TREND_UP:     75%-100%
    - MEAN_REVERSION: 25%-50%
    - COMPRESSION:   0%-25%
    - TREND_DOWN:   0%

    Args:
        base_weight: 基础仓位（如单票上限20%）
        regime: 市场状态
        confidence: 状态置信度 0~1

    Returns:
        调整后的仓位（绝对比例，如 0.15 表示15%）
    """
    from lib.monitoring.market_regime import REGIME_POSITION_SCALAR, TREND_UP

    min_scalar, max_scalar = REGIME_POSITION_SCALAR.get(regime, (0.25, 0.50))

    # 用置信度在 min~max 之间插值
    regime_position = min_scalar + confidence * (max_scalar - min_scalar)

    adjusted = base_weight * regime_position
    return float(max(0.0, min(1.0, adjusted)))


class RegimeAwarePositionSizer:
    """市场状态感知仓位管理器

    在 RiskSizedPosition 基础上，增加市场状态联动
    """

    def __init__(self, base_sizer: RiskSizedPosition = None):
        self.base_sizer = base_sizer or RiskSizedPosition()

    def calculate_with_regime(
        self,
        equity: float,
        entry_price: float,
        stop_price: float = None,
        atr: float = None,
        cash_available: float = None,
        current_exposure: float = 0,
        regime: str = None,
        regime_confidence: float = 0.5,
    ) -> Tuple[PositionSizeResult, float]:
        """计算考虑市场状态的仓位

        Returns:
            (PositionSizeResult, adjusted_weight)
        """
        base_result = self.base_sizer.calculate(
            equity=equity,
            entry_price=entry_price,
            stop_price=stop_price,
            atr=atr,
            cash_available=cash_available,
            current_exposure=current_exposure,
        )

        if regime is not None:
            adjusted_weight = apply_regime_position_scalar(
                base_result.weight_pct / 100,
                regime,
                regime_confidence,
            ) * 100  # 转回百分比
        else:
            adjusted_weight = base_result.weight_pct

        # 根据调整后的权重重新计算实际股数和名义金额
        adjusted_notional = base_result.notional
        adjusted_qty = base_result.qty
        if base_result.weight_pct > 0 and adjusted_weight < base_result.weight_pct:
            scale = adjusted_weight / base_result.weight_pct
            adjusted_notional = base_result.notional * scale
            adjusted_qty = int(adjusted_notional / entry_price / self.config.round_lot) * self.config.round_lot
            adjusted_notional = adjusted_qty * entry_price

        # 返回调整后的结果（qty/notional/weight_pct 全部更新）
        adjusted_result = PositionSizeResult(
            qty=adjusted_qty,
            notional=adjusted_notional,
            weight_pct=adjusted_weight,
            risk_budget=base_result.risk_budget,
            stop_distance=base_result.stop_distance,
            stop_price=base_result.stop_price,
            capped_by_cash=base_result.capped_by_cash,
            capped_by_max_position=base_result.capped_by_max_position,
            capped_by_round_lot=base_result.capped_by_round_lot,
            reason=base_result.reason + (f" [regime={regime}]" if regime else ""),
        )

        return adjusted_result, adjusted_weight