"""
HS300 代理回测脚本
- 扫描 D:/TDX/vipdoc 本地数据
- 过滤 A 股主板/创业板/科创板
- 取流动性前 100 只（近 20 日均成交额）
- 跑 backtest.engine.run_backtest(parallel=True) v3_hybrid 750 天
"""
import os, sys
from pathlib import Path

sys.path.insert(0, '.')
os.environ['NO_VERIFY_TDX'] = '1'

from pytdx.reader import TdxDailyBarReader
from backtest.engine import run_backtest, BacktestConfig

TDX_DIRS = [
    ('D:/TDX/vipdoc/sh/lday', 'sh'),
    ('D:/TDX/vipdoc/sz/lday', 'sz'),
]

# A 股普通股前缀（排除指数/ETF/债券/B股）
STOCK_PREFIXES_SH = ('sh600', 'sh601', 'sh603', 'sh605', 'sh688')
STOCK_PREFIXES_SZ = ('sz000', 'sz001', 'sz002', 'sz003', 'sz300', 'sz301')


def is_a_stock(code: str) -> bool:
    if code.startswith(STOCK_PREFIXES_SH):
        return True
    if code.startswith(STOCK_PREFIXES_SZ):
        return True
    return False


def scan_tdx_stocks(min_rows: int = 750, top_n: int = 100):
    reader = TdxDailyBarReader()
    candidates = []
    for subdir, _ in TDX_DIRS:
        path = Path(subdir)
        if not path.exists():
            print(f"  跳过（不存在）: {subdir}")
            continue
        for fp in path.iterdir():
            if not fp.name.endswith('.day'):
                continue
            code = fp.stem
            if not is_a_stock(code):
                continue
            # 关键：lib.data_tdx._to_tdx_market_code 期望纯 6 位代码（不带 sh/sz）
            pure_code = code[2:] if code.startswith(('sh', 'sz')) else code
            try:
                df = reader.get_df(str(fp))
                if df is None or len(df) < min_rows:
                    continue
                recent = df.tail(20)
                avg_amount = float(recent['amount'].mean()) if 'amount' in recent.columns else 0
                candidates.append((pure_code, len(df), avg_amount))
            except Exception:
                continue

    candidates.sort(key=lambda x: x[2], reverse=True)
    print(f"  扫描完成: {len(candidates)} 只股票满足 ≥{min_rows} 行数据")
    return candidates[:top_n]


def main():
    print("="*60)
    print("  HS300 代理回测 (v3_hybrid, 750 天)")
    print("="*60)

    print("\n[1/3] 扫描 TDX 本地数据...")
    selected = scan_tdx_stocks(min_rows=750, top_n=100)
    if len(selected) < 30:
        print(f"  ⚠️ 仅 {len(selected)} 只有效股票，样本量偏小（建议 ≥ 30）")

    stock_codes = [c[0] for c in selected]
    print(f"  选取股票数: {len(stock_codes)}")
    print(f"  Top 5: {[c[0] for c in selected[:5]]}")
    print(f"  Bot 5: {[c[0] for c in selected[-5:]]}")

    print("\n[2/3] 启动回测引擎...")
    # sector_filter_top_n=0 禁用板块过滤（SECTOR_MAP 仅硬编码 27 只，其他股会被错误跳过）
    config = BacktestConfig(
        initial_capital=1_000_000.0,
        position_size_pct=0.10,
        max_positions=10,
        sector_filter_top_n=0,
        relative_strength_threshold=0.0,
    )

    result = run_backtest(
        stock_codes=stock_codes,
        days=750,
        exit_version='v3_hybrid',
        config=config,
        parallel=True,
        max_workers=4,
    )

    print("\n[3/3] 输出报告...")
    m = result['metrics']
    print("\n" + "="*60)
    print("  HS300 代理回测报告 (v3_hybrid, 750 天)")
    print("="*60, flush=True)
    print(f"  股票池数量:    {len(stock_codes)}")
    print(f"  总交易次数:    {m['total_trades']}")
    print(f"  胜率:          {m['win_rate']:.1f}%")
    print(f"  平均收益/笔:   {m['avg_pnl']:.2f}%")
    print(f"  平均盈利:      {m['avg_win']:.2f}%")
    print(f"  平均亏损:      {m['avg_loss']:.2f}%")
    print(f"  盈亏比:        {m['profit_factor']:.2f}")
    print(f"  最大回撤:      {m['max_drawdown']:.2f}%")
    print(f"  总收益率:      {m['total_return_pct']:.2f}%")
    print(f"  终值:          {m['final_capital']:,.0f}")

    if 'by_reason' in result and not result['by_reason'].empty:
        print("\n  按退出原因统计:")
        print("  " + result['by_reason'].to_string().replace('\n', '\n  '))

    if 'monthly' in result and not result['monthly'].empty:
        print("\n  按月收益统计（前 12 月）:")
        print("  " + result['monthly'].head(12).to_string().replace('\n', '\n  '))

    if 'config' in result:
        c = result['config']
        print(f"\n  成本配置: 单边 {c['total_cost_pct']:.3f}% "
              f"(comm {c['commission_rate']*100:.3f}% + "
              f"stamp {c['stamp_duty_rate']*100:.3f}% + "
              f"slippage {c['slippage_rate']*100:.3f}%)")

    print("\n" + "="*60)
    print("  [WARN] 审计警告（来自 dim-3+dim-4 审计）")
    print("="*60, flush=True)
    print("  P0-1: 回测未集成风控，max_drawdown/daily_loss/weekly_loss=0")
    print("        实际 max_drawdown 可能比报告大 2-5 倍")
    print("  P0-2: 止损退出价未考虑跳空低开（高估 0.3-1%）")
    print("  P1-1: 印花税 0.1% （现行 A 股 0.05%，保守 0.05%）")
    print("  P1-3: 幸存者偏差未修复（高估 1-3%/年）")
    print("  实际总收益可能比报告低 5-15%")
    print("="*60)

    return result


if __name__ == "__main__":
    result = main()
