"""RF-01 Phase 4 阶段 B 特征测试 —— 灰度开关与消费者接入的硬守卫。

三条机器判据（防止「以为切了其实没切」这类空判据，坑表 P-54）：
  ① flag off 时**绝不**构造内核；flag on 时**必须**经过内核；
  ② 消费者文件里不许直接读环境变量 / 判定开关（判定只许在网关里）；
  ③ `_compute_base_scores` 必须经 `score_4d("strategies", ...)`，不许退回直调旧实现。
"""

from __future__ import annotations

import ast
import pathlib
import types
from unittest.mock import Mock

import pytest

from lib.scoring.scorer import comprehensive_score
from lib.scoring.scoring_gateway import GRAY_CONSUMERS, score_4d

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _node(tree: ast.Module, name: str) -> ast.FunctionDef:
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"function {name} not found")


# ---------------------------------------------------------------- 两条路径

def test_off_never_builds_kernel(monkeypatch):
    import lib.scoring.scoring_adapters as adapters

    def poison():
        raise AssertionError("off-side must not build a kernel")

    monkeypatch.delenv("FEATURE_UNIFIED_SCORING", raising=False)
    monkeypatch.setattr(adapters, "build_kernel", poison)
    assert score_4d("strategies", 7.3, 4.1, 6.2, 8.4) == comprehensive_score(
        7.3, 4.1, 6.2, 8.4
    )


@pytest.mark.parametrize("flag", ["strategies", "all", "*", "1"])
def test_on_goes_through_kernel(monkeypatch, flag):
    import lib.scoring.scoring_adapters as adapters

    stub = Mock()
    stub.score.return_value = types.SimpleNamespace(raw={"sentinel": True})
    monkeypatch.setenv("FEATURE_UNIFIED_SCORING", flag)
    monkeypatch.setattr(adapters, "build_kernel", lambda: stub)

    assert score_4d("strategies", 1, 2, 3, 4) == {"sentinel": True}
    assert stub.score.call_count == 1
    source, inputs = stub.score.call_args[0]
    assert source == "scorer_4d"
    assert inputs == {"fundamental": 1, "capital_flow": 2, "news": 3, "technical": 4}


@pytest.mark.parametrize("values", [
    (0, 0, 0, 0), (10, 10, 10, 10), (7.3, 4.1, 6.2, 8.4),
    (2.5, 8.8, 1.2, 9.9),
])
def test_off_on_results_identical(monkeypatch, values):
    monkeypatch.delenv("FEATURE_UNIFIED_SCORING", raising=False)
    off = score_4d("strategies", *values)
    monkeypatch.setenv("FEATURE_UNIFIED_SCORING", "strategies")
    on = score_4d("strategies", *values)
    monkeypatch.setenv("FEATURE_UNIFIED_SCORING", "all")
    on_all = score_4d("strategies", *values)
    assert off == comprehensive_score(*values) == on == on_all


def test_gray_consumers_declared():
    assert "strategies" in GRAY_CONSUMERS


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
    gateway_calls = [n for n in calls
                     if isinstance(n.func, ast.Name) and n.func.id == "score_4d"]
    assert gateway_calls[0].args[0].value == "strategies"


def test_consumer_does_not_read_flag_itself():
    """灰度判定只能存在于网关；消费者里出现环境变量读取 = 判定散落。"""
    tree = ast.parse((ROOT / "lib/strategy/strategies.py").read_text(encoding="utf-8"))
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
