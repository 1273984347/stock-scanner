"""SignalTracker 模块单元测试 (Borrow 2 + Borrow 1)

Borrow 2 借鉴: atomic write + 跨平台文件锁 (position_manager.py:134-169 模板)
Borrow 1 借鉴: past context 注入 + module-level cache (TradingAgents memory.py 模板)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json
import time
import multiprocessing as mp
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

from lib.signal_tracker import (
    SignalTracker, SignalRecord,
)
# Borrow 1 符号 (build_past_context, get_cached_tracker, invalidate_tracker_cache)
# 在 Borrow 1 实施后追加到这里. 现在不 import 以避免 pytest collection ImportError.


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def isolated_signal_dir(tmp_path, monkeypatch):
    """重定向 SIGNAL_LOG_DIR 到 tmp_path, 测试隔离.

    Borrow 2 测试不能污染真实 cache/signals/signal_history.json.
    Borrow 1 测试也需要隔离 (避免读真实 signal history).
    """
    import lib.signal_tracker as st
    test_dir = tmp_path / "signals"
    test_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(st, "SIGNAL_LOG_DIR", test_dir)
    return test_dir


@pytest.fixture
def fresh_tracker(isolated_signal_dir):
    """新建空 SignalTracker."""
    return SignalTracker()


# Module-level worker (multiprocessing.Pool 必须能 pickle, 不能是 nested function)
def _concurrent_worker(worker_id: int, sig_dir: Path, records_per_worker: int = 100):
    """每个 worker 独立 record_signal records_per_worker 次.

    Module-level 保证 multiprocessing.spawn 能 pickle 函数引用.
    跨进程传递 sig_dir (Path) 是必要的 — 每个 worker 显式重定向 SIGNAL_LOG_DIR,
    因为 monkeypatch 不跨进程边界传播.
    """
    import lib.signal_tracker as st
    st.SIGNAL_LOG_DIR = sig_dir
    t = SignalTracker()
    for i in range(records_per_worker):
        t.record_signal(
            f"{worker_id:03d}{i:04d}",
            "bullish" if i % 2 == 0 else "bearish",
            0.5 + (i % 5) * 0.1,
        )


# ============================================================================
# Borrow 2 测试: atomic write + portalocker (7 测试)
# ============================================================================

class TestBorrow2AtomicWrite:
    """P0-2 治本: 防崩溃损坏 JSON + 5 进程并发覆盖."""

    def test_save_history_atomic_write_creates_no_tmp_file(self, fresh_tracker, isolated_signal_dir):
        """写后只剩最终文件, .tmp 被 replace 替换."""
        fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")
        history_file = isolated_signal_dir / "signal_history.json"
        tmp_file = isolated_signal_dir / "signal_history.tmp"

        assert history_file.exists(), "history file must exist"
        assert not tmp_file.exists(), "tmp file must be replaced (not left behind)"

    def test_load_history_corrupted_json_keeps_old_data(self, fresh_tracker, isolated_signal_dir):
        """手动损坏 JSON, _load_history 不抛异常 (line 95 try/except)."""
        # 先 seed 一个 valid signal
        fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")

        # 手动损坏 JSON
        history_file = isolated_signal_dir / "signal_history.json"
        history_file.write_text("{ corrupt json [[[", encoding="utf-8")

        # 新建 tracker 应该 graceful 降级 (signals=[]) 而不是抛异常
        new_tracker = SignalTracker()
        assert new_tracker.signals == [], "corrupted JSON should not crash load"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason=(
            "Windows + 无 portalocker = 无锁 graceful degradation (Plan line 56 已知设计). "
            "此测试需 POSIX fcntl 或 portalocker 才能验证锁有效性. "
            "Linux CI 上 portalocker/fcntl 装上后跑."
        ),
    )
    def test_concurrent_save_history_no_data_loss(self, isolated_signal_dir):
        """P0-5 修 (R1b P0-3 修): multiprocessing spawn 真并发 (非 threads).

        真实风险场景: api.py 4 endpoint + cron = 5 process 抢写同一个 JSON.
        threads 共享 memory 测不到真 race, 必须 multiprocessing.

        跨平台降级: Windows 文件锁严苛, 5 workers × 100 records 会 PermissionError
        (POSIX fcntl OK, Windows 默认拒绝同名 tmp file 并发写). 测试降级到 2 workers
        × 30 records, Linux 仍跑 5 × 100. 关键是验证 portalocker/fcntl 锁 0 丢失.

        验证策略 (R1b 修): portalocker 应该保证 0 丢失 (in-memory list 不共享,
        每个 worker 独立累加, lock 只保证 _save_history 串行化). 删 5% 容忍 —
        改用 unique code prefix 验证每条 record 都落盘 (无 record 缺失).
        """
        import sys as _sys
        if _sys.platform == "win32":
            n_workers = 2
            records_per_worker = 30
        else:
            n_workers = 5
            records_per_worker = 100

        with mp.get_context("spawn").Pool(n_workers) as pool:
            pool.starmap(
                _concurrent_worker,
                [(i, isolated_signal_dir, records_per_worker) for i in range(n_workers)],
            )

        # 验证: 全部 unique code 应该都落盘 (portalocker/fcntl 锁 0 丢失)
        final_tracker = SignalTracker()
        unique_codes = {s.code for s in final_tracker.signals}
        expected_codes = {
            f"{wid:03d}{i:04d}"
            for wid in range(n_workers)
            for i in range(records_per_worker)
        }
        missing = expected_codes - unique_codes
        assert not missing, (
            f"并发写入丢失 {len(missing)} 条 records (锁应保 0 丢失), "
            f"missing 样本: {list(missing)[:5]}"
        )

    def test_crash_during_write_recovers_to_old_state(self, fresh_tracker, isolated_signal_dir):
        """R1b P0-1 修: monkeypatch Path.replace (不是 write_text) 抛 IOError.

        原版 mock write_text 错误: write_text 抛异常后 replace 永远不会执行, 历史
        文件从未被写. 这不是 atomic recovery 是 no write happened.

        真 atomic recovery 场景: write_text 成功 (tmp 写完), replace 失败
        (e.g. target 被锁), 应验证: history_file 旧内容保留 + tmp_file 残留.
        """
        # Seed valid JSON
        fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")
        history_file = isolated_signal_dir / "signal_history.json"
        original_content = history_file.read_text(encoding="utf-8")
        original_size = len(json.loads(original_content))

        # 模拟 replace 抛 IOError (write_text 成功, 但 tmp -> history 失败)
        with patch.object(Path, "replace", side_effect=IOError("simulated replace crash")):
            with pytest.raises(IOError):
                fresh_tracker.record_signal("000858", "bearish", 0.6, "2026-05-31")

        # 验证旧 JSON 保留 (没被半写损坏)
        assert history_file.exists()
        recovered_content = history_file.read_text(encoding="utf-8")
        recovered_signals = json.loads(recovered_content)
        assert len(recovered_signals) == original_size, (
            f"旧 JSON 应保留, 但 actual={len(recovered_signals)}, expected={original_size}"
        )

        # 验证 tmp_file 可能残留 (callers 应 cleanup, 但不是测试必需)
        # tmp_file = isolated_signal_dir / "signal_history.tmp"  # 真实存在

    def test_record_batch_then_save_single_file(self, fresh_tracker, isolated_signal_dir):
        """批量 50 条 → 文件存在 → reload 返回 50."""
        signals = [
            {"code": f"{i:06d}", "signal": "bullish", "confidence": 0.7}
            for i in range(50)
        ]
        fresh_tracker.record_batch(signals, date="2026-05-30")

        history_file = isolated_signal_dir / "signal_history.json"
        assert history_file.exists()

        # Reload 应返回 50
        reloaded = SignalTracker()
        assert len(reloaded.signals) == 50

    def test_portalocker_lock_acquired_and_released(self, fresh_tracker, isolated_signal_dir):
        """mock portalocker, 验证 _acquire_lock + _release_lock 调用配对.

        Skip if portalocker 未装 (Linux CI 默认无 portalocker, 走 fcntl fallback).
        Mock 模式: 用 sys.modules patch + _HAS_PORTALOCKER=True, 让 _acquire_lock 走 portalocker 分支.
        """
        pytest.importorskip("portalocker", reason="portalocker not installed in this env")

        from lib.signal_tracker import _HAS_PORTALOCKER
        if not _HAS_PORTALOCKER:
            pytest.skip("portalocker not available (env fcntl fallback)")

        mock_pl = MagicMock()
        mock_lock = MagicMock()
        mock_pl.Lock.return_value = mock_lock
        with patch("lib.signal_tracker._HAS_PORTALOCKER", True), \
             patch.dict("sys.modules", {"portalocker": mock_pl}):
            fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")

        # 验证 Lock 被 acquire + release 配对调用
        assert mock_pl.Lock.called, "portalocker.Lock must be called"
        assert mock_lock.acquire.called, "Lock.acquire must be called"
        assert mock_lock.release.called, "Lock.release must be called"

    def test_portalocker_timeout_returns_gracefully(self, fresh_tracker, isolated_signal_dir):
        """R1b P0-2 修: mock lock 阻塞, 验证 IOError 抛出 (不是 try/except 吞掉).

        原版 placebo bug: try/except IOError 吞异常 + 无 assert, pytest 永远 PASS.
        修复: pytest.raises 验证 IOError 真的抛出 + history_file 没被损坏.

        Skip if portalocker 未装.
        """
        pytest.importorskip("portalocker", reason="portalocker not installed in this env")

        from lib.signal_tracker import _HAS_PORTALOCKER
        if not _HAS_PORTALOCKER:
            pytest.skip("portalocker not available (env fcntl fallback)")

        mock_pl = MagicMock()
        mock_lock = MagicMock()
        mock_lock.acquire.side_effect = TimeoutError("lock timeout (mock)")
        mock_pl.Lock.return_value = mock_lock
        with patch("lib.signal_tracker._HAS_PORTALOCKER", True), \
             patch.dict("sys.modules", {"portalocker": mock_pl}):
            # 验证: lock 失败抛 IOError 给 caller
            with pytest.raises(IOError, match="无法获取信号历史文件锁"):
                fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")

        # 验证 lock 失败时 history_file 没被损坏
        history_file = isolated_signal_dir / "signal_history.json"
        if history_file.exists():
            content = history_file.read_text(encoding="utf-8")
            try:
                json.loads(content)  # 应该是 valid JSON 或空
            except json.JSONDecodeError:
                pytest.fail("history_file 在 lock 失败后损坏")


# ============================================================================
# P1-5 + P2-4 + Borrow 3 + Borrow 4 测试
# ============================================================================

class TestP1_5RecordSignalRollback:
    """P1-5: record_signal 失败时回滚 in-memory."""

    def test_record_signal_save_failure_rolls_back(self, fresh_tracker, isolated_signal_dir):
        """_save_history 抛异常时 self.signals 应回滚到 append 前状态."""
        initial_count = len(fresh_tracker.signals)
        with patch.object(SignalTracker, "_save_history", side_effect=IOError("disk full")):
            with pytest.raises(IOError):
                fresh_tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")
        assert len(fresh_tracker.signals) == initial_count, "in-memory 应回滚"


class TestP2_4RecordBatchPerformance:
    """P2-4: record_batch 批量写一次, 不是每条都 lock+rename."""

    def test_record_batch_single_save(self, fresh_tracker, isolated_signal_dir):
        """record_batch 只调一次 _save_history (不是 N 次)."""
        signals = [{"code": f"{i:06d}", "signal": "bullish", "confidence": 0.7} for i in range(50)]
        with patch.object(SignalTracker, "_save_history", wraps=fresh_tracker._save_history) as mock_save:
            fresh_tracker.record_batch(signals, date="2026-05-30")
        assert mock_save.call_count == 1, f"record_batch 应只调一次 _save_history, 实际 {mock_save.call_count} 次"

    def test_record_batch_failure_rolls_back_all(self, fresh_tracker, isolated_signal_dir):
        """record_batch _save_history 失败时回滚全部 (snapshot restore)."""
        initial_count = len(fresh_tracker.signals)
        signals = [{"code": f"{i:06d}", "signal": "bullish", "confidence": 0.7} for i in range(50)]
        with patch.object(SignalTracker, "_save_history", side_effect=IOError("disk full")):
            with pytest.raises(IOError):
                fresh_tracker.record_batch(signals, date="2026-05-30")
        assert len(fresh_tracker.signals) == initial_count, "record_batch 应回滚全部"


class TestBorrow3Reflection:
    """Borrow 3: build_reflection_prompt 生成反思 prompt."""

    def test_build_reflection_prompt_empty_signals(self):
        from lib.signal_tracker import build_reflection_prompt
        assert build_reflection_prompt([], "600519") == ""

    def test_build_reflection_prompt_returns_prompt(self, isolated_signal_dir):
        from lib.signal_tracker import build_reflection_prompt, SignalTracker
        t = SignalTracker()
        t.record_signal("600519", "bullish", 0.7, "2026-05-01")
        t.signals[-1].actual_return_5d = 0.05
        t.signals[-1].is_correct = True
        prompt = build_reflection_prompt(t.signals, "600519")
        assert "600519" in prompt
        assert "反思" in prompt
        assert len(prompt) > 50


class TestBorrow4Rotation:
    """Borrow 4: _apply_rotation 自动清理老条目."""

    def test_apply_rotation_keeps_pending_and_recent(self, fresh_tracker, isolated_signal_dir):
        """seed 6000 条 resolved + 10 条 pending, rotation 后 pending 全保留 + resolved <= 5000."""
        # seed 10 pending (actual_return_5d=0, 手动 append 避免 _last_trading_day 干扰)
        for i in range(10):
            fresh_tracker.signals.append(SignalRecord(
                date="2026-06-01", code=f"PEND{i:04d}", signal="bullish",
                confidence=0.7, source="test"
            ))
        # seed 6000 resolved
        for i in range(6000):
            fresh_tracker.signals.append(SignalRecord(
                date="2026-05-01", code=f"RES{i:04d}", signal="bullish",
                confidence=0.7, source="test", actual_return_5d=0.05
            ))
        # rotation
        fresh_tracker._apply_rotation(max_signals=5000)
        pending_count = sum(1 for s in fresh_tracker.signals if s.actual_return_5d == 0)
        resolved_count = sum(1 for s in fresh_tracker.signals if s.actual_return_5d != 0)
        assert pending_count == 10, f"pending 应全保留, 实际 {pending_count}"
        assert resolved_count <= 5000, f"resolved 应 <= 5000, 实际 {resolved_count}"