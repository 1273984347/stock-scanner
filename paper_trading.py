"""
模拟盘系统 — 记录信号 + 跟踪绩效

用法：
    python paper_trading.py scan          # 收盘后扫描，记录今日信号
    python paper_trading.py status        # 查看模拟盘状态
    python paper_trading.py update        # 更新持仓价格，检查止损/止盈
    python paper_trading.py report        # 生成绩效报告
"""
import json
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

# 设置路径
sys.path.insert(0, os.path.dirname(__file__))

CACHE_DIR = Path("cache")
POSITIONS_FILE = CACHE_DIR / "paper_positions.json"
TRADES_FILE = CACHE_DIR / "paper_trades.json"
SIGNALS_LOG = CACHE_DIR / "paper_signals.jsonl"


def _load_json(path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _get_current_price(code: str) -> float:
    """获取当前价格（从TDX本地数据）"""
    try:
        from pytdx.reader import TdxDailyBarReader
        reader = TdxDailyBarReader()
        if code.startswith(('6', '5', '9')):
            fp = f"D:/TDX/vipdoc/sh/lday/sh{code}.day"
        else:
            fp = f"D:/TDX/vipdoc/sz/lday/sz{code}.day"
        if not Path(fp).exists():
            return 0
        df = reader.get_df(fp)
        if df is not None and not df.empty:
            return float(df.iloc[-1]['close'])
    except Exception:
        pass
    return 0


def _get_atr(code: str, period: int = 14) -> float:
    """获取ATR"""
    try:
        from pytdx.reader import TdxDailyBarReader
        reader = TdxDailyBarReader()
        if code.startswith(('6', '5', '9')):
            fp = f"D:/TDX/vipdoc/sh/lday/sh{code}.day"
        else:
            fp = f"D:/TDX/vipdoc/sz/lday/sz{code}.day"
        if not Path(fp).exists():
            return 0
        df = reader.get_df(fp)
        if df is None or len(df) < period + 1:
            return 0
        high = df['high']
        low = df['low']
        prev_close = df['close'].shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if not pd.isna(atr) else 0
    except Exception:
        pass
    return 0


# ========== 扫描 ==========

def run_scan(preset: str = "balanced"):
    """运行盘后扫描，记录信号"""
    from lib.pipeline import run_all_strategies
    from config import get_config

    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] 开始扫描...")

    config = get_config(preset)
    scan_result = run_all_strategies(preset)

    if not scan_result or not scan_result.get("results"):
        print("所有策略均无候选股")
        return

    results = scan_result["results"]

    # 记录信号
    signals = []
    for strategy_name, df in results.items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get('代码', ''))
            name = str(row.get('名称', ''))
            price = float(row.get('最新价', 0)) if '最新价' in row.index else _get_current_price(code)
            score = float(row.get('打分卡', 0)) if '打分卡' in row.index else 0

            signal = {
                "date": datetime.now().strftime("%Y-%m-%d"),
                "code": code,
                "name": name,
                "strategy": strategy_name,
                "price": price,
                "score": score,
                "entry_timing": "次日开盘",
                "status": "pending",  # pending → entered → closed
            }
            signals.append(signal)

            # 追加到 JSONL 日志
            with open(SIGNALS_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(signal, ensure_ascii=False) + "\n")

    # 打印结果
    print(f"\n{'='*60}")
    print(f"今日信号: {len(signals)} 只")
    print(f"{'='*60}")
    for s in signals:
        print(f"  {s['code']} {s['name']:8s} | {s['strategy']:8s} | ¥{s['price']:.2f} | 评分={s['score']:.1f}")

    return signals


# ========== 持仓管理 ==========

def enter_position(code: str, name: str, price: float, strategy: str, score: float):
    """模拟入场"""
    positions = _load_json(POSITIONS_FILE, {})
    today = datetime.now().strftime("%Y-%m-%d")

    if code in positions:
        print(f"已持有 {code}，跳过")
        return

    atr = _get_atr(code)
    stop_loss = price - 2 * atr if atr > 0 else price * 0.95

    positions[code] = {
        "name": name,
        "entry_date": today,
        "entry_price": price,
        "current_price": price,
        "strategy": strategy,
        "score": score,
        "atr": atr,
        "stop_loss": stop_loss,
        "peak_price": price,
        "trailing_stop": stop_loss,
        "pnl_pct": 0,
        "hold_days": 0,
        "partial_exits": 0,
    }

    _save_json(POSITIONS_FILE, positions)
    print(f"入场: {code} {name} @ ¥{price:.2f} | 止损=¥{stop_loss:.2f}")


