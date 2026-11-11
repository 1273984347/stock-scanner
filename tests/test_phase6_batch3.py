"""RF-01 Phase 6 第 3 批 —— 范围决策、压测口径与 Tier B 留痕的机器判据。

三条硬守卫：
  ① 砍掉 TTL 缓存是**有证据的范围决策**，不是悄悄划掉待办；
  ② 压测必须从「批次发起」计时（P-70），且低负载、零触网；
  ③ Tier B 登记必须机械分类、0 未分类，且重计算端点不能伪装成轻量 IO。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
REGISTER = CONTRACTS / "blocking-io-tier-b.json"
BASELINE = CONTRACTS / "blocking-io-baseline.json"


def _load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- 3a 范围决策

def test_existing_scan_cache_has_ttl_jitter_and_lock():
    scan = (ROOT / "routers" / "scan.py").read_text(encoding="utf-8")
    api_core = (ROOT / "lib" / "infra" / "api_core.py").read_text(encoding="utf-8")
    assert "_cache_get(cache_key, max_age=3600)" in scan
    assert "jitter" in api_core and "_cache_lock" in api_core
    assert "unlink(missing_ok=True)" in api_core


def test_no_backtest_cache_feature_was_sneaked_in():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if any(part in {"node_modules", ".workbuddy", "cache"} for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "FEATURE_BACKTEST_CACHE" in text and path.name != "test_phase6_batch3.py":
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"ADR-0008 forbids a new backtest cache flag: {offenders}"


def test_cache_scope_decision_is_recorded_in_project():
    assert (ROOT / "docs" / "adr" / "0008-phase6-cache-scope.md").exists()
    plan = (ROOT / "docs" / "plans" / "2026-11-07-refactor-plan.md").read_text(encoding="utf-8")
    assert "范围裁剪，不做" in plan and "ADR-0008" in plan


# ---------------------------------------------------------------- 3b 压测口径

def test_concurrency_probe_uses_client_view_timing_and_low_load():
    src = (ROOT / "scripts" / "concurrency_probe.py").read_text(encoding="utf-8")
    assert "batch_start = time.perf_counter()" in src
    assert "time.perf_counter() - batch_start" in src
    assert "SKIP_HS300_PREFETCH" in src
    mod = _load_script("_concurrency_probe", ROOT / "scripts" / "concurrency_probe.py")
    assert mod.CONCURRENCY == 20
    assert mod.ROUNDS == 3
    assert mod.BLOCK_SECONDS <= 0.02, "must stay a small batch; device shuts down under sustained load"
    assert mod.P95_TOLERANCE == 1.10, "do not loosen the threshold to make a red go green"


# ---------------------------------------------------------------- 3c Tier B

def test_tier_b_register_is_complete_and_mechanical():
    mod = _load_script("_blocking_io_b3", ROOT / "scripts" / "blocking_io_check.py")
    reg = json.loads(REGISTER.read_text(encoding="utf-8"))
    assert reg["counts"]["unclassified_entries"] == 0
    assert reg["counts"]["registered"] == len(reg["registered"])
    assert reg["counts"]["blocking_calls"] == sum(r["count"] for r in reg["registered"])
    for row in reg["registered"]:
        assert row["reason"], f"missing reason: {row}"
        assert row["group"] == mod._tier_b_group(row["callee"]), (
            f"group tampered: {row['endpoint']} :: {row['callee']}")


def test_tier_b_register_matches_live_baseline():
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    reg = json.loads(REGISTER.read_text(encoding="utf-8"))
    live = {(f"{e['method']} {e['path']}", c)
            for e in base["endpoints"] for c in e["blocking"]}
    registered = {(r["endpoint"], r["callee"]) for r in reg["registered"]}
    assert live <= registered, f"unregistered blocking symbols: {sorted(live - registered)[:5]}"


def test_tier_a_candidates_are_listed_not_hidden():
    """诚实性守卫：未 offload 的重计算端点必须显式登记为遗留。"""
    reg = json.loads(REGISTER.read_text(encoding="utf-8"))
    cands = reg["tier_a_candidates"]
    assert len(cands) == reg["counts"]["tier_a_candidate_endpoints"] > 0
    assert all(c["status"] == "not_offloaded" for c in cands)
    assert all(c["callees"] and c["blocking_calls"] > 0 for c in cands)
    keys = {c["endpoint"] for c in cands}
    assert "GET /api/market" in keys  # 第 4 批后仍为最大遗留端点之一
    # Phase 6 不能宣称收官：30 个候选端点还没有 offload 取证
    offloaded = {
        "POST /api/backtest", "POST /api/backtest/compare", "POST /api/walkforward",
        "GET /api/factor-eval", "GET /api/factor-scatter", "GET /api/kline/{code}",
        "GET /api/risk", "GET /api/export/config",
    }
    assert keys.isdisjoint(offloaded)
