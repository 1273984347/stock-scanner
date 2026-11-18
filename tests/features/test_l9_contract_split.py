"""批次 L9：消费者注册表与迁移证据集的解耦判据。

拆分前：``check_consumer_registry`` 要求 ``found == registered == MIGRATIONS``，
新消费者天生没有历史 pre_commit 快照，无法加入灰度，只能绕过门禁用裸 to_thread。

拆分后：三条独立约束——
  1. 调用点集合 == 活跃注册表（防偷加消费者）；
  2. 历史迁移证据 ⊆ 活跃注册表（防静默摘除历史证据）；
  3. 增量消费者必须在 RUNTIME_EVIDENCE 登记可复现的运行时对拍测试。

本文件验证「解耦真的生效」且「证据没有降级」——不是把相等断言删掉换绿。
"""

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def l9():
    spec = importlib.util.spec_from_file_location(
        "_l9_contract_split", ROOT / "scripts" / "offload_switch_diff.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def offload():
    import lib.infra.async_offload as module
    return module


def _with_consumers(monkeypatch, l9, offload, extra=(), remove=()):
    """同步替换注册表与 AST 发现结果，隔离验证「证据」这一条约束。"""
    updated = tuple(c for c in offload.OFFLOAD_CONSUMERS if c not in remove) + tuple(extra)
    monkeypatch.setattr(offload, "OFFLOAD_CONSUMERS", updated)
    monkeypatch.setattr(l9, "_discover_registered_consumers", lambda: set(updated))
    return updated


def test_current_contract_is_consistent(l9):
    assert l9.check_consumer_registry() == []
    assert l9.check_runtime_evidence() == []


def test_migration_evidence_is_a_frozen_subset(l9, offload):
    migrated = {m[0] for m in l9.MIGRATIONS}
    assert len(migrated) == 37                       # 历史证据数量冻结
    diverged = set(l9.MIGRATION_DIVERGENCES)
    assert len(diverged) == 5                        # 3 既有 + 2 个 P2-10 新增
    assert {"notifications", "strategy_recommend", "export_config"} <= diverged
    assert {"export_trades", "export_trades_excel"} <= diverged
    assert migrated <= set(offload.OFFLOAD_CONSUMERS)


def test_new_consumer_without_evidence_is_rejected(l9, offload, monkeypatch):
    """拆分不等于放行：新增消费者没有运行时证据必须判红。"""
    _with_consumers(monkeypatch, l9, offload, extra=("l9_no_evidence",))
    failures = l9.check_consumer_registry()
    assert any("no migration snapshot and no runtime evidence" in f
               and "l9_no_evidence" in f for f in failures)


def test_new_consumer_with_runtime_evidence_is_allowed(l9, offload, monkeypatch):
    """解耦生效：有可复现证据的新消费者不再被「注册表 != 迁移集合」拦住。"""
    _with_consumers(monkeypatch, l9, offload, extra=("l9_runtime_probe",))
    monkeypatch.setattr(l9, "RUNTIME_EVIDENCE", {
        "l9_runtime_probe": {
            "test": "tests/features/test_l9_contract_split.py",
            "reason": "schema-only probe: 本文件点名该消费者并含断言",
        },
    })
    assert l9.check_consumer_registry() == []
    assert l9.check_runtime_evidence() == []


def test_runtime_evidence_must_point_to_a_real_assertive_test(l9, monkeypatch):
    monkeypatch.setattr(l9, "RUNTIME_EVIDENCE", {
        "l9_runtime_probe": {"test": "tests/features/does_not_exist.py", "reason": "probe"},
    })
    failures = l9.check_runtime_evidence()
    assert any("test file not found" in f for f in failures)

    monkeypatch.setattr(l9, "RUNTIME_EVIDENCE", {
        "l9_runtime_probe": {"test": "README.md", "reason": "probe"},
    })
    failures = l9.check_runtime_evidence()
    assert any("does not name it" in f for f in failures)


def test_stale_runtime_evidence_is_rejected(l9, monkeypatch):
    monkeypatch.setattr(l9, "RUNTIME_EVIDENCE", {
        "l9_ghost": {"test": "tests/features/test_l9_contract_split.py", "reason": "probe"},
    })
    assert any("stale runtime evidence" in f for f in l9.check_consumer_registry())


def test_double_evidence_is_ambiguous(l9, monkeypatch):
    """历史迁移消费者不能再登记运行时证据，防两套判据互相遮蔽。"""
    monkeypatch.setattr(l9, "RUNTIME_EVIDENCE", {
        "backtest": {"test": "tests/features/test_l9_contract_split.py", "reason": "probe"},
    })
    assert any("both migration and runtime evidence" in f
               for f in l9.check_consumer_registry())


def test_removing_a_migration_consumer_is_rejected(l9, offload, monkeypatch):
    """历史证据不能被静默摘除——解耦不是放宽。"""
    _with_consumers(monkeypatch, l9, offload, remove=("backtest",))
    assert any("migration evidence consumers missing" in f
               for f in l9.check_consumer_registry())


def test_migration_freeze_unchanged(l9):
    """37 个历史迁移的逐字等价 / 分歧登记判据保持原样。"""
    assert l9.check_migration() == []
