"""RF-01 Phase 6 第 1 批 —— 第 8 把尺子（阻塞 IO 基线）的机器判据。

对应 RULES §4.1 L8 与 §1.7 T1–T5。核心是三条：
  1. 端点集合必须与运行时归属表一致（不另造口径）；
  2. 把阻塞调用搬进 helper 绕不过判据（漏斗全覆盖，坑表 P-08）；
  3. 尺子必须自证「会因回归判红」，且红的原因就是该回归（P-10 / P-54）。
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
BASELINE_FILE = CONTRACTS / "blocking-io-baseline.json"
OWNERSHIP_FILE = CONTRACTS / "route-ownership.json"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "_blocking_io_check", ROOT / "scripts" / "blocking_io_check.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rule_tables_are_disjoint_and_explicit():
    mod = _load_script()
    blocking = set(mod.BLOCKING_TAILS) | set(mod.BLOCKING_FULLS)
    offloaded = set(mod.OFFLOADED_TAILS) | set(mod.OFFLOADED_FULLS)
    assert not (blocking & offloaded), "a symbol cannot be both blocking and offloaded"
    assert "run_blocking" in offloaded
    assert "open" in mod.BLOCKING_TAILS
    assert "_cache_get" in mod.BLOCKING_TAILS


def test_classify_does_not_confuse_ambiguous_tails():
    """P-28 同构：`dict.get` 不是网络请求；泛名只按完整名分类。"""
    mod = _load_script()
    assert mod.classify("data.get", "get") == "other"
    assert mod.classify("row.get", "get") == "other"
    assert mod.classify("json.load", "load") == "blocking"
    assert mod.classify("open", "open") == "blocking"
    assert mod.classify("run_blocking", "run_blocking") == "offloaded"
    assert mod.classify("anyio.to_thread.run_sync", "run_sync") == "offloaded"


def test_baseline_exists_with_69_endpoints_and_matches_ownership():
    assert BASELINE_FILE.exists(), "run: python scripts/blocking_io_check.py --capture"
    base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    own = json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8"))
    # ADR-0026：新增 GET /api/system/host-health（零阻塞）后 68 → 69
    assert base["counts"]["endpoints"] == 69
    base_pairs = {(e["method"], e["path"]) for e in base["endpoints"]}
    own_pairs = {(e["method"], e["path"]) for e in own["endpoints"]}
    assert base_pairs == own_pairs
    assert len(base_pairs) == len(own_pairs) == 69


def test_all_endpoints_remain_async():
    """Phase 6 不得把 async def 改成 def —— 那会静默改变 FastAPI 调度语义。"""
    own = json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8"))
    assert all(e["async"] for e in own["endpoints"])


def test_helper_expansion_catches_blocking_calls_moved_into_helpers():
    """把 `open` / `json.load` 搬进 `_load_alert_rules` 也必须被计入。

    两级嵌套展开（端点 → helper → helper）用合成 AST 直接钉死：第 5 批后唯一
    真实 depth-2 链（/api/risk-exposure → _batch_get_stock_hist → _get_orchestrator）
    已整体进入 offload 边界，真实基线只剩 depth-1 链 —— 嵌套能力是尺子的属性，
    不能押在「哪个端点恰好未 offload」上（见 ADR-0011）。
    """
    base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    expanded = [c for e in base["endpoints"] for c in e["calls"] if c["depth"] > 0]
    assert expanded, "helper expansion produced no calls -- the gate is blind"
    alerts = next(e for e in base["endpoints"] if e["path"] == "/api/alerts/rules")
    helper_calls = {(c["callee"], c["depth"]) for c in alerts["calls"] if c["depth"] > 0}
    assert ("open", 1) in helper_calls
    assert ("json.load", 1) in helper_calls

    mod = _load_script()
    src = (
        "def _inner():\n"
        "    open('x.txt')\n"
        "def _outer():\n"
        "    _inner()\n"
        "async def endpoint():\n"
        "    _outer()\n"
    )
    tree = ast.parse(src)
    helpers = mod._helper_index({pathlib.Path("fake.py"): tree})
    ep = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef))
    calls = mod._scan_endpoint(ep, "fake.py", helpers)
    depth2 = [(c["callee"], c["depth"]) for c in calls if c["depth"] == 2]
    assert ("open", 2) in depth2, f"nested helpers must be reachable: {depth2}"


def test_baseline_counts_are_internally_consistent():
    base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    eps = base["endpoints"]
    assert base["counts"]["total_calls"] == sum(len(e["calls"]) for e in eps)
    assert base["counts"]["blocking_calls"] == sum(sum(e["blocking"].values()) for e in eps)
    assert base["counts"]["other_calls"] == sum(sum(e["other"].values()) for e in eps)
    assert base["counts"]["blocking_endpoints"] == sum(1 for e in eps if e["blocking"])
    assert base["counts"]["blocking_calls"] > 0, "an empty baseline would be a vacuous gate"


def test_plan_stated_target_is_recorded_not_silently_aligned():
    """计划写「阻塞调用 <= 1」，实测 242；口径修正必须留痕（第 6 次判据修正）。"""
    base = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert base["constraints"]["plan_stated_target"] == "blocking call count <= 1"
    assert base["counts"]["blocking_calls"] > 1


def test_gate_turns_red_for_a_new_blocking_call():
    mod = _load_script()
    base = mod.capture()
    mutated = copy.deepcopy(base)
    ep = mutated["endpoints"][0]
    ep["calls"].append({"callee": "open", "tail": "open", "category": "blocking",
                        "file": "<test>", "lineno": -1, "depth": 0})
    ep["blocking"]["open"] = ep["blocking"].get("open", 0) + 1
    mod._recount(mutated)
    failures = mod.check(mutated, base)
    assert any("blocking INCREASED" in f for f in failures)


def test_gate_turns_red_for_an_unregistered_symbol():
    """阻塞表不完整也不会静默放过：表外新符号由 other 逐字段冻结兜住。"""
    mod = _load_script()
    base = mod.capture()
    mutated = copy.deepcopy(base)
    ep = mutated["endpoints"][0]
    ep["calls"].append({"callee": "requests.get", "tail": "get", "category": "other",
                        "file": "<test>", "lineno": -1, "depth": 0})
    ep["other"]["requests.get"] = ep["other"].get("requests.get", 0) + 1
    mod._recount(mutated)
    failures = mod.check(mutated, base)
    assert any("other drift" in f for f in failures)


def test_gate_turns_red_when_a_rule_is_deleted():
    mod = _load_script()
    base = mod.capture()
    mutated = copy.deepcopy(base)
    mutated["rules"]["blocking_tails"] = [
        x for x in mutated["rules"]["blocking_tails"] if x != "open"]
    mod._recount(mutated)
    failures = mod.check(mutated, base)
    assert any("removed entries" in f for f in failures)


def test_selftest_passes_and_live_gate_is_green():
    mod = _load_script()
    assert mod.selftest() == []
    assert mod.check() == []


# ========== ADR-0026：recapture 的「新增端点」通道 ==========
# 门禁原本没有任何「合法新增端点」的路径，只能 --capture 全量重建（丢掉逐字段取证）。
# 新增通道必须自带护栏：只允许 refactor + 显式 allow + 零阻塞，否则判红。

TARGET = "GET /api/system/host-health"


def _baseline_without(mod, key: str) -> tuple[dict, dict]:
    new = mod.capture()
    old = copy.deepcopy(new)
    old["endpoints"] = [e for e in old["endpoints"] if e["key"] != key]
    old["counts"]["endpoints"] = len(old["endpoints"])
    return old, new


def _replay(mod, tmp_path, monkeypatch, old: dict, new: dict, allowed: set, mode: str):
    baseline = tmp_path / "blocking-io-baseline.json"
    baseline.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(mod, "BASELINE_FILE", baseline)
    monkeypatch.setattr(mod, "capture", lambda: copy.deepcopy(new))
    return mod.recapture(allowed, mode=mode)


def test_recapture_allows_explicit_zero_blocking_addition(tmp_path, monkeypatch):
    mod = _load_script()
    old, new = _baseline_without(mod, TARGET)
    failures, _, evidence = _replay(mod, tmp_path, monkeypatch, old, new,
                                    {("GET", "/api/system/host-health")}, "refactor")
    assert failures == [], failures
    added = [r for r in evidence["per_endpoint"] if r.get("added")]
    assert [r["endpoint"] for r in added] == [TARGET]
    assert added[0]["blocking_after"] == 0


def test_recapture_rejects_undeclared_addition(tmp_path, monkeypatch):
    """allowed 非空但没列出新端点 —— 新增必须逐条声明，不能靠「反正有 allow」混过。"""
    mod = _load_script()
    old, new = _baseline_without(mod, TARGET)
    failures, _, _ = _replay(mod, tmp_path, monkeypatch, old, new,
                             {("GET", "/api/health")}, "refactor")
    assert any("added without --allow-endpoints" in f for f in failures), failures


def test_recapture_rejects_addition_outside_refactor_mode(tmp_path, monkeypatch):
    mod = _load_script()
    old, new = _baseline_without(mod, TARGET)
    failures, _, _ = _replay(mod, tmp_path, monkeypatch, old, new,
                             {("GET", "/api/system/host-health")}, "offload")
    assert any("only in --mode refactor" in f for f in failures), failures


def test_recapture_rejects_added_endpoint_with_blocking(tmp_path, monkeypatch):
    mod = _load_script()
    old, new = _baseline_without(mod, TARGET)
    for e in new["endpoints"]:
        if e["key"] == TARGET:
            e["blocking"]["open"] = 1
    failures, _, _ = _replay(mod, tmp_path, monkeypatch, old, new,
                             {("GET", "/api/system/host-health")}, "refactor")
    assert any("introduces 1 blocking call" in f for f in failures), failures


def test_recapture_rejects_endpoint_removal(tmp_path, monkeypatch):
    mod = _load_script()
    old, new = _baseline_without(mod, TARGET)   # old 少一个 → 反向构造「删除」
    failures, _, _ = _replay(mod, tmp_path, monkeypatch, new, old,
                             {("GET", "/api/system/host-health")}, "refactor")
    assert any("endpoint set shrank" in f for f in failures), failures
