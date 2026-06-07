"""P2-4: scripts/backtest_charts.py 临时图表文件清理测试

验证:
  1. temp_charts() context manager 退出后临时目录被自动删除
  2. cleanup_charts() 清理 cache/charts/ 中的 png/pdf 文件
  3. 即使在生成过程中抛异常，临时目录仍被清理（try/finally 语义）
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def _make_minimal_result():
    """构造最小可用 result dict —— equity_curve 图表所需的字段"""
    dates = pd.date_range("2024-01-01", periods=30, freq="D")
    return {
        "perf_df": pd.DataFrame(
            {
                "date": dates,
                "total_value": [1.0 + i * 0.005 for i in range(30)],
                "benchmark_nav": [1.0 + i * 0.003 for i in range(30)],
            }
        ),
        "trades": [
            {
                "entry_date": "2024-01-10",
                "exit_date": "2024-01-20",
                "pnl_pct": 0.02,
                "hold_days": 10,
            }
        ],
        "exit_reasons": {"stop_loss": 1, "take_profit": 2},
    }


def test_temp_charts_cleans_up_on_exit(tmp_path):
    """temp_charts context manager 退出后临时目录应被清理"""
    from backtest_charts import temp_charts

    result = _make_minimal_result()
    target_dir = tmp_path / "charts"

    with temp_charts(result, output_dir=str(target_dir)) as chart_dir:
        # 上下文内：目录存在且至少有一个图表
        assert chart_dir.exists(), "context 内临时目录应存在"
        files = list(chart_dir.iterdir())
        assert len(files) > 0, "应至少生成一个图表文件"

    # 上下文外：目录已被删除
    assert not target_dir.exists(), (
        f"temp_charts 退出后临时目录应被删除，但 {target_dir} 仍存在"
    )


def test_cleanup_charts_removes_png(tmp_path):
    """cleanup_charts 应删除目录内的 png 文件"""
    from backtest_charts import cleanup_charts

    chart_dir = tmp_path / "charts"
    chart_dir.mkdir()
    (chart_dir / "equity_curve.png").write_bytes(b"fake png")
    (chart_dir / "trade_heatmap.png").write_bytes(b"fake png")
    (chart_dir / "factor_contribution.png").write_bytes(b"fake png")
    (chart_dir / "keep.txt").write_text("keep me")  # 非图表文件

    removed = cleanup_charts(str(chart_dir))

    assert removed == 3, f"应删除 3 个图表文件，实际 {removed}"
    assert not (chart_dir / "equity_curve.png").exists()
    assert not (chart_dir / "trade_heatmap.png").exists()
    assert not (chart_dir / "factor_contribution.png").exists()
    assert (chart_dir / "keep.txt").exists(), "cleanup_charts 不应删除非图表文件"


def test_cleanup_charts_nonexistent_dir(tmp_path):
    """cleanup_charts 对不存在的目录应返回 0，不抛异常"""
    from backtest_charts import cleanup_charts

    nonexistent = tmp_path / "does_not_exist"
    removed = cleanup_charts(str(nonexistent))
    assert removed == 0


def test_temp_charts_cleans_up_on_exception(tmp_path):
    """即使生成过程中抛异常，临时目录也应被清理（try/finally 语义）"""
    from backtest_charts import temp_charts

    target_dir = tmp_path / "charts"

    # 用一个空 trades 列表，plot_trade_heatmap 会早 return，不会抛
    # 用 exit_reasons 也为空来确保 plot_factor_contribution 早 return
    result = {
        "perf_df": pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=5, freq="D"),
                "total_value": [1.0, 1.01, 1.02, 1.03, 1.04],
            }
        ),
        "trades": [],
        "exit_reasons": {},
    }

    with pytest.raises(RuntimeError, match="boom"):
        with temp_charts(result, output_dir=str(target_dir)) as chart_dir:
            assert chart_dir.exists()
            # 在上下文内主动抛异常
            raise RuntimeError("boom")

    # 异常后临时目录应仍被清理
    assert not target_dir.exists(), (
        "异常后临时目录应被清理（finally 语义），但仍存在"
    )
