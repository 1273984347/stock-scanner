#!/usr/bin/env python
"""§1.3 依赖方向机器判据 + 环检测（评审 P1-6 / 第 11 把尺子，2026-11-20）

RULES §1.3 的方向约束此前只靠人眼——评审实测发现 8 个违规方向、2 个未登记野环
（data↔risk、scoring↔strategy）。本尺子三层：

1. **方向规则**：lib 层级（低→高）``infra(0) < data(1) < {scoring, risk, portfolio,
   ml, monitoring}(2) < strategy(3)``。**低层 import 高层 = 违规**（同级横向合法，
   由第 3 层环检测管辖），除非显式登记 EXCEPTIONS（必须附 justification，新增未登记
   即判红——对齐 MIGRATION_DIVERGENCES 模式：债务可以存在，但必须可见且被钉住）。
2. **边界**：lib 不得 import 根级业务模块（scanner/paper_trading/trading_journal/
   dashboard）与顶层包（reporter/backtest）——例外登记；``config`` 为配置单一真源
   任意层可读，``factors`` 列 ROOT_SHARED（迁入 lib/scoring 是 P2-1 正解，暂列共享债）。
3. **环检测**：lib 包级 import 图必须无环；已登记互反对（scoring↔strategy）除外。

用法：``python scripts/dep_direction_check.py --check``（违规/环输出到 stdout，退出码 0/1）。
"""
import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 层级（低→高）；同级 import 不查
LEVEL = {
    "infra": 0,
    "data": 1,
    "scoring": 2, "risk": 2, "portfolio": 2, "ml": 2, "monitoring": 2,
    "strategy": 3,
}

# 已登记例外（src_pkg, dst_pkg) → justification。新增未登记 = 判红。
EXCEPTIONS = {
    ("scoring", "strategy"):
        "scoring_adapters 反向调策略层 quick_score/full_score（P7-2 灰度适配器架构，"
        "待评分域重构；与 strategy→scoring 构成已登记互反对）",
    ("strategy", "reporter"):
        "pipeline/paper_trading_runner 推送出口（IC 告警/模拟盘日报，"
        "依赖倒置待 P1-6 第二阶段）",
    ("monitoring", "reporter"):
        "alert_monitor 推送出口（告警降级落 logs/alert_fallback/ 后仍需触达飞书）",
    ("monitoring", "backtest"):
        "backtest_unified.py 是 B5 规约定义的薄壳 re-export（兼容旧路径，不新增引用）",
    ("scoring", "backtest"):
        "factor_optimizer 函数级惰性调用回测取数（B1 合规：走 backtest.unified）",
    ("strategy", "backtest"):
        "ab_testing / multi_market_validator 函数级惰性调用（B1 合规：走 unified）",
}

# 已登记互反对（环检测豁免；必须在 EXCEPTIONS 中有对应登记）
REGISTERED_MUTUAL_PAIRS = [("scoring", "strategy")]

# lib 不得 import 的根级业务模块 / 顶层包
ROOT_BUSINESS = {"scanner", "paper_trading", "trading_journal", "dashboard"}
TOP_PACKAGES = {"reporter", "backtest"}
# 共享根资源（任意层可读；factors 迁入 lib/scoring 是 P2-1 正解，暂列共享债）
ROOT_SHARED = {"config", "factors"}


def _pkg_of(py_file: Path) -> str:
    """lib/<pkg>/x.py → pkg；lib/x.py → 'lib-root'（D1 违规，另行处理）。"""
    rel = py_file.relative_to(ROOT / "lib")
    return rel.parts[0] if len(rel.parts) > 1 else "lib-root"


