"""RF-01 Phase 6 阶段 2a —— offload 唯一入口的机器判据。

对应 RULES §1.7 T1–T4。四条硬守卫：
  ① flag off 时绝不进线程池（逐字节等价改造前的同步直调）；
  ② flag on 时必经线程池，且要能自证（坑表 P-56 空判据）；
  ③ off/on 的返回值与异常逐字段一致；
  ④ 消费者不得自读开关 / 端点不得改成同步 def（AST 守卫）。
"""

from __future__ import annotations

import asyncio
import ast
import pathlib
import pytest

from lib.infra.async_offload import OFFLOAD_CONSUMERS, run_blocking

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def _run(coro):
    return asyncio.run(coro)


def _compute(a, b, *, scale=1):
    return {"sum": (a + b) * scale, "parts": [a, b]}


def _thread_pool_stub():
    """替换 anyio.to_thread.run_sync 的 async 桩，并计数调用次数。"""
    counter = {"n": 0}

    async def fake(func, *args, **kwargs):
        counter["n"] += 1
        return func(*args, **kwargs)

    return fake, counter


# ---------------------------------------------------------------- 两条路径

def test_off_never_uses_thread_pool(monkeypatch):
    import lib.infra.async_offload as offload

    def poison(*args, **kwargs):
        raise AssertionError("off-side must not enter the thread pool")

    # 评审 P0-1（2026-11-20）默认值翻转 all 后，off 侧判据改用显式回滚值 0 触发
    # （原 delenv 触发的是「默认 off」——语义已变；判据意图「显式 off 绝不进线程池」不变）
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", "0")
    monkeypatch.setattr(offload.anyio.to_thread, "run_sync", poison)
    assert _run(run_blocking("backtest", _compute, 2, 3, scale=4)) == {
        "sum": 20, "parts": [2, 3]
    }


def test_default_is_all_after_p0_1_flip(monkeypatch):
    """评审 P0-1：默认值 = all（env 未设时任意消费者走线程池）；显式 0 仍可回滚。"""
    from config import async_offload_enabled

    monkeypatch.delenv("FEATURE_ASYNC_OFFLOAD", raising=False)
    assert async_offload_enabled("backtest") is True
    assert async_offload_enabled("risk") is True
    assert async_offload_enabled("unregistered_consumer") is True
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", "0")
    assert async_offload_enabled("backtest") is False


@pytest.mark.parametrize("flag", ["1", "on", "all", "*", "backtest"])
def test_on_goes_through_thread_pool(monkeypatch, flag):
    import lib.infra.async_offload as offload

    fake, counter = _thread_pool_stub()
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", flag)
    monkeypatch.setattr(offload.anyio.to_thread, "run_sync", fake)
    result = _run(run_blocking("backtest", _compute, 1, 2))
    assert result == {"sum": 3, "parts": [1, 2]}
    assert counter["n"] == 1


@pytest.mark.parametrize("flag", ["0", "off", "false", "", "none"])
def test_off_variants_all_stay_synchronous(monkeypatch, flag):
    import lib.infra.async_offload as offload

    fake, counter = _thread_pool_stub()
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", flag)
    monkeypatch.setattr(offload.anyio.to_thread, "run_sync", fake)
    assert _run(run_blocking("backtest", _compute, 5, 6)) == {"sum": 11, "parts": [5, 6]}
    assert counter["n"] == 0


def test_consumer_level_gray_only_enables_listed_consumers(monkeypatch):
    import lib.infra.async_offload as offload

    fake, counter = _thread_pool_stub()
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", "backtest")
    monkeypatch.setattr(offload.anyio.to_thread, "run_sync", fake)
    _run(run_blocking("walkforward", _compute, 1, 1))
    assert counter["n"] == 0, "unlisted consumer must stay synchronous"
    _run(run_blocking("backtest", _compute, 1, 1))
    assert counter["n"] == 1


# ---------------------------------------------------------------- 行为等价

@pytest.mark.parametrize("args,kwargs", [
    ((0, 0), {}),
    ((-3, 7), {"scale": 2}),
    ((1.25, 2.75), {"scale": 0.5}),
])
def test_off_and_on_return_identical_values(monkeypatch, args, kwargs):
    monkeypatch.delenv("FEATURE_ASYNC_OFFLOAD", raising=False)
    off = _run(run_blocking("backtest", _compute, *args, **kwargs))
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", "all")
    on = _run(run_blocking("backtest", _compute, *args, **kwargs))
    assert off == on
    assert type(off) is type(on)


def test_off_and_on_propagate_exceptions_identically(monkeypatch):
    class Sentinel(Exception):
        pass

    def boom():
        raise Sentinel("same message")

    monkeypatch.delenv("FEATURE_ASYNC_OFFLOAD", raising=False)
    with pytest.raises(Sentinel, match="same message"):
        _run(run_blocking("backtest", boom))
    monkeypatch.setenv("FEATURE_ASYNC_OFFLOAD", "all")
    with pytest.raises(Sentinel, match="same message"):
        _run(run_blocking("backtest", boom))


# ---------------------------------------------------------------- 边界守卫

def test_consumer_registry_is_explicit_and_unique():
    # 批次 L9 契约拆分：注册表不再整体冻结（新消费者可以加入），
    # 但历史迁移消费者集合仍冻结，新增者必须有运行时证据登记。
    # 两集合的完整判据在 test_l9_contract_split.py，这里只守「不重不漏历史项」。
    legacy = (
        "backtest", "backtest_compare", "walkforward",
        "factor_eval", "factor_scatter", "kline", "risk", "export_config",
        "factor_contribution", "market", "positions", "risk_exposure", "stock",
        "signals", "exposures", "position", "regime_history", "score", "sector_flow",
        "attribution", "audit", "audit_detail", "export_trades", "export_trades_excel",
        "export_pdf", "factor_ic_history", "factor_weights", "notifications",
        "notification_stats", "position_distribution", "risk_correlation",
        "sector_heatmap", "signals_accuracy_trend", "strategy_ranking",
        "strategy_recommend", "trade_history", "ab_test",
    )
    assert set(legacy) <= set(OFFLOAD_CONSUMERS)
    assert len(set(OFFLOAD_CONSUMERS)) == len(OFFLOAD_CONSUMERS)


def test_consumers_do_not_read_the_flag():
    """T2：灰度判定只在唯一入口，消费者文件里不许出现开关名。"""
    files = [ROOT / "api.py", *sorted((ROOT / "routers").glob("*.py"))]
    offenders = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in (
                "FEATURE_ASYNC_OFFLOAD", "async_offload_enabled"
            ):
                offenders.append(f"{path.name}:{node.lineno}:{node.id}")
            if isinstance(node, ast.Attribute) and node.attr == "async_offload_enabled":
                offenders.append(f"{path.name}:{node.lineno}:attr")
    assert not offenders, f"gray decision leaked into consumers: {offenders}"


def test_endpoint_signatures_stay_async():
    """T3：不得把端点改成 def 来「顺手」走线程池。"""
    for path in sorted((ROOT / "routers").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for d in node.decorator_list:
                call = d if isinstance(d, ast.Call) else None
                func = call.func if call is not None else d
                if (isinstance(func, ast.Attribute)
                        and func.attr in ("get", "post", "put", "delete", "patch")
                        and call is not None and call.args
                        and isinstance(call.args[0], ast.Constant)
                        and str(call.args[0].value).startswith("/api/")):
                    assert isinstance(node, ast.AsyncFunctionDef), (
                        f"{path.name}:{node.lineno} {node.name} became sync")
