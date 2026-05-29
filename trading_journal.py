"""
trading_journal.py - V2 交易日志系统

用途：
- 记录每笔交易的全生命周期
- 追踪市场状态与个股行为
- 复盘分析
- 长期统计（哪些行情/板块/参数最适合系统）

使用方式：
    from trading_journal import TradingJournal, TradeLog
    journal = TradingJournal()
    journal.open(
        code="000858",
        name="五粮液",
        market_regime="TREND_UP",
        entry_price=122.50,
        entry_date="2024-09-25",
        signal_type="MA5_MA21_GC",
        atr_at_entry=3.5,      # ATR%
        stop_loss=119.00,       # ATR*2 止损位
        position_pct=10,        # 仓位%
        notes="放量突破，HS300同步走强"
    )
    # ... 持有期间记录预警 ...
    journal.close(
        exit_price=135.80,
        exit_date="2024-10-22",
        exit_reason="timeout",  # ATR止损/结构破坏/EMA21预警/timeout/市场状态恶化
        actual_hold_days=20,
        pnl_pct=10.86,
        max_runup_pct=18.2,    # 最大浮盈
        max_drawdown_pct=-2.1,  # 最大浮亏
        notes="持有20天如期到期，趋势完好，板块持续强势"
    )
"""

import json
import csv
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field, asdict
from enum import Enum


class ExitReason(Enum):
    ATR_STOP = "atr_stop"           # ATR止损触发
    TRAILING_STOP = "trailing_stop" # 移动止损触发
    STRUCTURAL_BREAK = "structural_break"  # 结构破坏
    EMA21_WARNING = "ema21_warning"  # EMA21预警（未触发止损）
    EMA21_CONFIRM = "ema21_confirm" # EMA21确认退出
    TIMEOUT = "timeout"             # 20天到期
    MARKET_REGIME_CHANGE = "market_regime_change"  # 市场状态恶化
    MANUAL = "manual"               # 手动平仓

class MarketRegime(Enum):
    TREND_UP = "TREND_UP"
    MEAN_REVERSION = "MEAN_REVERSION"
    COMPRESSION = "COMPRESSION"
    TREND_DOWN = "TREND_DOWN"
    UNKNOWN = "UNKNOWN"


@dataclass
class TradeEntry:
    """开仓记录"""
    code: str
    name: str
    market_regime: str
    entry_price: float
    entry_date: str
    signal_type: str           # MA5_MA21_GC / VOLUME_BREAK / ...
    atr_at_entry: float        # ATR%
    stop_loss: float           # 止损价
    position_pct: float       # 仓位%
    notes: str = ""
    # 情绪与纪律（G6）
    emotion_state: str = ""      # Emotion: 冷静/焦虑/贪婪/恐惧
    rule_violated: bool = False  # Rule violated?
    violation_desc: str = ""     # Violation description
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class TradeClose:
    """平仓记录"""
    exit_price: float
    exit_date: str
    exit_reason: str
    actual_hold_days: int
    pnl_pct: float
    max_runup_pct: float      # 最大浮盈%
    max_drawdown_pct: float   # 最大浮亏%
    notes: str = ""
    closed_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class WarningEvent:
    """持有期间预警事件"""
    date: str
    event_type: str            # EMA21预警 / 缩量 / 板块退潮 / HS300破位
    price: float
    description: str
    action_taken: str          # 观望 / 减仓 / 准备止损


@dataclass
class TradeRecord:
    """
    完整交易记录（开仓 → 持有 → 平仓）
    """
    id: str                    # UUID
    status: str                # "open" / "closed"

    # 开仓
    code: str
    name: str
    market_regime: str
    entry_price: float
    entry_date: str
    signal_type: str
    atr_at_entry: float
    stop_loss: float
    position_pct: float
    position_value: float       # 实际持仓金额
    entry_notes: str = ""

    # 持有期间
    warnings: List[Dict] = field(default_factory=list)

    # 平仓
    exit_price: Optional[float] = None
    exit_date: Optional[str] = None
    exit_reason: Optional[str] = None
    actual_hold_days: int = 0
    pnl_pct: float = 0.0
    pnl_value: float = 0.0
    max_runup_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    exit_notes: str = ""

    # 元数据
    created_at: str = ""
    closed_at: Optional[str] = None


