"""多品种回测引擎 —— 通道均值回归 + 宏观择时 + 指数过滤

设计原则：
  - 股票现货：通道均值回归（kc_pos < 0.08 买入，> 0.92 卖出）
  - 指数择时：均线交叉（20日均线方向）
  - 宏观过滤：SHIBOR/SLF 利率 > threshold 时降仓50%
  - 差异化风控：股票 ATR止损、指数跟踪止损、期权 Theta 管理

用法:
    python scripts/backtest_multi.py                          # 默认
    python scripts/backtest_multi.py --stocks 20 --years 3 # 指定
    python scripts/backtest_multi.py --with-macro           # 开启宏观过滤
    python scripts/backtest_multi.py --index-timing         # 开启指数择时
"""
import sys
import os
import argparse
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.indicators import (
    calculate_keltner_channels,
    calculate_xosi_channels,
    get_channel_signals,
    KC_LENGTH, XOSI_LONG,
)

PARSER = argparse.ArgumentParser(description="多品种回测引擎")
PARSER.add_argument("--stocks", type=int, default=20, help="股票数量")
PARSER.add_argument("--years", type=int, default=3, help="回测年数")
PARSER.add_argument("--top-volume", action="store_true", help="按成交额选股")
PARSER.add_argument("--with-macro", action="store_true", help="开启宏观过滤")
PARSER.add_argument("--index-timing", action="store_true", help="开启指数择时")
PARSER.add_argument("--macro-threshold", type=float, default=2.5,
                    help="SHIBOR 过滤阈值（%%）")
PARSER.add_argument("--output", type=str, default="", help="输出CSV路径")
PARSER.add_argument("--tdx-path", type=str, default="D:/TDX/vipdoc", help="TDX数据路径")
ARGS = PARSER.parse_args()


# ========== 数据加载 ==========

def load_stock_candidates(n: int = 20, by_volume: bool = False) -> list:
    """加载候选股票列表"""
    tdx_path = ARGS.tdx_path or "D:/TDX/vipdoc"
    import struct

    sh_dir = Path(tdx_path) / "sh" / "lday"
    sz_dir = Path(tdx_path) / "sz" / "lday"

    stock_volumes = []

    for day_dir in [sh_dir, sz_dir]:
        if not day_dir.exists():
            continue
        market = "sh" if "sh" in str(day_dir) else "sz"

        if market == "sh":
            patterns = ["sh6*.day", "sh9*.day"]
        else:
            patterns = ["sz0*.day", "sz3*.day", "sz2*.day"]

        for pattern in patterns:
            for fpath in day_dir.glob(pattern):
                code = fpath.stem
                try:
                    with open(fpath, "rb") as f:
                        f.seek(-32, 2)
                        data = f.read(32)
                        if len(data) == 32:
                            vol = struct.unpack("<I", data[24:28])[0] * 0.01
                            stock_volumes.append((code, vol, market))
                except Exception:
                    pass

    if by_volume and stock_volumes:
        stock_volumes.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _, _ in stock_volumes[:n * 2]][:n]

    import random
    sh_files = list(sh_dir.glob("sh6*.day"))[:100]
    sz_files = list(sz_dir.glob("sz0*.day"))[:100]
    all_files = sh_files + sz_files
    random.seed(42)
    random.shuffle(all_files)
    return [f.stem for f in all_files[:n]]


def load_kline(code: str, days: int = 1000) -> pd.DataFrame:
    """从本地TDX文件加载K线"""
    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    code = code.strip()
    if code.startswith(("sh", "sz", "bj", "SH", "SZ", "BJ")):
        market_str = code[:2].lower()
        code = code[2:].zfill(6)
    else:
        code = code.zfill(6)
        # 用 helper 替代手写市场判断（修复 bj 静默丢失）
        from lib.market_utils import detect_market
        market_str = detect_market(code)
        if market_str == "unknown":
            return pd.DataFrame()

    tdx_path = ARGS.tdx_path or "D:/TDX/vipdoc"
    file_path = Path(tdx_path) / market_str / "lday" / f"{market_str}{code}.day"

    if not file_path.exists():
        return pd.DataFrame()

    try:
        reader = TdxDailyBarReader()
        df = reader.get_df(str(file_path))
        if df.empty:
            return pd.DataFrame()
        df = df.reset_index()
        df = df.rename(columns={"date": "trade_date"})
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if len(df) > days:
            df = df.tail(days)
        return df[["trade_date", "open", "high", "low", "close", "volume"]].copy()
    except Exception:
        return pd.DataFrame()


