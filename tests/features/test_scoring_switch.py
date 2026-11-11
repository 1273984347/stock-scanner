"""RF-01 评分域特征测试 —— 网关唯一路径与消费者接入的硬守卫。

Phase 7 P7-3（2026-11-12，ADR-0014）灰度收口后，判据从「off/on 两条路径都要对」
改为「唯一路径正确 + 边界不回退」：
  ① 网关必须经 ScoringKernel（source/inputs 契约固定），数值与旧实现逐字段相等；
  ② 消费者文件里不许读环境变量 / 判定开关（flag 已删，出现即回退）；
  ③ 三个消费者必须经 `score_4d(...)`，不许退回直调旧实现。
"""

from __future__ import annotations

import ast
import pathlib
import types
from unittest.mock import Mock

import pytest

from lib.scoring.scorer import comprehensive_score
from lib.scoring.scoring_gateway import score_4d

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _node(tree: ast.Module, name: str):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"function {name} not found")


# ---------------------------------------------------------------- 唯一路径

def test_gateway_goes_through_kernel(monkeypatch):
    import lib.scoring.scoring_adapters as adapters

    stub = Mock()
    stub.score.return_value = types.SimpleNamespace(raw={"sentinel": True})
    monkeypatch.setattr(adapters, "build_kernel", lambda: stub)

    assert score_4d(1, 2, 3, 4) == {"sentinel": True}
    assert stub.score.call_count == 1
    source, inputs = stub.score.call_args[0]
    assert source == "scorer_4d"
    assert inputs == {"fundamental": 1, "capital_flow": 2, "news": 3, "technical": 4}


@pytest.mark.parametrize("values", [
    (0, 0, 0, 0), (10, 10, 10, 10), (7.3, 4.1, 6.2, 8.4),
    (2.5, 8.8, 1.2, 9.9),
])
def test_results_match_legacy(values):
    """收口不换算法：网关输出必须与旧实现逐字段相等（L5 基线之外的快速哨兵）。"""
    assert score_4d(*values) == comprehensive_score(*values)


def test_passthrough_consumers_declared():
    from lib.scoring.scoring_gateway import PASSTHROUGH_CONSUMERS
    assert "api_score" in PASSTHROUGH_CONSUMERS


# ---------------------------------------------------------------- 消费者接入（AST 守卫）

def test_strategies_routes_through_gateway():
    tree = ast.parse((ROOT / "lib/strategy/strategies.py").read_text(encoding="utf-8"))
    fn = _node(tree, "_compute_base_scores")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    names = {
        (n.func.id if isinstance(n.func, ast.Name)
         else n.func.attr if isinstance(n.func, ast.Attribute) else "")
        for n in calls
    }
    assert "score_4d" in names, "consumer must call the gateway"
    assert "comprehensive_score" not in names, "consumer must not call legacy scorer directly"


