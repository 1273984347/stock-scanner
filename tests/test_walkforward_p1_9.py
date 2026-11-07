"""P1-9: lib/walkforward.py risk_per_trade_pct 死配置

修复前: 字段声明但全代码无引用 (dead config)
修复后: 删除字段, 仓位管理由 lib/position_size 独立负责
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect
import dataclasses

from lib.strategy.walkforward import WalkForwardConfig


def test_p1_9_risk_per_trade_pct_removed_or_used():
    """P1-9: risk_per_trade_pct 应被删除或被实际使用"""
    fields = {f.name for f in dataclasses.fields(WalkForwardConfig)}
    if "risk_per_trade_pct" not in fields:
        # 字段已删除, OK
        return

    # 字段仍存在: 检查源码中是否被实际引用
    import lib.strategy.walkforward as walkforward
    source = inspect.getsource(walkforward)
    lines = source.split("\n")
    usage_lines = [
        l for l in lines
        if "risk_per_trade_pct" in l and "risk_per_trade_pct:" not in l
    ]
    assert len(usage_lines) > 0, (
        f"P1-9 修复未完成: risk_per_trade_pct 字段存在但全代码无使用（dead config）。\n"
        f"应删除字段或接入风控。"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
