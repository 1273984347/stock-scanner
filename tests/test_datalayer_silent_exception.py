"""P1-1 机器判据：pytdx 兜底不得静默吞异常。

2026-11-18 迁移（ADR-0024）：pytdx 兜底从 ``data_layer`` 迁到
``data_realtime._fetch_pytdx_sample``。原判据按 ``data_layer`` 源码扫描，
迁移后会 `pytest.skip("不含 pytdx 连接代码")` —— 变成空判据。
现改为扫描**现役实现模块**，并把「pytdx 段消失」判红（消失必须是有意决策，
要同步改本文件与 ADR，而不是让判据悄悄失效）。
"""
import importlib
import inspect

import pytest

REALTIME_MODULE = "lib.data.data_realtime"
LAYER_MODULE = "lib.data.data_layer"

SILENT_PATTERNS = [
    "except Exception:\n                continue",
    "except Exception: continue",
    "except: continue",
]


def _source(module_name: str) -> str:
    return inspect.getsource(importlib.import_module(module_name))


def test_pytdx_block_exists_and_logs_warning():
    """pytdx 连接失败必须打 warning（不静默），且兜底块必须存在。"""
    source = _source(REALTIME_MODULE)
    idx = source.find("TdxHq_API")
    assert idx >= 0, (
        f"{REALTIME_MODULE} 不再含 pytdx 兜底：若为有意移除，"
        f"须同步改本判据、SNAPSHOT_SOURCE_ORDER 与 ADR-0024"
    )
    section = source[idx:idx + 5000]
    assert "logger.warning" in section, "pytdx 连接段必须含 logger.warning（不静默）"


def test_no_silent_exception_continue():
    """两个模块都不得出现 `except Exception: continue` 静默吞异常。"""
    for module_name in (LAYER_MODULE, REALTIME_MODULE):
        source = _source(module_name)
        for pattern in SILENT_PATTERNS:
            assert pattern not in source, \
                f"{module_name} 仍含静默吞异常的 {pattern!r}——P1-1 修复未完成"


def test_pytdx_exception_caught_with_logger():
    """pytdx 段必须有 warning/error 级日志（debug 不够，故障时看不见）。"""
    source = _source(REALTIME_MODULE)
    idx = source.find("TdxHq_API")
    assert idx >= 0
    section = source[idx:idx + 5000]
    assert "logger.warning" in section or "logger.error" in section


def test_failure_path_returns_empty_not_sample_frame():
    """pytdx 不可用/连不上必须返回空帧（由调用方显式降级），不得伪造样本行情。"""
    from lib.data import data_realtime as rt
    assert rt.SNAPSHOT_SOURCE_ORDER[-1] == "pytdx_sample"
    source = _source(REALTIME_MODULE)
    block = source[source.find("def _fetch_pytdx_sample"):source.find("def get_market_snapshot")]
    assert block, "未找到 _fetch_pytdx_sample 实现"
    assert block.count("return pd.DataFrame()") >= 3, \
        "pytdx 的每个失败分支（import/连接/空返回）都必须显式返回空帧"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
