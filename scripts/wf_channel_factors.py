"""
Walk-Forward 验证：凯尔特纳/薛斯通道因子 IC/IR 分析

用法:
    python scripts/wf_channel_factors.py                    # 默认：20只股票，4轮walk-forward
    python scripts/wf_channel_factors.py --stocks 50       # 50只股票
    python scripts/wf_channel_factors.py --rounds 6        # 6轮
    python scripts/wf_channel_factors.py --top-volume      # 按成交额选股
"""
import os
import sys
import argparse
import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from dataclasses import dataclass

# 确保 lib 在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.indicators import (
    calculate_keltner_channels,
    calculate_xosi_channels,
    get_channel_position,
    compute_all_channels,
    KC_LENGTH, KC_ATR, KC_SCALAR,
    XOSI_SHORT, XOSI_LONG,
)
from lib.walkforward import (
    WalkForwardBacktest, WalkForwardConfig,
    summarize_rounds,
)

# ========== 参数 ==========

PARSER = argparse.ArgumentParser(description="Walk-Forward 通道因子 IC/IR 验证")
PARSER.add_argument("--stocks", type=int, default=20, help="股票数量")
PARSER.add_argument("--rounds", type=int, default=4, help="Walk-forward 轮次")
PARSER.add_argument("--train", type=int, default=180, help="训练天数")
PARSER.add_argument("--val", type=int, default=30, help="验证天数")
PARSER.add_argument("--step", type=int, default=30, help="步进天数")
PARSER.add_argument("--top-volume", action="store_true", help="按成交额选股（默认随机）")
PARSER.add_argument("--output", type=str, default="", help="输出CSV路径")
PARSER.add_argument("--tdx-path", type=str, default="D:/TDX/vipdoc", help="TDX数据路径")
ARGS = PARSER.parse_args()


# ========== TDX 数据加载 ==========

def load_stock_candidates(n: int = 20, by_volume: bool = False) -> List[str]:
    """加载候选股票列表

    Args:
        n: 数量
        by_volume: 是否按成交额排序选股（从TDX shlday文件）

    Returns:
        股票代码列表，如 ["600519", "000001", ...]
    """
    tdx_path = ARGS.tdx_path or "D:/TDX/vipdoc"

    if by_volume:
        # 按成交额排序：读取所有shlday文件，计算总成交额，取前N
        import struct
        from pathlib import Path

        sh_dir = Path(tdx_path) / "sh" / "lday"
        sz_dir = Path(tdx_path) / "sz" / "lday"

        stock_volumes = []

        for day_dir in [sh_dir, sz_dir]:
            if not day_dir.exists():
                continue
            market = "sh" if "sh" in str(day_dir) else "sz"

            # 只选择真正的股票，排除ETF/指数/债券
            if market == "sh":
                # 上海：60开头是A股，90开头是B股
                patterns = ["sh6*.day", "sh9*.day"]
            else:
                # 深圳：00/30开头是A股，20开头是B股
                patterns = ["sz0*.day", "sz3*.day", "sz2*.day"]

            for pattern in patterns:
                for fpath in day_dir.glob(pattern):
                    code = fpath.stem  # 如 "sh600519"
                    try:
                        with open(fpath, "rb") as f:
                            # 读取最后一条记录（最新数据）的成交量
                            f.seek(-32, 2)  # 每条记录32字节
                            data = f.read(32)
                            if len(data) == 32:
                                # 格式: date(4) + open(4) + high(4) + low(4) + close(4) + amount(4) + volume(4) + unknown(4)
                                # volume 在 offset 24-27，乘以 0.01 得到真实成交量
                                vol = struct.unpack("<I", data[24:28])[0] * 0.01
                                stock_volumes.append((code.replace(market, ""), vol, market))
                    except Exception:
                        pass

        if not stock_volumes:
            # fallback：随机选
            pass
        else:
            # 按成交量排序，取前N
            stock_volumes.sort(key=lambda x: x[1], reverse=True)
            codes = []
            for code, _, market in stock_volumes[:n * 2]:
                prefix = "sh" if market == "sh" else "sz"
                codes.append(f"{prefix}{code}")
            return codes[:n]

    # 默认：随机选取上海+深圳各一半
    import random
    sh_dir = Path(tdx_path) / "sh" / "lday"
    sz_dir = Path(tdx_path) / "sz" / "lday"

    sh_files = list(sh_dir.glob("sh6*.day"))[:100]  # 限制范围
    sz_files = list(sz_dir.glob("sz0*.day"))[:100]

    all_files = sh_files + sz_files
    random.seed(42)
    random.shuffle(all_files)

    codes = []
    for fpath in all_files[:n]:
        codes.append(fpath.stem)  # 如 "sh600519"
    return codes[:n]


