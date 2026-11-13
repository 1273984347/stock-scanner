"""RF-01 Phase 4 前置批次 —— 对拍基线 pin 机制的机器判据。

背景（2026-11-10 实测）：改动前基线即判红，根因不是业务回归，而是
`pinned_environment()` 只 pin 了数据源优先级 + 阻网，留下两类未 pin 输入：
  ① 评分链路的 get_stock_fund_flow / get_news_signals / get_sector_score_for_stock
     => 读到本地随时间更新的数据，capital_flow 4.0 -> 5.0
  ② engine.run_backtest 未传 start_date 时用 datetime.now() 推导窗口起点
     => equity_curve[0] 随日历漂移（11-07 -> 11-10 正好 +3 天）

本文件把这些教训变成**自动判据**：以后有人删掉 pin 或改回 wall-clock，
测试会立刻判红，而不是等基线某天自己漂移。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PINNED_FUND_FLOW = {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}


def _load(module_name: str, rel_path: str):
    """按文件路径加载脚本模块（scripts/ 不是包，不能用普通 import）。"""
    spec = importlib.util.spec_from_file_location(module_name, ROOT / rel_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _func(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name} not found")


def _wall_clock_calls(node: ast.AST) -> list[str]:
    """找出 now()/today()/utcnow() 调用（wall-clock 依赖）。"""
    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        attr = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if attr in ("now", "today", "utcnow"):
            out.append(attr)
    return out


# ---------------------------------------------------------------- L3 pin

def test_dual_run_pins_scoring_external_inputs():
    """pin 上下文里，评分链路读到的必须是固定注入值，与定义方数据无关。"""
    drd = _load("_dual_run_for_test", "scripts/dual_run_diff.py")
    import lib.data.data_layer as data_layer
    import lib.strategy.strategies as strategies

    def poisoned(*_a, **_k):
        raise RuntimeError("definition-module data source must not be reached")

    orig_ff = data_layer.get_stock_fund_flow
    orig_news = data_layer.get_news_signals
    data_layer.get_stock_fund_flow = poisoned
    data_layer.get_news_signals = poisoned
    try:
        with drd.pinned_environment():
            with drd._inject_shared_data(["600519"]):
                assert strategies.get_stock_fund_flow("600519") == PINNED_FUND_FLOW
                news = strategies.get_news_signals("600519")
                assert news["no_negative"] is True
                assert news["major_positive"] is False
                # 定义方也被 pin（函数内延迟 import 的路径）
                assert data_layer.get_stock_fund_flow("600519") == PINNED_FUND_FLOW
                import lib.strategy.sector_score as sector_score
                assert sector_score.get_sector_score_for_stock("600519") == 50.0
    finally:
        data_layer.get_stock_fund_flow = orig_ff
        data_layer.get_news_signals = orig_news


def test_dual_run_pin_restores_originals():
    """退出 pin 上下文后必须还原，避免污染同进程其它用例（P-18 同构）。"""
    drd = _load("_dual_run_restore_test", "scripts/dual_run_diff.py")
    import lib.strategy.strategies as strategies

    orig = strategies.get_stock_fund_flow
    with drd.pinned_environment():
        with drd._inject_shared_data(["600519"]):
            assert strategies.get_stock_fund_flow is not orig
    assert strategies.get_stock_fund_flow is orig


def test_dual_run_capture_chain_has_no_wall_clock():
    """捕获链路不得依赖 '今天'。generated_at 属元数据，不在捕获函数内。"""
    tree = ast.parse((ROOT / "scripts/dual_run_diff.py").read_text(encoding="utf-8"))
    for name in ("capture_score", "capture_scan", "capture_backtest",
                 "_inject_shared_data", "pinned_environment"):
        assert not _wall_clock_calls(_func(tree, name)), f"{name} depends on wall clock"


# ---------------------------------------------------------------- L4 pin

def test_backtest_baseline_has_no_wall_clock_anywhere():
    """回测基线脚本全文不得出现 now()/today()。"""
    tree = ast.parse((ROOT / "scripts/backtest_baseline.py").read_text(encoding="utf-8"))
    assert not _wall_clock_calls(tree)


def test_engine_chain_passes_pinned_start_date():
    """run_backtest 调用点必须显式传 start_date=PIN_START_DATE。"""
    tree = ast.parse((ROOT / "scripts/backtest_baseline.py").read_text(encoding="utf-8"))
    fn = _func(tree, "_run_engine_chain")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "run_backtest"]
    assert calls, "run_backtest call not found in _run_engine_chain"
    for call in calls:
        kws = {kw.arg: kw.value for kw in call.keywords}
        assert "start_date" in kws, "start_date must be pinned explicitly"
        assert isinstance(kws["start_date"], ast.Name)
        assert kws["start_date"].id == "PIN_START_DATE"


def test_pinned_start_date_matches_baseline_fingerprint():
    """锚定日推导结果必须与已入库基线的 equity_first[0] 一致。"""
    bb = _load("_backtest_baseline_for_test", "scripts/backtest_baseline.py")
    baseline = json.loads((ROOT / "docs/refactor/backtest-baseline.json").read_text(encoding="utf-8"))
    for chain in ("engine_donchian_entry", "engine_multi_signal_entry"):
        first = baseline["chains"][chain]["data"]["equity_first"][0]
        assert bb.PIN_START_DATE == first, f"{chain}: pinned {bb.PIN_START_DATE} vs baseline {first}"


# ---------------------------------------------------------------- L5 pin (P-78)

def _sb_module():
    return _load("_scoring_baseline_for_test", "scripts/scoring_baseline.py")


def test_capture_expands_judge_groups_constraints():
    """capture() 必须把评委组配置源展开进 constraints，不能只定义不用（P-78）。"""
    tree = ast.parse((ROOT / "scripts/scoring_baseline.py").read_text(encoding="utf-8"))
    fn = _func(tree, "capture")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_judge_groups_constraints"]
    assert calls, "capture() 必须调用 _judge_groups_constraints() 并展开进 constraints"


def test_judge_groups_constraints_match_runtime():
    """登记的权重必须与 judge_panel.JUDGE_GROUPS 运行时实际值一致。"""
    import lib.scoring.judge_panel as jp
    sb = _sb_module()
    c = sb._judge_groups_constraints()
    assert set(c) == {"judge_groups_source", "judge_groups_weights"}
    assert c["judge_groups_source"] in ("config/judges.json", "builtin-default")
    runtime = {str(k): float(cfg.get("weight", 0.0)) for k, cfg in jp.JUDGE_GROUPS.items()}
    assert c["judge_groups_weights"] == runtime


def test_baseline_file_matches_runtime_judge_groups():
    """基线冻结的配置源/权重必须与运行时一致 —— 配置一变就判红并提示重建。

    根因（P-78）：config/judges.json 被删后权重 0.20→1.00，L5 只报 34 行数值 diff，
    长期被当作「既有漂移」豁免。此判据让配置源变化直接变成可读信号，
    而不是等下一次全量对拍时才发现。
    """
    sb = _sb_module()
    frozen = json.loads((ROOT / "docs/refactor/scoring-baseline.json").read_text(
        encoding="utf-8"))["constraints"]
    runtime = sb._judge_groups_constraints()
    for key in ("judge_groups_source", "judge_groups_weights"):
        assert key in frozen, f"基线 constraints 缺少 {key}，请用 --capture 重建"
        assert frozen[key] == runtime[key], (
            f"评委组配置源已变化：基线={frozen[key]!r} 运行时={runtime[key]!r}；"
            "请按 G4 字段级取证后重新 --capture"
            "（见 docs/audit/l5-scoring-baseline-drift-2026-11-13.md / 坑表 P-78）"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
