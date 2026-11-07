"""P2-3: backtest_saver 暴露 losses 字段与 win/loss ratio

修复点：trades 数组的 pnl 字段能算出 wins/losses，
但 backtest_saver 仅暴露 win_rate/avg_win/avg_loss，缺少：
  - n_wins / n_losses 显式计数
  - win_loss_ratio (avg_win / abs(avg_loss))
  - profit_factor  (sum_win / abs(sum_loss))
  - total_win_pnl / total_loss_pnl 实际金额
  - expectancy      平均每笔期望收益

测试数据：
  6 笔交易，3 盈 3 亏
  wins   = [+0.10, +0.20, +0.30] → sum=+0.60, avg=+0.20
  losses = [-0.05, -0.10, -0.15] → sum=-0.30, avg=-0.10
  win_rate            = 0.5
  win_loss_ratio      = 0.20 / 0.10 = 2.0
  profit_factor       = 0.60 / 0.30 = 2.0
  total_win_pnl       = 0.60
  total_loss_pnl      = -0.30
  expectancy          = (0.60 + -0.30) / 6 = 0.05
"""
import numpy as np
import pytest

from lib.monitoring.backtest_saver import _compute_metrics


def _nav():
    # 简单递增 nav, 6 个交易日即可（与 trades 数量无关，但 length>=2 需要）
    return np.array([1.0, 1.01, 1.02, 1.015, 1.025, 1.03, 1.04])


def _trades():
    return [
        {"pnl": 0.10, "reason": "止盈"},
        {"pnl": -0.05, "reason": "硬止损"},
        {"pnl": 0.20, "reason": "止盈"},
        {"pnl": -0.10, "reason": "ATR止损"},
        {"pnl": 0.30, "reason": "止盈"},
        {"pnl": -0.15, "reason": "超时"},
    ]


def test_exposes_n_wins_and_n_losses():
    """P2-3 核心修复：必须显式暴露 n_wins / n_losses 计数"""
    m = _compute_metrics(_nav(), _trades())
    assert m["n_wins"] == 3, f"n_wins 应为 3，实际 {m.get('n_wins')}"
    assert m["n_losses"] == 3, f"n_losses 应为 3，实际 {m.get('n_losses')}"


def test_win_loss_ratio_computed_from_trades():
    """win/loss ratio = avg_win / abs(avg_loss)"""
    m = _compute_metrics(_nav(), _trades())
    # avg_win=0.20, avg_loss=-0.10, ratio = 0.20/0.10 = 2.0
    assert m["win_loss_ratio"] == pytest.approx(2.0, abs=1e-4), \
        f"win_loss_ratio 应为 2.0，实际 {m.get('win_loss_ratio')}"


def test_profit_factor_computed_from_trades():
    """profit_factor = sum(win) / abs(sum(loss))"""
    m = _compute_metrics(_nav(), _trades())
    # 0.60 / 0.30 = 2.0
    assert m["profit_factor"] == pytest.approx(2.0, abs=1e-4), \
        f"profit_factor 应为 2.0，实际 {m.get('profit_factor')}"


def test_total_win_pnl_and_total_loss_pnl():
    """暴露总盈/亏金额（不是只暴露均值）"""
    m = _compute_metrics(_nav(), _trades())
    assert m["total_win_pnl"] == pytest.approx(0.60, abs=1e-4), \
        f"total_win_pnl 应为 0.60，实际 {m.get('total_win_pnl')}"
    assert m["total_loss_pnl"] == pytest.approx(-0.30, abs=1e-4), \
        f"total_loss_pnl 应为 -0.30，实际 {m.get('total_loss_pnl')}"


def test_expectancy_per_trade():
    """expectancy = (sum_win + sum_loss) / n_trades"""
    m = _compute_metrics(_nav(), _trades())
    # (0.60 - 0.30) / 6 = 0.05
    assert m["expectancy"] == pytest.approx(0.05, abs=1e-4), \
        f"expectancy 应为 0.05，实际 {m.get('expectancy')}"


def test_handles_all_winning_trades():
    """全部盈利：losses=0, win_loss_ratio 应为 0（不除零），profit_factor=inf"""
    trades = [{"pnl": 0.10, "reason": "x"} for _ in range(3)]
    m = _compute_metrics(_nav(), trades)
    assert m["n_wins"] == 3
    assert m["n_losses"] == 0
    assert m["win_loss_ratio"] == 0  # 没有 loss → 0
    assert m["profit_factor"] == float("inf")  # 行业惯例
    assert m["total_loss_pnl"] == 0


def test_handles_all_losing_trades():
    """全部亏损：wins=0, profit_factor=0"""
    trades = [{"pnl": -0.10, "reason": "x"} for _ in range(3)]
    m = _compute_metrics(_nav(), trades)
    assert m["n_wins"] == 0
    assert m["n_losses"] == 3
    assert m["win_rate"] == 0
    assert m["win_loss_ratio"] == 0  # 没有 win → 0
    assert m["profit_factor"] == 0
    assert m["total_win_pnl"] == 0


def test_no_trades_keeps_legacy_behavior():
    """trades 为空/None 时：不应报错，也不暴露新键（保持向后兼容）"""
    m = _compute_metrics(_nav(), None)
    assert "n_trades" not in m
    assert "win_loss_ratio" not in m


def test_zero_pnl_trades_excluded_from_win_loss_count():
    """pnl=0 的交易既不算赢也不算亏（与现有 pnl>0 / pnl<0 严格匹配）"""
    trades = [
        {"pnl": 0.10, "reason": "x"},
        {"pnl": 0.0, "reason": "平"},   # pnl=0
        {"pnl": -0.05, "reason": "y"},
    ]
    m = _compute_metrics(_nav(), trades)
    assert m["n_wins"] == 1
    assert m["n_losses"] == 1
    assert m["n_trades"] == 3  # 总笔数仍含 0
