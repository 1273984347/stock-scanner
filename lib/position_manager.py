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

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path(__file__).parent.parent / "cache" / "positions.json"


@dataclass
class Position:
    code: str
    name: str
    buy_price: float
    shares: int
    buy_date: str
    stop_loss: float = 0.05
    take_profit: float = 0.10
    notes: str = ""


class PositionManager:
    """仓位管理器"""

    def __init__(self, positions_file: str = None):
        self.file = Path(positions_file) if positions_file else POSITIONS_FILE
        self.positions: Dict[str, Position] = {}
        self._load()

    def _load(self):
        """从文件加载持仓"""
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            for code, p in data.items():
                self.positions[code] = Position(**p)
        except Exception as e:
            logger.warning(f"加载持仓数据失败: {e}")

    def _save(self):
        """保存持仓到文件"""
        self.file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for code, p in self.positions.items():
            data[code] = {
                "code": p.code, "name": p.name,
                "buy_price": p.buy_price, "shares": p.shares,
                "buy_date": p.buy_date,
                "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                "notes": p.notes,
            }
        self.file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def track_position(self, code: str, name: str, buy_price: float,
                       shares: int, stop_loss: float = 0.05,
                       take_profit: float = 0.10, notes: str = ""):
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

            pnl_pct = (price - pos.buy_price) / pos.buy_price

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
            pnl_pct = (price - pos.buy_price) / pos.buy_price

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