def update_positions():
    """更新持仓价格，检查止损/止盈"""
    positions = _load_json(POSITIONS_FILE, {})
    trades = _load_json(TRADES_FILE, [])

    if not positions:
        print("无持仓")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    closed = []

    for code, pos in list(positions.items()):
        price = _get_current_price(code)
        if price <= 0:
            continue

        # 更新价格
        pos["current_price"] = price
        pos["pnl_pct"] = round((price / pos["entry_price"] - 1) * 100, 2)
        pos["hold_days"] = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(pos["entry_date"], "%Y-%m-%d")).days

        # 更新峰值和移动止损
        if price > pos["peak_price"]:
            pos["peak_price"] = price
            atr = pos.get("atr", 0)
            if atr > 0:
                pos["trailing_stop"] = max(pos["trailing_stop"], price - 2 * atr)

        # 检查退出条件
        exit_reason = None

        # 1. ATR止损
        if price <= pos["stop_loss"]:
            exit_reason = "ATR止损"

        # 2. 移动止损
        elif price <= pos["trailing_stop"] and pos["pnl_pct"] > 0:
            exit_reason = "移动止损"

        # 3. 趋势破坏（连续2天跌破EMA21）
        # 简化版：用价格跌破入场价-3%作为趋势破坏
        elif pos["hold_days"] > 5 and price < pos["entry_price"] * 0.97:
            exit_reason = "趋势破坏"

        # 4. 超时（持有超过20天）
        elif pos["hold_days"] >= 20:
            exit_reason = "超时"

        if exit_reason:
            # 记录交易
            trade = {
                "code": code,
                "name": pos["name"],
                "entry_date": pos["entry_date"],
                "exit_date": today,
                "entry_price": pos["entry_price"],
                "exit_price": price,
                "pnl_pct": pos["pnl_pct"],
                "hold_days": pos["hold_days"],
                "exit_reason": exit_reason,
                "strategy": pos["strategy"],
            }
            trades.append(trade)
            closed.append(trade)
            del positions[code]
        else:
            # 打印持仓状态
            emoji = "↑" if pos["pnl_pct"] > 0 else "↓"
            print(f"  {code} {pos['name']:8s} | ¥{price:.2f} | {pos['pnl_pct']:+.2f}% {emoji} | 持{pos['hold_days']}天 | 止损=¥{pos['stop_loss']:.2f}")

    _save_json(POSITIONS_FILE, positions)
    _save_json(TRADES_FILE, trades)

    if closed:
        print(f"\n平仓 {len(closed)} 笔:")
        for t in closed:
            emoji = "✓" if t["pnl_pct"] > 0 else "✗"
            print(f"  {emoji} {t['code']} {t['name']} | {t['pnl_pct']:+.2f}% | {t['exit_reason']} | 持{t['hold_days']}天")


# ========== 绩效报告 ==========

def generate_report():
    """生成模拟盘绩效报告"""
    trades = _load_json(TRADES_FILE, [])
    positions = _load_json(POSITIONS_FILE, {})

    if not trades and not positions:
        print("无交易记录")
        return

    print(f"\n{'='*60}")
    print(f"模拟盘绩效报告")
    print(f"{'='*60}")

    if trades:
        df = pd.DataFrame(trades)
        total = len(df)
        wins = len(df[df['pnl_pct'] > 0])
        losses = len(df[df['pnl_pct'] <= 0])
        win_rate = wins / total * 100 if total > 0 else 0
        avg_pnl = df['pnl_pct'].mean()
        avg_win = df[df['pnl_pct'] > 0]['pnl_pct'].mean() if wins > 0 else 0
        avg_loss = abs(df[df['pnl_pct'] <= 0]['pnl_pct'].mean()) if losses > 0 else 0
        pf = abs(df[df['pnl_pct'] > 0]['pnl_pct'].sum() / df[df['pnl_pct'] <= 0]['pnl_pct'].sum()) if losses > 0 and df[df['pnl_pct'] <= 0]['pnl_pct'].sum() != 0 else 0
        max_dd = df['pnl_pct'].min()

        print(f"\n已平仓交易: {total} 笔")
        print(f"  胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
        print(f"  均盈: {avg_pnl:+.2f}%")
        print(f"  平均盈利: {avg_win:+.2f}%")
        print(f"  平均亏损: -{avg_loss:.2f}%")
        print(f"  盈亏比: {avg_win/avg_loss:.2f}" if avg_loss > 0 else "  盈亏比: N/A")
        print(f"  PF: {pf:.2f}")
        print(f"  最大单笔亏损: {max_dd:+.2f}%")

        # 按退出原因统计
        print(f"\n退出原因分布:")
        for reason, group in df.groupby('exit_reason'):
            count = len(group)
            avg = group['pnl_pct'].mean()
            print(f"  {reason}: {count}笔, 均盈={avg:+.2f}%")

    if positions:
        print(f"\n当前持仓: {len(positions)} 只")
        total_pnl = 0
        for code, pos in positions.items():
            emoji = "↑" if pos['pnl_pct'] > 0 else "↓"
            print(f"  {code} {pos['name']:8s} | ¥{pos['current_price']:.2f} | {pos['pnl_pct']:+.2f}% {emoji} | 持{pos['hold_days']}天")
            total_pnl += pos['pnl_pct']
        print(f"  持仓合计浮盈: {total_pnl:+.2f}%")

    print(f"\n{'='*60}")


# ========== 主入口 ==========

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "scan":
        preset = sys.argv[2] if len(sys.argv) > 2 else "balanced"
        signals = run_scan(preset)
        # 自动入场（模拟盘：次日开盘入场，这里用信号价模拟）
        if signals:
            print(f"\n是否自动入场？(y/n)")
            # 模拟盘默认自动入场
            for s in signals:
                if s['price'] > 0:
                    enter_position(s['code'], s['name'], s['price'], s['strategy'], s['score'])

    elif cmd == "update":
        update_positions()

    elif cmd == "status":
        positions = _load_json(POSITIONS_FILE, {})
        if not positions:
            print("无持仓")
        else:
            print(f"持仓 {len(positions)} 只:")
            update_positions()

    elif cmd == "report":
        generate_report()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()
