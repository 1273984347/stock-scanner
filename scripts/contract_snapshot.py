#!/usr/bin/env python3
"""契约快照工具 —— 固化 API 对外契约（RF-01 / Phase 0）

产出两件东西：
  1. openapi-baseline.json    —— FastAPI 标准 OpenAPI 文档（可直接给前端/网关比对）
  2. route-contract-baseline.json —— 路由契约：路径 + 方法 + **async/sync 属性**
     + 鉴权依赖 + 参数 + 响应状态码

为什么必须单独记 async/sync：
  FastAPI 把 `def` 端点交给线程池执行，把 `async def` 端点放在事件循环里执行。
  重构时把 `async def` 误改成 `def`（或反之）会**静默改变调度语义**——
  外部完全无感，但阻塞行为与并发上限已经变了。
  OpenAPI 规范里没有这个字段 ⇒ 只能由本工具从 route.endpoint 单独固化。

用法：
  python scripts/contract_snapshot.py --out docs/refactor/contracts   # 生成/更新快照
  python scripts/contract_snapshot.py --check                          # 与基线比对，有漂移则退出码 1
  python scripts/contract_snapshot.py --summary                        # 只打印统计，不落盘

判定口径（全工具统一，不容有第二个口径）：
  /api/* HTTP 端点   = 业务契约主体
  非 /api HTTP 端点   = / , /favicon.svg , /sw.js  （页面与前端资源，仍纳入快照）
  WebSocket           = /ws
  静态挂载(Mount)      = /css , /js  —— 不属于 API 契约，不入快照
  FastAPI 内置        = /openapi.json , /docs , /docs/oauth2-redirect , /redoc —— 不入快照
"""
from __future__ import annotations

import argparse
import ast
import collections
import hashlib
import inspect
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows CI 的 stdout 默认不是 UTF-8（cp1252/cp936），中文输出会直接抛
# UnicodeEncodeError 让门禁崩溃 —— 显式重配为 UTF-8，errors="replace" 兜底。
# 注意：本脚本面向 CI 日志，所有门禁输出保持纯 ASCII，中文说明留在文档与注释里。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

DEFAULT_DIR = ROOT / "docs" / "refactor" / "contracts"
CONTRACT_FILE = "route-contract-baseline.json"
OPENAPI_FILE = "openapi-baseline.json"
SCHEMA_VERSION = 1

# 会被判定为「已鉴权」的依赖名
AUTH_DEP_NAMES = {"verify_api_key"}


# ---------------------------------------------------------------- 采集

def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=15
        )
        return out.stdout.strip() or "-"
    except Exception:
        return "-"