# ========== 核心回测引擎 ==========

class MultiAssetBacktest:
    """多品种回测引擎"""

    def __init__(self, initial_cash: float = 1_000_000.0,
                 macro_threshold: float = 2.5,
                 enable_macro_filter: bool = False,
                 enable_index_timing: bool = False):
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.macro_threshold = macro_threshold  # SHIBOR 过滤阈值（%）
        self.enable_macro_filter = enable_macro_filter
        self.enable_index_timing = enable_index_timing

        # 持仓：{code: {type, shares, buy_price, buy_date, ...}}
        self.holdings: dict = {}

        # 历史
        self.trade_history: list = []
        self.daily_values: list = []

        # 参数
        self.stop_loss_atr = 1.5
        self.max_hold_days = 80
        self.partial_take_pct = 0.5

        # 宏观数据（按日期索引）
        self._macro_df: pd.DataFrame = pd.DataFrame()
        self._index_df: pd.DataFrame = pd.DataFrame()
        self._index_ma20: float = None  # 上证20日均线

    # ---------- 数据加载 ----------

    def load_index(self, index_code: str = "000001", days: int = 250):
        """加载指数数据（上证为主）"""
        from lib.data_layer import get_index_hist
        self._index_df = get_index_hist(index_code, days=days)
        if not self._index_df.empty:
            self._index_df = self._index_df.rename(columns={"trade_date": "date", "close": "index_close"})
            self._index_df["ma20"] = self._index_df["index_close"].rolling(20).mean()

    def load_macro(self, indicator: str = "SHIBOR_1W", days: int = 60):
        """加载宏观指标"""
        from lib.data_layer import get_macro_hist_local
        # 尝试多种宏观指标
        for ind in [f"{indicator}", f"38#9_{indicator}", indicator]:
            df = get_macro_hist_local(ind, days=days)
            if not df.empty:
                self._macro_df = df.rename(columns={"close": "macro_value"})
                break

    def load_stocks(self, codes: list, years: int = 3):
        """加载股票数据"""
        self._stock_data = {}
        for code in codes:
            df = load_kline(code, days=years * 365 + 100)
            if df is not None and not df.empty and len(df) >= 60:
                self._stock_data[code] = df
        return len(self._stock_data)

    # ---------- 信号计算 ----------

    def _get_macro_signal(self, date) -> float:
        """宏观状态：返回 position_multiplier（0~1）"""
        if self._macro_df.empty:
            return 1.0
        row = self._macro_df[self._macro_df["date"] <= date]
        if row.empty:
            return 1.0
        latest = row.iloc[-1]["macro_value"]
        # 利率高于阈值 → 降仓
        if latest > self.macro_threshold:
            return 0.5
        return 1.0

    def _get_index_signal(self, date) -> bool:
        """指数择时：MA20 趋势方向"""
        if self._index_df.empty:
            return True  # 无指数数据，默认多头
        df = self._index_df[self._index_df["date"] <= date]
        if len(df) < 25:
            return True
        latest = df.iloc[-1]
        prev = df.iloc[-5]  # 5天前均线
        return latest["ma20"] > prev["ma20"] if not pd.isna(latest["ma20"]) else True

    def _get_stock_signals(self, date) -> list:
        """获取股票买入候选"""
        candidates = []
        for code, df in self._stock_data.items():
            if code in self.holdings:
                continue
            hist = df[df["trade_date"] <= date]
            if len(hist) < max(KC_LENGTH, XOSI_LONG) + 10:
                continue

            row = hist[hist["trade_date"] == date]
            if row.empty:
                continue
            current_price = float(row["close"].iloc[-1])

            try:
                kc = calculate_keltner_channels(hist)
                xosi = calculate_xosi_channels(hist)
                signals = get_channel_signals(hist, kc, xosi)
            except Exception:
                continue

            if signals is None or signals.empty:
                continue

            latest = signals.iloc[-1]
            signal = latest.get("signal", 0)
            confidence = latest.get("confidence", 0)

            if signal != 1 or confidence < 0.92:
                continue

            atr_val = float(kc["atr"].iloc[-1]) if not kc.empty and "atr" in kc.columns and not pd.isna(kc["atr"].iloc[-1]) else 0
            kc_mid = float(kc["kc_mid"].iloc[-1]) if not kc.empty and not pd.isna(kc["kc_mid"].iloc[-1]) else current_price * 1.03

            candidates.append({
                "code": code,
                "type": "stock",
                "price": current_price,
                "confidence": confidence,
                "atr": atr_val,
                "stop_price": current_price - atr_val * self.stop_loss_atr if atr_val > 0 else current_price * 0.90,
                "take_profit_price": kc_mid,
            })
        candidates.sort(key=lambda x: x["confidence"], reverse=True)
        return candidates

    # ---------- 持仓管理 ----------

    def _check_stops(self, date, current_prices: dict, position_mult: float):
        """检查止损止盈"""
        to_remove = []
        for code, h in list(self.holdings.items()):
            if h["type"] != "stock":
                continue
            current_price = current_prices.get(code)
            if current_price is None:
                continue

            pnl_pct = (current_price - h["buy_price"]) / h["buy_price"]
            date_ts = pd.Timestamp(date) if not isinstance(date, pd.Timestamp) else date
            hold_days = (date_ts - h["buy_date"]).days if isinstance(h["buy_date"], pd.Timestamp) else 0

            sold = False

            # ATR 止损
            if h.get("stop_price") and current_price <= h["stop_price"]:
                proceeds = h["shares_remaining"] * current_price * position_mult
                self.cash += proceeds
                self.trade_history.append({
                    "code": code, "date": str(date.date()) if hasattr(date, "date") else str(date),
                    "direction": "SELL", "price": current_price,
                    "shares": h["shares_remaining"],
                    "reason": "ATR止损", "pnl_pct": pnl_pct,
                })
                to_remove.append(code)
                sold = True
            # 硬止损 5%
            elif pnl_pct <= -0.05:
                proceeds = h["shares_remaining"] * current_price * position_mult
                self.cash += proceeds
                self.trade_history.append({
                    "code": code, "date": str(date.date()) if hasattr(date, "date") else str(date),
                    "direction": "SELL", "price": current_price,
                    "shares": h["shares_remaining"],
                    "reason": "硬止损5%", "pnl_pct": pnl_pct,
                })
                to_remove.append(code)
                sold = True
            # 止盈：触及 KC 中轨
            elif h.get("take_profit_price") and current_price >= h["take_profit_price"]:
                tp_price = h["take_profit_price"]
                partial_exited = h.get("partial_exited", False)
                if not partial_exited:
                    sell_shares = int(h["shares_remaining"] * self.partial_take_pct)
                    if sell_shares > 0:
                        proceeds = sell_shares * current_price * position_mult
                        self.cash += proceeds
                        self.trade_history.append({
                            "code": code, "date": str(date.date()) if hasattr(date, "date") else str(date),
                            "direction": "SELL", "price": current_price, "shares": sell_shares,
                            "reason": "分批止盈(半)", "pnl_pct": (current_price - h["buy_price"]) / h["buy_price"],
                        })
                        h["shares_remaining"] -= sell_shares
                        h["partial_exited"] = True
                        h["stop_price"] = h["buy_price"]  # 盈亏平衡止损
                        h["rolling_high"] = current_price
                else:
                    rh = h.get("rolling_high", current_price)
                    h["rolling_high"] = max(rh, current_price)
                    trailing_stop = h["rolling_high"] * 0.95
                    if current_price <= trailing_stop:
                        proceeds = h["shares_remaining"] * current_price * position_mult
                        self.cash += proceeds
                        self.trade_history.append({
                            "code": code, "date": str(date.date()) if hasattr(date, "date") else str(date),
                            "direction": "SELL", "price": current_price,
                            "shares": h["shares_remaining"],
                            "reason": "移动止损", "pnl_pct": pnl_pct,
                        })
                        to_remove.append(code)
                        sold = True
            # 超时
            elif hold_days >= self.max_hold_days:
                proceeds = h["shares_remaining"] * current_price * position_mult
                self.cash += proceeds
                self.trade_history.append({
                    "code": code, "date": str(date.date()) if hasattr(date, "date") else str(date),
                    "direction": "SELL", "price": current_price,
                    "shares": h["shares_remaining"],
                    "reason": "超时", "pnl_pct": pnl_pct,
                })
                to_remove.append(code)
                sold = True

            if sold and code in self.holdings:
                del self.holdings[code]

        for code in to_remove:
            if code in self.holdings:
                del self.holdings[code]

    def _enter_positions(self, date, candidates: list, position_mult: float):
        """建仓"""
        date_ts = pd.Timestamp(date) if not isinstance(date, pd.Timestamp) else date
        max_positions = 10
        if len(self.holdings) >= max_positions:
            return

        for cand in candidates[:5]:
            if len(self.holdings) >= max_positions:
                break
            if cand["code"] in self.holdings:
                continue
            # 10% 仓位
            shares = int(self.cash * 0.1 / cand["price"])
            if shares <= 0:
                continue
            cost = shares * cand["price"]
            self.cash -= cost
            self.holdings[cand["code"]] = {
                "type": "stock",
                "shares": shares,
                "shares_remaining": shares,
                "buy_price": cand["price"],
                "buy_date": date_ts,
                "stop_price": cand["stop_price"],
                "take_profit_price": cand["take_profit_price"],
            }

    # ---------- 主循环 ----------

    def run(self) -> pd.DataFrame:
        """运行回测"""
        if not hasattr(self, "_stock_data") or len(self._stock_data) < 5:
            print("[ERROR] 股票数据不足")
            return pd.DataFrame()

        # 公共日期范围
        all_dates = set()
        for df in self._stock_data.values():
            all_dates.update(df["trade_date"].tolist())
        dates = sorted(all_dates)
        print(f"  公共日期范围: {dates[0].date()} ~ {dates[-1].date()} ({len(dates)} 个交易日)\n")

        print("[2/3] 运行回测...")

        for i, date in enumerate(dates):
            date_str = str(date.date()) if hasattr(date, "date") else str(date)

            # 1. 宏观状态
            position_mult = self._get_macro_signal(date) if self.enable_macro_filter else 1.0

            # 2. 指数择时
            index_bull = self._get_index_signal(date) if self.enable_index_timing else True

            # 3. 当日收盘价
            current_prices = {}
            for code, df in self._stock_data.items():
                row = df[df["trade_date"] == date]
                if not row.empty:
                    current_prices[code] = float(row["close"].iloc[-1])

            # 4. 计算总权益
            stock_value = sum(
                h["shares_remaining"] * current_prices.get(code, h["buy_price"])
                for code, h in self.holdings.items()
                if h["type"] == "stock"
            )
            total_value = self.cash + stock_value
            self.daily_values.append({"date": date_str, "total_value": total_value, "position_mult": position_mult})

            # 5. 止损止盈检查
            if position_mult > 0:  # 宏观允许持仓时才检查止损
                self._check_stops(date, current_prices, position_mult)

            # 6. 买入信号（只在指数多头时买入）
            if index_bull and position_mult > 0 and len(self.holdings) < 10:
                candidates = self._get_stock_signals(date)
                self._enter_positions(date, candidates, position_mult)

        print("[3/3] 计算绩效...")
        return self._compute_metrics()

    def _compute_metrics(self) -> pd.DataFrame:
        if not self.daily_values:
            return pd.DataFrame()

        perf_df = pd.DataFrame(self.daily_values)
        perf_df["nav"] = perf_df["total_value"] / self.initial_cash

        nav = perf_df["nav"].values
        total_return = nav[-1] - 1 if len(nav) > 0 else 0

        n_years = len(self.daily_values) / 252
        annualized = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

        peak = np.maximum.accumulate(nav)
        drawdown = (nav - peak) / peak
        max_drawdown = drawdown.min()

        daily_returns = np.diff(nav) / nav[:-1]
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

        if self.trade_history:
            trades_df = pd.DataFrame(self.trade_history)
            n_trades = len(trades_df)
            wins = trades_df[trades_df["pnl_pct"] > 0]
            win_rate = len(wins) / n_trades if n_trades > 0 else 0
            avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
            losses = trades_df[trades_df["pnl_pct"] <= 0]
            avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
            exit_counts = trades_df["reason"].value_counts().to_dict()
        else:
            n_trades, win_rate, avg_win, avg_loss, exit_counts = 0, 0, 0, 0, {}

        print(f"\n{'='*60}")
        mode_tags = []
        if self.enable_macro_filter:
            mode_tags.append(f"宏观过滤(>{self.macro_threshold}%)")
        if self.enable_index_timing:
            mode_tags.append("指数择时")
        mode_str = " + ".join(mode_tags) if mode_tags else "纯股票通道"
        print(f"多品种回测 | {mode_str}")
        print(f"回测期:       {self.daily_values[0]['date']} ~ {self.daily_values[-1]['date']} ({n_years:.1f}年)")
        print(f"总收益:       {total_return:+.2%}")
        print(f"年化收益:     {annualized:+.2%}")
        print(f"最大回撤:     {max_drawdown:.2%}")
        print(f"夏普比:       {sharpe:.2f}")
        print(f"交易次数:     {n_trades}")
        print(f"胜率:         {win_rate:.1%}")
        if n_trades > 0:
            print(f"平均盈利:     {avg_win:+.2%}")
            print(f"平均亏损:     {avg_loss:+.2%}")
            print(f"退出原因:     {exit_counts}")

        return perf_df


