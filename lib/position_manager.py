"""仓位管理 —— 持仓跟踪、止损止盈监控、再平衡建议

数据持久化到 cache/positions.json
"""
import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime
import logging
import sys
import threading

from config import DEFAULT_STOP_LOSS, DEFAULT_TAKE_PROFIT, RiskConfig
from lib.risk_manager import ProofInvalidationManager

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path(__file__).parent.parent / "cache" / "positions.json"

# 跨平台文件锁
try:
    import portalocker
    _HAS_PORTALOCKER = True
except ImportError:
    _HAS_PORTALOCKER = False
    if sys.platform != "win32":
        import fcntl
        _HAS_FCNTL = True
    else:
        _HAS_FCNTL = False


@dataclass
class Position:
    code: str
    name: str
    buy_price: float
    shares: int
    buy_date: str
    stop_loss: float = DEFAULT_STOP_LOSS
    take_profit: float = DEFAULT_TAKE_PROFIT
    notes: str = ""
    # ATR 自适应止损
    atr: float = 0.0          # 入场时 ATR 值（0=无ATR，使用固定止损）
    atr_mult: float = 2.0     # ATR 倍数
    atr_stop_price: float = 0.0  # ATR 止损价位（入场时计算）
    # 移动止盈
    peak_price: float = 0.0   # 持仓期间最高价
    trailing_mult: float = 3.0  # 移动止盈 ATR 倍数
    trailing_stop_price: float = 0.0  # 移动止盈价位（随 peak 更新）
    # ATR 止盈
    atr_tp_mult: float = 4.0   # ATR 止盈倍数
    atr_take_profit_price: float = 0.0  # ATR 止盈价位
    # 分批止盈
    partial_exits_done: int = 0   # number of partial exits executed
    remaining_ratio: float = 1.0  # remaining position ratio
    # 结构破坏退出
    _key_low: float = 0.0  # 入场前20日最低价（关键低点）


