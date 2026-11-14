#!/usr/bin/env python
"""Blocking-IO baseline -- RF-01 Phase 6 measure #8.

Phase 6 promise: "eliminate blocking IO running on the event loop, without
changing any response content". Before offloading anything, the current state
must be machine-readable, otherwise there is no way to tell "moved to a
thread" from "silently added a new blocking call".

Design (follows the project's measurement discipline, pitfalls P-01/P-08/P-10/P-61):

  * Endpoint set is cross-checked against runtime `route-ownership.json`
    (68 endpoints, members + count), NOT re-derived from a regex.
  * Every call inside an `async def` endpoint body is recorded, together with
    calls inside project-local sync helpers it invokes (depth <= 2). Moving a
    blocking call into a helper therefore cannot bypass the gate.
  * A call wrapped by the offload entry point is classified `offloaded` and
    its branch is not expanded -- that is the only legitimate way for a
    blocking symbol to leave the event loop.
  * `blocking` counts may only go DOWN vs the baseline; `offloaded` may only
    go UP; all `other` calls are frozen field-by-field. A new symbol that is
    not in the explicit blocking table still trips the gate via `other`
    drift, so an incomplete table can never silently let a call through.
  * `--selftest` proves the gate actually turns red (P-10) and stays green
    for a legitimate fix (P-54).

Usage:
  python scripts/blocking_io_check.py --capture
  python scripts/blocking_io_check.py --check
  python scripts/blocking_io_check.py --summary
  python scripts/blocking_io_check.py --selftest

Gate output is pure ASCII (Windows CI stdout defaults to cp1252/cp936).
"""

from __future__ import annotations

import argparse
import ast
import copy
import importlib.util
import json
import os
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CONTRACTS = ROOT / "docs" / "refactor" / "contracts"
BASELINE_FILE = CONTRACTS / "blocking-io-baseline.json"
EVIDENCE_FILE = CONTRACTS / "blocking-io-recapture-evidence.json"
TIER_B_FILE = CONTRACTS / "blocking-io-tier-b.json"
OWNERSHIP_FILE = CONTRACTS / "route-ownership.json"
SCHEMA_VERSION = 1
ROUTE_METHODS = ("get", "post", "put", "delete", "patch", "api_route", "websocket")
MIDDLEWARE_DECORATOR = "middleware"
HELPER_EXPANSION_DEPTH = 2

# ---------------------------------------------------------------- rules
# Tail names that are unambiguous blocking operations in this codebase.
# Every entry is auditable; the table is NOT assumed to be complete (see
# `other` freeze above). Categories mirror plan section 3 Phase 6:
# data fetch / parquet read / pandas compute / file write / network.
BLOCKING_TAILS: tuple[str, ...] = (
    # file / disk IO
    "open", "read_text", "write_text", "read_bytes", "write_bytes",
    "exists", "mkdir", "stat", "glob", "rglob", "unlink", "is_file",
    "is_dir", "namelist", "extractall", "writerow", "seek",
    "read_csv", "read_parquet", "to_csv", "to_parquet", "read_excel",
    "to_excel", "ZipFile", "sleep",
    # project cache IO helpers
    "_cache_get", "_cache_set", "_load_cache", "_load_alert_rules",
    "_save_alert_rules", "_flush_metrics", "_record_metric",
    # project data fetch helpers
    "_batch_get_stock_hist", "_get_orchestrator", "get_realtime_quotes",
    "get_stock_hist", "get_stock_hist_multi_source", "get_index_hist",
    # project heavy compute helpers
    "run_factor_backtest", "BacktestRunner", "WalkForwardBacktest",
    "summarize_rounds", "compute_all_signals", "compute_ic",
    "compute_sector_scores", "compute_atr", "compute_var_cvar",
    "compute_correlation_matrix", "full_risk_assessment",
    "get_market_sentiment", "evaluate_macro_gate", "detect_market_regime",
    "evaluate_signals", "evaluate_weights", "get_portfolio_summary",
    "get_stock_score", "get_recent_trades", "get_recent_signals",
    "get_scan_history", "get_scan_detail", "get_notifications",
    "get_ranking", "get_risk_budget", "get_stock_blocks",
    "check_stop_loss", "calc_market_exposure", "calc_composite_verdict",
    "update_actual_returns", "run_test", "get_config",
    # pandas compute
    "iterrows", "tolist", "nlargest", "rolling", "corr", "to_dict",
    "head", "mean", "apply", "compute_ic_weighted_score",
)

# Ambiguous tails (get/load/dump/write/save/run/...) are classified by their
# FULL dotted name only. This prevents `dict.get` from being counted as a
# network call, and prevents a new `requests.get` from being exempted.
BLOCKING_FULLS: tuple[str, ...] = (
    "json.load", "json.dump", "wb.save", "zf.write", "f.write",
    "wf.run", "csv.writer", "t.start", "pdf_generator.generate",
    "snapshot_manager.save", "snapshot_manager.restore",
    "snapshot_manager.list", "Thread.start", "Process.start",
    "unified.to_dict", "factor_df.apply", "factor_df.corr",
    "arr.mean", "arr.sum", "quotes.head", "sectors_data.head",
    "hist.index.tolist", "corr.columns.tolist", "result.head",
)

OFFLOADED_TAILS: tuple[str, ...] = ("run_blocking", "to_thread", "run_in_executor")
OFFLOADED_FULLS: tuple[str, ...] = (
    "anyio.to_thread.run_sync", "asyncio.to_thread", "loop.run_in_executor",
)


def _full_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))
    return "<expr>"


