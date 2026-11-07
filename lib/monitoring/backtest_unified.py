"""统一回测入口的薄壳 re-export（已迁至 backtest/unified.py）。

⚠️ **新代码请直接用** `from backtest.unified import BacktestRunner, BacktestRequest`。

保留本模块仅为兼容既有 import 路径（RF-01 Phase 3 的「薄壳 re-export」模式）。
内容为纯转发，函数/类对象与 `backtest.unified` 中的**完全同一对象**。
"""

from backtest.unified import (  # noqa: F401
    BacktestRequest,
    BacktestRunner,
)

__all__ = ["BacktestRequest", "BacktestRunner"]