# ========== 主程序 ==========

if __name__ == "__main__":
    codes = load_stock_candidates(n=ARGS.stocks, by_volume=ARGS.top_volume)
    print(f"候选股票: {codes[:10]}...")

    engine = MultiAssetBacktest(
        macro_threshold=ARGS.macro_threshold,
        enable_macro_filter=ARGS.with_macro,
        enable_index_timing=ARGS.index_timing,
    )

    print("\n[1/3] 加载数据...")

    # 加载指数
    if ARGS.index_timing or ARGS.with_macro:
        print("  加载指数数据...")
        engine.load_index("000001", days=250)

    # 加载宏观
    if ARGS.with_macro:
        print("  加载宏观数据...")
        engine.load_macro("SHIBOR_1W", days=60)

    # 加载股票
    n_loaded = engine.load_stocks(codes, years=ARGS.years)
    print(f"  成功加载: {n_loaded}/{len(codes)} 只股票")

    perf_df = engine.run()

    # 自动存档
    if not perf_df.empty:
        from datetime import datetime as _dt
        import json as _json
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(__file__).resolve().parent.parent / "cache" / "backtest_results"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"multi_{ts}.json"

        nav = perf_df["nav"].values
        daily_returns = np.diff(nav) / nav[:-1]
        n_years = len(perf_df) / 252
        total_return = nav[-1] - 1 if len(nav) > 0 else 0
        annualized = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0
        peak = np.maximum.accumulate(nav)
        drawdown = (nav - peak) / peak
        max_drawdown = drawdown.min()
        sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

        trades_df = pd.DataFrame(engine.trade_history) if engine.trade_history else pd.DataFrame()
        exit_counts = trades_df["reason"].value_counts().to_dict() if not trades_df.empty else {}

        result = {
            "timestamp": ts,
            "args": {
                "stocks": ARGS.stocks,
                "years": ARGS.years,
                "with_macro": ARGS.with_macro,
                "index_timing": ARGS.index_timing,
                "macro_threshold": ARGS.macro_threshold,
                "top_volume": ARGS.top_volume,
            },
            "metrics": {
                "total_return": float(total_return),
                "annual_return": float(annualized),
                "max_drawdown": float(max_drawdown),
                "sharpe": float(sharpe),
                "n_trades": len(trades_df),
                "win_rate": float(len(trades_df[trades_df["pnl_pct"] > 0]) / len(trades_df)) if not trades_df.empty else 0,
                "avg_win": float(trades_df[trades_df["pnl_pct"] > 0]["pnl_pct"].mean()) if not trades_df.empty and len(trades_df[trades_df["pnl_pct"] > 0]) > 0 else 0,
                "avg_loss": float(trades_df[trades_df["pnl_pct"] <= 0]["pnl_pct"].mean()) if not trades_df.empty and len(trades_df[trades_df["pnl_pct"] <= 0]) > 0 else 0,
                "exit_counts": exit_counts,
            },
            "nav": list(nav[-100:]),  # 最近100个净值点
        }
        with open(out_path, "w", encoding="utf-8") as f:
            _json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已自动存档: {out_path}")

    if ARGS.output and not perf_df.empty:
        perf_df.to_csv(ARGS.output, index=False)
        print(f"净值曲线已保存: {ARGS.output}")
