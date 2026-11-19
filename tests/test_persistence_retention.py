"""持久化治理判据（P1-3，批次 C7，2026-11-20）

覆盖：
- audit.db retention：90 天前旧记录（含 children）被清理、新记录保留；
  retention 失败不阻塞审计写入
- append_jsonl 轮转：超阈值让位 .1、继续写新文件、自动建父目录
- 接线守卫：paper_signals / frontend_errors 走带轮转追加；备份白名单含
  4 类不可再生资产；update_tdx 用 RotatingFileHandler；通知写失败 warning 级

patch 口径（I4）：audit_logger.DB_PATH → tmp（不在 conftest 隔离名单，自行重定向）。
"""

import inspect
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from lib.infra.append_log import append_jsonl
from lib.monitoring import audit_logger


@pytest.fixture(autouse=True)
def _isolate_audit_db(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_logger, "DB_PATH", tmp_path / "audit.db")
    yield


# ---------------------------------------------------------------- audit retention


def _seed_old_scan(days_ago: int, with_child: bool = True) -> int:
    """直接 SQL 造一条 N 天前的审计（log_scan 只写 now，历史只能人工种）。"""
    conn = sqlite3.connect(str(audit_logger.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        audit_logger._init_db(conn)
        old_time = (datetime.now() - timedelta(days=days_ago)).isoformat()
        cur = conn.execute(
            "INSERT INTO scan_logs (scan_time, preset) VALUES (?, ?)",
            (old_time, "balanced"))
        sid = cur.lastrowid
        if with_child:
            conn.execute(
                "INSERT INTO stock_decisions (scan_id, code, strategy) "
                "VALUES (?, '600519', '多因子')", (sid,))
        conn.commit()
        return sid
    finally:
        conn.close()


def test_audit_retention_prunes_old_rows_and_children(tmp_path):
    old_id = _seed_old_scan(days_ago=100)
    fresh_id = _seed_old_scan(days_ago=1)

    result = {"preset": "balanced", "results": {}}
    scan_id = audit_logger.log_scan(result)
    assert scan_id > 0

    conn = sqlite3.connect(str(audit_logger.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        ids = {r["id"] for r in conn.execute("SELECT id FROM scan_logs").fetchall()}
        orphans = conn.execute(
            "SELECT COUNT(*) AS n FROM stock_decisions WHERE scan_id = ?",
            (old_id,)).fetchone()["n"]
    finally:
        conn.close()
    assert old_id not in ids, "超过 90 天的审计必须被清理"
    assert fresh_id in ids and scan_id in ids, "保留期内记录不得误删"
    assert orphans == 0, "children 必须随父记录同清"


def test_audit_prune_failure_is_contained():
    """接线守卫：_prune_old_scans 必须自带兜底（内部 except Exception），
    且调用点在本次写入 commit 之后（retention 失败不回滚审计写入）。"""
    src = inspect.getsource(audit_logger._prune_old_scans)
    assert "except Exception" in src
    caller = inspect.getsource(audit_logger._log_scan_locked)
    assert "_prune_old_scans(conn)" in caller
    assert caller.index("conn.commit()") < caller.index("_prune_old_scans(conn)")


# ---------------------------------------------------------------- append_jsonl


def test_append_jsonl_rotates_at_threshold(tmp_path):
    target = tmp_path / "logs" / "app.jsonl"
    for i in range(10):
        append_jsonl(target, {"msg": "x" * 40, "i": i}, max_bytes=200)
    rotated = target.with_name(target.name + ".1")
    assert rotated.exists(), "超过阈值必须让位 .1"
    cur_lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert 1 <= len(cur_lines) < 10, "轮转后当前文件必须显著小于全部 10 条"
    assert json.loads(cur_lines[-1])["i"] == 9, "最新记录必须落在当前文件且合法 JSONL"


def test_append_jsonl_creates_parent_dirs(tmp_path):
    target = tmp_path / "a" / "b" / "c.jsonl"
    append_jsonl(target, {"k": 1})
    assert json.loads(target.read_text(encoding="utf-8").strip()) == {"k": 1}


# ---------------------------------------------------------------- 接线守卫


def test_paper_signal_log_uses_rotation():
    import paper_trading

    src = inspect.getsource(paper_trading)
    assert "append_jsonl(SIGNALS_LOG" in src
    assert 'open(SIGNALS_LOG, "a"' not in src  # 旧无限 append 必须消失


def test_frontend_error_log_uses_rotation():
    import routers.system as system_mod

    src = inspect.getsource(system_mod)
    assert "append_jsonl(error_file" in src
    assert 'open(error_file, "a"' not in src


def test_backup_whitelist_covers_irreplaceable_assets():
    """4 类不可再生资产必须在白名单（C7 前备份恢复不了它们 = 备份形同虚设）。"""
    import routers.backup as backup_mod

    src = inspect.getsource(backup_mod._compute_backup_create)
    for asset in ("cache/signals/signal_history.json", "cache/paper_trading",
                  "cache/audit.db", "cache/models"):
        assert asset in src, f"备份白名单缺不可再生资产: {asset}"


def test_update_tdx_log_uses_rotating_handler():
    script = Path(__file__).resolve().parent.parent / "scripts" / "update_tdx_data.py"
    src = script.read_text(encoding="utf-8")
    assert "RotatingFileHandler" in src
    assert "logging.handlers" in src


def test_notification_write_failure_is_warning_level():
    from lib.monitoring import notification_logger

    src = inspect.getsource(notification_logger.NotificationLogger.log)
    assert "logger.warning" in src  # debug→warning：磁盘满必须可观测
    assert "logger.debug" not in src