def _sha256(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _type_name(annotation) -> str:
    if annotation is None:
        return ""
    if isinstance(annotation, str):
        return annotation
    return getattr(annotation, "__name__", str(annotation))


def _params_of(dependant) -> list:
    """从 FastAPI dependant 提取参数（query/path/header/cookie）。"""
    out = []
    buckets = (
        ("query", getattr(dependant, "query_params", [])),
        ("path", getattr(dependant, "path_params", [])),
        ("header", getattr(dependant, "header_params", [])),
        ("cookie", getattr(dependant, "cookie_params", [])),
    )
    for where, items in buckets:
        for p in items:
            out.append(
                {
                    "name": p.name,
                    "in": where,
                    "required": bool(getattr(p, "required", False)),
                    "type": _type_name(getattr(p, "type_", None)),
                    "default": None,
                }
            )
    return sorted(out, key=lambda x: (x["in"], x["name"]))


def _deps_of(route) -> tuple:
    """返回 (是否鉴权, 依赖名列表, 安全方案列表)。

    鉴权判定覆盖两条来源：
      - 构造时传入的 dependencies=[Security(verify_api_key)]
      - 端点签名里的 Security(...) 参数
    两者最终都会落到 dependant.security_requirements / dependant.dependencies。
    """
    names, schemes = set(), []
    dependant = getattr(route, "dependant", None)
    if dependant is not None:
        for sec in getattr(dependant, "security_requirements", []) or []:
            schemes.append(sec.security_scheme.scheme_name if hasattr(sec, "security_scheme") else str(sec))
        for dep in getattr(dependant, "dependencies", []) or []:
            call = getattr(dep, "call", None)
            name = getattr(call, "__name__", None)
            if name:
                names.add(name)
    for dep in getattr(route, "dependencies", []) or []:
        call = getattr(dep, "call", None)
        name = getattr(call, "__name__", None)
        if name:
            names.add(name)
    auth_names = sorted(names & AUTH_DEP_NAMES)
    auth = bool(schemes) or bool(auth_names)
    return auth, sorted(names), auth_names


def collect() -> dict:
    from fastapi.routing import APIRoute, APIWebSocketRoute
    from starlette.routing import Mount

    # 跳过 api 模块级的 HS300 网络预取（本工具只需路由契约，不需要行情数据）
    os.environ.setdefault("SKIP_HS300_PREFETCH", "1")

    from api import app  # 导入在函数内：便于 --help / --summary 不触发 app 装配

    openapi = app.openapi()
    openapi_paths = openapi.get("paths", {})

    routes, counts = [], collections.Counter()
    for r in app.routes:
        if isinstance(r, Mount):
            counts["static_mount"] += 1
            continue
        if isinstance(r, APIWebSocketRoute):
            counts["websocket"] += 1
            ep = r.endpoint
            routes.append(
                {
                    "path": r.path,
                    "method": "WEBSOCKET",
                    "handler": getattr(ep, "__name__", "?"),
                    "async": bool(inspect.iscoroutinefunction(ep)),
                    "auth_required": False,
                    "dependencies": [],
                    "security_schemes": [],
                    "params": [],
                    "responses": {},
                    "status_code": None,
                    "category": "websocket",
                }
            )
            continue
        if not isinstance(r, APIRoute):
            counts["framework_builtin"] += 1
            continue

        ep = r.endpoint
        auth, dep_names, auth_deps = _deps_of(r)
        is_api = r.path.startswith("/api/")
        counts["http_api" if is_api else "http_non_api"] += 1

        # 状态码在下方循环内按 method 逐个提取（openapi 的 key 是小写方法名）

        for method in sorted(r.methods - {"HEAD"}):
            op_m = openapi_paths.get(r.path, {}).get(method.lower(), {})
            _codes = [c for c in op_m.get("responses", {}).keys() if c.isdigit()]
            status = int(_codes[0]) if _codes else None
            routes.append(
                {
                    "path": r.path,
                    "method": method,
                    "handler": getattr(ep, "__name__", "?"),
                    "async": bool(inspect.iscoroutinefunction(ep)),
                    "auth_required": auth,
                    "dependencies": dep_names,
                    "auth_dependencies": auth_deps,
                    "params": _params_of(r.dependant),
                    "responses": op_m.get("responses", {}),
                    "status_code": status,
                    "category": "api" if is_api else "page_or_asset",
                }
            )

    routes.sort(key=lambda x: (x["path"], x["method"]))
    api_routes = [r for r in routes if r["category"] == "api"]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": getattr(app, "version", "?"),
        "auth_mechanism": "APIKeyHeader via Depends 链（本项目不使用 FastAPI SecurityRequirement，\n                            故 OpenAPI 中不存在 apiKey security scheme；鉴权只能靠依赖名识别）",
        "source": {
            "commit": _git("rev-parse", "--short", "HEAD"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
            "api_py_sha256": _sha256(ROOT / "api.py"),
        },
        "counts": {
            "total_routes": len(app.routes),
            "http_api": counts["http_api"],
            "http_non_api": counts["http_non_api"],
            "websocket": counts["websocket"],
            "static_mount": counts["static_mount"],
            "framework_builtin": counts["framework_builtin"],
            "methods": dict(
                collections.Counter(r["method"] for r in api_routes)
            ),
            "async_true": sum(1 for r in api_routes if r["async"]),
            "async_false": sum(1 for r in api_routes if not r["async"]),
            "auth_required": sum(1 for r in api_routes if r["auth_required"]),
        },
        "routes": routes,
    }


# ---------------------------------------------------------------- AST 交叉校验

def ast_pairs() -> set:
    """从 api.py 源码 AST 提取 (path, method) 集合，与运行时互证。

    目的：防止「用 app.add_api_route 注册的端点」被 AST 漏掉，
    或「源码删了装饰器但路由还在」这类漂移悄悄发生。
    """
    tree = ast.parse((ROOT / "api.py").read_text(encoding="utf-8"))
    pairs = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            c = d.func if isinstance(d, ast.Call) else d
            if isinstance(c, ast.Attribute) and c.attr in (
                "get", "post", "put", "delete", "patch", "api_route", "websocket",
            ):
                if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant):
                    method = "WEBSOCKET" if c.attr == "websocket" else c.attr.upper()
                    pairs.add((d.args[0].value, method))
    return pairs


