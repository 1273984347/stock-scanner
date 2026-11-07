"""历史信号追踪器 —— 记录并回测推送信号的实际表现

功能：
1. 每日记录扫描信号到 JSON 文件
2. 事后回测信号的实际收益
3. 计算信号准确率、平均收益、盈亏比

使用方式：
    from lib.scoring.signal_tracker import SignalTracker
    tracker = SignalTracker()
    tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")
    report = tracker.evaluate_signals(days=30)
"""

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_LOG_DIR = Path("cache/signals")

# 跨平台文件锁 (Borrow 2 借鉴 position_manager.py:23-33)
try:
    import portalocker
    _HAS_PORTALOCKER = True
except ImportError:
    _HAS_PORTALOCKER = False
    if sys.platform != "win32":
        import fcntl
        _HAS_FCNTL = True
    else:
        _HAS_FCNTL = False


def _atomic_replace(tmp_path: Path, target_path: Path, retries: int = 10) -> None:
    """原子替换（带重试）。

    Windows 上 os.replace 会因**外部句柄**（杀毒软件实时扫描、索引服务等
    短暂打开目标文件）而**偶发** PermissionError [WinError 5] —— 这是瞬时状态，
    重试即可成功；POSIX 上通常一次成功。

    退避用 threading.Event().wait() 而非 time.sleep —— 后者会被测试的
    「默认零退避」autouse fixture 短路掉，导致重试窗口失效。
    """
    last_exc = None
    for attempt in range(retries):
        try:
            tmp_path.replace(target_path)  # os.replace() 原子 rename
            return
        except PermissionError as e:
            last_exc = e
            if attempt < retries - 1:
                threading.Event().wait(0.02 * (attempt + 1))
    raise last_exc


def _last_trading_day() -> str:
    """获取最近的交易日（跳过周末和节假日）

    P0-12 修复 (2026-06-08, lib-incremental-audit §2.5):
    - 旧实现硬编码 D:/TDX/vipdoc/sh/lday_4/sh600000.day + struct 解析 .day 二进制
      Linux/macOS/容器/CI 全崩, 跨平台强耦合
    - 修复: 用 akshare.tool_trade_date_hist_sina() 查 A 股交易日历
      + 自实现周几过滤 (周一-周五) + 节假日回退 (akshare 无历史节假日 API, 靠 bdate_range 估计)
    """
    try:
        import akshare as ak
        # akshare 获取 A 股交易日历 (历史 + 未来 1 年)
        trade_cal = ak.tool_trade_date_hist_sina()
        if trade_cal is None or trade_cal.empty:
            raise ValueError("akshare trade_cal empty")
        # trade_cal 是 DataFrame, 'trade_date' 列
        # 取 <= today 的最大日期
        today = pd.Timestamp(datetime.now().date())
        trade_dates = pd.to_datetime(trade_cal["trade_date"])
        valid = trade_dates[trade_dates <= today]
        if not valid.empty:
            return valid.max().strftime("%Y-%m-%d")
    except Exception as e:
        logger.debug(f"[signal_tracker] akshare 交易日历查询失败, 降级: {e}")

    # 降级方案: 用 bdate_range 估计 (跳过周末, 节假日会有误差但比硬编码 TDX 好)
    dt = datetime.now()
    while dt.weekday() >= 5:  # 周六周日回退
        dt -= timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


@dataclass
class SignalRecord:
    """信号记录"""
    date: str
    code: str
    signal: str  # bullish / bearish / neutral
    confidence: float
    source: str  # ic_score / agent_panel / etc.
    ic_score: float = 0
    # 事后填充
    actual_return_5d: float = 0
    actual_return_10d: float = 0
    actual_return_20d: float = 0
    is_correct: bool = False


