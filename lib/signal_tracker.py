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
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_LOG_DIR = Path("cache/signals")


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
        """保存信号历史"""
        history_file = SIGNAL_LOG_DIR / "signal_history.json"
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump([asdict(s) for s in self.signals], f, indent=2, ensure_ascii=False, default=str)

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