def classify(full: str, tail: str) -> str:
    """One classification entry point -- no second口径 anywhere."""
    if tail in OFFLOADED_TAILS or full in OFFLOADED_FULLS:
        return "offloaded"
    if full in BLOCKING_FULLS or tail in BLOCKING_TAILS:
        return "blocking"
    return "other"


def _source_files() -> list[pathlib.Path]:
    files = [ROOT / "api.py"]
    routers = ROOT / "routers"
    if routers.is_dir():
        files.extend(sorted(routers.glob("*.py")))
    return [p for p in files if p.exists()]


def _trees() -> dict[pathlib.Path, ast.Module]:
    return {p: ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
            for p in _source_files()}


def _endpoint_decorators(node: ast.AST) -> list[tuple[str, str]]:
    """发现所有路由装饰器（批次 B1：不再过滤 /api/ 前缀，且纳入 websocket）。

    返回 (path, method)。调用方按 path 是否以 /api/ 开头分流到 `endpoints`
    （与 L7 对齐的原口径）或 `extended_endpoints`（本次新增的独立维度）。
    """
    out = []
    for d in getattr(node, "decorator_list", []):
        call = d if isinstance(d, ast.Call) else None
        func = call.func if call is not None else d
        if not isinstance(func, ast.Attribute) or func.attr not in ROUTE_METHODS:
            continue
        if call is None or not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        path = str(call.args[0].value)
        method = "WEBSOCKET" if func.attr == "websocket" else func.attr.upper()
        out.append((path, method))
    return out


def _middleware_decorators(node: ast.AST) -> list[str]:
    """`@app.middleware("http")` 装饰的 async 函数也是事件循环路径（上午报告 P2-5）。"""
    out = []
    for d in getattr(node, "decorator_list", []):
        call = d if isinstance(d, ast.Call) else None
        func = call.func if call is not None else d
        if isinstance(func, ast.Attribute) and func.attr == MIDDLEWARE_DECORATOR:
            out.append(getattr(node, "name", "<anonymous>"))
    return out


def _helper_index(trees: dict[pathlib.Path, ast.Module]) -> dict[str, ast.AST]:
    """Top-level helpers defined in api.py / routers/*.py.

    批次 B1：同时索引 **async** helper。/ws 调的 `_get_ws_data` 是 async def，
    旧实现只收 ast.FunctionDef ⇒ 整条阻塞链（_cache_get / PositionManager /
    get_realtime_quotes / iterrows）对门禁完全不可见。
    带路由/中间件装饰器的函数是端点本身，不能当 helper，否则会互相串调用。
    Library code behind an offload boundary remains out of scope on purpose.
    """
    idx: dict[str, ast.AST] = {}
    for tree in trees.values():
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _endpoint_decorators(node) or _middleware_decorators(node):
                continue
            idx.setdefault(node.name, node)
    return idx


def _scan_endpoint(node: ast.AsyncFunctionDef, rel_file: str,
                   helpers: dict[str, ast.FunctionDef]) -> list[dict]:
    calls: list[dict] = []
    visited: set[str] = set()

    def visit(fn: ast.AST, depth: int, under_offload: bool) -> None:
        for stmt in getattr(fn, "body", []):
            for call in ast.walk(stmt):
                if not isinstance(call, ast.Call):
                    continue
                full = _full_name(call.func)
                tail = full.rsplit(".", 1)[-1]
                category = classify(full, tail)
                calls.append({
                    "callee": full,
                    "tail": tail,
                    "category": "offloaded" if under_offload else category,
                    "file": rel_file,
                    "lineno": call.lineno,
                    "depth": depth,
                })
                if category == "offloaded" or under_offload:
                    continue
                if depth < HELPER_EXPANSION_DEPTH and tail in helpers and tail not in visited:
                    visited.add(tail)
                    visit(helpers[tail], depth + 1, False)

    visit(node, 0, False)
    calls.sort(key=lambda c: (c["lineno"], c["callee"], c["depth"]))
    return calls


def _make_record(node: ast.AST, rel_file: str, helpers: dict[str, ast.AST],
                 key: str, route_path: str, method: str) -> dict:
    calls = _scan_endpoint(node, rel_file, helpers)
    return {
        "key": key,
        "path": route_path,
        "method": method,
        "handler": node.name,
        "file": rel_file,
        "lineno": node.lineno,
        "calls": calls,
        "blocking": {c["callee"]: sum(1 for x in calls
                                     if x["category"] == "blocking"
                                     and x["callee"] == c["callee"])
                     for c in calls if c["category"] == "blocking"},
        "other": {c["callee"]: sum(1 for x in calls
                                  if x["category"] == "other"
                                  and x["callee"] == c["callee"])
                  for c in calls if c["category"] == "other"},
        "offloaded": {c["callee"]: sum(1 for x in calls
                                      if x["category"] == "offloaded"
                                      and x["callee"] == c["callee"])
                      for c in calls if c["category"] == "offloaded"},
    }


def _summarize(eps: list[dict]) -> dict:
    """集合内部的计数口径（两个集合各算各的，不互相污染）。"""
    return {
        "endpoints": len(eps),
        "total_calls": sum(len(e["calls"]) for e in eps),
        "blocking_calls": sum(sum(e["blocking"].values()) for e in eps),
        "blocking_endpoints": sum(1 for e in eps if e["blocking"]),
        "offloaded_calls": sum(sum(e["offloaded"].values()) for e in eps),
        "offloaded_endpoints": sum(1 for e in eps if e["offloaded"]),
        "other_calls": sum(sum(e["other"].values()) for e in eps),
    }