def load_kline_from_tdx(code: str, days: int = 400) -> pd.DataFrame:
    """从本地TDX文件加载K线数据

    Args:
        code: 如 "sh600519" 或 "600519"
        days: 加载天数

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    try:
        from pytdx.reader import TdxDailyBarReader
    except ImportError:
        return pd.DataFrame()

    # 解析 market 和 tdx_code
    # 处理带前缀的代码如 "sh600519" 或 "sz000100"
    original_code = code
    code = code.strip()
    if code.startswith(("sh", "sz", "SH", "SZ")):
        # 提取市场前缀和数字代码
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

        col_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
        }
        df = df.rename(columns=col_map)
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        if len(df) > days:
            df = df.tail(days)

        return df[["date", "open", "high", "low", "close", "volume"]].copy()
    except Exception as e:
        print(f"  [WARN] 读取 {code} 失败: {e}", file=sys.stderr)
        return pd.DataFrame()


# ========== 因子计算 ==========

def compute_factors(df: pd.DataFrame) -> pd.DataFrame:
    """计算通道因子

    Returns DataFrame with columns:
      - kc_pos: 凯尔特纳通道位置 (0=下轨, 1=上轨)
      - kc_lower_dist: 价格到下轨的距离（%）
      - kc_upper_dist: 价格到上轨的距离（%）
      - xosi_long_pos: 薛斯长期通道位置
      - xosi_short_pos: 薛斯短期通道位置
      - slope_long: 长期通道斜率
      - ic_5d: 5日后收益率（用于IC计算）
    """
    if df is None or df.empty or len(df) < max(KC_LENGTH, XOSI_LONG) + 10:
        return pd.DataFrame()

    df = df.copy().reset_index(drop=True)

    # 计算通道
    kc = calculate_keltner_channels(df)
    xosi = calculate_xosi_channels(df)

    if kc.empty or xosi.empty:
        return pd.DataFrame()

    # 通道位置
    kc_range = kc["kc_upper"] - kc["kc_lower"]
    df["kc_pos"] = np.where(kc_range > 0, (df["close"] - kc["kc_lower"]) / kc_range, 0.5)

    # 到上下轨距离
    df["kc_lower_dist"] = (df["close"] - kc["kc_lower"]) / (kc["kc_lower"] + 1e-9)
    df["kc_upper_dist"] = (kc["kc_upper"] - df["close"]) / (kc["kc_upper"] + 1e-9)

    # 薛斯位置
    xosi_range_long = xosi["xu_long"] - xosi["xd_long"]
    df["xosi_long_pos"] = np.where(
        xosi_range_long > 0,
        (df["close"] - xosi["xd_long"]) / xosi_range_long,
        0.5,
    )

    xosi_range_short = xosi["xu_short"] - xosi["xd_short"]
    df["xosi_short_pos"] = np.where(
        xosi_range_short > 0,
        (df["close"] - xosi["xd_short"]) / xosi_range_short,
        0.5,
    )

    # 长期通道斜率
    long_mid = (xosi["xu_long"] + xosi["xd_long"]) / 2
    df["slope_long"] = long_mid.pct_change(3) * 100

    # 未来收益率（用于IC计算）
    for horizon in [5, 10, 20]:
        df[f"return_{horizon}d"] = df["close"].shift(-horizon) / df["close"] - 1

    return df.dropna(subset=["kc_pos", "xosi_long_pos"])


# ========== IC/IR 计算 ==========

def calculate_ic(factor_series: pd.Series, return_series: pd.Series) -> float:
    """计算信息系数（IC）—— Pearson相关系数"""
    if len(factor_series) < 10:
        return 0.0
    return float(factor_series.corr(return_series))


def calculate_rank_ic(factor_series: pd.Series, return_series: pd.Series) -> float:
    """计算秩相关系数（Rank IC / Spearman）"""
    if len(factor_series) < 10:
        return 0.0
    return float(factor_series.corr(return_series, method="spearman"))


@dataclass
class ICResult:
    ic: float
    rank_ic: float
    n_samples: int


def calculate_factor_ic(
    factor_df: pd.DataFrame,
    factor_name: str,
    return_col: str = "return_5d",
) -> ICResult:
    """计算单个因子的 IC """
    if factor_df is None or factor_df.empty:
        return ICResult(0, 0, 0)
    factor = factor_df[factor_name].dropna()
    returns = factor_df[return_col].dropna()
    common = factor.index.intersection(returns.index)
    if len(common) < 10:
        return ICResult(0, 0, len(common))
    ic = calculate_ic(factor[common], returns[common])
    rank_ic = calculate_rank_ic(factor[common], returns[common])
    return ICResult(ic, rank_ic, len(common))


# ========== Walk-Forward 策略函数 ==========

def channel_strategy(
    market_data: Dict[str, pd.DataFrame],
    factor_data,  # unused
    dates: List,
    symbols: List[str],
) -> Dict:
    """基于通道因子的选股策略（用于 WalkForwardBacktest）"""
    if not market_data or not dates:
        return {}

    # 对每只股票计算因子
    all_signals = []

    for code, df in market_data.items():
        if df is None or df.empty or len(df) < 60:
            continue

        factors = compute_factors(df)
        if factors.empty or len(factors) < 20:
            continue

        # 只取最后一行（最新信号）
        latest = factors.iloc[-1]

        # 入场信号：价格接近下轨（kc_pos < 0.2）+ 薛斯长期不空头
        if latest["kc_pos"] < 0.2 and latest["slope_long"] > -0.1:
            signal_strength = 1 - latest["kc_pos"]  # 越接近下轨越强
            all_signals.append({
                "code": code,
                "signal": 1,
                "confidence": float(min(signal_strength, 0.95)),
                "entry_price": float(latest["close"]),
                "stop_price": float(latest["kc_lower"]) - float(factors["kc_lower"].iloc[-1]) if "kc_lower" in factors.columns else float(latest["close"]) * 0.97,
            })

    if not all_signals:
        return {"total_trades": 0, "total_return": 0.0}

    # 简化回测：等权持有到下一个walk-forward窗口
    # 实际由 WalkForwardBacktest 评估，这里返回信号列表
    return {
        "total_trades": len(all_signals),
        "signals": all_signals,
        "total_return": 0.0,  # 由框架计算
    }


# ========== Walk-Forward 主流程 ==========

def run_walkforward_ic(
    codes: List[str],
    train_days: int = 180,
    val_days: int = 30,
    step_days: int = 30,
    n_rounds: int = 4,
) -> pd.DataFrame:
    """执行 Walk-Forward IC/IR 分析

    Returns:
        DataFrame with IC/IR results per round and per factor
    """
    print(f"\n{'='*60}")
    print(f"Walk-Forward IC/IR 分析")
    print(f"股票数: {len(codes)} | 训练: {train_days}d | 验证: {val_days}d | 步进: {step_days}d")
    print(f"{'='*60}\n")

    # ========== 阶段1: 加载全部数据 ==========
    print("[1/4] 加载数据...")
    stock_data = {}
    for i, code in enumerate(codes):
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(codes)}")
        df = load_kline_from_tdx(code, days=train_days + val_days + step_days * n_rounds + 100)
        if df is not None and not df.empty and len(df) >= 60:
            stock_data[code] = df

    print(f"  成功加载: {len(stock_data)}/{len(codes)} 只股票\n")

    if len(stock_data) < 5:
        print("[ERROR] 有效股票太少，请检查TDX数据路径")
        return pd.DataFrame()

    # 获取公共日期范围
    all_dates = set()
    for df in stock_data.values():
        all_dates.update(df["date"].tolist())
    dates = sorted(all_dates)
    print(f"  公共日期范围: {dates[0]} ~ {dates[-1]} ({len(dates)} 个交易日)")

    # ========== 阶段2: 创建 Walk-Forward 窗口 ==========
    print("\n[2/4] 创建 Walk-Forward 窗口...")
    wf_config = WalkForwardConfig(
        train_days=train_days,
        validation_days=val_days,
        step_days=step_days,
        warmup_bars=60,
        min_train_trades=3,
        min_validation_trades=1,
    )
    wf = WalkForwardBacktest(wf_config)
    windows = wf.create_windows(dates)

    if not windows:
        print("[ERROR] 无法创建Walk-Forward窗口")
        return pd.DataFrame()

    windows = windows[:n_rounds]
    print(f"  共 {len(windows)} 个窗口:")
    for w in windows:
        print(f"    窗口{w.index}: 训练 {w.train_start_date}~{w.train_end_date} | "
              f"验证 {w.validation_start_date}~{w.validation_end_date}")

    # ========== 阶段3: 每窗口计算 IC/IR ==========
    print("\n[3/4] 计算 IC/IR...")
    ic_records = []

    for w_idx, window in enumerate(windows):
        print(f"\n  窗口 {w_idx} ({window.validation_start_date} ~ {window.validation_end_date})")

        # 收集所有股票在验证期的因子和收益率
        factor_records = []

        for code, df in stock_data.items():
            # 切分到验证期
            val_dates = set(dates[window.validation_start:window.validation_end + 1])
            val_df = df[df["date"].isin(val_dates)].copy()

            if val_df is None or val_df.empty or len(val_df) < 10:
                continue

            # 计算因子（使用训练期数据预热）
            train_dates_set = set(dates[window.train_start:window.train_end + 1])
            warmup_start_idx = max(0, window.validation_start - 60)
            warmup_dates_set = set(dates[warmup_start_idx:window.validation_start + 1])

            full_df = df[df["date"].isin(warmup_dates_set | val_dates)].copy()
            if len(full_df) < 60:
                continue

            factors = compute_factors(full_df)
            if factors.empty:
                continue

            # 取验证期的因子
            val_factors = factors[factors["date"].isin(val_dates)]
            if val_factors.empty or len(val_factors) < 5:
                continue

            for _, row in val_factors.iterrows():
                factor_records.append({
                    "code": code,
                    "date": row["date"],
                    "kc_pos": row.get("kc_pos", 0.5),
                    "kc_lower_dist": row.get("kc_lower_dist", 0),
                    "xosi_long_pos": row.get("xosi_long_pos", 0.5),
                    "xosi_short_pos": row.get("xosi_short_pos", 0.5),
                    "slope_long": row.get("slope_long", 0),
                    f"return_5d": row.get("return_5d", np.nan),
                    f"return_10d": row.get("return_10d", np.nan),
                    f"return_20d": row.get("return_20d", np.nan),
                })

        if not factor_records:
            print(f"    无有效因子记录")
            continue

        factor_df = pd.DataFrame(factor_records)

        # 计算各因子 IC
        factors_to_test = {
            "kc_pos": "return_5d",
            "kc_lower_dist": "return_5d",
            "xosi_long_pos": "return_5d",
            "xosi_short_pos": "return_5d",
            "slope_long": "return_5d",
        }

        for factor_name, return_col in factors_to_test.items():
            ic_res = calculate_factor_ic(factor_df, factor_name, return_col)
            ic_records.append({
                "window": w_idx,
                "val_start": window.validation_start_date,
                "val_end": window.validation_end_date,
                "factor": factor_name,
                "ic": ic_res.ic,
                "rank_ic": ic_res.rank_ic,
                "n_samples": ic_res.n_samples,
            })
            print(f"    {factor_name:20s}: IC={ic_res.ic:+.4f}  RankIC={ic_res.rank_ic:+.4f}  (n={ic_res.n_samples})")

    ic_results_df = pd.DataFrame(ic_records)

    # ========== 阶段4: 汇总统计 ==========
    print("\n[4/4] 汇总统计...")
    print(f"\n{'='*60}")
    print("IC/IR 汇总（按因子）")
    print(f"{'='*60}")

    summary_rows = []
    for factor_name in ic_results_df["factor"].unique():
        fac_df = ic_results_df[ic_results_df["factor"] == factor_name]
        ic_mean = fac_df["ic"].mean()
        ic_std = fac_df["ic"].std()
        ir = ic_mean / (ic_std + 1e-9) if ic_std > 0 else 0
        rank_ic_mean = fac_df["rank_ic"].mean()

        win_rate = (fac_df["ic"] > 0).mean() * 100

        print(f"\n  {factor_name}:")
        print(f"    IC均值:     {ic_mean:+.4f}")
        print(f"    IC标准差:   {ic_std:.4f}")
        print(f"    IR (IC/IC_std): {ir:.4f}")
        print(f"    RankIC均值: {rank_ic_mean:+.4f}")
        print(f"    正IC率:     {win_rate:.1f}%")
        print(f"    轮次明细:   {[f'{x:.4f}' for x in fac_df['ic'].tolist()]}")

        summary_rows.append({
            "factor": factor_name,
            "ic_mean": ic_mean,
            "ic_std": ic_std,
            "ir": ir,
            "rank_ic_mean": rank_ic_mean,
            "win_rate": win_rate,
            "n_rounds": len(fac_df),
        })

    summary_df = pd.DataFrame(summary_rows).sort_values("ir", ascending=False)

    # 保存结果
    if ARGS.output:
        ic_results_df.to_csv(ARGS.output, index=False)
        summary_df.to_csv(ARGS.output.replace(".csv", "_summary.csv"), index=False)
        print(f"\n结果已保存: {ARGS.output}")

    print(f"\n结论:")
    for _, row in summary_df.iterrows():
        direction = "反向" if row["ic_mean"] < 0 else "正向"
        quality = "***" if abs(row["ir"]) > 1.0 else "**" if abs(row["ir"]) > 0.5 else "*"
        print(f"  {row['factor']:20s}: {direction} | IR={row['ir']:+.2f} {quality}")

    return ic_results_df


# ========== 主入口 ==========

if __name__ == "__main__":
    print(f"TDX数据路径: {ARGS.tdx_path}")
    print(f"候选股票数: {ARGS.stocks} | Walk-Forward轮次: {ARGS.rounds}")
    print(f"训练期: {ARGS.train}d | 验证期: {ARGS.val}d | 步进: {ARGS.step}d\n")

    codes = load_stock_candidates(n=ARGS.stocks, by_volume=ARGS.top_volume)
    print(f"候选股票: {codes[:10]}{'...' if len(codes) > 10 else ''}")

    results = run_walkforward_ic(
        codes=codes,
        train_days=ARGS.train,
        val_days=ARGS.val,
        step_days=ARGS.step,
        n_rounds=ARGS.rounds,
    )

    if not results.empty:
        print(f"\n完整IC记录已保存至: {ARGS.output or '(未保存)'}")
