"""§1.3 依赖方向机器判据测试（P1-6，批次 C8，2026-11-20）

覆盖：
- 尺子 --check 退出码 0（方向合规、无未登记环）
- 尺子自证：注入违规探针必须判红（P-56 空判据教训——判据必须能区分
  「真的没变」和「压根没跑到」；探针用 import 形态验证 _imports_of 的
  lib.X 二段解析）
- V1 环回归守卫：broker 不得 import lib.risk；risk/datatypes 已删、
  trade_datatypes 已在 lib/data
- 三节点环守卫：scan_notify / ml_data 不得 import scanner
- 门面守卫：scanner.py 不得 re-export 下划线私有符号
"""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "dep_direction_check", ROOT / "scripts" / "dep_direction_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_direction_gate_green():
    assert _load_script().main(["--check"]) == 0


def test_gate_turns_red_on_injected_violation():
    """尺子自证：注入 `import lib.strategy`（infra→strategy 低层碰高层）必须判红。"""
    mod = _load_script()
    probe = ROOT / "lib" / "infra" / "_zz_direction_probe.py"
    probe.write_text("import lib.strategy.pipeline\n", encoding="utf-8")
    try:
        violations, cycles, _ = mod.collect()
        assert any("_zz_direction_probe" in v and "lib.strategy" in v
                   for v in violations), "注入违规必须被抓到（空判据守卫）"
        assert cycles == []
    finally:
        probe.unlink(missing_ok=True)


def test_v1_data_risk_ring_stays_broken():
    """V1 回归守卫：broker 的**代码**（AST import 节点，免疫 docstring 历史）不得
    引 lib.risk；类型模块已下沉、旧文件已删。"""
    tree = ast.parse((ROOT / "lib" / "data" / "broker.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] == "lib" and "risk" in node.module.split("."):
                offenders.append(f"{node.module}: line {node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("lib.risk"):
                    offenders.append(f"{alias.name}: line {node.lineno}")
    assert not offenders, f"broker 代码仍引用 lib.risk: {offenders}"
    assert (ROOT / "lib" / "data" / "trade_datatypes.py").exists()
    assert (ROOT / "lib" / "data" / "market_impact.py").exists()
    assert (ROOT / "lib" / "data" / "trading_constraints.py").exists()
    assert not (ROOT / "lib" / "risk" / "datatypes.py").exists()
    assert not (ROOT / "lib" / "risk" / "impact.py").exists()
    assert not (ROOT / "lib" / "risk" / "constraints.py").exists()


def test_scanner_three_node_ring_edges_removed():
    """scan_notify / ml_data 直连真源——scanner→pipeline→scan_notify→scanner 环断边。"""
    sn = (ROOT / "reporter" / "scan_notify.py").read_text(encoding="utf-8")
    assert "from scanner import" not in sn and "import scanner" not in sn
    ml = (ROOT / "lib" / "ml" / "ml_data.py").read_text(encoding="utf-8")
    assert "from scanner import" not in ml and "import scanner" not in ml


def test_scanner_facade_exports_public_only():
    """门面收缩守卫：scanner.py 不得 import/re-export 下划线私有符号。"""
    tree = ast.parse((ROOT / "scanner.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name.startswith("_"):
                    offenders.append(f"{node.module}.{alias.name}")
    assert not offenders, f"scanner 门面残留私有 re-export: {offenders}"


def test_trade_datatypes_consumers_via_aggregate(tmp_path):
    """聚合出口兼容：from lib.risk import Order/Holding 仍可用（backtest/tests 在用）。"""
    from lib.risk import Order, Holding, TradingConstraints  # noqa: F401
    from lib.data.trade_datatypes import Order as Order2
    assert Order is Order2  # 同一对象：聚合出口不是副本