@dataclass
class TradeAttribution:
    """Trade attribution (design: why profit/loss)"""
    market_contribution: float  # Market regime contribution
    sector_contribution: float  # Sector contribution
    signal_quality: float       # Signal quality at entry
    timing_score: float         # Entry timing score
    main_factor: str            # Main P&L factor


def analyze_trade(record: Dict) -> TradeAttribution:
    """Automatic attribution analysis for a closed trade.

    Decomposes P&L into market, sector, signal quality, and timing components.
    Requires record to have: market_regime, pnl_pct, signal_type, max_runup_pct,
    max_drawdown_pct, entry_date, exit_date.
    """
    pnl = record.get("pnl_pct", 0.0)
    regime = record.get("market_regime", "UNKNOWN")
    signal = record.get("signal_type", "")

    # 1. Market contribution: heuristic based on regime
    regime_bias = {
        "TREND_UP": 0.4,
        "MEAN_REVERSION": 0.0,
        "COMPRESSION": -0.1,
        "TREND_DOWN": -0.3,
        "UNKNOWN": 0.0,
    }
    market_contribution = pnl * regime_bias.get(regime, 0.0)

    # 2. Sector contribution: residual after market (placeholder — needs sector data)
    sector_contribution = 0.0  # TODO: needs sector index data

    # 3. Signal quality: reward well-known signals more
    signal_quality_map = {
        "MA5_MA21_GC": 0.8,
        "VOLUME_BREAK": 0.7,
        "MACD_GC": 0.6,
        "KDJ_CROSS": 0.5,
    }
    signal_quality = signal_quality_map.get(signal, 0.5)

    # 4. Timing score: based on max_runup vs realized pnl
    max_runup = record.get("max_runup_pct", 0.0)
    if max_runup > 0 and pnl > 0:
        # How much of the available profit was captured
        timing_score = min(pnl / max_runup, 1.0) if max_runup > 0 else 0.5
    else:
        timing_score = 0.3  # Losing trade gets low timing score

    # 5. Main factor: which factor contributed most (absolute)
    factors = {
        "市场": abs(market_contribution),
        "信号质量": abs(signal_quality * pnl * 0.3),
        "择时": abs(timing_score * pnl * 0.3),
    }
    # If sector data available, include it
    if sector_contribution != 0:
        factors["板块"] = abs(sector_contribution)

    main_factor = max(factors, key=factors.get) if factors else "未知"

    return TradeAttribution(
        market_contribution=round(market_contribution, 4),
        sector_contribution=round(sector_contribution, 4),
        signal_quality=round(signal_quality, 4),
        timing_score=round(timing_score, 4),
        main_factor=main_factor,
    )