def capture() -> dict:
    """批次 B1：端点发现分成两个互不干扰的集合。

    * `endpoints`：HTTP `/api/` 端点，成员+数量继续与 L7 对齐（68 个），
      所有既有判据（L7/L9/L10、`other` 冻结、blocking 单调）口径不变；
    * `extended_endpoints`：`/ws`、非 `/api/` 路由、`@app.middleware` 中间件。
      这是上午报告 §5 指出的门禁盲区，独立冻结，不参与 L7 对比，
      因此扩展视野**不会**改动另外三把尺子的端点集合。
    """
    trees = _trees()
    helpers = _helper_index(trees)
    endpoints: list[dict] = []
    extended: list[dict] = []
    for path, tree in trees.items():
        rel = str(path.relative_to(ROOT)).replace("\\", "/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            routes = _endpoint_decorators(node)
            mids = _middleware_decorators(node)
            if not routes and not mids:
                continue
            for route_path, method in routes:
                key = f"{method} {route_path}"
                rec = _make_record(node, rel, helpers, key, route_path, method)
                (endpoints if route_path.startswith("/api/") else extended).append(rec)
            for name in mids:
                key = f"MIDDLEWARE {name}"
                rec = _make_record(node, rel, helpers, key,
                                   f"middleware:{name}", "MIDDLEWARE")
                extended.append(rec)
    endpoints.sort(key=lambda e: (e["path"], e["method"]))
    extended.sort(key=lambda e: (e["path"], e["method"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "constraints": {
            "source": "AST over api.py + routers/*.py, endpoint set cross-checked "
                      "against runtime route-ownership.json",
            "plan_stated_target": "blocking call count <= 1",
            "plan_stated_counting_unit": "calls (plan section 3 Phase 6)",
            "helper_expansion_depth": HELPER_EXPANSION_DEPTH,
            "other_frozen": True,
            "blocking_monotonic": "may only decrease",
            "offloaded_monotonic": "may only increase",
            "scan_scope": "HTTP /api/ endpoints (L7-aligned) + non-API routes + "
                          "websocket + http middlewares; sync AND async top-level "
                          "helpers expanded (depth<=2)",
        },
        "rules": {
            "blocking_tails": sorted(BLOCKING_TAILS),
            "blocking_fulls": sorted(BLOCKING_FULLS),
            "offloaded_tails": sorted(OFFLOADED_TAILS),
            "offloaded_fulls": sorted(OFFLOADED_FULLS),
        },
        "counts": _summarize(endpoints),
        "endpoints": endpoints,
        "extended_endpoints": extended,
        "extended_counts": _summarize(extended),
    }


def _ownership() -> dict:
    if not OWNERSHIP_FILE.exists():
        raise FileNotFoundError(f"missing {OWNERSHIP_FILE}; run route_ownership_check.py --capture")
    return json.loads(OWNERSHIP_FILE.read_text(encoding="utf-8"))


def _recount(data: dict) -> None:
    """Recompute every summary field from the endpoint records.

    Used by selftest so each injected regression is judged on its OWN gate,
    not on an incidental counts mismatch (pitfall P-54: green with no
    discriminating power / red for the wrong reason).
    """
    eps = data["endpoints"]
    data["counts"] = _summarize(eps)
    if "extended_endpoints" in data:
        data["extended_counts"] = _summarize(data["extended_endpoints"])


def _normalize_endpoint(record: dict) -> dict:
    """机器比较口径：去掉 `lineno` 后再逐字段比。

    `lineno` 是实现细节（P-05「dtype 是实现细节不是行为契约」同构）：
    在 common.py 里删掉一个函数体，其下方所有 helper 的行号都会前移，
    于是十几个**完全没被改动的端点**的 calls 记录集体「变化」。
    把它纳入比较会让 G4「其余链路逐字节一致」退化为无法执行的判据，
    进而诱使执行者把无关端点加进白名单 —— 那才是真正的补丁加补丁。
    `lineno` 仍保留在基线里供人排查，只是不参与机器比较。

    注意：端点**顶层也有一个 `lineno`**（handler 定义所在行），不止 `calls`
    内部有。在 router 顶部新增一行 import（如 Phase 6 第 4 批给 4 个 router
    加 `from lib.infra.async_offload import run_blocking`）会把其下方所有
    handler 行号整体下移，若只剥 calls 内的 lineno、漏掉端点顶层 lineno，
    会令十几个未改动的端点整条记录被判 drift（即本批初遇的 21 条误报）。
    端点顶层 lineno 同样是位置实现细节，一并剥掉才是根因修，而非把无关
    端点塞进白名单。
    """
    out = copy.deepcopy(record)
    out.pop("lineno", None)
    for call in out.get("calls", []):
        call.pop("lineno", None)
    return out


def recapture(allowed: set[tuple[str, str]], mode: str = "offload") -> tuple[list[str], dict, dict]:
    """Rebuild the baseline after a legitimate offload fix (RULES §4.5 G4).

    A recapture is NOT a free pass. It must prove, field by field, that:
      * only the explicitly allowed endpoints changed;
      * every OTHER endpoint record is byte-identical to the old baseline;
      * allowed endpoints actually improved (blocking down, offloaded up,
        and no per-callee blocking increase);
      * the rule table did not shrink.
    A machine-readable evidence file is written next to the baseline.
    """
    if not allowed:
        return ["--recapture requires --allow-endpoints (unbounded rebuild refused)"], {}, {}
    if mode not in ("offload", "refactor", "scope"):
        return [f"unknown recapture mode: {mode!r}"], {}, {}
    if not BASELINE_FILE.exists():
        return ["baseline missing; run --capture first"], {}, {}
    old = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    new = capture()
    failures: list[str] = []

    # 批次 B2：G4 字段级取证必须同时覆盖 extended_endpoints，否则 /ws、
    # 中间件的变化会在重建时被静默吞掉（B1 扩了视野，取证范围要同步跟上）。
    def _all_records(data: dict) -> dict[str, dict]:
        return {e["key"]: e for e in
                list(data["endpoints"]) + list(data.get("extended_endpoints", []))}

    old_eps = _all_records(old)
    new_eps = _all_records(new)
    allowed_keys = {f"{m} {p}" for m, p in allowed}
    if set(old_eps) != set(new_eps):
        failures.append(f"endpoint set changed: missing={sorted(set(old_eps) - set(new_eps))} "
                        f"added={sorted(set(new_eps) - set(old_eps))}")

    for field in ("blocking_tails", "blocking_fulls", "offloaded_tails", "offloaded_fulls"):
        if not set(old["rules"][field]) <= set(new["rules"][field]):
            failures.append(f"rules.{field} shrank")

    evidence_rows = []
    for key in sorted(old_eps.keys() | new_eps.keys()):
        b, n = old_eps.get(key), new_eps.get(key)
        if b is None or n is None:
            continue
        b_block = sum(b["blocking"].values())
        n_block = sum(n["blocking"].values())
        b_off = sum(b["offloaded"].values())
        n_off = sum(n["offloaded"].values())
        if key not in allowed_keys:
            if _normalize_endpoint(b) != _normalize_endpoint(n):
                failures.append(f"UNEXPECTED drift in untouched endpoint {key}")
            continue
        # allowed endpoint: prove it is an actual fix, not a reshuffle
        for callee, count in n["blocking"].items():
            if count > b["blocking"].get(callee, 0):
                failures.append(f"blocking INCREASED in {key}: {callee}")
        # mode 按变更语义分层，不是放宽门禁：
        #   offload  = 阻塞段进线程池，必须看到 blocking 降 / offloaded 升；
        #   refactor = 状态/调用收口，阻塞数不变属正常，但仍禁止 blocking 增加，
        #             且非 allowed 端点必须逐字段一致。
        if mode == "offload":
            if n_block >= b_block:
                failures.append(f"{key}: blocking did not decrease ({b_block} -> {n_block})")
            if n_off <= b_off:
                failures.append(f"{key}: offloaded did not increase ({b_off} -> {n_off})")
        evidence_rows.append({
            "endpoint": key,
            "blocking_before": b_block, "blocking_after": n_block,
            "offloaded_before": b_off, "offloaded_after": n_off,
            "other_before": sum(b["other"].values()),
            "other_after": sum(n["other"].values()),
        })

    # 批次 B1：判据范围扩展（scope）的额外取证。
    # 既有 68 个 HTTP 端点的记录必须逐字段一致；新增的扩展端点必须全部
    # 显式列在 --allow-endpoints 里，禁止「悄悄多扫进来几个」。
    scope_added: list[dict] = []
    if mode == "scope":
        old_ext = {e["key"] for e in old.get("extended_endpoints", [])}
        new_ext = {e["key"] for e in new.get("extended_endpoints", [])}
        added = new_ext - old_ext
        declared = {f"{m} {p}" for m, p in allowed}
        if not added <= declared:
            failures.append(f"scope: undeclared extended endpoints {sorted(added - declared)}")
        if old_ext - new_ext:
            failures.append(f"scope: extended endpoints removed {sorted(old_ext - new_ext)}")
        scope_added = [
            {"endpoint": e["key"],
             "blocking": sum(e["blocking"].values()),
             "other": sum(e["other"].values())}
            for e in new.get("extended_endpoints", []) if e["key"] in added
        ]
        if not scope_added:
            failures.append("scope: no extended endpoint added (nothing to expand)")

    reasons = {
        "offload": "RF-01 Phase 6: Tier A 端点阻塞调用 offload 进线程池",
        "refactor": "批次重构：状态/调用收口、白名单裁剪或线程池迁移；"
                    "blocking 不得增加，非 allowed 端点逐字段一致",
        "scope": "批次 B1: L8 端点发现扩展到 websocket / 非 /api/ 路由 / 中间件，"
                 "HTTP 端点记录逐字段不变",
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "reason": reasons[mode],
        "allowed_endpoints": [f"{m} {p}" for m, p in sorted(allowed)],
        "comparison": "endpoint records compared field-by-field; lineno excluded "
                      "(implementation detail, see _normalize_endpoint)",
        "untouched_endpoints": len(set(old_eps) - allowed_keys),
        "untouched_identical": not any("UNEXPECTED drift" in f for f in failures),
        "totals_before": old["counts"],
        "totals_after": new["counts"],
        "per_endpoint": evidence_rows,
        "scope_extended_added": scope_added,
    }

    new["constraints"]["recapture_history"] = list(
        old["constraints"].get("recapture_history", [])) + [{
            "mode": mode,
            "reason": evidence["reason"],
            "allowed_endpoints": evidence["allowed_endpoints"],
            "blocking_before": old["counts"]["blocking_calls"],
            "blocking_after": new["counts"]["blocking_calls"],
        }]
    return failures, new, evidence


# ---------------------------------------------------------------- Tier B 留痕
# ADR-0006 D2 要求「Tier B 轻量文件 IO 逐条登记理由，不得新增、不得长成 Tier A」。
# 关键诚实性约束：重计算/取数符号所在的端点**必须**进 tier_a_candidates，
# 不允许为了好看把它登记成「轻量 IO」。group 由符号表机械判定，机器可校验。

LIGHT_IO_TAILS = frozenset({
    "_cache_get", "_cache_set", "_load_cache", "_load_alert_rules",
    "_save_alert_rules", "_flush_metrics", "_record_metric",
    "exists", "stat", "glob", "rglob", "mkdir", "unlink", "is_file", "is_dir",
    "open", "read_text", "write_text", "read_bytes", "write_bytes",
    "namelist", "extractall", "writerow", "seek", "write", "save", "read",
    "read_csv", "read_parquet", "to_csv", "to_parquet", "read_excel", "to_excel",
    "load", "dump", "ZipFile", "BytesIO", "StringIO", "sleep",
})

COMPUTE_TAILS = frozenset({
    "_get_orchestrator", "_batch_get_stock_hist",
    "get_realtime_quotes", "get_stock_hist", "get_stock_hist_multi_source",
    "get_index_hist", "iterate", "iterrows", "to_dict", "tolist", "mean",
    "head", "apply", "corr", "rolling", "nlargest", "compute_all_signals",
    "compute_ic", "compute_sector_scores", "compute_atr", "compute_var_cvar",
    "compute_correlation_matrix", "full_risk_assessment", "get_market_sentiment",
    "evaluate_macro_gate", "detect_market_regime", "evaluate_signals",
    "evaluate_weights", "get_portfolio_summary", "get_stock_score",
    "get_recent_trades", "get_recent_signals", "get_scan_history",
    "get_scan_detail", "get_notifications", "get_ranking", "get_risk_budget",
    "get_stock_blocks", "check_stop_loss", "calc_market_exposure",
    "calc_composite_verdict", "update_actual_returns", "run_test", "get_config",
    "run_factor_backtest", "BacktestRunner", "WalkForwardBacktest",
    "summarize_rounds", "run", "compute_ic_weighted_score",
})

# 泛名只按完整点号名归类：csv.writer 是内存写、pdf_generator.generate 是重计算，
# 只看尾名会把二者混为一谈（坑表 P-28 同构）。
TIER_B_FULL_OVERRIDES = {
    "csv.writer": "light_io",
    "t.start": "light_io",
    "snapshot_manager.save": "light_io",
    "snapshot_manager.list": "light_io",
    "snapshot_manager.restore": "light_io",
    "json.load": "light_io",
    "json.dump": "light_io",
    "wb.save": "light_io",
    "zf.write": "light_io",
    "f.write": "light_io",
    "pdf_generator.generate": "compute_pending",
    "arr.sum": "compute_pending",
    "arr.mean": "compute_pending",
    "wf.run": "compute_pending",
    "unified.to_dict": "compute_pending",
}

REASON_BY_GROUP = {
    "light_io": "本地轻量 IO：缓存命中/元数据查询/小文件读写，已有 TTL、锁与失效策略；"
                "单次开销低于线程调度成本，登记保留且禁止新增",
    "compute_pending": "重计算 / 外部取数 / 全目录遍历压缩：所在端点属 Tier A 候选、"
                       "尚未 offload，明确列为遗留，不伪装成 Tier B",
    "unclassified": "未归类符号：必须先补分类规则并说明理由，否则不得推进",
}

# 批次 B1 语义分类：只看尾名会把「全目录遍历 + DEFLATE 压缩」判成轻量 IO
# （backup_create 的 rglob / ZipFile / extractall 都在 LIGHT_IO_TAILS 里）。
# 判据改看**同一个端点内符号的组合**，而不是单个名字。
TRAVERSAL_TAILS = frozenset({"rglob", "glob"})
ARCHIVE_TAILS = frozenset({"ZipFile", "extractall", "namelist", "write", "writerow"})


def _tier_b_group(callee: str, peers: frozenset[str] = frozenset()) -> str:
    """按语义分类；peers 是该端点全部阻塞符号（批次 B1）。

    组合规则优先于单名 override：目录遍历与压缩同时出现 ⇒ 重 IO，
    不允许被 `zf.write -> light_io` 这类单点 override 稀释掉。
    """
    tails = {c.rsplit(".", 1)[-1] for c in peers}
    if tails & TRAVERSAL_TAILS and tails & ARCHIVE_TAILS:
        return "compute_pending"
    if callee in TIER_B_FULL_OVERRIDES:
        return TIER_B_FULL_OVERRIDES[callee]
    tail = callee.rsplit(".", 1)[-1]
    if tail in LIGHT_IO_TAILS:
        return "light_io"
    if tail in COMPUTE_TAILS:
        return "compute_pending"
    return "unclassified"


def capture_tier_b(base: dict | None = None) -> dict:
    d = base if base is not None else json.loads(
        BASELINE_FILE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    candidates: dict[str, dict] = {}
    all_eps = list(d["endpoints"]) + list(d.get("extended_endpoints", []))
    for e in all_eps:
        key = e["key"]
        peers = frozenset(e["blocking"])
        for callee, count in sorted(e["blocking"].items()):
            group = _tier_b_group(callee, peers)
            rows.append({"endpoint": key, "callee": callee, "count": count,
                         "group": group, "reason": REASON_BY_GROUP[group]})
            if group == "compute_pending":
                rec = candidates.setdefault(key, {
                    "endpoint": key, "status": "not_offloaded",
                    "callees": [], "blocking_calls": 0,
                })
                rec["callees"].append(callee)
                rec["blocking_calls"] += count
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "blocking-io-baseline.json",
        "policy": {
            "group_is_semantic": True,
            "archive_plus_traversal_is_compute_pending": True,
            "new_blocking_must_be_registered": True,
            "compute_endpoints_cannot_be_marked_light_io": True,
        },
        "counts": {
            "registered": len(rows),
            "blocking_calls": sum(r["count"] for r in rows),
            "light_io_entries": sum(1 for r in rows if r["group"] == "light_io"),
            "compute_pending_entries": sum(1 for r in rows
                                           if r["group"] == "compute_pending"),
            "unclassified_entries": sum(1 for r in rows
                                        if r["group"] == "unclassified"),
            "tier_a_candidate_endpoints": len(candidates),
        },
        "tier_a_candidates": [candidates[k] for k in sorted(candidates)],
        "registered": rows,
    }


def check_tier_b(live: dict, reg: dict | None = None) -> list[str]:
    """reg 可由调用方注入内存登记（selftest 用），默认读磁盘登记文件。"""
    if reg is None:
        if not TIER_B_FILE.exists():
            return ["tier-b register missing (run --tier-b first)"]
        reg = json.loads(TIER_B_FILE.read_text(encoding="utf-8"))
    failures: list[str] = []
    known = {(r["endpoint"], r["callee"]): r for r in reg["registered"]}
    all_eps = list(live["endpoints"]) + list(live.get("extended_endpoints", []))
    for e in all_eps:
        key = e["key"]
        peers = frozenset(e["blocking"])
        for callee, count in e["blocking"].items():
            entry = known.get((key, callee))
            if entry is None:
                failures.append(f"UNREGISTERED blocking symbol: {key} :: {callee}")
                continue
            expected_group = _tier_b_group(callee, peers)
            if entry["group"] != expected_group:
                failures.append(f"tier-b group tampered: {key} :: {callee} "
                                f"{entry['group']} != {expected_group}")
            if count > entry["count"]:
                failures.append(f"registered blocking count exceeded: {key} :: "
                                f"{callee} {entry['count']} -> {count}")
    if reg["counts"]["unclassified_entries"]:
        failures.append(f"{reg['counts']['unclassified_entries']} unclassified "
                        f"blocking symbol(s): add a classification rule first")
    return failures


def check(live: dict | None = None, baseline: dict | None = None,
          tier_b_reg: dict | None = None) -> list[str]:
    live = live if live is not None else capture()
    base = baseline if baseline is not None else json.loads(
        BASELINE_FILE.read_text(encoding="utf-8"))
    failures: list[str] = []

    # 1. endpoint set: members + count, against the runtime-derived L7 baseline
    own = _ownership()
    own_keys = {(e["method"], e["path"]) for e in own["endpoints"]}
    live_keys = {(e["method"], e["path"]) for e in live["endpoints"]}
    if len(live_keys) != len(own_keys):
        failures.append(f"endpoint count: ownership={len(own_keys)} live={len(live_keys)}")
    if live_keys != own_keys:
        failures.append(f"endpoint members: missing={sorted(own_keys - live_keys)} "
                        f"added={sorted(live_keys - own_keys)}")
    non_async = [e["key"] for e in own["endpoints"] if not e["async"]]
    if non_async:
        failures.append(f"async/sync changed (endpoints must stay async): {non_async}")

    # 1b. 批次 B1：扩展端点（websocket / 非 /api/ 路由 / 中间件）独立冻结。
    #     成员+数量双校验 + blocking 只准降 + other 逐字段冻结，
    #     与 HTTP 端点使用同一套判据强度，避免「扩了视野却没守住」。
    base_ext = {e["key"]: e for e in base.get("extended_endpoints", [])}
    live_ext = {e["key"]: e for e in live.get("extended_endpoints", [])}
    if set(base_ext) != set(live_ext):
        failures.append(
            f"extended endpoint set: missing={sorted(set(base_ext) - set(live_ext))} "
            f"added={sorted(set(live_ext) - set(base_ext))}")
    for key in sorted(set(base_ext) & set(live_ext)):
        b, n = base_ext[key], live_ext[key]
        for callee, cnt in n["blocking"].items():
            if cnt > b["blocking"].get(callee, 0):
                failures.append(f"extended blocking INCREASED {key}: {callee} "
                                f"{b['blocking'].get(callee, 0)} -> {cnt}")
        if b["other"] != n["other"]:
            diff = {k: (b["other"].get(k, 0), n["other"].get(k, 0))
                    for k in set(b["other"]) | set(n["other"])
                    if b["other"].get(k, 0) != n["other"].get(k, 0)}
            failures.append(f"extended other drift {key}: {diff}")
    if live.get("extended_counts") != _summarize(live.get("extended_endpoints", [])):
        failures.append("extended_counts inconsistent (hand-edited summary?)")

    # 2. rules table may only grow, never shrink or be rewritten
    for field in ("blocking_tails", "blocking_fulls", "offloaded_tails", "offloaded_fulls"):
        old, new = set(base["rules"][field]), set(live["rules"][field])
        if not old <= new:
            failures.append(f"rules.{field} removed entries: {sorted(old - new)}")

    base_eps = {(e["method"], e["path"]): e for e in base["endpoints"]}
    live_eps = {(e["method"], e["path"]): e for e in live["endpoints"]}

    # 3. `other` is frozen field-by-field: any new/unclassified call trips it
    for key in sorted(set(base_eps) | set(live_eps)):
        b = base_eps.get(key, {}).get("other", {})
        n = live_eps.get(key, {}).get("other", {})
        if b != n:
            diff = {k: (b.get(k, 0), n.get(k, 0)) for k in set(b) | set(n)
                    if b.get(k, 0) != n.get(k, 0)}
            failures.append(f"other drift {key}: {diff}")

    # 4. blocking may only decrease, per callee, per endpoint
    for key, e in live_eps.items():
        b = base_eps.get(key, {}).get("blocking", {})
        for callee, n in e["blocking"].items():
            if n > b.get(callee, 0):
                failures.append(f"blocking INCREASED {key}: {callee} "
                                f"{b.get(callee, 0)} -> {n}")

    # 5. counts must be internally consistent (no hand-edited summary)
    recomputed = {
        "endpoints": len(live["endpoints"]),
        "total_calls": sum(len(e["calls"]) for e in live["endpoints"]),
        "blocking_calls": sum(sum(e["blocking"].values()) for e in live["endpoints"]),
        "blocking_endpoints": sum(1 for e in live["endpoints"] if e["blocking"]),
        "offloaded_calls": sum(sum(e["offloaded"].values()) for e in live["endpoints"]),
        "offloaded_endpoints": sum(1 for e in live["endpoints"] if e["offloaded"]),
        "other_calls": sum(sum(e["other"].values()) for e in live["endpoints"]),
    }
    if recomputed != live["counts"]:
        failures.append(f"counts inconsistent: {recomputed} vs {live['counts']}")

    # 6. Tier B 逐条登记：新出现的阻塞符号必须先写明理由，且不许把重计算端点
    #    手工标成「轻量 IO」（ADR-0006 D2）
    failures += check_tier_b(live, tier_b_reg)
    return failures


def selftest() -> list[str]:
    """Prove the gate turns red on regressions and green on a real fix (P-10/P-54).

    Each red scenario asserts the SPECIFIC gate that fired. A red caused by
    an unrelated counts mismatch would be an empty judgement (P-54), so every
    mutated snapshot is re-counted before it is checked.
    """
    base = capture()
    problems: list[str] = []
    # selftest 必须自洽：Tier B 登记由本轮 capture 在内存中生成，
    # 不依赖磁盘登记文件是否已经 --tier-b 重生成（那是另一把尺子的职责）。
    tier_reg = capture_tier_b(base)

    def expect_red(name: str, mutated: dict, fragment: str) -> None:
        _recount(mutated)
        failures = check(mutated, base, tier_reg)
        if not failures:
            problems.append(f"{name}: gate stayed GREEN (no discriminating power)")
        elif not any(fragment in f for f in failures):
            problems.append(f"{name}: red for the wrong reason: {failures}")

    # A: a known blocking symbol appears -> blocking INCREASED gate
    a = copy.deepcopy(base)
    ep = a["endpoints"][0]
    ep["calls"].append({"callee": "open", "tail": "open", "category": "blocking",
                        "file": "<selftest>", "lineno": -1, "depth": 0})
    ep["blocking"]["open"] = ep["blocking"].get("open", 0) + 1
    expect_red("new blocking call", a, "blocking INCREASED")

    # B: a completely unknown symbol (not in the table) -> other drift gate.
    # This is what makes an incomplete blocking table safe.
    b = copy.deepcopy(base)
    ep = b["endpoints"][0]
    ep["calls"].append({"callee": "requests.get", "tail": "get",
                        "category": "other", "file": "<selftest>",
                        "lineno": -1, "depth": 0})
    ep["other"]["requests.get"] = ep["other"].get("requests.get", 0) + 1
    expect_red("unclassified new call", b, "other drift")

    # C: endpoint disappears -> runtime ownership cross-check gate
    c = copy.deepcopy(base)
    c["endpoints"] = c["endpoints"][:-1]
    expect_red("endpoint removed", c, "endpoint count")

    # D: rule silently deleted to whitelist a blocking symbol -> rules gate
    d = copy.deepcopy(base)
    d["rules"]["blocking_tails"] = [x for x in d["rules"]["blocking_tails"] if x != "open"]
    expect_red("rule deleted", d, "removed entries")

    # E: a legitimate fix (one blocking call offloaded) must be FULLY green
    e = copy.deepcopy(base)
    target = next(x for x in e["endpoints"] if x["blocking"])
    callee = sorted(target["blocking"])[0]
    target["blocking"][callee] -= 1
    if target["blocking"][callee] == 0:
        del target["blocking"][callee]
    target["offloaded"]["run_blocking"] = target["offloaded"].get("run_blocking", 0) + 1
    for call in target["calls"]:
        if call["callee"] == callee and call["category"] == "blocking":
            call["category"] = "offloaded"
            call["callee"] = "run_blocking"
            break
    _recount(e)
    failures = check(e, base, tier_reg)
    if failures:
        problems.append(f"legitimate fix turned RED: {failures}")

    # F: 批次 B1 —— /ws 里新增阻塞调用必须被扩展维度判红（原门禁完全看不见）
    f_mut = copy.deepcopy(base)
    ws = [x for x in f_mut["extended_endpoints"] if x["method"] == "WEBSOCKET"]
    if not ws:
        problems.append("scope: no WEBSOCKET endpoint captured (blind spot again)")
    else:
        target = ws[0]
        target["calls"].append({"callee": "open", "tail": "open",
                                "category": "blocking", "file": "<selftest>",
                                "lineno": -1, "depth": 0})
        target["blocking"]["open"] = target["blocking"].get("open", 0) + 1
        expect_red("ws blocking call", f_mut, "extended blocking INCREASED")

    # G: 扩展端点被删 -> 集合成员+数量判据
    g_mut = copy.deepcopy(base)
    g_mut["extended_endpoints"] = g_mut["extended_endpoints"][:-1]
    expect_red("extended endpoint removed", g_mut, "extended endpoint set")

    # H: 语义分类：全目录遍历 + 压缩不得再被判成轻量 IO（P1-6）
    peers = frozenset({"cache.rglob", "zf.write", "zipfile.ZipFile"})
    if _tier_b_group("cache.rglob", peers) != "compute_pending":
        problems.append("tier-b semantic: rglob + ZipFile still classified light_io")
    if _tier_b_group("zf.write", peers) != "compute_pending":
        problems.append("tier-b semantic: single-name override diluted archive rule")
    if _tier_b_group("cache.rglob") != "light_io":
        problems.append("tier-b semantic: isolated rglob should stay light_io")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capture", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--summary", action="store_true")
    g.add_argument("--selftest", action="store_true")
    g.add_argument("--recapture", action="store_true")
    g.add_argument("--tier-b", action="store_true")
    ap.add_argument("--allow-endpoints", default="",
                    help="comma-separated 'METHOD /path' keys allowed to change on --recapture")
    ap.add_argument("--mode", choices=("offload", "refactor", "scope"), default="offload",
                    help="offload requires blocking down + offloaded up; "
                         "refactor allows unchanged blocking but still forbids increases; "
                         "scope = gate scope expansion, HTTP endpoints must stay byte-identical")
    args = ap.parse_args()

    if args.tier_b:
        data = capture_tier_b()
        bad = [r for r in data["registered"] if r["group"] == "unclassified"]
        if bad:
            print(f"[FAIL] tier-b: {len(bad)} unclassified blocking symbol(s); "
                  f"add a classification rule before registering")
            for r in bad[:20]:
                print(f"       {r['callee']}  {r['endpoint']}")
            return 1
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        TIER_B_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        c = data["counts"]
        print(f"[OK] tier-b register written: {TIER_B_FILE.name} "
              f"({c['registered']} entries / {c['blocking_calls']} blocking calls; "
              f"light_io={c['light_io_entries']}, "
              f"compute_pending={c['compute_pending_entries']}, "
              f"Tier A candidate endpoints={c['tier_a_candidate_endpoints']})")
        return 0

    if args.recapture:
        allowed = set()
        for item in args.allow_endpoints.split(","):
            item = item.strip()
            if not item:
                continue
            if " " not in item:
                print(f"[FAIL] invalid endpoint key: {item!r} (expected 'METHOD /path')")
                return 1
            method, path = item.split(" ", 1)
            allowed.add((method.upper(), path))
        failures, new, evidence = recapture(allowed, args.mode)
        if failures:
            print(f"[FAIL] recapture refused: {len(failures)} problem(s)")
            for f in failures[:30]:
                print("      " + f)
            return 1
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(
            json.dumps(new, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        # 历次重建都要留痕：审计轨迹不能只保留最后一次，否则无法回答
        # 「这个基线是被哪一批、以什么理由改动的」（G4 字段级取证的延伸）。
        history = []
        if EVIDENCE_FILE.exists():
            prev = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))
            history = prev if isinstance(prev, list) else [prev]
        history.append(evidence)
        EVIDENCE_FILE.write_text(
            json.dumps(history, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"[OK] recapture evidence written: {EVIDENCE_FILE.name} "
              f"(record {len(history)}: {len(evidence['per_endpoint'])} changed, "
              f"{evidence['untouched_endpoints']} untouched identical)")
        print(f"[OK] baseline rebuilt: blocking "
              f"{evidence['totals_before']['blocking_calls']} -> "
              f"{evidence['totals_after']['blocking_calls']}, offloaded "
              f"{evidence['totals_before']['offloaded_calls']} -> "
              f"{evidence['totals_after']['offloaded_calls']}")
        return 0

    if args.capture:
        data = capture()
        CONTRACTS.mkdir(parents=True, exist_ok=True)
        BASELINE_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
        print(f"[OK] blocking-io baseline written: {BASELINE_FILE.name} "
              f"({data['counts']['endpoints']} endpoints, "
              f"{data['counts']['total_calls']} calls, "
              f"{data['counts']['blocking_calls']} blocking)")
        return 0

    if args.summary:
        if not BASELINE_FILE.exists():
            print("[FAIL] baseline not found; run --capture first")
            return 1
        d = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        c = d["counts"]
        print(f"[INFO] endpoints={c['endpoints']} total_calls={c['total_calls']} "
              f"blocking_calls={c['blocking_calls']} "
              f"blocking_endpoints={c['blocking_endpoints']} "
              f"offloaded_calls={c['offloaded_calls']} other_calls={c['other_calls']}")
        top = sorted(d["endpoints"], key=lambda e: -sum(e["blocking"].values()))[:10]
        for e in top:
            print(f"       {sum(e['blocking'].values()):4d}  {e['key']}")
        return 0

    if args.selftest:
        problems = selftest()
        if problems:
            print(f"[FAIL] selftest: {len(problems)} problem(s)")
            for p in problems:
                print("      " + p)
            return 1
        print("[PASS] selftest: gate turns red on 6 injected regressions "
              "(incl. ws blocking / extended endpoint removed) + 3 tier-b "
              "semantic assertions, stays green on a legitimate offload fix")
        return 0

    if not BASELINE_FILE.exists():
        print("[FAIL] baseline not found; run --capture first")
        return 1
    live = capture()
    failures = check(live)
    if failures:
        print(f"[FAIL] blocking-io: {len(failures)} problem(s)")
        for f in failures[:30]:
            print("      " + f)
        return 1
    c = live["counts"]
    ext = live.get("extended_counts", {})
    print(f"[PASS] blocking-io: {c['endpoints']} endpoints / "
          f"{c['blocking_calls']} blocking calls (baseline "
          f"{json.loads(BASELINE_FILE.read_text(encoding='utf-8'))['counts']['blocking_calls']}) / "
          f"{c['offloaded_calls']} offloaded; "
          f"extended {ext.get('endpoints', 0)} paths / "
          f"{ext.get('blocking_calls', 0)} blocking frozen "
          f"(ws + non-api routes + middlewares); other frozen, async+ownership unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