def runtime_pairs(snapshot: dict) -> set:
    return {(r["path"], r["method"]) for r in snapshot["routes"]}


# ---------------------------------------------------------------- 比对

WATCHED_FIELDS = (
    "async",
    "auth_required",
    "dependencies",
    "security_schemes",
    "params",
    "responses",
    "status_code",
    "handler",
)


def compare(old: dict, new: dict) -> list:
    old_map = {(r["path"], r["method"]): r for r in old.get("routes", [])}
    new_map = {(r["path"], r["method"]): r for r in new.get("routes", [])}
    diffs = []

    for k in sorted(new_map.keys() - old_map.keys()):
        diffs.append({"kind": "ADDED", "route": f"{k[1]} {k[0]}", "detail": "新增端点"})
    for k in sorted(old_map.keys() - new_map.keys()):
        diffs.append({"kind": "REMOVED", "route": f"{k[1]} {k[0]}", "detail": "端点消失"})
    for k in sorted(old_map.keys() & new_map.keys()):
        changed = {
            f: {"old": old_map[k].get(f), "new": new_map[k].get(f)}
            for f in WATCHED_FIELDS
            if old_map[k].get(f) != new_map[k].get(f)
        }
        if changed:
            diffs.append({"kind": "CHANGED", "route": f"{k[1]} {k[0]}", "detail": changed})
    return diffs


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="API contract snapshot / drift detection")
    p.add_argument("--out", default=str(DEFAULT_DIR), help="snapshot output directory")
    p.add_argument("--check", action="store_true", help="compare against baseline; exit 1 on drift")
    p.add_argument("--summary", action="store_true", help="print stats only")
    p.add_argument("--baseline", default=None, help="baseline file for --check (default: <out>/route-contract-baseline.json)")
    p.add_argument("--verify-ast", action="store_true", help="also cross-check AST vs runtime endpoint sets")
    args = p.parse_args(argv)

    snap = collect()

    if args.summary:
        print(json.dumps(snap["counts"], ensure_ascii=True, indent=2))
        return 0

    out_dir = pathlib.Path(args.out)
    contract_path = out_dir / CONTRACT_FILE
    openapi_path = out_dir / OPENAPI_FILE

    if args.check:
        base_path = pathlib.Path(args.baseline) if args.baseline else contract_path
        if not base_path.exists():
            print(f"[FAIL] baseline not found: {base_path} (run --out first)", file=sys.stderr)
            return 1
        old = json.loads(base_path.read_text(encoding="utf-8"))
        diffs = compare(old, snap)

        if args.verify_ast or True:
            a, b = ast_pairs(), runtime_pairs(snap)
            if a != b:
                print("[FAIL] AST vs runtime endpoint set mismatch:", file=sys.stderr)
                for x in sorted(a - b):
                    print(f"   only in AST   : {x}", file=sys.stderr)
                for x in sorted(b - a):
                    print(f"   only in runtime: {x}", file=sys.stderr)
                diffs.append({"kind": "AST_MISMATCH", "route": "-", "detail": "见上"})

        if not diffs:
            print(f"[PASS] contract unchanged ({len(snap['routes'])} routes, baseline {base_path})")
            return 0

        print(f"[FAIL] {len(diffs)} contract drift(s) detected:", file=sys.stderr)
        for d in diffs:
            print(f"  - {d['kind']:<14} {d['route']}", file=sys.stderr)
            if d["kind"] == "CHANGED":
                for f, v in d["detail"].items():
                    print(f"        {f}: {v['old']!r} -> {v['new']!r}", file=sys.stderr)
            else:
                print(f"        {d['detail']}", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    # newline="\n"：显式 LF，避免 Windows 上生成 CRLF 造成跨平台 git diff 噪声
    contract_path.write_text(
        json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    from api import app  # 已在 sys.modules 缓存中（collect() 内已导入），无额外副作用

    openapi_path.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    c = snap["counts"]
    print(f"[OK] contract snapshot written to {contract_path}")
    print(f"[OK] OpenAPI written to {openapi_path}")
    print(
        f"     /api/* endpoints: {c['http_api']} | async {c['async_true']} / sync {c['async_false']} "
        f"| auth-required {c['auth_required']} | non-/api {c['http_non_api']} | ws {c['websocket']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