@pytest.mark.parametrize("rel", [
    "lib/strategy/strategies.py",
    "lib/strategy/steps.py",
    "run.py",
    "api.py",
])
def test_consumer_does_not_read_flag_itself(rel):
    """判定只能存在于网关；消费者里出现环境变量读取 = 判定散落。

    P7-3 收口后 flag 已删除——消费者里再出现这些名字只可能是「把灰度带回来」。
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    src = ast.unparse(tree)
    assert "FEATURE_UNIFIED_SCORING" not in src
    assert "unified_scoring_enabled" not in src


def test_gateway_has_no_mutable_module_state():
    """Phase 1 已清理跨端点共享可变状态；网关不得把它带回来。"""
    tree = ast.parse((ROOT / "lib/scoring/scoring_gateway.py").read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, (ast.Assign, ast.AnnAssign)):
            value = n.value
            assert not isinstance(value, (ast.Dict, ast.List, ast.Set)), (
                "gateway must stay stateless; use function-local objects"
            )


# ---------------------------------------------------------------- 第 2 个消费者：pipeline / 无实时行情

def _steps_tree() -> ast.Module:
    return ast.parse((ROOT / "lib/strategy/steps.py").read_text(encoding="utf-8"))


def _call_names(fn: ast.FunctionDef) -> set[str]:
    return {
        (n.func.id if isinstance(n.func, ast.Name)
         else n.func.attr if isinstance(n.func, ast.Attribute) else "")
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    }


def test_pipeline_consumer_uses_gateway():
    tree = _steps_tree()
    fn = _node(tree, "score_without_realtime")
    names = _call_names(fn)
    assert "score_4d" in names, "pipeline consumer must use the gateway"
    assert "comprehensive_score" not in names, "must not call legacy scorer directly"
    assert "compute_4d_dimensions" in names, "dimension extraction must be a separate pure function"


def test_dimension_extraction_is_pure():
    """抽出的函数只做维度计算，不含评分决策 —— 否则就会算出两份分。"""
    fn = _node(_steps_tree(), "compute_4d_dimensions")
    names = _call_names(fn)
    assert "comprehensive_score" not in names
    assert "score_4d" not in names
    assert any(isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)
               for n in ast.walk(fn)), "must return the 4 dimensions dict"


CASES = [
    ("600519", {"momentum_5": 5, "momentum_20": 10}, None),
    ("000858", {"momentum_5": -3, "momentum_20": 18}, {
        "pe": 20, "gross_margin": 0.4, "operating_cash_flow": 1e8,
        "tdx_fundamentals": {
            "profitability": {"ROE": 0.2, "gross_margin": 0.45},
            "solvency": {"debt_to_asset": 0.3},
            "growth": {"net_income_growth": 0.25},
        },
    }),
    ("600036", {"momentum_5": 0, "momentum_20": 0}, "series"),
]


def _fin(case_value):
    if case_value == "series":
        import pandas as pd
        return pd.Series({"_roe": 0.15, "_debt_to_asset": 0.5,
                          "revenue_growth": 0.1})
    return case_value


@pytest.fixture
def pinned_fund_flow(monkeypatch):
    import lib.data.data_layer as data_layer
    monkeypatch.setattr(
        data_layer, "get_stock_fund_flow",
        lambda code, *a, **k: {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8},
    )


@pytest.mark.parametrize("code,factors,fin", CASES)
def test_extraction_preserves_legacy_formula(monkeypatch, pinned_fund_flow, code, factors, fin):
    """旧函数 = 算出四维分 + 一次 comprehensive_score；这条等价式锁住抽取没改口径。

    另有一次 `git show HEAD:lib/strategy/steps.py` 动态加载旧函数的实跑对拍，
    覆盖 None / dict / pd.Series 三种 fin_data，结果见计划 §七 第 3 批。
    （P7-3 收口后不再需要 pin flag——评分路径唯一，等价式直接成立。）
    """
    from lib.strategy.steps import compute_4d_dimensions, score_without_realtime

    fin_data = _fin(fin)
    dims = compute_4d_dimensions(code, factors, fin_data)
    assert set(dims) == {"fundamental", "capital_flow", "news", "technical"}
    legacy = comprehensive_score(
        dims["fundamental"], dims["capital_flow"], dims["news"], dims["technical"]
    )
    assert score_without_realtime(code, factors, fin_data) == legacy


def test_dimension_extraction_is_deterministic(pinned_fund_flow):
    from lib.strategy.steps import compute_4d_dimensions
    for code, factors, fin in CASES:
        fin_data = _fin(fin)
        assert compute_4d_dimensions(code, factors, fin_data) ==                compute_4d_dimensions(code, factors, fin_data)


# ---------------------------------------------------------------- 第 3 个消费者：CLI / 端点透传

def _gateway_call(fn: ast.AST) -> ast.Call:
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "score_4d"]
    assert calls, "expected a score_4d(...) call"
    return calls[0]


def test_cli_consumer_uses_gateway():
    tree = ast.parse((ROOT / "run.py").read_text(encoding="utf-8"))
    fn = _node(tree, "run_score_mode")
    names = _call_names(fn)
    assert "score_4d" in names, "CLI --score must use the gateway"
    assert "comprehensive_score" not in names, "must not call legacy scorer directly"


def test_endpoint_is_pure_passthrough():
    """`/api/score/{code}` 是三层纯透传，没有独立评分调用点。

    硬给它加一个 flag 分支 = 为清单打勾加补丁（坑表 P-58）。
    这里用 AST 钉住「它就是透传」，让 L6 的 endpoint 分支有明确语义。
    Phase 6 第 6 批起端点把调用搬进 `_compute_stock_score`（run_blocking 边界）：
    端点只准 run_blocking，评分调用只准在搬迁体里；禁止名单同时覆盖两处。
    """
    tree = ast.parse((ROOT / "routers/scoring.py").read_text(encoding="utf-8"))
    endpoint = _node(tree, "stock_score")
    compute = _node(tree, "_compute_stock_score")
    endpoint_names = _call_names(endpoint)
    compute_names = _call_names(compute)
    assert "run_blocking" in endpoint_names, "endpoint must go through the offload entry"
    assert "get_stock_score" not in endpoint_names, "scoring call moved out of the endpoint"
    assert "get_stock_score" in compute_names, "compute body must remain the passthrough call"
    for forbidden in ("score_4d", "comprehensive_score", "unified_scoring_enabled"):
        assert forbidden not in endpoint_names
        assert forbidden not in compute_names


def test_cli_matches_legacy():
    """CLI 经网关的输出必须与旧实现逐字段相等（收口不换算法）。"""
    values = (7.3, 4.1, 6.2, 8.4)
    assert score_4d(*values) == comprehensive_score(*values)


# ---------------------------------------------------------------- 端点分类（阶段 B 收官）

SCORING_SYMBOLS = (
    "score_4d", "comprehensive_score", "get_stock_score",
    "unified_scoring_enabled", "ScoringKernel", "FEATURE_UNIFIED_SCORING",
)


def test_endpoint_classification_is_disjoint_and_complete():
    from lib.scoring.scoring_gateway import (
        PASSTHROUGH_CONSUMERS, INDIRECT_CONSUMERS, OUT_OF_SCOPE_ENDPOINTS,
    )
    groups = [set(PASSTHROUGH_CONSUMERS),
              set(INDIRECT_CONSUMERS), set(OUT_OF_SCOPE_ENDPOINTS)]
    assert sum(len(g) for g in groups) == len(set().union(*groups)), "classes must be disjoint"
    all_endpoints = set().union(*groups)
    assert {"api_score", "api_dashboard", "api_strategy_recommend"} <= all_endpoints


def test_dashboard_is_aggregator_not_scorer():
    """`/api/dashboard` 是缓存聚合端点，评分只经 run_all_strategies 间接受益。"""
    tree = ast.parse((ROOT / "routers/system.py").read_text(encoding="utf-8"))
    fn = _node(tree, "dashboard")
    names = _call_names(fn)
    for symbol in SCORING_SYMBOLS:
        assert symbol not in names, f"dashboard unexpectedly calls {symbol}"
    assert "_cache_get" in names and "_cache_set" in names
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    assert "run_all_strategies" in attrs, "indirect scoring path must stay visible"


def test_recommend_is_out_of_scoring_domain():
    """`/api/strategy/recommend` 属因子权重优化域，根本不消费评分结果。

    Phase 6 第 7 批起端点把调用搬进 `_compute_strategy_recommend`（run_blocking 边界，
    同 scoring 透传守卫的边界跟随法，ADR-0012 D2）：端点只准 run_blocking，
    优化器调用只准在搬迁体；评分符号禁止名单同时覆盖两处。
    """
    tree = ast.parse((ROOT / "routers/strategy.py").read_text(encoding="utf-8"))
    endpoint = _node(tree, "strategy_recommend")
    compute = _node(tree, "_compute_strategy_recommend")
    endpoint_names = _call_names(endpoint)
    compute_names = _call_names(compute)
    assert "run_blocking" in endpoint_names, "endpoint must go through the offload entry"
    assert "FactorOptimizer" not in endpoint_names, "optimizer call moved out of the endpoint"
    assert "FactorOptimizer" in compute_names and "evaluate_weights" in compute_names
    for symbol in SCORING_SYMBOLS:
        assert symbol not in endpoint_names, f"recommend unexpectedly calls {symbol}"
        assert symbol not in compute_names, f"recommend unexpectedly calls {symbol}"
