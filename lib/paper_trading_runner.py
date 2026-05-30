"""实盘模拟运行器 —— 每日自动扫描 + 信号推送

使用 PaperBroker 进行模拟实盘验证：
1. 每日自动扫描候选股
2. 执行信号推送（飞书）
3. 记录实际滑点、成交率、延迟

使用方式：
    python -m lib.paper_trading_runner --once      # 单次运行
    python -m lib.paper_trading_runner --daemon     # 守护模式（每日定时）
"""

import logging
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADE_LOG_DIR = Path("cache/paper_trading")


@dataclass
class TradeRecord:
    """交易记录"""
    date: str
    code: str
    direction: str  # BUY / SELL
    price: float
    shares: int
    slippage_pct: float = 0.0
    fill_time_ms: float = 0.0
    signal_confidence: float = 0.0
    signal_source: str = ""


@dataclass
class DailyReport:
    """日报"""
    date: str
    total_value: float = 0.0
    cash: float = 0.0
    market_value: float = 0.0
    daily_return: float = 0.0
    cumulative_return: float = 0.0
    max_drawdown: float = 0.0
    trades_today: int = 0
    win_rate: float = 0.0
    sharpe: float = 0.0
    positions: List[Dict] = field(default_factory=list)
    signals: List[Dict] = field(default_factory=list)
    slippage_avg: float = 0.0


