"""P1-6: lib/walkforward.py _build_diagnostics 合并 val exit_reasons

修复前: 只用 train_result 的 exit_reasons, val 阶段永远是 0
修复后: 合并 train + val 的 stop_loss/take_profit/opposite_signal/end_exit 计数
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.walkforward import WalkForwardBacktest, WalkForwardConfig


def test_p1_6_diagnostics_merges_val_exit_reasons():
    """P1-6: stop_loss_count 应 = train.stop_loss + val.stop_loss"""
    config = WalkForwardConfig()
    wf = WalkForwardBacktest(config=config)
    train_result = {
        "exit_reasons": {"stop_loss": 3, "take_profit": 5, "opposite_signal": 1, "end_exit": 2},
        "total_fees": 100,
    }
    val_result = {
        "exit_reasons": {"stop_loss": 2, "take_profit": 4, "opposite_signal": 0, "end_exit": 1},
        "total_fees": 50,
    }
    diag = wf._build_diagnostics(train_result, val_result)

    assert diag["stop_loss_count"] == 5, (
        f"P1-6 修复未完成: stop_loss_count 应为 3+2=5, 实际: {diag['stop_loss_count']}"
    )
    assert diag["take_profit_count"] == 9, (
        f"P1-6 修复未完成: take_profit_count 应为 5+4=9, 实际: {diag['take_profit_count']}"
    )
    assert diag["opposite_signal_count"] == 1, (
        f"P1-6 修复未完成: opposite_signal_count 应为 1+0=1, 实际: {diag['opposite_signal_count']}"
    )
    assert diag["end_exit_count"] == 3, (
        f"P1-6 修复未完成: end_exit_count 应为 2+1=3, 实际: {diag['end_exit_count']}"
    )
    assert diag["total_fees"] == 150, (
        f"P1-6 修复未完成: total_fees 应为 100+50=150, 实际: {diag['total_fees']}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
