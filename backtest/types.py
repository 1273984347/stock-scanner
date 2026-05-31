"""backtest/types.py — 回测结果标准数据结构"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class BacktestResult:
    """标准回测结果"""
    metrics: Dict = field(default_factory=dict)
    trades: List[Dict] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    exit_reasons: Dict = field(default_factory=dict)
    benchmark: Dict = field(default_factory=dict)
    config: Dict = field(default_factory=dict)
    trade_count: int = 0
    mode: str = ""

    def to_dict(self) -> Dict:
        return {
            "mode": self.mode,
            "metrics": self.metrics,
            "trade_count": self.trade_count,
            "trades": self.trades,
            "exit_reasons": self.exit_reasons,
            "equity_curve": self.equity_curve,
            "benchmark": self.benchmark,
            "config": self.config,
        }
