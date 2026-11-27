#!/usr/bin/env python
"""端点归属表 + 契约基线复核 —— RF-01 Phase 5 第 1 批的尺子。

Phase 5 要把 `api.py` 按域拆成 router，退出判据要求：
  - 68 端点归属表与实际路由清单一致（**成员 + 数量双校验**）
  - 契约快照 diff = 0
  - 每个端点的**鉴权依赖数不得减少**
  - 18 条前端路径实跑通过（本批先固化「前端路径清单」的可复算读数）

本工具在**拆分之前**先把这四件事的基线钉成 JSON，拆分后 `--check` 即可判断
是「搬家」还是「改契约」。口径完全复用 `contract_snapshot.py` 的运行时采集，
不容许第二个口径。

用法：
  python scripts/route_ownership_check.py --capture
  python scripts/route_ownership_check.py --check
  python scripts/route_ownership_check.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _resolve_repo_root(start):
    """自锚定仓库根：向上找同时含 AGENTS.md 与 lib/ 的目录，脚本可任意移动。"""
    for cand in (start, *start.parents):
        if (cand / "AGENTS.md").is_file() and (cand / "lib").is_dir():
            return cand
    raise SystemExit(f"[gate] 无法定位仓库根: 从 {start} 向上未找到 AGENTS.md + lib/")


ROOT = _resolve_repo_root(pathlib.Path(__file__).resolve().parent)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
OWNERSHIP_FILE = CONTRACTS / "route-ownership.json"
FRONTEND_FILE = CONTRACTS / "frontend-api-paths.json"
CONTRACT_FILE = CONTRACTS / "route-contract-baseline.json"
SCHEMA_VERSION = 1

# 17 个域 —— 直接来自 `docs/plans/2026-11-07-refactor-plan.md` §三 Phase 5 规划表。
# 按「路径」声明；同路径多 method 的端点自动归同一 router。
ROUTERS: dict[str, tuple[str, ...]] = {
    "scan": ("/api/scan", "/api/scan/status"),
    "scoring": ("/api/score/{code}",),
    "stock": ("/api/stock/{code}",),
    "market": (
        "/api/market", "/api/regime-history", "/api/sector-heatmap",
        "/api/sector-flow", "/api/kline/{code}", "/api/trade-history",
        "/api/treemap/sectors", "/api/treemap/factors", "/api/sankey/flow",
    ),
    "portfolio": ("/api/position/{code}", "/api/positions", "/api/position-distribution"),
    "risk": ("/api/risk", "/api/risk-exposure", "/api/exposures", "/api/risk/correlation"),
    "factor": (
        "/api/factor-eval", "/api/factor-scatter", "/api/factor-weights",
        "/api/factor-contribution", "/api/factor-ic-history",
    ),
    "backtest": ("/api/backtest", "/api/backtest/compare", "/api/walkforward"),
    "analysis": ("/api/attribution",),
    "export": (
        "/api/export/positions", "/api/export/trades",
        "/api/export/positions/excel", "/api/export/trades/excel",
        "/api/export/config", "/api/import/config",
        "/api/export/pdf", "/api/export/pdf/status",
    ),
    "settings": ("/api/settings",),
    "signals_audit": (
        "/api/audit", "/api/audit/{scan_id}", "/api/signals",
        "/api/signals/accuracy-trend",
    ),
    "strategy": (
        "/api/strategy/history", "/api/strategy/save", "/api/strategy/recommend",
        "/api/strategy/snapshot", "/api/strategy/snapshots",
        "/api/strategy/snapshot/{snapshot_id}/apply",
        "/api/strategy/snapshot/{snapshot_id}", "/api/strategy/ranking",
        "/api/ab-test/run", "/api/ab-test/presets",
    ),
    "alerts": ("/api/alerts/rules", "/api/alerts/rules/{rule_id}", "/api/alerts/check"),
    "backup": (
        "/api/backup/create", "/api/backup/list", "/api/backup/restore",
        "/api/backup/{backup_file}",
    ),
    "notifications": ("/api/notifications", "/api/notifications/stats"),
    "system": ("/api/health", "/api/dashboard", "/api/reports", "/api/errors",
               "/api/system/host-health"),
}

PATH_RE = re.compile(r"['\"`](/api/[A-Za-z0-9_/{}.$\-]+)['\"`]")
FRONTEND_SUFFIXES = (".js", ".html", ".json")


def _load_contract_tool():
    spec = importlib.util.spec_from_file_location(
        "_contract_snapshot", ROOT / "scripts" / "contract_snapshot.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _owner_of(path: str) -> str | None:
    for router, paths in ROUTERS.items():
        if path in paths:
            return router
    return None


def _declared_paths() -> set[str]:
    return {p for paths in ROUTERS.values() for p in paths}


def capture_ownership() -> dict:
    """从运行时 app.routes 采集 68 个端点，并附上归属域与鉴权读数。"""
    tool = _load_contract_tool()
    os.environ.setdefault("SKIP_HS300_PREFETCH", "1")
    live = tool.collect()
    endpoints = []
    for r in live["routes"]:
        if r.get("category") != "api":
            continue
        endpoints.append({
            "path": r["path"],
            "method": r["method"],
            "handler": r["handler"],
            "router": _owner_of(r["path"]),
            "async": r["async"],
            "auth_required": r["auth_required"],
            "auth_dependency_count": len(r.get("auth_dependencies", [])),
            "dependency_count": len(r.get("dependencies", [])),
        })
    endpoints.sort(key=lambda x: (x["path"], x["method"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "constraints": {
            "source": "runtime app.routes via scripts/contract_snapshot.py::collect()",
            "router_count": len(ROUTERS),
            "declared_path_count": len(_declared_paths()),
            "auth_must_not_decrease": True,
        },
        "counts": {
            "endpoints": len(endpoints),
            "unique_paths": len({e["path"] for e in endpoints}),
            "unassigned": sum(1 for e in endpoints if e["router"] is None),
            "by_router": {
                name: sum(1 for e in endpoints if e["router"] == name)
                for name in ROUTERS
            },
        },
        "endpoints": endpoints,
    }


def capture_frontend_paths() -> dict:
    """实测 `static/**` 里的 `/api/` 字符串，并规范化到路由模板。

    计划 §1.3 写「前端仅消费 18/68 条路径」。实测数量与该读数不同时，
    **以实测为准并留痕**，不为了对上 18 去删条目（P-52 / P-58 同构）。
    """
    declared = _declared_paths()
    raw: dict[str, set[str]] = {}
    for p in sorted((ROOT / "static").rglob("*")):
        if not p.is_file() or p.suffix not in FRONTEND_SUFFIXES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for m in PATH_RE.finditer(text):
            # as_posix() 统一为 "/"：str(Path) 在 Windows 产出反斜杠、
            # 在 Linux/macOS 产出正斜杠，跨平台 CI 比对会因分隔符漂移判红
            # （2026-11-28 L7 补入 CI 后实测 macOS/ubuntu 判红）。
            raw.setdefault(m.group(1), set()).add(p.relative_to(ROOT).as_posix())

    def normalize(s: str) -> str | None:
        if s in declared:
            return s
        # 模板参数 ${xxx} 与 FastAPI 的 {param} 都视作同一占位符
        generic = re.sub(r"\$\{[^}]+\}", "{}", s)
        candidates = [t for t in declared
                      if re.sub(r"\{[^}]+\}", "{}", t) == generic]
        if len(candidates) == 1:
            return candidates[0]
        # 字符串拼接前缀（例如 "/api/score/" + code）=> 匹配只多一个参数段的模板
        if s.endswith("/"):
            prefix = s.rstrip("/")
            prefixed = [t for t in declared
                        if t.startswith(prefix + "/")
                        and t.count("/") == prefix.count("/") + 1
                        and "{" in t]
            if len(prefixed) == 1:
                return prefixed[0]
        return None

    records = []
    for s in sorted(raw):
        records.append({
            "raw": s,
            "normalized": normalize(s),
            "sources": sorted(raw[s]),
        })
    normalized = sorted({r["normalized"] for r in records if r["normalized"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "constraints": {
            "source": "static/**/*.js|html|json literal /api/ strings",
            "plan_stated_count": 18,
            "normalization": "exact path, then ${...}/{...} placeholder, then single-param prefix",
        },
        "counts": {
            "raw_strings": len(records),
            "normalized_paths": len(normalized),
            "unmapped": sum(1 for r in records if r["normalized"] is None),
        },
        "normalized_paths": normalized,
        "records": records,
    }


def capture() -> tuple[dict, dict]:
    return capture_ownership(), capture_frontend_paths()


def _diff(old, new, path="", out=None):
    if out is None:
        out = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            if k not in old or k not in new:
                out.append(f"{path}.{k}: MISSING")
            else:
                _diff(old[k], new[k], f"{path}.{k}", out)
    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            out.append(f"{path}[len]: {len(old)} -> {len(new)}")
        for i, (a, b) in enumerate(zip(old, new)):
            _diff(a, b, f"{path}[{i}]", out)
    elif old != new:
        out.append(f"{path}: {old!r} -> {new!r}")
    return out


def check() -> tuple[list[str], dict, dict]:
    ownership, frontend = capture()
    failures = []

    # ---- 1. 归属表与契约基线的路径集合（成员 + 数量双校验）----
    baseline = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    base_api = [r for r in baseline["routes"] if r.get("category") == "api"]
    base_pairs = {(r["path"], r["method"]) for r in base_api}
    live_pairs = {(e["path"], e["method"]) for e in ownership["endpoints"]}
    if len(live_pairs) != len(base_pairs):
        failures.append(f"endpoint count: baseline={len(base_pairs)} live={len(live_pairs)}")
    if live_pairs != base_pairs:
        failures.append(f"endpoint members: missing={sorted(base_pairs - live_pairs)} "
                        f"added={sorted(live_pairs - base_pairs)}")

    # ---- 2. 归属完整性：68 端点必须都有域，且规划表路径集合相等 ----
    if ownership["counts"]["unassigned"]:
        failures.append(f"unassigned endpoints: {ownership['counts']['unassigned']}")
    declared = _declared_paths()
    live_paths = {e["path"] for e in ownership["endpoints"]}
    if declared != live_paths:
        failures.append(f"declared path set mismatch: missing={sorted(live_paths - declared)} "
                        f"extra={sorted(declared - live_paths)}")

    # ---- 3. 每个端点的鉴权依赖数 / async 属性不得减少 ----
    base_by_key = {(r["path"], r["method"]): r for r in base_api}
    for e in ownership["endpoints"]:
        b = base_by_key.get((e["path"], e["method"]))
        if b is None:
            continue
        if e["auth_dependency_count"] < len(b.get("auth_dependencies", [])):
            failures.append(f"auth dependency DECREASED: {e['method']} {e['path']}")
        if e["auth_required"] != b.get("auth_required"):
            failures.append(f"auth_required changed: {e['method']} {e['path']}")
        if e["async"] != b.get("async"):
            failures.append(f"async changed: {e['method']} {e['path']}")

    # ---- 4. 与已入库基线的逐字段 diff ----
    if OWNERSHIP_FILE.exists():
        failures += _diff(json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8")),
                          ownership, "ownership")
    else:
        failures.append("ownership baseline missing (run --capture first)")
    if FRONTEND_FILE.exists():
        failures += _diff(json.loads(FRONTEND_FILE.read_text(encoding="utf-8")),
                          frontend, "frontend")
    else:
        failures.append("frontend baseline missing (run --capture first)")

    # ---- 5. 契约快照 diff = 0（复用同一口径）----
    tool = _load_contract_tool()
    live_contract = tool.collect()
    for d in tool.compare(baseline, live_contract):
        failures.append(f"contract {d.get('kind')}: {d.get('route')} {d.get('detail')}")

    return failures, ownership, frontend


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capture", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    if args.capture:
        ownership, frontend = capture()
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        OWNERSHIP_FILE.write_text(
            json.dumps(ownership, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        FRONTEND_FILE.write_text(
            json.dumps(frontend, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"[OK] ownership baseline written: {OWNERSHIP_FILE.name} "
              f"({ownership['counts']['endpoints']} endpoints, {len(ROUTERS)} routers)")
        print(f"[OK] frontend paths written: {FRONTEND_FILE.name} "
              f"({frontend['counts']['raw_strings']} raw -> "
              f"{frontend['counts']['normalized_paths']} normalized, "
              f"unmapped={frontend['counts']['unmapped']})")
        if ownership["counts"]["unassigned"]:
            print(f"[FAIL] {ownership['counts']['unassigned']} endpoint(s) have no router")
            return 1
        return 0

    if args.summary:
        if not OWNERSHIP_FILE.exists() or not FRONTEND_FILE.exists():
            print("[FAIL] baselines not found; run --capture first")
            return 1
        o = json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8"))
        f = json.loads(FRONTEND_FILE.read_text(encoding="utf-8"))
        print(f"[INFO] routers={len(ROUTERS)} endpoints={o['counts']['endpoints']} "
              f"unique_paths={o['counts']['unique_paths']}")
        for name, n in o["counts"]["by_router"].items():
            print(f"       {name:16s} {n:3d}")
        print(f"[INFO] frontend raw={f['counts']['raw_strings']} "
              f"normalized={f['counts']['normalized_paths']} "
              f"unmapped={f['counts']['unmapped']} "
              f"(plan stated {f['constraints']['plan_stated_count']})")
        return 0

    failures, ownership, frontend = check()
    if failures:
        print(f"[FAIL] route ownership: {len(failures)} problem(s)")
        for d in failures[:30]:
            print("      " + d)
        return 1
    print(f"[PASS] route ownership: {ownership['counts']['endpoints']} endpoints / "
          f"{len(ROUTERS)} routers / {frontend['counts']['normalized_paths']} frontend paths; "
          f"auth+async+contract unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
