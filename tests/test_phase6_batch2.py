"""RF-01 Phase 6 第 2 批 —— 迁移、取证与收口的机器判据。

防止三类回退：
  ① 共享状态读写又散落回 routers/（收口白干）；
  ② 新增 offload 消费者却没登记 / 没做对拍（空判据）；
  ③ 基线被无约束重建（G4 字段级取证失效）。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
EVIDENCE_FILE = CONTRACTS / "blocking-io-recapture-evidence.json"
BASELINE_FILE = CONTRACTS / "blocking-io-baseline.json"


def _load_script(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shared_state_access_is_collapsed_to_locked_apis():
    """routers/ 里不得再直接下标/方法访问两个共享容器。"""
    offenders = []
    containers = {"_scan_tasks", "_perf_metrics"}
    for path in sorted((ROOT / "routers").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and node.value.id in containers):
                offenders.append(f"{path.name}:{node.lineno} subscript on {node.value.id}")
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id in containers):
                offenders.append(f"{path.name}:{node.lineno} .{node.attr} on {node.value.id}")
    assert not offenders, f"shared state access leaked back into routers: {offenders}"


def test_recapture_evidence_records_are_complete_and_monotonic():
    assert EVIDENCE_FILE.exists(), "run blocking_io_check.py --recapture first"
    data = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))
    records = data if isinstance(data, list) else [data]
    assert len(records) >= 2
    for rec in records:
        assert rec["untouched_identical"] is True
        assert rec["untouched_endpoints"] > 0
        assert rec["per_endpoint"], "no changed endpoints recorded"
        assert rec["totals_after"]["blocking_calls"] <= rec["totals_before"]["blocking_calls"]
        assert rec["totals_after"]["offloaded_calls"] >= rec["totals_before"]["offloaded_calls"]
    offload = [r for r in records if r.get("mode", "offload") == "offload"]
    assert offload and offload[0]["totals_after"]["blocking_calls"] < \
        offload[0]["totals_before"]["blocking_calls"]


def test_baseline_constraints_keep_the_rejected_plan_target():
    """判据被推翻后仍要留对照，不能悄悄把计划数字改成实测值。"""
    base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert base["constraints"]["plan_stated_target"] == "blocking call count <= 1"
    assert len(base["constraints"].get("recapture_history", [])) >= 2


def test_pre_migration_commit_is_pinned_and_readable():
    mod = _load_script("_offload_switch", ROOT / "scripts" / "offload_switch_diff.py")
    # L9 已泛化为多迁移：每个迁移自带 pinned pre_commit
    # （backtest 系 523b353，第 4 批 77bc366，第 5 批 6d0e434，第 6 批 497550f，第 7 批 c9b4d6f）
    assert len(mod.MIGRATIONS) >= 37
    for m in mod.MIGRATIONS:
        out = subprocess.run(
            ["git", "cat-file", "-e", f"{m.pre_commit}:{m.source_rel}"],
            cwd=ROOT, capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, f"pinned commit unreadable: {m.pre_commit}:{m.source_rel}"
    assert mod.check_migration() == []


def test_consumer_registry_matches_actual_call_sites_and_evidence():
    mod = _load_script("_offload_switch2", ROOT / "scripts" / "offload_switch_diff.py")
    assert mod.check_consumer_registry() == []
    from lib.infra.async_offload import OFFLOAD_CONSUMERS
    assert {m[0] for m in mod.MIGRATIONS} == set(OFFLOAD_CONSUMERS)