class PositionManager:
    """仓位管理器（支持并发文件锁）"""

    def __init__(self, positions_file: str = None, risk_config: Optional[RiskConfig] = None):
        self.file = Path(positions_file) if positions_file else POSITIONS_FILE
        self.positions: Dict[str, Position] = {}
        self._lock_file: Optional[object] = None
        self._stop_event = threading.Event()
        self._proof_manager = ProofInvalidationManager()
        self._risk_config = risk_config or RiskConfig()
        self._load()

    def _acquire_lock(self, mode="r"):
        """获取文件锁（fail-fast：失败时抛出异常）"""
        if not self.file.exists():
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.touch(exist_ok=True)
        try:
            if _HAS_PORTALOCKER:
                self._lock_file = portalocker.Lock(
                    self.file, timeout=5,
                    fail_when_locked=True if mode == "r" else False
                )
                self._lock_file.acquire()
            elif _HAS_FCNTL:
                self._lock_file = open(self.file, "r+" if mode == "r" else "w")
                fcntl.flock(self._lock_file.fileno(),
                           fcntl.LOCK_EX if mode == "w" else fcntl.LOCK_SH)
            else:
                logger.warning("无可用文件锁库（portalocker/fcntl），并发写入可能冲突")
        except Exception as e:
            logger.error(f"获取文件锁失败: {e}")
            if self._lock_file:
                try:
                    self._lock_file.close()
                except Exception as e:
                    logger.debug(f"[position_manager] 文件锁关闭异常: {e}")
                self._lock_file = None
            raise IOError(f"无法获取文件锁: {self.file}") from e

    def _release_lock(self):
        """释放文件锁"""
        try:
            if _HAS_PORTALOCKER and self._lock_file:
                self._lock_file.release()
                self._lock_file = None
            elif _HAS_FCNTL and self._lock_file:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_file = None
        except Exception as e:
            logger.debug(f"[position_manager] 文件锁释放异常: {e}")

    def _load(self):
        """从文件加载持仓"""
        if not self.file.exists():
            return
        self._acquire_lock("r")
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            for code, p in data.items():
                self.positions[code] = Position(**p)
        except IOError:
            # 锁获取失败已在 _acquire_lock 中记录，不重复处理
            raise
        except Exception as e:
            logger.warning(f"加载持仓数据失败: {e}")
        finally:
            self._release_lock()

    def _save(self):
        """保存持仓到文件（原子写入）"""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_lock("w")
        try:
            data = {}
            for code, p in self.positions.items():
                data[code] = {
                    "code": p.code, "name": p.name,
                    "buy_price": p.buy_price, "shares": p.shares,
                    "buy_date": p.buy_date,
                    "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                    "notes": p.notes,
                    "atr": p.atr,
                    "atr_mult": p.atr_mult,
                    "atr_stop_price": p.atr_stop_price,
                    "peak_price": p.peak_price,
                    "trailing_mult": p.trailing_mult,
                    "trailing_stop_price": p.trailing_stop_price,
                    "atr_tp_mult": p.atr_tp_mult,
                    "atr_take_profit_price": p.atr_take_profit_price,
                    "partial_exits_done": p.partial_exits_done,
                    "remaining_ratio": p.remaining_ratio,
                    "_key_low": p._key_low,
                }
            # 原子写入：先写临时文件再 rename
            tmp_file = self.file.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp_file.replace(self.file)
        except IOError:
            # 锁获取失败已在 _acquire_lock 中记录，不重复处理
            raise
        except Exception as e:
            logger.error(f"保存持仓数据失败: {e}")
        finally:
            self._release_lock()

    @staticmethod
    def get_regime_adjusted_params(regime: str) -> dict:
        """根据市场状态返回动态参数（V6自适应设计）

        TREND_UP: 更宽止损(2.5ATR) + 更长持有(25天)
        MEAN_REVERSION: 更紧止损(1.5ATR) + 更短持有(10天)
        COMPRESSION: 标准止损(2ATR) + 短持有(5天)
        TREND_DOWN: 不持仓
        """
        params = {
            "TREND_UP": {"atr_mult": 2.5, "max_hold_days": 25, "trailing_mult": 3.0},
            "MEAN_REVERSION": {"atr_mult": 1.5, "max_hold_days": 10, "trailing_mult": 2.0},
            "COMPRESSION": {"atr_mult": 2.0, "max_hold_days": 5, "trailing_mult": 2.5},
            "TREND_DOWN": {"atr_mult": 1.0, "max_hold_days": 0, "trailing_mult": 1.5},
            "RISK_OFF": {"atr_mult": 1.0, "max_hold_days": 0, "trailing_mult": 1.0},
        }
        return params.get(regime, {"atr_mult": 2.0, "max_hold_days": 20, "trailing_mult": 3.0})

    def track_position(self, code: str, name: str, buy_price: float,
                       shares: int, stop_loss: float = DEFAULT_STOP_LOSS,
                       take_profit: float = DEFAULT_TAKE_PROFIT, notes: str = "",
                       atr: float = 0.0, atr_mult: float = 2.0,
                       atr_tp_mult: float = 4.0, trailing_mult: float = 3.0,
                       regime: str = None, market_data=None):
        """记录持仓

        Args:
            atr: 入场时 ATR 值（0=无ATR，使用固定止损）
            atr_mult: ATR 止损倍数
            atr_tp_mult: ATR 止盈倍数
            trailing_mult: 移动止盈 ATR 倍数
            regime: 市场状态（可选，传入时自动调整 atr_mult / trailing_mult）
        """
        # 根据市场状态动态调整参数
        if regime:
            regime_params = self.get_regime_adjusted_params(regime)
            atr_mult = regime_params["atr_mult"]
            trailing_mult = regime_params["trailing_mult"]

        peak_price = buy_price
        atr_stop_price = buy_price - atr * atr_mult if atr > 0 else 0.0
        atr_take_profit_price = buy_price + atr * atr_tp_mult if atr > 0 else 0.0
        trailing_stop_price = buy_price  # 初始移动止盈 = 入场价
        self.positions[code] = Position(
            code=code, name=name, buy_price=buy_price,
            shares=shares, buy_date=datetime.now().strftime("%Y-%m-%d"),
            stop_loss=stop_loss, take_profit=take_profit, notes=notes,
            atr=atr, atr_mult=atr_mult,
            atr_stop_price=atr_stop_price,
            peak_price=peak_price,
            trailing_mult=trailing_mult,
            trailing_stop_price=trailing_stop_price,
            atr_tp_mult=atr_tp_mult,
            atr_take_profit_price=atr_take_profit_price,
        )
        # 新建持仓时计算关键低点（入场前20日最低价）
        if market_data is not None and len(market_data) >= 20:
            self.positions[code]._key_low = float(market_data["low"].tail(20).min())
        else:
            self.positions[code]._key_low = 0.0
        self._save()
        logger.info(f"记录持仓: {code} {name} {shares}股 @ {buy_price} ATR={atr:.4f} stop={atr_stop_price:.2f}" if atr > 0 else f"记录持仓: {code} {name} {shares}股 @ {buy_price}")
        # 记录到证伪管理器
        entry_date = datetime.now().strftime("%Y-%m-%d")
        stop_price = atr_stop_price if atr > 0 else buy_price * (1 - stop_loss)
        first_target = atr_take_profit_price if atr > 0 else 0.0
        self._proof_manager.record_entry(code, entry_date, buy_price, stop_price, first_target)

    def set_atr(self, code: str, atr: float, atr_mult: float = 2.0,
                 atr_tp_mult: float = 4.0, trailing_mult: float = 3.0):
        """为已有持仓设置/更新 ATR 参数

        Args:
            code: 股票代码
            atr: 当前 ATR 值
            atr_mult: ATR 止损倍数
            atr_tp_mult: ATR 止盈倍数
            trailing_mult: 移动止盈 ATR 倍数
        """
        if code not in self.positions:
            return
        pos = self.positions[code]
        pos.atr = atr
        pos.atr_mult = atr_mult
        pos.atr_tp_mult = atr_tp_mult
        pos.trailing_mult = trailing_mult
        pos.peak_price = max(pos.peak_price, pos.buy_price)
        if atr > 0:
            pos.atr_stop_price = pos.buy_price - atr * atr_mult
            pos.atr_take_profit_price = pos.buy_price + atr * atr_tp_mult
            pos.trailing_stop_price = pos.peak_price - atr * trailing_mult
        self._save()
        logger.info(f"更新ATR: {code} ATR={atr:.4f} stop={pos.atr_stop_price:.2f} tp={pos.atr_take_profit_price:.2f}")

    def can_add_position(self, code: str, current_price: float) -> Tuple[bool, str]:
        """Check if adding to position is allowed (design: only profitable positions)

        Returns:
            (allowed, reason)
        """
        pos = self.positions.get(code)
        if pos is None:
            return True, "新建仓位"

        pnl_pct = (current_price - pos.buy_price) / pos.buy_price
        if pnl_pct <= 0:
            return False, f"禁止亏损补仓（当前亏损 {pnl_pct:.1%}）"

        # Profitable position allows adding, but check max position
        if pos.remaining_ratio >= 1.5:  # Max 1.5x initial
            return False, "已达到加仓上限"

        return True, f"盈利 {pnl_pct:.1%}，允许加仓"

    def remove_position(self, code: str, close_price: float = None):
        """移除持仓

        Args:
            code: 股票代码
            close_price: 平仓价格（用于证伪管理器记录）
        """
        if code in self.positions:
            pos = self.positions[code]
            if close_price is not None:
                self._proof_manager.close_position(code, close_price)
            else:
                self._proof_manager.close_position(code, pos.buy_price)
            del self.positions[code]
            self._save()
            logger.info(f"移除持仓: {code}")

    def _check_partial_exit(self, pos: Position, price: float, pnl_pct: float) -> Optional[dict]:
        """Check for staged position reduction (分批减仓止盈)

        Returns partial exit alert dict if triggered, None otherwise.
        """
        cfg = self._risk_config
        if not cfg.partial_exit_enabled:
            return None
        if pos.partial_exits_done >= len(cfg.partial_exit_levels):
            return None

        level = cfg.partial_exit_levels[pos.partial_exits_done]
        target_pnl = level["profit_threshold"]
        exit_ratio = level["sell_ratio"]

        if pnl_pct >= target_pnl:
            pos.partial_exits_done += 1
            pos.remaining_ratio *= (1 - exit_ratio)
            self._save()  # persist partial exit state
            return {
                "code": pos.code, "name": pos.name,
                "action": "分批止盈",
                "reason": f"盈利 {pnl_pct:.1%} 达到第{pos.partial_exits_done}级 {target_pnl:.0%}，减仓 {exit_ratio:.0%}",
                "exit_ratio": exit_ratio,
                "remaining_ratio": pos.remaining_ratio,
                "pnl_pct": round(pnl_pct, 4),
                "buy_price": pos.buy_price, "current_price": price,
            }
        return None

    def check_stop_loss(self, current_prices: Dict[str, float],
                        max_hold_days: int = 20) -> List[Dict]:
        """检查止损/止盈/移动止盈/持仓天数

        Args:
            current_prices: {code: current_price}
            max_hold_days: 最大持仓天数（默认20天）

        Returns:
            [{"code", "name", "action", "reason", "pnl_pct"}]
        """
        alerts = []
        today = datetime.now()
        for code, pos in self.positions.items():
            price = current_prices.get(code)
            if price is None:
                continue

            pnl_pct = (price - pos.buy_price) / pos.buy_price if pos.buy_price > 0 else 0

            # 1. ATR 止损（优先）
            if pos.atr > 0 and pos.atr_stop_price > 0:
                if price <= pos.atr_stop_price:
                    alerts.append({
                        "code": code, "name": pos.name,
                        "action": "止损", "reason": f"ATR止损 @{pos.atr_stop_price:.2f} (ATR×{pos.atr_mult})",
                        "pnl_pct": round(pnl_pct, 4),
                        "buy_price": pos.buy_price, "current_price": price,
                    })
                    continue
                # 2. 固定止损（无ATR时）
                if pnl_pct <= -pos.stop_loss:
                    alerts.append({
                        "code": code, "name": pos.name,
                        "action": "止损", "reason": f"固定止损 {pnl_pct:.1%} 超过 {pos.stop_loss:.0%}",
                        "pnl_pct": round(pnl_pct, 4),
                        "buy_price": pos.buy_price, "current_price": price,
                    })
                    continue
            else:
                if pnl_pct <= -pos.stop_loss:
                    alerts.append({
                        "code": code, "name": pos.name,
                        "action": "止损", "reason": f"亏损 {pnl_pct:.1%} 超过 {pos.stop_loss:.0%}",
                        "pnl_pct": round(pnl_pct, 4),
                        "buy_price": pos.buy_price, "current_price": price,
                    })
                    continue

            # 2.5. 结构破坏退出（跌破关键低点）
            if pos._key_low > 0 and price < pos._key_low:
                alerts.append({
                    "code": code, "name": pos.name,
                    "action": "结构破坏",
                    "reason": f"跌破关键低点 {pos._key_low:.2f}",
                    "pnl_pct": round(pnl_pct, 4),
                    "buy_price": pos.buy_price, "current_price": price,
                })
                continue

            # 3. 分批止盈（优先于固定止盈）
            partial = self._check_partial_exit(pos, price, pnl_pct)
            if partial:
                alerts.append(partial)
                continue  # Skip fixed take-profit; trailing stop protects remainder

            # 4. 固定止盈（仅 use_fixed_take_profit=True 时生效）
            if self._risk_config.use_fixed_take_profit:
                if pos.atr > 0 and pos.atr_take_profit_price > 0:
                    if price >= pos.atr_take_profit_price:
                        alerts.append({
                            "code": code, "name": pos.name,
                            "action": "止盈", "reason": f"ATR止盈 @{pos.atr_take_profit_price:.2f} (ATR×{pos.atr_tp_mult})",
                            "pnl_pct": round(pnl_pct, 4),
                            "buy_price": pos.buy_price, "current_price": price,
                        })
                        continue
                else:
                    if pnl_pct >= pos.take_profit:
                        alerts.append({
                            "code": code, "name": pos.name,
                            "action": "止盈", "reason": f"盈利 {pnl_pct:.1%} 达到 {pos.take_profit:.0%}",
                            "pnl_pct": round(pnl_pct, 4),
                            "buy_price": pos.buy_price, "current_price": price,
                        })
                        continue

            # 5. 移动止盈（仅在持仓盈利时生效）
            if pos.atr > 0 and pos.trailing_stop_price > 0:
                if price <= pos.trailing_stop_price and pnl_pct > 0:
                    alerts.append({
                        "code": code, "name": pos.name,
                        "action": "移动止盈", "reason": f"移动止盈 @{pos.trailing_stop_price:.2f} (peak-{pos.peak_price:.2f}, ATR×{pos.trailing_mult})",
                        "pnl_pct": round(pnl_pct, 4),
                        "buy_price": pos.buy_price, "current_price": price,
                    })
                    continue

            # 6. 最大持仓天数
            try:
                buy_dt = datetime.strptime(pos.buy_date, "%Y-%m-%d")
                hold_days = (today - buy_dt).days
                if hold_days >= max_hold_days:
                    alerts.append({
                        "code": code, "name": pos.name,
                        "action": "到期", "reason": f"持仓 {hold_days} 天，超限 {max_hold_days} 天",
                        "pnl_pct": round(pnl_pct, 4),
                        "buy_price": pos.buy_price, "current_price": price,
                    })
            except Exception as e:
                logger.debug(f"[position_manager] 持仓到期检查异常 [{code}]: {e}")

        return alerts

    def update_peak_and_trailing_stop(self, current_prices: Dict[str, float]):
        """更新持仓期间最高价和移动止盈价位

        每次行情更新时调用，动态跟踪止盈
        """
        for code, pos in self.positions.items():
            price = current_prices.get(code)
            if price is None or pos.atr <= 0:
                continue
            if price > pos.peak_price:
                pos.peak_price = price
                # 移动止盈：peak - ATR * trailing_mult
                pos.trailing_stop_price = pos.peak_price - pos.atr * pos.trailing_mult
                logger.debug(f"更新peak: {code} peak={pos.peak_price:.2f} trailing_stop={pos.trailing_stop_price:.2f}")

    def get_portfolio_summary(self, current_prices: Dict[str, float]) -> Dict:
        """组合概况"""
        if not self.positions:
            return {"total_positions": 0, "total_value": 0, "total_pnl": 0}

        total_cost = 0
        total_value = 0
        details = []

        for code, pos in self.positions.items():
            price = current_prices.get(code, pos.buy_price)
            cost = pos.buy_price * pos.shares
            value = price * pos.shares
            pnl = value - cost
            pnl_pct = (price - pos.buy_price) / pos.buy_price if pos.buy_price > 0 else 0

            total_cost += cost
            total_value += value

            details.append({
                "code": code, "name": pos.name,
                "shares": pos.shares,
                "buy_price": pos.buy_price,
                "current_price": round(price, 2),
                "value": round(value, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 4),
                "weight": 0,  # 下面计算
            })

        # 计算权重
        for d in details:
            d["weight"] = round(d["value"] / total_value, 4) if total_value > 0 else 0

        return {
            "total_positions": len(self.positions),
            "total_cost": round(total_cost, 2),
            "total_value": round(total_value, 2),
            "total_pnl": round(total_value - total_cost, 2),
            "total_pnl_pct": round((total_value - total_cost) / total_cost, 4) if total_cost > 0 else 0,
            "details": details,
        }

    def suggest_rebalance(self, target_weights: Dict[str, float],
                          current_prices: Dict[str, float],
                          total_value: float) -> List[Dict]:
        """再平衡建议

        Args:
            target_weights: {code: target_weight} (0-1)
            current_prices: {code: current_price}
            total_value: 组合总值

        Returns:
            [{"code", "action", "shares", "reason"}]
        """
        suggestions = []
        summary = self.get_portfolio_summary(current_prices)
        current_weights = {d["code"]: d["weight"] for d in summary.get("details", [])}

        for code, target_w in target_weights.items():
            current_w = current_weights.get(code, 0)
            diff = target_w - current_w

            if abs(diff) < 0.02:  # 偏差小于 2% 不调整
                continue

            price = current_prices.get(code)
            if price is None or price <= 0:
                continue

            target_value = total_value * target_w
            current_value = total_value * current_w
            diff_value = target_value - current_value
            shares = int(abs(diff_value) / price / 100) * 100

            if shares <= 0:
                continue

            action = "买入" if diff > 0 else "卖出"
            suggestions.append({
                "code": code,
                "action": action,
                "shares": shares,
                "reason": f"目标权重 {target_w:.0%} vs 当前 {current_w:.0%}",
            })

        return suggestions

    def monitor_positions(self) -> Dict:
        """实时监控持仓状态（接入实时行情）

        获取持仓股票的实时价格，检查止损止盈条件，
        返回监控报告和告警列表。

        Returns:
            {
                "timestamp": str,  # 监控时间
                "prices": Dict[str, float],  # 当前价
                "alerts": List[Dict],  # 触发告警的持仓
                "portfolio_summary": Dict,  # 组合概况
                "all_positions": List[Dict],  # 所有持仓状态
            }
        """
        from lib.data_layer import get_realtime_quotes

        if not self.positions:
            return {
                "timestamp": datetime.now().isoformat(),
                "prices": {},
                "alerts": [],
                "portfolio_summary": {"total_positions": 0, "total_value": 0},
                "all_positions": [],
            }

        # 获取实时行情
        quotes = get_realtime_quotes()
        current_prices = {}
        if not quotes.empty:
            code_col = None
            price_col = None
            for col in quotes.columns:
                if "代码" in str(col):
                    code_col = col
                if "最新价" in str(col) or "收盘" in str(col):
                    price_col = col
            if code_col and price_col:
                # 向量化处理，避免 iterrows
                if not quotes.empty:
                    codes = quotes[code_col].astype(str).str.zfill(6).tolist()
                    prices = quotes[price_col].tolist()
                    for code, price in zip(codes, prices):
                        if price is not None and isinstance(price, (int, float)):
                            current_prices[code] = float(price)

        # 检查告警
        alerts = self.check_stop_loss(current_prices)

        # 组合概况
        summary = self.get_portfolio_summary(current_prices)

        # 所有持仓状态
        all_positions = []
        for code, pos in self.positions.items():
            price = current_prices.get(code, pos.buy_price)
            pnl_pct = (price - pos.buy_price) / pos.buy_price if pos.buy_price > 0 else 0
            value = price * pos.shares
            all_positions.append({
                "code": code,
                "name": pos.name,
                "shares": pos.shares,
                "buy_price": pos.buy_price,
                "current_price": round(price, 2),
                "value": round(value, 2),
                "pnl": round(value - pos.buy_price * pos.shares, 2),
                "pnl_pct": round(pnl_pct, 4),
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "buy_date": pos.buy_date,
                "notes": pos.notes,
            })

        result = {
            "timestamp": datetime.now().isoformat(),
            "prices": current_prices,
            "alerts": alerts,
            "portfolio_summary": summary,
            "all_positions": all_positions,
        }

        # 记录告警到日志
        for alert in alerts:
            logger.warning(f"持仓监控告警: {alert['code']} {alert['name']} {alert['action']} "
                          f"@{alert['current_price']} ({alert['pnl_pct']:.1%})")

        return result

    def stop(self):
        """优雅停止实盘监控"""
        self._stop_event.set()

    def run_live_monitoring(self, interval_seconds: int = 60, max_iterations: int = None):
        """持续监控持仓（定时检查）

        Args:
            interval_seconds: 检查间隔（秒）
            max_iterations: 最大迭代次数（None=无限）

        这是一个阻塞调用，会持续运行直到达到 max_iterations、被 stop() 中断或异常退出。
        常用于实盘盯盘场景。

        证伪机制：每次迭代调用 update_holding_days() 更新持仓天数，
        并在 check_stop_loss() 中同步调用证伪检查。
        """
        import time
        self._stop_event.clear()
        iterations = 0
        while not self._stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
            try:
                # 更新证伪管理器的持仓天数
                self._proof_manager.update_holding_days()
                result = self.monitor_positions()
                if result["alerts"]:
                    logger.warning(f"实盘监控 [{iterations}]: 发现 {len(result['alerts'])} 个告警")
                    for alert in result["alerts"]:
                        logger.warning(f"  {alert['code']} {alert['name']}: {alert['action']} "
                                     f"@{alert['current_price']} ({alert['pnl_pct']:.1%})")
                else:
                    logger.debug(f"实盘监控 [{iterations}]: 无告警，持仓 {result['portfolio_summary']['total_positions']} 只")
            except Exception as e:
                logger.error(f"实盘监控异常: {e}")
            iterations += 1
            if not self._stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
                self._stop_event.wait(timeout=interval_seconds)