class TradingJournal:
    """
    交易日志管理器

    数据存储：
    - JSON: 每笔交易的完整详情（cache/trades/{id}.json）
    - CSV: 所有交易的摘要（cache/trades/trade_log.csv）
    """

    def __init__(self, data_dir: str = "cache/trades"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trades_file = self.data_dir / "trade_log.csv"
        self._ensure_csv_header()

    def _ensure_csv_header(self):
        """确保 CSV 文件有表头"""
        if not self.trades_file.exists():
            header = [
                "id", "status", "code", "name", "market_regime",
                "entry_date", "entry_price", "signal_type",
                "atr_at_entry", "stop_loss", "position_pct",
                "exit_date", "exit_price", "exit_reason",
                "hold_days", "pnl_pct", "pnl_value",
                "max_runup", "max_drawdown",
                "entry_notes", "exit_notes", "warnings_json",
                "created_at", "closed_at"
            ]
            with open(self.trades_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=header)
                writer.writeheader()

    def _new_id(self) -> str:
        return datetime.now().strftime("%Y%m%d%H%M%S")

    def _serialize_warnings(self, warnings: List) -> str:
        return json.dumps(warnings, ensure_ascii=False)

    # ============================================================
    # 核心 API
    # ============================================================

    def open(
        self,
        code: str,
        name: str,
        market_regime: str,
        entry_price: float,
        entry_date: str,
        signal_type: str,
        atr_at_entry: float,
        stop_loss: float,
        position_pct: float,
        position_value: float,
        notes: str = "",
    ) -> str:
        """
        记录新开仓

        Returns:
            trade_id
        """
        trade_id = self._new_id()

        record = TradeRecord(
            id=trade_id,
            status="open",
            code=code,
            name=name,
            market_regime=market_regime,
            entry_price=entry_price,
            entry_date=entry_date,
            signal_type=signal_type,
            atr_at_entry=atr_at_entry,
            stop_loss=stop_loss,
            position_pct=position_pct,
            position_value=position_value,
            entry_notes=notes,
            warnings=[],
            created_at=datetime.now().isoformat(),
        )

        # 保存 JSON
        trade_file = self.data_dir / f"{trade_id}.json"
        with open(trade_file, "w", encoding="utf-8") as f:
            json.dump(asdict(record), f, ensure_ascii=False, indent=2)

        # 追加 CSV
        self._append_csv(record)

        return trade_id

    def add_warning(
        self,
        trade_id: str,
        date: str,
        event_type: str,
        price: float,
        description: str,
        action_taken: str = "观望",
    ):
        """持有期间添加预警事件"""
        trade_file = self.data_dir / f"{trade_id}.json"
        if not trade_file.exists():
            return

        with open(trade_file, "r", encoding="utf-8") as f:
            record = json.load(f)

        warning = {
            "date": date,
            "event_type": event_type,
            "price": price,
            "description": description,
            "action_taken": action_taken,
        }
        record["warnings"].append(warning)

        with open(trade_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    def close(
        self,
        trade_id: str,
        exit_price: float,
        exit_date: str,
        exit_reason: str,
        actual_hold_days: int,
        pnl_pct: float,
        pnl_value: float,
        max_runup_pct: float,
        max_drawdown_pct: float,
        notes: str = "",
    ):
        """记录平仓"""
        trade_file = self.data_dir / f"{trade_id}.json"
        if not trade_file.exists():
            return

        with open(trade_file, "r", encoding="utf-8") as f:
            record = json.load(f)

        record["status"] = "closed"
        record["exit_price"] = exit_price
        record["exit_date"] = exit_date
        record["exit_reason"] = exit_reason
        record["actual_hold_days"] = actual_hold_days
        record["pnl_pct"] = pnl_pct
        record["pnl_value"] = pnl_value
        record["max_runup_pct"] = max_runup_pct
        record["max_drawdown_pct"] = max_drawdown_pct
        record["exit_notes"] = notes
        record["closed_at"] = datetime.now().isoformat()

        with open(trade_file, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

        # 更新 CSV
        self._update_csv(record)

    def get_trade(self, trade_id: str) -> Optional[Dict]:
        """获取单笔交易详情"""
        trade_file = self.data_dir / f"{trade_id}.json"
        if not trade_file.exists():
            return None
        with open(trade_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_open_trades(self) -> List[Dict]:
        """获取所有持仓中交易"""
        records = []
        for f in self.data_dir.glob("*.json"):
            with open(f, "r", encoding="utf-8") as fp:
                rec = json.load(fp)
                if rec.get("status") == "open":
                    records.append(rec)
        return records

    def get_all_trades(self, status: Optional[str] = None) -> List[Dict]:
        """获取所有交易，支持按 status 过滤"""
        records = []
        for f in self.data_dir.glob("*.json"):
            with open(f, "r", encoding="utf-8") as fp:
                rec = json.load(fp)
                if status is None or rec.get("status") == status:
                    records.append(rec)
        return sorted(records, key=lambda x: x.get("entry_date", ""), reverse=True)

    def get_summary(self) -> Dict[str, Any]:
        """获取汇总统计"""
        closed = self.get_all_trades(status="closed")
        if not closed:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "avg_pnl": 0,
                "profit_factor": 0,
                "max_drawdown": 0,
            }

        pnls = [t["pnl_pct"] for t in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total_ret = sum(pnls)
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0

        # 按退出原因
        by_reason = {}
        for t in closed:
            r = t.get("exit_reason", "unknown")
            if r not in by_reason:
                by_reason[r] = {"count": 0, "pnls": []}
            by_reason[r]["count"] += 1
            by_reason[r]["pnls"].append(t["pnl_pct"])

        # 按市场状态
        by_regime = {}
        for t in closed:
            r = t.get("market_regime", "UNKNOWN")
            if r not in by_regime:
                by_regime[r] = {"count": 0, "pnls": []}
            by_regime[r]["count"] += 1
            by_regime[r]["pnls"].append(t["pnl_pct"])

        return {
            "total_trades": len(closed),
            "open_trades": len(self.get_open_trades()),
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(sum(pnls) / len(pnls), 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(pf, 2),
            "total_return": round(total_ret, 1),
            "by_exit_reason": {
                r: {
                    "count": v["count"],
                    "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 2),
                    "win_rate": round(sum(1 for p in v["pnls"] if p > 0) / len(v["pnls"]) * 100, 1),
                }
                for r, v in by_reason.items()
            },
            "by_market_regime": {
                r: {
                    "count": v["count"],
                    "avg_pnl": round(sum(v["pnls"]) / len(v["pnls"]), 2),
                    "win_rate": round(sum(1 for p in v["pnls"] if p > 0) / len(v["pnls"]) * 100, 1),
                }
                for r, v in by_regime.items()
            },
        }

    # ============================================================
    # CSV 操作
    # ============================================================

    def _append_csv(self, record: Dict):
        record_dict = asdict(record) if hasattr(record, '__dataclass_fields__') else record
        row = self._to_csv_row(record_dict)
        with open(self.trades_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writerow(row)

    def _update_csv(self, record: Dict):
        """重写整个 CSV（简单但有效）"""
        all_trades = self.get_all_trades()
        with open(self.trades_file, "w", newline="", encoding="utf-8") as f:
            header = [
                "id", "status", "code", "name", "market_regime",
                "entry_date", "entry_price", "signal_type",
                "atr_at_entry", "stop_loss", "position_pct",
                "exit_date", "exit_price", "exit_reason",
                "hold_days", "pnl_pct", "pnl_value",
                "max_runup", "max_drawdown",
                "entry_notes", "exit_notes", "warnings_json",
                "created_at", "closed_at"
            ]
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            for t in all_trades:
                writer.writerow(self._to_csv_row(t))

    def _to_csv_row(self, record: Dict) -> Dict:
        warnings = record.get("warnings", [])
        if isinstance(warnings, list):
            warnings_json = json.dumps(warnings, ensure_ascii=False)
        else:
            warnings_json = str(warnings)

        return {
            "id": record.get("id", ""),
            "status": record.get("status", ""),
            "code": record.get("code", ""),
            "name": record.get("name", ""),
            "market_regime": record.get("market_regime", ""),
            "entry_date": record.get("entry_date", ""),
            "entry_price": record.get("entry_price", ""),
            "signal_type": record.get("signal_type", ""),
            "atr_at_entry": record.get("atr_at_entry", ""),
            "stop_loss": record.get("stop_loss", ""),
            "position_pct": record.get("position_pct", ""),
            "exit_date": record.get("exit_date", ""),
            "exit_price": record.get("exit_price", ""),
            "exit_reason": record.get("exit_reason", ""),
            "hold_days": record.get("actual_hold_days", 0),
            "pnl_pct": record.get("pnl_pct", 0),
            "pnl_value": record.get("pnl_value", 0),
            "max_runup": record.get("max_runup_pct", 0),
            "max_drawdown": record.get("max_drawdown_pct", 0),
            "entry_notes": record.get("entry_notes", ""),
            "exit_notes": record.get("exit_notes", ""),
            "warnings_json": warnings_json,
            "created_at": record.get("created_at", ""),
            "closed_at": record.get("closed_at", ""),
        }

    # ============================================================
    # 报告
    # ============================================================

    def print_summary(self):
        """打印交易摘要"""
        s = self.get_summary()
        print(f"\n{'='*60}")
        print(f"V2 交易日志摘要")
        print(f"{'='*60}")
        print(f"总交易笔数: {s['total_trades']}")
        print(f"当前持仓: {s['open_trades']}")
        print(f"胜率: {s['win_rate']}%")
        print(f"平均盈亏: {s['avg_pnl']}%")
        print(f"盈亏比: {s['profit_factor']}")
        print(f"累计收益: {s['total_return']}%")

        print(f"\n按退出方式:")
        for r, v in s.get("by_exit_reason", {}).items():
            print(f"  {r}: {v['count']}次, 均={v['avg_pnl']}%, 胜率={v['win_rate']}%")

        print(f"\n按市场状态:")
        for r, v in s.get("by_market_regime", {}).items():
            print(f"  {r}: {v['count']}次, 均={v['avg_pnl']}%, 胜率={v['win_rate']}%")

    def print_log(self, limit: int = 20):
        """打印交易记录"""
        all_trades = self.get_all_trades()
        print(f"\n{'='*90}")
        print(f"{'日期':<12} {'代码':<8} {'名称':<10} {'入场':<8} {'出场':<8} {'天数':>4} {'盈亏%':>7} {'退出':<20} {'市场':<15}")
        print(f"{'='*90}")
        for t in all_trades[:limit]:
            exit_price = t.get('exit_price')
            exit_reason = t.get('exit_reason') or ''
            print(
                f"{t.get('entry_date', ''):<12} "
                f"{t.get('code', ''):<8} "
                f"{t.get('name', ''):<10} "
                f"{t.get('entry_price', 0):>8.2f} "
                f"{exit_price if exit_price else '持仓':>8} "
                f"{t.get('actual_hold_days', 0):>4} "
                f"{t.get('pnl_pct', 0):>7.2f} "
                f"{exit_reason:<20} "
                f"{t.get('market_regime', ''):<15}"
            )
        print(f"{'='*90}")
        print(f"共 {len(all_trades)} 笔交易")


# ============================================================
# 使用示例
# ============================================================

if __name__ == "__main__":
    journal = TradingJournal()

    # 示例：开仓
    trade_id = journal.open(
        code="000858",
        name="五粮液",
        market_regime="TREND_UP",
        entry_price=122.50,
        entry_date="2024-09-25",
        signal_type="MA5_MA21_GC",
        atr_at_entry=3.5,
        stop_loss=119.00,
        position_pct=10.0,
        position_value=100000.0,
        notes="放量突破，HS300同步走强，板块白酒联动",
    )
    print(f"开仓记录: {trade_id}")

    # 示例：持有期间预警
    journal.add_warning(
        trade_id=trade_id,
        date="2024-09-30",
        event_type="EMA21_WARNING",
        price=134.50,
        description="股价回踩EMA21，暂未破，观察",
        action_taken="观望",
    )

    # 示例：平仓
    journal.close(
        trade_id=trade_id,
        exit_price=135.80,
        exit_date="2024-10-22",
        exit_reason="timeout",
        actual_hold_days=20,
        pnl_pct=10.86,
        pnl_value=10860.0,
        max_runup_pct=18.2,
        max_drawdown_pct=-2.1,
        notes="持有20天如期到期，趋势完好，板块持续强势，预期实现",
    )

    # 打印摘要
    journal.print_summary()
    journal.print_log()
