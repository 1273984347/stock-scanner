"""RF-01 Phase 5 第 1 批 —— 端点归属表 / 契约基线的机器判据。

拆分 `api.py` 之前先把 68 端点的归属、鉴权、前端消费面钉死；
这些断言防止「拆完才发现漏挂鉴权」或「归属表与运行时悄悄漂移」。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs" / "refactor" / "contracts"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "_route_ownership", ROOT / "scripts" / "route_ownership_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ownership():
    p = CONTRACTS / "route-ownership.json"
    assert p.exists(), "run: python scripts/route_ownership_check.py --capture"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frontend():
    p = CONTRACTS / "frontend-api-paths.json"
    assert p.exists(), "run: python scripts/route_ownership_check.py --capture"
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def contract():
    p = CONTRACTS / "route-contract-baseline.json"
    return json.loads(p.read_text(encoding="utf-8"))


def test_router_definition_is_disjoint_and_17_domains():
    mod = _load_script()
    assert len(mod.ROUTERS) == 17
    all_paths = [p for paths in mod.ROUTERS.values() for p in paths]
    assert len(all_paths) == len(set(all_paths)), "a path must belong to exactly one router"
    assert len(all_paths) == 65


def test_all_68_endpoints_have_a_router(ownership):
    assert ownership["counts"]["endpoints"] == 68
    assert ownership["counts"]["unique_paths"] == 65
    assert ownership["counts"]["unassigned"] == 0
    assert sum(ownership["counts"]["by_router"].values()) == 68
    assert all(e["router"] for e in ownership["endpoints"])


def test_ownership_matches_contract_members_and_count(ownership, contract):
    base = [r for r in contract["routes"] if r.get("category") == "api"]
    base_pairs = {(r["path"], r["method"]) for r in base}
    own_pairs = {(e["path"], e["method"]) for e in ownership["endpoints"]}
    assert len(base_pairs) == len(own_pairs) == 68
    assert own_pairs == base_pairs


def test_router_paths_cover_all_runtime_paths(ownership):
    mod = _load_script()
    declared = mod._declared_paths()
    live = {e["path"] for e in ownership["endpoints"]}
    assert declared == live


def test_auth_and_async_match_contract(ownership, contract):
    base = {(r["path"], r["method"]): r
            for r in contract["routes"] if r.get("category") == "api"}
    for e in ownership["endpoints"]:
        b = base[(e["path"], e["method"])]
        assert e["auth_dependency_count"] == len(b["auth_dependencies"])
        assert e["auth_required"] == b["auth_required"]
        assert e["async"] == b["async"]


def test_three_unauthenticated_endpoints_are_frozen(ownership, contract):
    """Phase 0 基线：仅这 3 条免鉴权；拆分时不得新增。"""
    unauth = sorted({e["path"] for e in ownership["endpoints"]
                     if not e["auth_required"]})
    assert unauth == ["/api/errors", "/api/health", "/api/scan/status"]


def test_frontend_paths_all_mapped(frontend):
    assert frontend["counts"]["unmapped"] == 0
    assert frontend["counts"]["raw_strings"] >= frontend["counts"]["normalized_paths"]


def test_frontend_paths_are_declared_routes(frontend, ownership):
    declared = {e["path"] for e in ownership["endpoints"]}
    assert set(frontend["normalized_paths"]) <= declared
    assert len(set(frontend["normalized_paths"])) == frontend["counts"]["normalized_paths"]


def test_frontend_hard_constraint_is_recorded(frontend):
    """计划写 18；实测读数不同时必须有显式记录，不能悄悄对齐。"""
    assert frontend["constraints"]["plan_stated_count"] == 18
    assert frontend["counts"]["raw_strings"] > 0
    assert frontend["counts"]["normalized_paths"] > 0