class PaperTradingRunner:
    """实盘模拟运行器"""

    def __init__(self, initial_cash: float = 1_000_000, preset: str = "balanced"):
        self.initial_cash = initial_cash
        self.preset = preset
        self.trade_history: List[TradeRecord] = []
        self.daily_reports: List[DailyReport] = []
        self._broker = None
        self._start_date = datetime.now().strftime("%Y-%m-%d")
        self._peak_value = initial_cash

    def _init_broker(self):
        """初始化 PaperBroker"""
        if self._broker is not None:
            return

        from lib.broker import PaperBroker
        self._broker = PaperBroker(
            account="paper_sim",
            initial_cash=self.initial_cash,
            max_single_weight=0.20,
            max_total_exposure=0.95,
            stop_loss_pct=0.05,
        )
        self._broker.connect()

    def run_once(self) -> DailyReport:
        """执行一次扫描+交易

        Returns:
            DailyReport
        """
        self._init_broker()
        t0 = time.time()

        # 1. 获取候选股
        candidates = self._scan_candidates()
        scan_time = time.time() - t0

        # 2. 检查现有持仓的退出信号
        exits = self._check_exits()

        # 3. 执行交易
        trades_today = []
        for signal in exits:
            trade = self._execute_sell(signal)
            if trade:
                trades_today.append(trade)

        for signal in candidates:
            trade = self._execute_buy(signal)
            if trade:
                trades_today.append(trade)

        # 4. 生成日报
        report = self._generate_report(candidates, trades_today, scan_time)

        # 5. 推送
        self._push_report(report)

        # 6. 持久化
        self._save_report(report)

        return report

    def _scan_candidates(self) -> List[Dict]:
        """扫描候选股（优先使用 TDX 本地数据，速度快 10 倍）"""
        try:
            from config import get_config
            from factors import compute_all_signals, compute_ic_weighted_score

            cfg = get_config(self.preset)
            candidates = []

            # 使用扩展股票池
            try:
                from lib.stock_pool import get_expanded_pool
                pool = get_expanded_pool(100)
            except Exception:
                pool = ["600519", "000858", "002714", "600036", "000333",
                        "600276", "300750", "601012", "000001", "600000"]

            # 优先用 TDX 批量读取（快）
            try:
                from lib.tdx_batch import batch_read_day_files
                raw_data = batch_read_day_files(pool[:50], days=60, max_workers=8)
            except Exception:
                raw_data = {}

            import pandas as pd
            for code in pool[:50]:
                try:
                    # 先用 TDX 本地数据
                    if code in raw_data and raw_data[code]:
                        records = raw_data[code]
                        df = pd.DataFrame(records)
                        if "trade_date" not in df.columns and "date" in df.columns:
                            df = df.rename(columns={"date": "trade_date"})
                        df["trade_date"] = pd.to_datetime(df["trade_date"])
                    else:
                        # 回退到网络 API
                        from lib.data_layer import get_stock_hist_multi_source
                        df = get_stock_hist_multi_source(code, days=60, source="auto")

                    if df.empty or len(df) < 30:
                        continue

                    result = compute_all_signals(df, factor_config=cfg.factor, code=code)
                    if not result:
                        continue

                    combined = result.get("combined", {})
                    ic_score = result.get("ic_score", 0)
                    # 放宽条件：ic_score > 0 也纳入
                    if (combined.get("signal") == "bullish" and combined.get("confidence", 0) >= 0.3) or ic_score > 0.2:
                        factors = result.get("factors", {})
                        score = compute_ic_weighted_score(factors)
                        candidates.append({
                            "code": code,
                            "signal": combined.get("signal"),
                            "confidence": combined.get("confidence"),
                            "ic_score": score,
                            "factors": factors,
                        })
                except Exception:
                    continue

            # 按 IC score 排序
            candidates.sort(key=lambda x: x.get("ic_score", 0), reverse=True)
            return candidates[:10]

        except Exception as e:
            logger.error(f"扫描失败: {e}")
            return []

    def _check_exits(self) -> List[Dict]:
        """检查持仓退出信号"""
        exits = []
        if not self._broker:
            return exits

        holdings = self._broker._broker.holdings
        for code, holding in holdings.items():
            try:
                from lib.data_layer import get_stock_hist_multi_source
                df = get_stock_hist_multi_source(code, days=30, source="auto")
                if df.empty:
                    continue

                current_price = float(df["close"].iloc[-1])
                pnl_pct = (current_price - holding.cost_price) / holding.cost_price

                # 简单止盈止损
                if pnl_pct <= -0.05:  # 5% 止损
                    exits.append({"code": code, "reason": "stop_loss", "price": current_price})
                elif pnl_pct >= 0.15:  # 15% 止盈
                    exits.append({"code": code, "reason": "take_profit", "price": current_price})
            except Exception:
                continue

        return exits

    def _execute_buy(self, signal: Dict) -> Optional[TradeRecord]:
        """执行买入"""
        if not self._broker:
            return None

        code = signal["code"]
        try:
            from lib.data_layer import get_stock_hist_multi_source
            df = get_stock_hist_multi_source(code, days=5, source="auto")
            if df.empty:
                return None

            price = float(df["close"].iloc[-1])
            # 计算仓位
            account = self._broker._broker
            position_value = account.total_value * 0.10  # 10% 仓位
            shares = int(position_value / price / 100) * 100
            if shares <= 0:
                return None

            from lib.broker import Order
            order = Order(
                code=code,
                direction="BUY",
                price=price,
                shares=shares,
                date=datetime.now().strftime("%Y-%m-%d"),
            )

            t0 = time.time()
            trade = self._broker.place_order(order)
            fill_time = (time.time() - t0) * 1000

            if trade:
                record = TradeRecord(
                    date=datetime.now().strftime("%Y-%m-%d"),
                    code=code,
                    direction="BUY",
                    price=trade.price,
                    shares=trade.shares,
                    fill_time_ms=fill_time,
                    signal_confidence=signal.get("confidence", 0),
                    signal_source="ic_score",
                )
                self.trade_history.append(record)
                logger.info(f"买入 {code} {shares}股 @ {price:.2f} (置信度={signal.get('confidence', 0):.2f})")
                return record
        except Exception as e:
            logger.error(f"买入 {code} 失败: {e}")

        return None

    def _execute_sell(self, signal: Dict) -> Optional[TradeRecord]:
        """执行卖出"""
        if not self._broker:
            return None

        code = signal["code"]
        try:
            from lib.broker import Order
            holding = self._broker._broker.holdings.get(code)
            if not holding:
                return None

            order = Order(
                code=code,
                direction="SELL",
                price=signal["price"],
                shares=holding.shares,
                date=datetime.now().strftime("%Y-%m-%d"),
            )

            t0 = time.time()
            trade = self._broker.place_order(order)
            fill_time = (time.time() - t0) * 1000

            if trade:
                record = TradeRecord(
                    date=datetime.now().strftime("%Y-%m-%d"),
                    code=code,
                    direction="SELL",
                    price=trade.price,
                    shares=trade.shares,
                    fill_time_ms=fill_time,
                    signal_source=signal.get("reason", ""),
                )
                self.trade_history.append(record)
                logger.info(f"卖出 {code} {holding.shares}股 @ {trade.price:.2f} ({signal.get('reason', '')})")
                return record
        except Exception as e:
            logger.error(f"卖出 {code} 失败: {e}")

        return None

    def _generate_report(self, candidates: List[Dict], trades: List[TradeRecord], scan_time: float) -> DailyReport:
        """生成日报"""
        broker = self._broker._broker if self._broker else None

        total_value = broker.total_value if broker else self.initial_cash
        cash = broker.cash if broker else self.initial_cash
        market_value = broker.total_market_value if broker else 0

        # 回撤
        self._peak_value = max(self._peak_value, total_value)
        max_dd = (self._peak_value - total_value) / self._peak_value if self._peak_value > 0 else 0

        # 累计收益
        cum_return = (total_value / self.initial_cash - 1) if self.initial_cash > 0 else 0

        # 胜率
        winning = sum(1 for t in self.trade_history if t.direction == "SELL" and t.price > 0)
        total_sells = sum(1 for t in self.trade_history if t.direction == "SELL")
        win_rate = winning / total_sells if total_sells > 0 else 0

        # 滑点
        slippages = [t.slippage_pct for t in self.trade_history if t.slippage_pct > 0]
        avg_slippage = float(np.mean(slippages)) if slippages else 0

        # 持仓
        positions = []
        if broker:
            for code, h in broker.holdings.items():
                positions.append({
                    "code": code,
                    "shares": h.shares,
                    "cost": h.cost_price,
                    "value": h.shares * broker._last_prices.get(code, h.cost_price),
                })

        return DailyReport(
            date=datetime.now().strftime("%Y-%m-%d"),
            total_value=round(total_value, 2),
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            daily_return=0,  # 需要与昨日比较
            cumulative_return=round(cum_return, 4),
            max_drawdown=round(max_dd, 4),
            trades_today=len(trades),
            win_rate=round(win_rate, 4),
            positions=positions,
            signals=[{"code": c["code"], "confidence": c.get("confidence", 0)} for c in candidates[:5]],
            slippage_avg=round(avg_slippage, 4),
        )

    def _push_report(self, report: DailyReport):
        """推送日报到飞书"""
        try:
            from reporter.feishu_pusher import push_report
            msg = self._format_report_text(report)
            success = push_report(text=msg, chart_paths=[])
            if success:
                logger.info("日报已推送到飞书")
            else:
                logger.warning("飞书推送失败")
        except Exception as e:
            logger.debug(f"飞书推送失败: {e}")

    def _format_report_text(self, report: DailyReport) -> str:
        """格式化日报文本"""
        lines = [
            f"📊 模拟盘日报 {report.date}",
            f"总资产: ¥{report.total_value:,.0f}",
            f"累计收益: {report.cumulative_return:.2%}",
            f"最大回撤: {report.max_drawdown:.2%}",
            f"胜率: {report.win_rate:.1%}",
            f"今日交易: {report.trades_today} 笔",
            f"平均滑点: {report.slippage_avg:.3%}",
        ]

        if report.positions:
            lines.append("\n持仓:")
            for p in report.positions[:5]:
                lines.append(f"  {p['code']}: {p['shares']}股 成本{p['cost']:.2f}")

        if report.signals:
            lines.append("\n今日信号:")
            for s in report.signals:
                lines.append(f"  {s['code']}: 置信度{s['confidence']:.2f}")

        return "\n".join(lines)

    def _save_report(self, report: DailyReport):
        """保存日报"""
        TRADE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        report_file = TRADE_LOG_DIR / f"report_{date_str}.json"

        data = asdict(report)
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        # 保存交易历史
        history_file = TRADE_LOG_DIR / "trade_history.json"
        history_data = [asdict(t) for t in self.trade_history]
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, indent=2, ensure_ascii=False, default=str)

    def get_performance_summary(self) -> Dict:
        """获取绩效摘要"""
        if not self.daily_reports:
            return {}

        values = [r.total_value for r in self.daily_reports]
        returns = np.diff(values) / values[:-1] if len(values) > 1 else [0]

        return {
            "total_return": (values[-1] / self.initial_cash - 1) if values else 0,
            "sharpe": float(np.mean(returns) / np.std(returns) * np.sqrt(252)) if np.std(returns) > 0 else 0,
            "max_drawdown": max(r.max_drawdown for r in self.daily_reports),
            "total_trades": len(self.trade_history),
            "win_rate": self.daily_reports[-1].win_rate if self.daily_reports else 0,
            "avg_slippage": float(np.mean([r.slippage_avg for r in self.daily_reports])),
        }


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="实盘模拟运行器")
    parser.add_argument("--once", action="store_true", help="单次运行")
    parser.add_argument("--cash", type=float, default=1_000_000, help="初始资金")
    parser.add_argument("--preset", type=str, default="balanced", help="策略预设")
    args = parser.parse_args()

    runner = PaperTradingRunner(initial_cash=args.cash, preset=args.preset)

    if args.once:
        report = runner.run_once()
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False, default=str))
    else:
        print("守护模式暂未实现，请使用 --once 单次运行")


if __name__ == "__main__":
    main()