class SignalTracker:
    """信号追踪器"""

    def __init__(self):
        SIGNAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.signals: List[SignalRecord] = []
        self._load_history()

    def _load_history(self):
        """加载历史信号"""
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        if history_file.exists():
            try:
                with open(history_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.signals = [SignalRecord(**s) for s in data]
            except Exception as e:
                logger.warning(f"加载信号历史失败: {e}")

    def _apply_rotation(self, max_signals: int = 5000, signals=None):
        """保留所有 pending (actual_return_5d=0) + 最近 max_signals 条已定型.

        Borrow 4: 防止 signal_history.json 无限增长.
        pending (待回填) 永远保留, resolved (已有 actual_return) 超限时 drop 最老.

        P0 修复 (2026-11-07): 支持传入 signals 对**合并后**的集合做 rotation,
        并返回结果（不直接改 self.signals），供 _save_history 的 read-modify-write 使用。
        """
        target = self.signals if signals is None else signals
        pending = [s for s in target if s.actual_return_5d == 0]
        resolved = [s for s in target if s.actual_return_5d != 0]
        if len(resolved) <= max_signals:
            return target
        rotated = pending + resolved[-max_signals:]
        if signals is None:
            self.signals = rotated
        return rotated

    @staticmethod
    def _sig_key(rec) -> tuple:
        """信号去重键：同一天 + 同股票 + 同方向 + 同来源 = 同一条。"""
        return (rec.date, rec.code, rec.signal, rec.source)

    def _merge_disk_signals(self, disk_signals: List[SignalRecord]) -> List[SignalRecord]:
        """合并磁盘上的信号与本实例内存信号（内存优先，已结算结果优先保留）。

        P0 修复 (2026-11-07)：原实现只写**内存副本**，多进程并发时后写者直接
        覆盖先写者 ⇒ CI 上 5 进程 × 100 条只剩 100 条（丢 400 条）。
        修法：写盘时在锁内做 read-modify-write —— 先读磁盘最新内容，
        再合并本实例记录，按去重键合并后整体写回。

        顺序：磁盘记录在前、本实例记录在后 ⇒ rotation 取"末尾 max 条"时
        优先保留本实例的最新记录。
        """
        merged, order = {}, []
        for s in disk_signals:
            k = self._sig_key(s)
            if k not in merged:
                order.append(k)
            merged[k] = s
        for s in self.signals:
            k = self._sig_key(s)
            if k not in merged:
                order.append(k)
                merged[k] = s
            else:
                prev = merged[k]
                # 内存优先；但若磁盘侧已结算(actual_return_5d != 0)，
                # 保留结算结果（否则会丢掉其它进程刚回填的收益率）
                if prev.actual_return_5d == 0 and s.actual_return_5d != 0:
                    merged[k] = s
        return [merged[k] for k in order]

    def _save_history(self):
        """保存信号历史 (Borrow 2 atomic write: tmp + replace + 跨平台文件锁)

        治本 (P0-2): 防崩溃损坏 JSON + 5 进程并发覆盖 (api.py 4 endpoint + cron).
        模板来自 position_manager.py:134-169 `_save` 模式 (跨平台 portalocker/fcntl).

        P0 修复 (2026-11-07)：锁内 **read-modify-write**。
        原实现只写**内存副本** ⇒ 并发时后写者直接覆盖先写者
        (CI 实测 5 进程 × 100 条只剩 100 条, 丢 400 条)。
        现在：加锁 → 读磁盘最新内容 → 与内存合并去重 → rotation → 原子写回。
        """
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        self._acquire_lock("w")
        try:
            # 锁内 read-modify-write: 先读磁盘最新内容 (可能含其它进程刚写的)
            disk_signals = []
            if history_file.exists():
                try:
                    with open(history_file, "r", encoding="utf-8") as f:
                        disk_signals = [SignalRecord(**s) for s in json.load(f)]
                except Exception as e:
                    logger.warning(f"合并前读取信号历史失败（以内存为准）: {e}")

            merged = self._merge_disk_signals(disk_signals)
            merged = self._apply_rotation(signals=merged)  # Borrow 4: rotation 在 write 之前

            # 原子写入: 先写 .tmp 再 replace (Borrow 2 P0-2 治本)
            tmp_file = history_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps([asdict(s) for s in merged], indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            _atomic_replace(tmp_file, history_file)
            self.signals = merged  # 写成功后才同步内存（失败时保持原样以便回滚）
        except IOError:
            # 锁获取失败已在 _acquire_lock 中记录, 不重复处理
            raise
        except Exception as e:
            logger.error(f"保存信号历史失败: {e}")
            raise  # re-raise 让 record_signal 能捕获并回滚 in-memory
        finally:
            self._release_lock()

    def _acquire_lock(self, mode: str = "r") -> None:
        """获取文件锁 (fail-fast: 失败时抛 IOError)

        Borrow 2: 跨平台文件锁, 模板 position_manager.py:76-102.
        锁超时 30s (vs position_manager 5s) 避免 cache miss 死循环 (P2-4).
        """
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        # P0 修复 (2026-11-07)：锁**伴生 .lock 文件**，不再锁目标文件本身。
        # 原因：portalocker.Lock 持有目标文件句柄，而 **Windows 不允许 os.replace()
        # 覆盖一个仍被打开的文件** ⇒ 原实现（锁目标文件 + replace 目标文件）
        # 在 Windows 上必然 PermissionError [WinError 5]。
        lock_file = SIGNAL_LOG_DIR / "signal_history.lock"
        if not lock_file.parent.exists():
            lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _HAS_PORTALOCKER:
                self._lock_file = portalocker.Lock(
                    str(lock_file), timeout=30,
                    fail_when_locked=False  # 30s 等待, 不 fail-fast
                )
                self._lock_file.acquire()
            elif _HAS_FCNTL:
                self._lock_file = open(lock_file, "a+")
                fcntl.flock(
                    self._lock_file.fileno(),
                    fcntl.LOCK_EX if mode == "w" else fcntl.LOCK_SH,
                )
            else:
                logger.warning("无可用文件锁库 (portalocker/fcntl), 并发写入可能冲突")
                self._lock_file = None
        except Exception as e:
            logger.error(f"获取信号历史文件锁失败: {e}")
            if getattr(self, "_lock_file", None):
                try:
                    self._lock_file.close()
                except Exception as inner_e:
                    logger.debug(f"[signal_tracker] 文件锁关闭异常: {inner_e}")
                self._lock_file = None
            raise IOError(f"无法获取信号历史文件锁: {history_file}") from e

    def _release_lock(self) -> None:
        """释放文件锁 (Borrow 2 跨平台)"""
        try:
            if _HAS_PORTALOCKER and getattr(self, "_lock_file", None):
                self._lock_file.release()
                self._lock_file = None
            elif _HAS_FCNTL and getattr(self, "_lock_file", None):
                try:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    self._lock_file.close()
                    self._lock_file = None
        except Exception as e:
            logger.warning(f"释放信号历史文件锁异常: {e}")

    def record_signal(self, code: str, signal: str, confidence: float,
                      date: str = None, source: str = "ic_score", ic_score: float = 0,
                      skip_save: bool = False):
        """记录一条信号

        Args:
            skip_save: True 时只 append in-memory 不写盘 (record_batch 批量写用)
        """
        if date is None:
            date = _last_trading_day()

        record = SignalRecord(
            date=date,
            code=code,
            signal=signal,
            confidence=confidence,
            source=source,
            ic_score=ic_score,
        )
        self.signals.append(record)
        if not skip_save:
            try:
                self._save_history()
            except Exception:
                self.signals.pop()  # P1-5: 回滚 in-memory, 保持与 disk 一致
                raise
        # Borrow 1.1b: cache 失效, 让下次 get_cached_tracker() 重建
        invalidate_tracker_cache()

    def record_batch(self, signals: List[Dict], date: str = None):
        """批量记录信号 (P2-4: 批量写一次, 不是每条都 lock+rename)"""
        snapshot = list(self.signals)  # 快照, 失败时 restore
        for s in signals:
            self.record_signal(
                code=s.get("code", ""),
                signal=s.get("signal", "neutral"),
                confidence=s.get("confidence", 0),
                date=date,
                source=s.get("source", "ic_score"),
                ic_score=s.get("ic_score", 0),
                skip_save=True,
            )
        try:
            self._save_history()
        except Exception:
            self.signals = snapshot  # P2-4: 回滚全部
            raise
        invalidate_tracker_cache()

    def update_actual_returns(self):
        """更新信号的实际收益率（需要事后数据）"""
        from lib.data.data_layer import get_stock_hist_multi_source

        updated = 0
        for record in self.signals:
            if record.actual_return_5d != 0:
                continue  # 已更新

            try:
                signal_date = pd.to_datetime(record.date)
                today = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
                days_since = (today - signal_date).days

                if days_since < 5:
                    continue  # 还不够 5 天

                df = get_stock_hist_multi_source(record.code, days=days_since + 30, source="auto")
                if df.empty:
                    continue

                df["date"] = pd.to_datetime(df["date"])
                signal_row = df[df["date"] >= signal_date].head(1)
                if signal_row.empty:
                    continue

                entry_price = float(signal_row["close"].iloc[0])

                # 计算 5/10/20 日收益
                for period, attr in [(5, "actual_return_5d"), (10, "actual_return_10d"), (20, "actual_return_20d")]:
                    future_rows = df[df["date"] > signal_date].head(period)
                    if len(future_rows) >= period:
                        exit_price = float(future_rows["close"].iloc[-1])
                        ret = (exit_price - entry_price) / entry_price
                        setattr(record, attr, round(ret, 4))

                # 判断信号是否正确
                if record.signal == "bullish":
                    record.is_correct = record.actual_return_5d > 0
                elif record.signal == "bearish":
                    record.is_correct = record.actual_return_5d < 0

                updated += 1
            except Exception as e:
                logger.debug(f"更新 {record.code} 收益失败: {e}")
                continue

        if updated > 0:
            self._save_history()
            logger.info(f"更新了 {updated} 条信号的实际收益")

        return updated

    def evaluate_signals(self, days: int = 30, min_confidence: float = 0.3) -> Dict:
        """评估信号质量

        Args:
            days: 评估最近 N 天的信号
            min_confidence: 最低置信度阈值

        Returns:
            dict with evaluation metrics
        """
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [s for s in self.signals if s.date >= cutoff and s.confidence >= min_confidence]

        if not recent:
            return {
                "total_signals": 0,
                "accuracy": 0,
                "avg_return_5d": 0,
                "profit_loss_ratio": 0,
            }

        # 过滤有实际收益的
        with_return = [s for s in recent if s.actual_return_5d != 0]

        if not with_return:
            return {
                "total_signals": len(recent),
                "evaluated": 0,
                "accuracy": 0,
                "avg_return_5d": 0,
                "profit_loss_ratio": 0,
            }

        # 准确率
        bullish = [s for s in with_return if s.signal == "bullish"]
        correct = [s for s in bullish if s.is_correct]
        accuracy = len(correct) / len(bullish) if bullish else 0

        # 平均收益
        returns_5d = [s.actual_return_5d for s in with_return]
        avg_return = float(np.mean(returns_5d))

        # 盈亏比
        wins = [r for r in returns_5d if r > 0]
        losses = [abs(r) for r in returns_5d if r < 0]
        avg_win = float(np.mean(wins)) if wins else 0
        avg_loss = float(np.mean(losses)) if losses else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # 按置信度分层
        high_conf = [s for s in with_return if s.confidence >= 0.6]
        high_conf_accuracy = 0
        if high_conf:
            high_bullish = [s for s in high_conf if s.signal == "bullish"]
            high_correct = [s for s in high_bullish if s.is_correct]
            high_conf_accuracy = len(high_correct) / len(high_bullish) if high_bullish else 0

        return {
            "total_signals": len(recent),
            "evaluated": len(with_return),
            "accuracy": round(accuracy, 4),
            "avg_return_5d": round(avg_return, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "profit_loss_ratio": round(profit_loss_ratio, 3),
            "high_confidence_accuracy": round(high_conf_accuracy, 4),
            "bullish_count": len(bullish),
            "bearish_count": len([s for s in with_return if s.signal == "bearish"]),
        }

    def get_recent_signals(self, days: int = 7) -> List[Dict]:
        """获取最近的信号"""
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        recent = [s for s in self.signals if s.date >= cutoff]
        return [asdict(s) for s in recent[-50:]]


# ============================================================================
# Borrow 1 借鉴 (TradingAgents memory.py get_past_context)
# P0-3 修复: module-level cache 避免 dashboard 100 RPS 反复 _load_history
# ============================================================================

def build_past_context(
    ticker: str,
    signals: List[SignalRecord],
    n_same: int = 5,
    n_cross: int = 3,
) -> str:
    """从 SignalTracker.signals 构建 past context string (AgentPanel/LLM prompt 注入用).

    输出格式仿 TradingMemoryLog.get_past_context (lib/llm_utils/memory.py:95-131):
    - "History {ticker} (newest first):"
    - "{date} {signal} conf={confidence} 5d_return={actual_return_5d} ..."
    - "Recent cross-ticker lessons:"
    无历史返回空字符串.

    Borrow 1 vs TradingMemoryLog.get_past_context:
    - TradingAgents 数据源: markdown log (含 reflection prose)
    - stock-scanner 数据源: SignalRecord dataclass (结构化字段 + actual_return_*)
    - 输出格式借鉴但不复用 lib/llm_utils/memory.py 的 markdown parser 依赖
    """
    if not signals:
        return ""

    # 过滤 same-ticker 历史 (audit P2-2 警告: actual_return_5d=0 视为待回填跳过)
    same_ticker = [
        s for s in reversed(signals)
        if s.code == ticker and s.actual_return_5d != 0
    ][:n_same]

    # 过滤 cross-ticker lessons (is_correct=False 提供反思价值)
    cross_ticker = [
        s for s in reversed(signals)
        if s.code != ticker and s.is_correct is False and s.actual_return_5d != 0
    ][:n_cross]

    parts = []
    if same_ticker:
        parts.append(f"History {ticker} (newest first):")
        for s in same_ticker:
            parts.append(
                f"  {s.date} {s.signal} conf={s.confidence:.2f} "
                f"5d_return={s.actual_return_5d:+.2%} correct={s.is_correct}"
            )
    if cross_ticker:
        parts.append("\nRecent cross-ticker lessons:")
        for s in cross_ticker:
            parts.append(
                f"  {s.code} {s.date} {s.signal} 5d_return={s.actual_return_5d:+.2%}"
            )
    return "\n".join(parts)


# Module-level cache (P0-3 修复): 避免 dashboard 100 RPS 时反复 _load_history
_tracker_cache = {"tracker": None, "loaded_at": 0.0}


def get_cached_tracker(ttl_seconds: int = 30) -> "SignalTracker":
    """返回 cached SignalTracker instance, ttl_seconds 内复用, 过期重建.

    实证 (P0-3): dashboard 100 RPS 投票 + SignalTracker._load_history 每次读 disk
    + parse JSON × N records → 性能 regression. 30s TTL 平衡 freshness 和性能.
    """
    import time as _time
    now = _time.time()
    cached = _tracker_cache["tracker"]
    if cached is not None and (now - _tracker_cache["loaded_at"]) < ttl_seconds:
        return cached
    tracker = SignalTracker()
    _tracker_cache["tracker"] = tracker
    _tracker_cache["loaded_at"] = now
    return tracker


def invalidate_tracker_cache() -> None:
    """让 cache 失效 (e.g. record_signal 后). 由 SignalTracker.record_signal 内部调用."""
    _tracker_cache["tracker"] = None
    _tracker_cache["loaded_at"] = 0.0


# ============================================================================
# Borrow 3: LLM reflection prose (仿 TradingAgents memory.py:14-29)
# ============================================================================

def build_reflection_prompt(signals: List[SignalRecord], ticker: str) -> str:
    """生成反思 prompt, 供 LLM 生成 2-3 句 narrative insight.

    仿 TradingAgents memory.py:14-29 Reflector._get_log_reflection_prompt:
    - 输入: past context (build_past_context 输出)
    - 输出: prompt string, 供 _call_deepseek / _call_kimi 调用
    - 无历史返回空字符串
    """
    past = build_past_context(ticker, signals)
    if not past:
        return ""
    return (
        f"你是量化分析师, 回顾对 {ticker} 的历史判断.\n"
        f"{past}\n"
        f"写 2-3 句反思: 方向判断对了吗? 哪个维度对了/错了? 下次类似情况怎么改?\n"
        f"要求: 具体引用数据 (如 5d_return=+5%), 不要泛泛而谈."
    )