def _imports_of(tree: ast.AST):
    """AST 抽取顶层依赖名：``from lib.X import`` / ``import lib.X`` → ``X``；
    根级/顶层模块取首段（config / factors / scanner / reporter / backtest ...）。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "lib" and len(parts) > 1:
                found.add(parts[1])
            else:
                found.add(parts[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "lib" and len(parts) > 1:
                    found.add(parts[1])
                else:
                    found.add(parts[0])
    return found


def collect() -> tuple[list, list, dict]:
    """扫描 lib/**.py → (violations, cycles, edges)。"""
    lib_dir = ROOT / "lib"
    violations: list[str] = []
    edges: dict[str, set] = {}
    for py in sorted(lib_dir.rglob("*.py")):
        src_pkg = _pkg_of(py)
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError as e:
            violations.append(f"syntax error: {py}: {e}")
            continue
        for top in _imports_of(tree):
            if top == "lib":
                continue  # from lib import X 形态被 I2 禁止且不构成包级边
            rel = f"{py.relative_to(ROOT)}:{py.name}"
            if src_pkg != top and top in LEVEL:
                dst = top
                if src_pkg in LEVEL and LEVEL[src_pkg] >= LEVEL[dst]:
                    continue  # 高→低允许；同级横向合法（环检测管辖）
                if (src_pkg, dst) in EXCEPTIONS:
                    continue  # 已登记
                violations.append(
                    f"direction: {rel} -> lib.{dst} "
                    f"(低层 {src_pkg} import 高层 {dst}；未登记例外)")
                edges.setdefault(src_pkg, set()).add(dst)
            elif src_pkg in LEVEL and top in ROOT_BUSINESS:
                violations.append(
                    f"boundary: {rel} -> 根级业务模块 {top}（lib 层不得 import）")
            elif src_pkg in LEVEL and top in TOP_PACKAGES:
                if (src_pkg, top) in EXCEPTIONS:
                    continue
                violations.append(
                    f"boundary: {rel} -> 顶层包 {top}（未登记例外）")

    # 环检测（包级图；同包边与已登记互反对豁免）
    cycles: list[str] = []
    exempt = {frozenset(p) for p in REGISTERED_MUTUAL_PAIRS}

    def _dfs(node, path, visited_in_path):
        for nxt in sorted(edges.get(node, ())):
            if nxt == node:
                continue
            if {node, nxt} in exempt:
                continue
            if nxt in visited_in_path:
                cyc = path[visited_in_path.index(nxt):] + [nxt]
                cycles.append(" -> ".join(cyc))
                return
            if nxt not in edges:
                continue
            _dfs(nxt, path + [nxt], visited_in_path | {nxt})

    for start in sorted(edges):
        _dfs(start, [start], {start})
    # 去重（同环多起点）
    uniq = sorted({tuple(sorted(set(c.split(" -> ")))) for c in cycles})
    cycles = [" / ".join(c) for c in uniq]
    return violations, cycles, edges


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="RULES §1.3 依赖方向机器判据")
    ap.add_argument("--check", action="store_true", help="判红模式（退出码 0/1）")
    ap.add_argument("--list", action="store_true", help="打印例外登记表")
    args = ap.parse_args(argv)

    if args.list:
        print("已登记例外：")
        for (src, dst), why in sorted(EXCEPTIONS.items()):
            print(f"  {src} -> {dst}: {why}")
        print(f"已登记互反对（环豁免）：{REGISTERED_MUTUAL_PAIRS}")
        return 0

    violations, cycles, _ = collect()
    if args.list or not args.check:
        if not args.check:
            for v in violations:
                print(v)
            for c in cycles:
                print(f"cycle: {c}")
            if not violations and not cycles:
                print("[PASS] dep direction: no violation, no cycle")
            return 0

    if violations or cycles:
        for v in violations:
            print(f"[FAIL] {v}")
        for c in cycles:
            print(f"[FAIL] cycle: {c}")
        print(f"[FAIL] dep direction: {len(violations)} violation(s), "
              f"{len(cycles)} cycle(s)；新例外必须先在 EXCEPTIONS 登记 justification")
        return 1
    print("[PASS] dep direction: lib 8 包方向合规、无未登记环 "
          f"（{len(EXCEPTIONS)} 条已登记例外 / {REGISTERED_MUTUAL_PAIRS} 已登记互反对）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
