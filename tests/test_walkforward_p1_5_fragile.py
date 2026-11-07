"""P1-5: lib/walkforward.py _classify_status fragile 自动判定

修复前: fragile 硬编码 False, FRAGILE 状态永远走不到
修复后: 基于 train/val sharpe 偏离度自动判定 (|train-val|/|train| > 0.5 → FRAGILE)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import inspect

from lib.strategy.walkforward import (
    WalkForwardBacktest, WalkForwardConfig, ValidationStatus,
)


def test_p1_5_classify_status_signature_accepts_sharpes():
    """P1-5: _classify_status 签名应接受 train_sharpe/val_sharpe"""
    sig = inspect.signature(WalkForwardBacktest._classify_status)
    assert "train_sharpe" in sig.parameters, (
        f"P1-5 修复未完成: _classify_status 应接受 train_sharpe 参数, "
        f"实际签名: {sig}"
    )
    assert "val_sharpe" in sig.parameters, (
        f"P1-5 修复未完成: _classify_status 应接受 val_sharpe 参数, "
        f"实际签名: {sig}"
    )


def test_p1_5_fragile_when_sharpe_diverges_largely():
    """P1-5: train_sharpe=2.0, val_sharpe=0.0 → 偏离度 100% → FRAGILE"""
    config = WalkForwardConfig(min_train_trades=0, min_validation_trades=0)
    wf = WalkForwardBacktest(config=config)
    status = wf._classify_status(
        train_trades=10,
        validation_trades=5,
        train_sharpe=2.0,
        val_sharpe=0.0,
    )
    assert status == ValidationStatus.FRAGILE, (
        f"P1-5 修复未完成: train=2.0/val=0.0 应判 FRAGILE, 实际: {status}"
    )


def test_p1_5_stable_when_sharpe_similar():
    """P1-5: train_sharpe=1.5, val_sharpe=1.3 → 偏离度 13% → STABLE"""
    config = WalkForwardConfig(min_train_trades=0, min_validation_trades=0)
    wf = WalkForwardBacktest(config=config)
    status = wf._classify_status(
        train_trades=10,
        validation_trades=5,
        train_sharpe=1.5,
        val_sharpe=1.3,
    )
    assert status == ValidationStatus.STABLE, (
        f"P1-5 修复未完成: 接近时应判 STABLE, 实际: {status}"
    )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
