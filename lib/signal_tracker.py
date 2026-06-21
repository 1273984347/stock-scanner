"""历史信号追踪器 —— 记录并回测推送信号的实际表现

功能：
1. 每日记录扫描信号到 JSON 文件
2. 事后回测信号的实际收益
3. 计算信号准确率、平均收益、盈亏比

使用方式：
    from lib.signal_tracker import SignalTracker
    tracker = SignalTracker()
    tracker.record_signal("600519", "bullish", 0.8, "2026-05-30")
    report = tracker.evaluate_signals(days=30)
"""

import json
import logging
import sys
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

    def _save_history(self):
        """保存信号历史 (Borrow 2 atomic write: tmp + replace + 跨平台文件锁)

        治本 (P0-2): 防崩溃损坏 JSON + 5 进程并发覆盖 (api.py 4 endpoint + cron).
        模板来自 position_manager.py:134-169 `_save` 模式 (跨平台 portalocker/fcntl).
        """
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        self._acquire_lock("w")
        try:
            # 原子写入: 先写 .tmp 再 replace (Borrow 2 P0-2 治本)
            tmp_file = history_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps([asdict(s) for s in self.signals], indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp_file.replace(history_file)  # os.replace() 原子 rename
        except IOError:
            # 锁获取失败已在 _acquire_lock 中记录, 不重复处理
            raise
        except Exception as e:
            logger.error(f"保存信号历史失败: {e}")
        finally:
            self._release_lock()

    def _acquire_lock(self, mode: str = "r") -> None:
        """获取文件锁 (fail-fast: 失败时抛 IOError)

        Borrow 2: 跨平台文件锁, 模板 position_manager.py:76-102.
        锁超时 30s (vs position_manager 5s) 避免 cache miss 死循环 (P2-4).
        """
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        if not history_file.exists():
            history_file.parent.mkdir(parents=True, exist_ok=True)
            history_file.touch(exist_ok=True)
        try:
            if _HAS_PORTALOCKER:
                self._lock_file = portalocker.Lock(
                    str(history_file), timeout=30,
                    fail_when_locked=False  # 30s 等待, 不 fail-fast
                )
                self._lock_file.acquire()
            elif _HAS_FCNTL:
                self._lock_file = open(history_file, "r+" if mode == "r" else "w")
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
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_file = None
        except Exception as e:
            logger.warning(f"释放信号历史文件锁异常: {e}")

    def record_signal(self, code: str, signal: str, confidence: float,
                      date: str = None, source: str = "ic_score", ic_score: float = 0):
        """记录一条信号"""
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
        self._save_history()
        # Borrow 1.1b: cache 失效, 让下次 get_cached_tracker() 重建
        invalidate_tracker_cache()

    def record_batch(self, signals: List[Dict], date: str = None):
        """批量记录信号"""
        for s in signals:
            self.record_signal(
                code=s.get("code", ""),
                signal=s.get("signal", "neutral"),
                confidence=s.get("confidence", 0),
                date=date,
                source=s.get("source", "ic_score"),
                ic_score=s.get("ic_score", 0),
            )

    def update_actual_returns(self):
        """更新信号的实际收益率（需要事后数据）"""
        from lib.data_layer import get_stock_hist_multi_source

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
