"""结构化交易日志 —— 记录每笔交易的完整上下文"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

TRADE_LOG_DIR = Path("cache/trade_logs")


@dataclass
class TradeLogEntry:
    """单笔交易的完整上下文"""
    timestamp: str
    code: str
    action: str  # buy / sell
    price: float
    shares: int
    # 信号上下文
    signal_source: str = ""
    signal_confidence: float = 0
    ic_score: float = 0
    # 市场状态
    market_regime: str = ""
    sector_score: float = 0
    # 因子
    factors: Dict = None
    # 执行细节
    order_type: str = ""
    slippage: float = 0
    commission: float = 0
    # 结果（卖出时）
    pnl_pct: float = 0
    hold_days: int = 0
    exit_reason: str = ""

    def __post_init__(self):
        if self.factors is None:
            self.factors = {}


class TradeLogger:
    """交易日志管理器"""

    def __init__(self):
        TRADE_LOG_DIR.mkdir(parents=True, exist_ok=True)

    def log_trade(self, entry: TradeLogEntry):
        """记录一笔交易"""
        date_str = datetime.now().strftime("%Y%m%d")
        log_file = TRADE_LOG_DIR / f"trades_{date_str}.jsonl"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n")
            logger.debug(f"交易日志: {entry.action} {entry.code} @ {entry.price}")
        except Exception as e:
            logger.warning(f"交易日志写入失败: {e}")

    def get_recent_trades(self, days: int = 7) -> list:
        """获取最近 N 天的交易日志"""
        from datetime import timedelta
        trades = []
        cutoff = datetime.now() - timedelta(days=days)

        for f in sorted(TRADE_LOG_DIR.glob("trades_*.jsonl"), reverse=True):
            try:
                date_str = f.stem.replace("trades_", "")
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date < cutoff:
                    break
                with open(f, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            trades.append(json.loads(line))
            except Exception:
                continue

        return trades[-100:]  # 最近 100 笔
