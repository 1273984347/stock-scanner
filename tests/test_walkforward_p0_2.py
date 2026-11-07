"""P0-2: lib/walkforward.py summarize_rounds worst_drawdown 用 min 不是 max

修复前: drawdown 是负值, max(drawdowns) 返回最浅（最好）的回撤
        → 报告的 worst_drawdown 实际是"最佳回撤"，反向语义
修复后: 用 min(drawdowns) 选出最深（最差）的回撤
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.strategy.walkforward import (
    WalkForwardRound, ValidationStatus, summarize_rounds,
)


def test_worst_drawdown_uses_min():
    """P0-2: worst_drawdown 应取最深（最小值）回撤，不是最浅"""
    # 三个窗口: -0.05, -0.20, -0.10
    # 真正的 worst (最差) = -0.20 (最小值)
    # 错误 max 取的是 -0.05 (最浅)
    rounds = [
        WalkForwardRound(
            window_index=0,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            validation_metrics={"max_drawdown": -0.05},
        ),
        WalkForwardRound(
            window_index=1,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            validation_metrics={"max_drawdown": -0.20},
        ),
        WalkForwardRound(
            window_index=2,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            validation_metrics={"max_drawdown": -0.10},
        ),
    ]

    summary = summarize_rounds(rounds)

    # P0-2: worst_drawdown 必须是最深的回撤 (最小值)
    assert summary["worst_drawdown"] == -0.20, (
        f"P0-2 修复未完成: worst_drawdown 应为 -0.20 (最深), "
        f"实际: {summary['worst_drawdown']}。"
        f"原代码用 max() 会返回 -0.05 (最浅), 这是反向语义。"
    )


def test_worst_drawdown_handles_positive_values():
    """P0-2: 当所有 drawdown 都为正（异常情况），min 仍能工作"""
    # 正常情况下 drawdown ≤ 0; 如果全部为 0/正, min 返回最小正值
    rounds = [
        WalkForwardRound(
            window_index=0,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            validation_metrics={"max_drawdown": 0.0},
        ),
        WalkForwardRound(
            window_index=1,
            strategy="test",
            validation_status=ValidationStatus.STABLE,
            validation_metrics={"max_drawdown": -0.15},
        ),
    ]
    summary = summarize_rounds(rounds)
    assert summary["worst_drawdown"] == -0.15, (
        f"P0-2 修复未完成: worst_drawdown 应为 -0.15, 实际: {summary['worst_drawdown']}"
    )


def test_worst_drawdown_empty():
    """P0-2: 无 round 时 worst_drawdown 应为 0"""
    summary = summarize_rounds([])
    assert summary["worst_drawdown"] == 0, (
        f"P0-2: 空轮次 worst_drawdown 应为 0, 实际: {summary['worst_drawdown']}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
