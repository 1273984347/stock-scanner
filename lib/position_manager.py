"""仓位管理 —— 持仓跟踪、止损止盈监控、再平衡建议

数据持久化到 cache/positions.json
"""
import json
import pandas as pd
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
import logging
import sys
import threading

from lib import config

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
    stop_loss: float = config.DEFAULT_STOP_LOSS
    take_profit: float = config.DEFAULT_TAKE_PROFIT
    notes: str = ""


class PositionManager:
    """仓位管理器（支持并发文件锁）"""

    def __init__(self, positions_file: str = None):
        self.file = Path(positions_file) if positions_file else POSITIONS_FILE
        self.positions: Dict[str, Position] = {}
        self._lock_file: Optional[object] = None
        self._stop_event = threading.Event()
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
                except Exception:
                    pass
                self._lock_file = None
            raise IOError(f"无法获取文件锁: {self._lock_path}") from e

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
        except Exception:
            pass

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

    def track_position(self, code: str, name: str, buy_price: float,
                       shares: int, stop_loss: float = config.DEFAULT_STOP_LOSS,
                       take_profit: float = config.DEFAULT_TAKE_PROFIT, notes: str = ""):
        """记录持仓"""
        self.positions[code] = Position(
            code=code, name=name, buy_price=buy_price,
            shares=shares, buy_date=datetime.now().strftime("%Y-%m-%d"),
            stop_loss=stop_loss, take_profit=take_profit, notes=notes,
        )
        self._save()
        logger.info(f"记录持仓: {code} {name} {shares}股 @ {buy_price}")

    def remove_position(self, code: str):
        """移除持仓"""
        if code in self.positions:
            del self.positions[code]
            self._save()
            logger.info(f"移除持仓: {code}")

    def check_stop_loss(self, current_prices: Dict[str, float]) -> List[Dict]:
        """检查止损止盈

        Args:
            current_prices: {code: current_price}

        Returns:
            [{"code", "name", "action", "reason", "pnl_pct"}]
        """
        alerts = []
        for code, pos in self.positions.items():
            price = current_prices.get(code)
            if price is None:
                continue

            pnl_pct = (price - pos.buy_price) / pos.buy_price if pos.buy_price > 0 else 0

            if pnl_pct <= -pos.stop_loss:
                alerts.append({
                    "code": code, "name": pos.name,
                    "action": "止损", "reason": f"亏损 {pnl_pct:.1%} 超过 {pos.stop_loss:.0%}",
                    "pnl_pct": round(pnl_pct, 4),
                    "buy_price": pos.buy_price, "current_price": price,
                })
            elif pnl_pct >= pos.take_profit:
                alerts.append({
                    "code": code, "name": pos.name,
                    "action": "止盈", "reason": f"盈利 {pnl_pct:.1%} 达到 {pos.take_profit:.0%}",
                    "pnl_pct": round(pnl_pct, 4),
                    "buy_price": pos.buy_price, "current_price": price,
                })

        return alerts

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
        """
        import time
        self._stop_event.clear()
        iterations = 0
        while not self._stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
            try:
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
