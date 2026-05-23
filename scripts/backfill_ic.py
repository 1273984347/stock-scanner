#!/usr/bin/env python
"""回填历史 IC 时序（冷启动绕过）

回测最近 60 个交易日的历史行情，对每只股票计算所有因子值 + forward returns，
一次性写入 cache/ic/panel/ic_panel_{历史date}.json。

用法:
    python scripts/backfill_ic.py
    python scripts/backfill_ic.py --days 120
    python scripts/backfill_ic.py --start 2026-01-01 --end 2026-03-31
"""

import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import sys

# 确保项目根目录在 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib.ic.panel_builder import build_ic_panel, save_ic_panel
from lib.ic.ic_calculator import update_ic_history

IC_PANEL_DIR = Path("cache/ic/panel")

def get_trading_dates(days: int = 60) -> list:
    """生成最近 N 个交易日（跳过周末）"""
    dates = []
    d = datetime.now()
    while len(dates) < days and d > datetime(2020, 1, 1):
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return dates

def backfill_ic(days: int = 60, start_date: str = None, end_date: str = None):
    """回填历史 IC"""
    import json
    from lib.data_source_registry import get_stock_hist_multi_source

    # 获取候选股列表（用宽基指数成分股模拟）
    codes = _get_universe_codes()

    trading_dates = get_trading_dates(days)
    if start_date:
        trading_dates = [d for d in trading_dates if d >= start_date]
    if end_date:
        trading_dates = [d for d in trading_dates if d <= end_date]

    print(f"回填 {len(trading_dates)} 个交易日，{len(codes)} 只股票")

    for date in trading_dates:
        # 跳过已有 panel 的日期
        panel_path = IC_PANEL_DIR / f"ic_panel_{date}.json"
        if panel_path.exists():
            print(f"  跳过 {date}（已有数据）")
            continue

        # 获取历史数据
        hist_data = {}
        for code in codes:
            try:
                df = get_stock_hist_multi_source(code, days=60, source="auto")
                if not df.empty and len(df) >= 30:
                    hist_data[code] = df
            except Exception:
                continue

        if len(hist_data) < 10:
            print(f"  {date}: 有效数据 < 10 只，跳过")
            continue

        candidates = [{"code": c, "name": c} for c in hist_data.keys()]
        panel = build_ic_panel(date, candidates, hist_data)
        save_ic_panel(panel, date)

        # 更新 IC 时序
        update_ic_history(panel)
        print(f"  {date}: 完成 {len(panel['stocks'])} 只 IC 记录")

    print("回填完成")

def _get_universe_codes() -> list:
    """获取候选股列表（简化版：用 cache/hs300_cache.parquet 的成分股）"""
    try:
        import pandas as pd
        from pathlib import Path
        hs300_path = Path("cache/hs300_cache.parquet")
        if hs300_path.exists():
            df = pd.read_parquet(hs300_path)
            if "code" in df.columns:
                return df["code"].dropna().unique().tolist()[:100]
    except Exception:
        pass
    # 回退：返回常见蓝筹股
    return [
        "000001", "000002", "000063", "000333", "000338",
        "000858", "000876", "002415", "002594", "600009",
        "600016", "600019", "600028", "600030", "600036",
        "600050", "600089", "600104", "600111", "600176",
        "600519", "600690", "600887", "600900", "601012",
        "601066", "601088", "601166", "601186", "601288",
        "601318", "601328", "601398", "601601", "601628",
        "601668", "601688", "601728", "601766", "601818",
        "601857", "601888", "601939", "601989", "603259",
    ]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="回填历史 IC 数据")
    parser.add_argument("--days", type=int, default=60, help="回填天数")
    parser.add_argument("--start", type=str, default=None, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()
    backfill_ic(days=args.days, start_date=args.start, end_date=args.end)
