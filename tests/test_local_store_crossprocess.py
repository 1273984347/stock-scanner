"""Δ4/N1 双进程判据（2026-11-22）：local_store 写边界的跨进程收口。

评审依据：docs/audit/architecture-review-delta-2026-11-22.md Δ4——
* manifest 整文件 RMW 此前只有线程锁 ⇒ API 进程 × data_sync 并发写时
  last-writer-wins 丢更新（账实漂移，``--verify`` 间歇判红）；
* 固定 tmp 名（无 pid/uuid）⇒ 两进程同写一个 key 互相覆盖 tmp；
* ``data_layer.py:1059`` fundflow 落仓裸抛 ⇒ 竞态异常被计为策略失败（误伤放大）。

修复 = lib/infra/file_lock.py（写边界互斥 + 会话单飞，**非** process_leader——
排他 leader 语义与单 worker 前提冲突，复核 D③ 否决）+ uuid tmp + 落仓守卫。
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.local_store

import lib.data.local_store as ls
from lib.infra.file_lock import cross_process_lock, try_session_lock

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "STORE_ROOT", tmp_path / "store")
    monkeypatch.setattr(ls, "MANIFEST_PATH", tmp_path / "store" / "manifest.json")
    monkeypatch.delenv("QUANT_LOCAL_STORE", raising=False)
    monkeypatch.delenv("QUANT_OFFLINE", raising=False)
    yield


def _df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
        "open": [10.0, 11.0], "high": [11.0, 12.0],
        "low": [9.0, 10.0], "close": [10.5, 11.5], "volume": [1e6, 2e6],
    })


# 子进程写手：patch STORE_ROOT 后对**另一个 key** 写 N 次（不同 key 的 files
# 计数才会暴露跨进程丢更新——同 key 重写 files 恒 1，区分度不足）。
_SUB_WRITER = (
    "import sys, pathlib, pandas as pd;"
    "sys.path.insert(0, sys.argv[3]);"
    "import lib.data.local_store as ls;"
    "root = pathlib.Path(sys.argv[1]);"
    "ls.STORE_ROOT = root; ls.MANIFEST_PATH = root / 'manifest.json';"
    "df = pd.DataFrame({'date': pd.to_datetime(['2026-01-01','2026-01-02']),"
    "'open':[1.0,1.0],'high':[1.0,1.0],'low':[1.0,1.0],'close':[1.0,1.0],'volume':[1e6,1e6]});"
    "n = int(sys.argv[2]);"
    "[ls.write_frame('kline', '000001', df) for _ in range(n)]"
)


def test_cross_process_writes_keep_manifest_consistent(tmp_path):
    """双进程并发写不同 key：manifest 账必须等于盘上真值（丢更新 = files 少 1）。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", _SUB_WRITER,
         str(tmp_path / "store"), "30", str(_REPO)],
        cwd=str(_REPO))
    for _ in range(30):  # 主进程同窗并发写另一个 key（真实 IO 节奏，无需 sleep）
        ls.write_frame("kline", "600519", _df())
    proc.wait(timeout=120)
    assert proc.returncode == 0

    leftovers = sorted(p.name for p in (tmp_path / "store" / "kline").glob("*.tmp"))
    assert not leftovers, f"tmp 泄漏（uuid 命名后仍不得残留）: {leftovers}"

    truth = ls.rebuild_manifest(dry_run=True)
    acc = json.loads(ls.MANIFEST_PATH.read_text(encoding="utf-8"))["datasets"]
    assert acc["kline"]["files"] == truth["kline"]["files"] == 2, \
        "跨进程 files 记账丢更新 ⇒ 账实漂移（Δ4 修复点）"
    assert acc["kline"]["rows"] == truth["kline"]["rows"] == 4
    assert len(ls.read_frame("kline", "600519")) == 2
    assert len(ls.read_frame("kline", "000001")) == 2


def test_session_lock_second_handle_fails(tmp_path):
    """会话单飞：同进程第二个句柄也必须拿不到（data_sync 防双实例的判据）。"""
    p = tmp_path / "s.lock"
    with try_session_lock(p) as first:
        assert first is True
        with try_session_lock(p) as second:
            assert second is False, "锁被占用时 try_session_lock 必须 False"


def test_cross_process_lock_blocks_across_processes(tmp_path):
    """写边界互斥（真跨进程）：子进程持锁期间，本进程获取必须阻塞到释放。

    Windows 语义（实测）：同进程第二句柄 = 立即 EDEADLOCK（Errno 36），
    跨进程才是 LK_LOCK 的 1s 间隔阻塞重试——生产路径同进程并发已被线程锁
    串行化，互斥判据必须用真子进程。挂起用 time.sleep（子进程无 conftest，
    P-81 不适用）。
    """
    p = tmp_path / "w.lock"
    holder_src = (
        "import sys, time\n"
        f"sys.path.insert(0, r'{_REPO}')\n"
        "from lib.infra.file_lock import cross_process_lock\n"
        f"with cross_process_lock(r'{p}'):\n"
        "    print('held', flush=True)\n"
        "    time.sleep(1.5)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", holder_src],
                            stdout=subprocess.PIPE, text=True, cwd=str(_REPO))
    try:
        assert proc.stdout.readline().strip() == "held", "holder 未持锁"
        t0 = time.monotonic()
        with cross_process_lock(p):  # 阻塞至 holder 退出（~1.5s，1s 粒度重试）
            elapsed = time.monotonic() - t0
    finally:
        proc.wait(timeout=15)
    assert elapsed >= 0.5, "持锁期间跨进程获取必须阻塞（elapsed=%.2fs）" % elapsed


def test_tmp_cleaned_up_on_replace_failure(tmp_path, monkeypatch):
    """os.replace 失败（Windows 下目标被读句柄打开）时 tmp 必须清理，不留垃圾文件。"""
    def boom(a, b):
        raise PermissionError("target locked by reader")
    monkeypatch.setattr(ls.os, "replace", boom)
    with pytest.raises(PermissionError):
        ls.write_frame("kline", "600519", _df())
    assert not list((tmp_path / "store" / "kline").glob("*.tmp")), \
        "replace 失败后 tmp 必须被 finally 清理"
    # manifest 未被 touch（写失败 = 不记账）
    assert not ls.MANIFEST_PATH.exists()


def test_fundflow_store_write_failure_does_not_break_fetch(monkeypatch):
    """data_layer:1059 守卫：落仓异常不得污染成功取数（旧实现裸抛被策略层
    计为策略失败 ⇒ failure_tracker 误伤放大，Δ4/N1 路径②）。"""
    from lib.data import data_layer

    monkeypatch.setattr(
        data_layer, "_fetch_fund_flow",
        lambda code: {"main_net_ratio": 0.1, "net_inflow_5d": 1.0})

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ls, "write_json", boom)

    with data_layer._fundflow_cache_lock:
        data_layer._fundflow_cache.clear()
    out = data_layer.get_stock_fund_flow("600519")
    assert out["main_net_ratio"] == 0.1, "落仓失败不得影响返回值"
    with data_layer._fundflow_cache_lock:
        data_layer._fundflow_cache.clear()
