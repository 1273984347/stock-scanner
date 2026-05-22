"""通道策略回测 - 均值回归版本（kc_pos < 0.08 买入，> 0.92 卖出）

用法:
    python scripts/backtest_channel.py                    # 默认：20只股票，3年数据
    python scripts/backtest_channel.py --stocks 30      # 30只股票
    python scripts/backtest_channel.py --years 5        # 5年数据
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

PARSER = argparse.ArgumentParser(description="通道策略回测（均值回归版）")
PARSER.add_argument("--stocks", type=int, default=20, help="股票数量")
PARSER.add_argument("--years", type=int, default=3, help="回测年数")
PARSER.add_argument("--top-volume", action="store_true", help="按成交额选股")
PARSER.add_argument("--output", type=str, default="", help="输出CSV路径")
PARSER.add_argument("--tdx-path", type=str, default="D:/TDX/vipdoc", help="TDX数据路径")
ARGS = PARSER.parse_args()


def load_stock_candidates(n: int = 20, by_volume: bool = False) -> list:
    """加载候选股票列表"""
    tdx_path = ARGS.tdx_path or "D:/TDX/vipdoc"
    import struct
    from pathlib import Path

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
        # code already includes market prefix (e.g., "sz000100")
        return [c for c, _, _ in stock_volumes[:n * 2]][:n]

    # 默认：随机
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

    original_code = code
    code = code.strip()
    if code.startswith(("sh", "sz", "SH", "SZ")):
        market_str = code[:2].lower()
        code = code[2:].zfill(6)
    else:
        code = code.zfill(6)
        if code.startswith(("6", "5", "9")):
            market_str = "sh"
        elif code.startswith(("0", "3", "2", "4", "8")):
            market_str = "sz"
        else:
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

        # 只保留需要的年数
        if len(df) > days:
            df = df.tail(days)

        return df[["trade_date", "open", "high", "low", "close", "volume"]].copy()
    except Exception:
        return pd.DataFrame()


def run_backtest(codes: list, years: int = 3) -> pd.DataFrame:
    """运行通道策略回测

    均值回归策略：
    - 买入信号：kc_pos < 0.08（价格在下轨8%以内）
    - 卖出信号：kc_pos > 0.92（价格在上轨92%以上）
    - 持仓：最多40天
    - 止损：ATR * 3 / 硬止损 -5%
    - 止盈：KC中轨（分批止盈50% + 移动止损5%）

    """
    print(f"\n{'='*60}")
    print(f"通道策略回测（均值回归版）")
    print(f"股票数: {len(codes)} | 回测期: {years}年")
    print(f"{'='*60}\n")

    # 加载所有数据
    print("[1/3] 加载数据...")
    stock_data = {}
    for i, code in enumerate(codes):
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(codes)}")
        df = load_kline(code, days=years * 365)
        if df is not None and not df.empty and len(df) >= 60:
            stock_data[code] = df

    print(f"  成功加载: {len(stock_data)}/{len(codes)} 只股票\n")

    if len(stock_data) < 5:
        print("[ERROR] 有效股票太少")
        return pd.DataFrame()

    # 获取公共日期范围
    all_dates = set()
    for df in stock_data.values():
        all_dates.update(df["trade_date"].tolist())
    dates = sorted(all_dates)
    print(f"  公共日期范围: {dates[0].date()} ~ {dates[-1].date()} ({len(dates)} 个交易日)\n")

    # ATR参数（优化后：均值回归策略，止盈看中轨，止损放宽）
    initial_cash = 1_000_000.0
    cash = initial_cash
    # holdings: {code: {shares, buy_price, buy_date, stop_price, take_profit_price, shares_remaining}}
    holdings = {}
    trade_history = []
    daily_values = []

    stop_loss_atr = 1.5   # 1.5x ATR 止损
    max_hold_days = 40    # 40天持仓
    partial_take_pct = 0.5  # 分批止盈：先卖一半

    print("[2/3] 运行回测...")

    for i, date in enumerate(dates):
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)

        # 获取当日收盘价
        current_prices = {}
        for code, df in stock_data.items():
            row = df[df["trade_date"] == date]
            if not row.empty:
                current_prices[code] = float(row["close"].iloc[-1])

        total_value = cash + sum(
            h["shares_remaining"] * current_prices.get(code, h["buy_price"])
            for code, h in holdings.items()
        )
        daily_values.append({"date": date_str, "total_value": total_value})

        # 止损止盈检查
        for code in list(holdings.keys()):
            h = holdings[code]
            current_price = current_prices.get(code)
            if current_price is None:
                continue

            pnl_pct = (current_price - h["buy_price"]) / h["buy_price"]

            # ATR止损
            if h.get("stop_price") and current_price <= h["stop_price"]:
                proceeds = h["shares_remaining"] * current_price
                cash += proceeds
                trade_history.append({
                    "code": code, "date": date_str, "direction": "SELL",
                    "price": current_price, "shares": h["shares_remaining"],
                    "reason": "ATR止损", "pnl_pct": pnl_pct,
                })
                del holdings[code]
                continue

            # 硬止损（5%）
            if pnl_pct <= -0.05:
                proceeds = h["shares_remaining"] * current_price
                cash += proceeds
                trade_history.append({
                    "code": code, "date": date_str, "direction": "SELL",
                    "price": current_price, "shares": h["shares_remaining"],
                    "reason": "硬止损5%", "pnl_pct": pnl_pct,
                })
                del holdings[code]
                continue

            # 分批止盈：第一次触及 KC 中轨 → 卖出50%，止损移至盈亏平衡
            tp_price = h.get("take_profit_price")
            if tp_price and current_price >= tp_price:
                partial_exited = h.get("partial_exited", False)
                if not partial_exited:
                    # 第一次到中轨：卖出一半
                    sell_shares = int(h["shares_remaining"] * partial_take_pct)
                    if sell_shares > 0:
                        proceeds = sell_shares * current_price
                        cash += proceeds
                        trade_history.append({
                            "code": code, "date": date_str, "direction": "SELL",
                            "price": current_price, "shares": sell_shares,
                            "reason": "分批止盈(半)", "pnl_pct": (current_price - h["buy_price"]) / h["buy_price"],
                        })
                        h["shares_remaining"] = h["shares_remaining"] - sell_shares
                        h["partial_exited"] = True
                        h["stop_price"] = h["buy_price"]  # 盈亏平衡止损
                        h["rolling_high"] = current_price  # 追踪最高价
                else:
                    # 分批止盈后：移动止损，追踪rolling_high
                    rh = h.get("rolling_high", current_price)
                    h["rolling_high"] = max(rh, current_price)
                    # 移动止损：最高价回落3%则出局
                    trailing_stop = h["rolling_high"] * 0.95
                    if current_price <= trailing_stop:
                        proceeds = h["shares_remaining"] * current_price
                        cash += proceeds
                        trade_history.append({
                            "code": code, "date": date_str, "direction": "SELL",
                            "price": current_price, "shares": h["shares_remaining"],
                            "reason": "移动止损", "pnl_pct": pnl_pct,
                        })
                        del holdings[code]
                        continue

            # 超时
            hold_days = (date - h["buy_date"]).days if isinstance(h["buy_date"], pd.Timestamp) else 0
            if hold_days >= max_hold_days:
                proceeds = h["shares_remaining"] * current_price
                cash += proceeds
                trade_history.append({
                    "code": code, "date": date_str, "direction": "SELL",
                    "price": current_price, "shares": h["shares_remaining"],
                    "reason": "超时", "pnl_pct": pnl_pct,
                })
                del holdings[code]

        # 买入信号
        if len(holdings) < 10:  # 最多10持仓
            candidates = []

            for code, df in stock_data.items():
                if code in holdings:
                    continue

                hist = df[df["trade_date"] <= date]
                if len(hist) < max(KC_LENGTH, XOSI_LONG) + 10:
                    continue

                current_price = current_prices.get(code)
                if current_price is None:
                    continue

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

                if signal != 1 or confidence < 0.7:
                    continue

                # 计算ATR和止盈（KC中轨）
                atr_val = float(kc["atr"].iloc[-1]) if not kc.empty and "atr" in kc.columns and not pd.isna(kc["atr"].iloc[-1]) else 0
                kc_mid = float(kc["kc_mid"].iloc[-1]) if not kc.empty and not pd.isna(kc["kc_mid"].iloc[-1]) else current_price * 1.03

                candidates.append({
                    "code": code,
                    "price": current_price,
                    "confidence": confidence,
                    "atr": atr_val,
                    "stop_price": current_price - atr_val * stop_loss_atr if atr_val > 0 else current_price * 0.90,
                    "take_profit_price": kc_mid,  # 止盈：KC中轨
                })

            # 按置信度排序，买入top
            candidates.sort(key=lambda x: x["confidence"], reverse=True)
            for cand in candidates[:5]:
                if cash < cand["price"] * 100:
                    continue

                shares = int(cash * 0.1 / cand["price"])  # 10%仓位
                if shares <= 0:
                    continue

                cost = shares * cand["price"]
                cash -= cost
                holdings[cand["code"]] = {
                    "shares": shares,
                    "shares_remaining": shares,
                    "buy_price": cand["price"],
                    "buy_date": date,
                    "stop_price": cand["stop_price"],
                    "take_profit_price": cand["take_profit_price"],
                }

    # 计算绩效
    print("\n[3/3] 计算绩效...")

    if not daily_values:
        print("[ERROR] 无日常记录")
        return pd.DataFrame()

    perf_df = pd.DataFrame(daily_values)
    perf_df["nav"] = perf_df["total_value"] / initial_cash

    # 指标计算
    nav = perf_df["nav"].values
    total_return = nav[-1] - 1 if len(nav) > 0 else 0

    # 年化收益
    n_years = len(daily_values) / 252
    annualized = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

    # 最大回撤
    peak = np.maximum.accumulate(nav)
    drawdown = (nav - peak) / peak
    max_drawdown = drawdown.min()

    # 夏普比（简化）
    daily_returns = np.diff(nav) / nav[:-1]
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 0 else 0

    # 交易统计
    if trade_history:
        trades_df = pd.DataFrame(trade_history)
        n_trades = len(trades_df)
        wins = trades_df[trades_df["pnl_pct"] > 0]
        win_rate = len(wins) / n_trades if n_trades > 0 else 0
        avg_win = wins["pnl_pct"].mean() if len(wins) > 0 else 0
        losses = trades_df[trades_df["pnl_pct"] <= 0]
        avg_loss = losses["pnl_pct"].mean() if len(losses) > 0 else 0
        exit_counts = trades_df["reason"].value_counts().to_dict()
    else:
        n_trades = 0
        win_rate = 0
        avg_win = 0
        avg_loss = 0
        exit_counts = {}

    # 输出
    print(f"\n{'='*60}")
    print(f"回测结果")
    print(f"{'='*60}")
    print(f"  回测期:       {dates[0].date()} ~ {dates[-1].date()} ({n_years:.1f}年)")
    print(f"  总收益:       {total_return:+.2%}")
    print(f"  年化收益:     {annualized:+.2%}")
    print(f"  最大回撤:     {max_drawdown:.2%}")
    print(f"  夏普比:       {sharpe:.2f}")
    print(f"  交易次数:     {n_trades}")
    print(f"  胜率:         {win_rate:.1%}")
    if n_trades > 0:
        print(f"  平均盈利:     {avg_win:+.2%}")
        print(f"  平均亏损:     {avg_loss:+.2%}")
        print(f"  赔率:        {abs(avg_win / avg_loss) if avg_loss != 0 else 0:.2f}")
        print(f"  退出原因:     {exit_counts}")

    # 保存结果
    if ARGS.output:
        perf_df.to_csv(ARGS.output, index=False)
        print(f"\n净值曲线已保存: {ARGS.output}")

    return perf_df


if __name__ == "__main__":
    codes = load_stock_candidates(n=ARGS.stocks, by_volume=ARGS.top_volume)
    print(f"候选股票: {codes[:10]}...")
    perf_df = run_backtest(codes, years=ARGS.years)
