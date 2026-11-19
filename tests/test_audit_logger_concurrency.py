"""audit_logger 并发机器判据（架构评审 P0-1 就绪链，2026-11-20）

背景：``_LOG_LOCK`` 定义后从未 acquire（:23-25 注释声称「关键写入持锁」与代码不符，
「写了没接线」同族见坑表 P-108）——offload 默认值翻转为 all 后，线程池并发写入会撞
``database is locked`` → 审计记录静默丢失（写失败被 ``except → logger.error → return 0``
吞掉）。本文件三例判据：

1. N 线程并发 log_scan 全部落库、scan_id 唯一（修后行为）；
2. WAL 生效（读不阻塞写的前提）；
3. 锁接线守卫（防「写了没接线」再次静默发生）。

写路径纪律（P-80）：``DB_PATH`` 是模块级常量直指生产 ``cache/audit.db`` 且不在
conftest 隔离名单 —— 判据 autouse 自行重定向到 tmp，绝不写生产审计库。
"""

import inspect
import threading

import pandas as pd
import pytest

from lib.monitoring import audit_logger


@pytest.fixture(autouse=True)
def _isolate_audit_db(tmp_path, monkeypatch):
    """audit.db 重定向到 tmp（生产 audit.db 不可被测试污染）。"""
    monkeypatch.setattr(audit_logger, "DB_PATH", tmp_path / "audit.db")
    yield


def _sample_result(i: int) -> dict:
    return {
        "preset": "balanced",
        "risk_check": {
            "sentiment": 0.1,
            "macro_gate": {"state": "ALLOW_FULL"},
            "recommendation": "ok",
        },
        "portfolio": {
            "n_positions": 1,
            "expected_return": 0.05,
            "sharpe_ratio": 1.2,
            "weights": {"600519": 1.0},
        },
        "factor_health": {"recommendation": "ok"},
        "results": {
            # 生产形状：results 值是 DataFrame（log_scan 按 iterrows 消费）
            "多因子": pd.DataFrame([{
                "代码": "600519", "名称": "贵州茅台", "综合评分": 0.8,
                "评级": "A", "行业": "白酒",
            }]),
        },
    }


def test_concurrent_log_scan_records_all_without_loss():
    """24 线程并发写入：零失败、零丢失、scan_id 唯一。

    修前形态：并发撞 database is locked → except 吞掉 → 返回 0 = 静默丢审计。
    """
    ids, failures = [], []

    def worker(i):
        try:
            scan_id = audit_logger.log_scan(_sample_result(i))
            (ids if scan_id else failures).append(scan_id or i)
        except Exception as exc:  # noqa: BLE001 —— 判据要抓一切异常形态
            failures.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not failures, f"并发写入失败 {len(failures)} 条: {failures[:3]}"
    assert len(ids) == 24 and len(set(ids)) == 24
    history = audit_logger.get_scan_history(limit=50)
    assert len(history) == 24
    detail = audit_logger.get_scan_detail(ids[0])
    assert detail["stocks"][0]["code"] == "600519"


def test_wal_mode_enabled():
    """WAL 生效：评审 P0-1 翻 all 后读端点不因写锁排队。"""
    conn = audit_logger._get_db()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"


def test_log_scan_lock_wiring_guard():
    """接线守卫：log_scan 必须真的 acquire _LOG_LOCK（防「写了没接线」复发）。"""
    src = inspect.getsource(audit_logger.log_scan)
    assert "with _LOG_LOCK" in src
    body_src = inspect.getsource(audit_logger._log_scan_locked)
    assert "with _LOG_LOCK" not in body_src, "锁必须只包一次（写入体不应重复持锁）"
