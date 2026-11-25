#!/usr/bin/env python
"""回测对拍基线 —— 把回测引擎的当前输出固化为机器可判据。

目的：RF-01 Phase 3 的核心安全网。回测数值是**策略决策依据**（风险登记 R6：
错一位可能误导后续所有优化），但 Phase 0 装的三把尺子里只有一条 backtest 链路
（dual_run_diff），不足以覆盖 engine/core/full 三套引擎的**指标口径**。

本工具的三条内置硬约束（缺一不可）：
  1. **注入固定合成数据** —— 不用真实行情，保证跨环境（本地/CI）可复现
  2. **确定性种子** —— 用 zlib.crc32（禁用内置 hash()：字符串带随机盐，跨进程不稳）
  3. **pin 住数据入口** —— patch 使用方的模块属性，绕开 parquet 缓存与网络

三条链路（覆盖三份 check_entry 口径）：
  - core.run_factor_backtest       —— 因子回测主链路
  - engine.run_backtest            —— 单股信号回测（默认 Donchian 突破口径）
  - engine.run_backtest(multi_signal_entry=True) —— 同上但走多信号 OR 口径

用法：
  python scripts/backtest_baseline.py --capture --out docs/refactor/backtest-baseline.json
  python scripts/backtest_baseline.py --check   --out docs/refactor/backtest-baseline.json
  python scripts/backtest_baseline.py --summary
"""

from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys
import traceback
import zlib
from datetime import datetime, timedelta


def _resolve_repo_root(start):
    """自锚定仓库根：向上找同时含 AGENTS.md 与 lib/ 的目录，脚本可任意移动。"""
    for cand in (start, *start.parents):
        if (cand / "AGENTS.md").is_file() and (cand / "lib").is_dir():
            return cand
    raise SystemExit(f"[gate] 无法定位仓库根: 从 {start} 向上未找到 AGENTS.md + lib/")


ROOT = _resolve_repo_root(pathlib.Path(__file__).resolve().parent)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---- 确定性合成数据参数（改动这里 = 基线失效，需重新 capture）----
SEED_SALT = "stock-scanner-backtest-baseline-v1"
DAYS = 320
SYMBOLS = ["600519", "000858", "600036"]
AS_OF_DATE = "2026-05-30"
TOLERANCE = 1e-9

# ---- 基线窗口锚定（RF-01 Phase 4 前置批次）-------------------------------
# engine.run_backtest 在**未传 start_date** 时用
#   datetime.now() - timedelta(days=days) 推导窗口起点（engine.py:642），
# 并把该字符串直接写进 equity_curve[0]（engine.py:679）。
# 旧基线采集于 2026-11-07 => equity_first[0] = "2025-12-22"；
# 2026-11-10 重跑变成 "2025-12-25"（+3 天 == 日历推进 3 天），门禁假红。
# 修法：把「采集日」显式固化为常量，让窗口起点由常量推导，不依赖运行当天。
# 验证：datetime(2026,11,7) - timedelta(days=320) == 2025-12-22（与旧基线逐字一致）。
BASELINE_ANCHOR_DATE = "2026-11-07"
PIN_START_DATE = (
    datetime.strptime(BASELINE_ANCHOR_DATE, "%Y-%m-%d") - timedelta(days=DAYS)
).strftime("%Y-%m-%d")


def _seed(text: str) -> int:
    """确定性种子：绝不用内置 hash()（对字符串带随机盐，跨进程不稳定）。"""
    return zlib.crc32((SEED_SALT + "|" + text).encode("utf-8"))


def _synthetic_frame(symbol: str, days: int = DAYS):
    """合成一段可复现的 OHLCV + 指标列。

    列集与 backtest/core.get_stock_data 的返回保持一致（含 add_indicators 补的列），
    以免引擎走到不同分支。
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(_seed(symbol))
    n = days
    # 几何布朗运动 + **趋势项**
    # 趋势项不可省：engine 的市场过滤要求 HS300 处于 TREND_UP（close>EMA21 且 pvem>3%），
    # 纯随机游走会让 pvem 停在 ±1% 内 ⇒ 全部被 L800 的 MEAN_REVERSION 过滤掉 ⇒ 零交易
    # ⇒ 基线无鉴别力（改动前后都是 0 笔，比不出差异）
    if symbol == "000300":
        drift = 0.0022          # HS300：强上行，保证 TREND_UP
    else:
        drift = 0.0012          # 个股：温和上行
    vol = 0.010 + (_seed(symbol) % 5) * 0.001
    steps = rng.normal(drift, vol, n)
    close = 100.0 * np.exp(np.cumsum(steps))
    # 部分 bar 让 close == high（收盘在当日最高）—— 真实行情常见
    # ⚠️ 必要性：engine 的 Donchian 条件是 close >= high_20，而 high_20 含当日最高价
    #    ⇒ 若 close 恒小于 high，则该条件**恒假** ⇒ 零入场 ⇒ 基线无鉴别力
    high = close * (1.0 + np.abs(rng.normal(0, vol / 2, n)))
    close_at_high = rng.random(n) < 0.35
    high = np.where(close_at_high, close, high)
    low = close * (1.0 - np.abs(rng.normal(0, vol / 2, n)))
    open_ = np.concatenate(([close[0]], close[:-1]))
    volume = np.abs(rng.normal(1_000_000, 180_000, n)).round()

    idx = pd.bdate_range(end=AS_OF_DATE, periods=n)
    df = pd.DataFrame({
        "date": idx,
        "trade_date": idx,
        "open": open_.round(2),
        "high": high.round(2),
        "low": low.round(2),
        "close": close.round(2),
        "volume": volume.astype("int64"),
    })
    # 引擎需要的指标列（backtest/utils.add_indicators 会补，但引擎路径各异）
    df["EMA5"] = df["close"].ewm(span=5, adjust=False).mean().round(4)
    df["EMA21"] = df["close"].ewm(span=21, adjust=False).mean().round(4)
    df["vol_ma20"] = df["volume"].rolling(20, min_periods=1).mean().round(2)
    # ⚠️ high_20/low_20 必须是**前 N 日**（不含当日）—— 否则 close >= high_20 恒假，
    #    Donchian 入场条件永远无法满足（实测 0/320 命中）
    df["high_20"] = df["high"].rolling(20, min_periods=1).max().shift(1).round(4)
    df["low_20"] = df["low"].rolling(20, min_periods=1).min().shift(1).round(4)
    df["ATR"] = (df["high"] - df["low"]).rolling(14, min_periods=1).mean().round(4)
    df["rsi"] = 50.0  # 常数占位：仅需列存在，避免 KeyError
    df["macd"] = 0.0
    df["macd_signal"] = 0.0
    df["pre_close"] = df["close"].shift(1)
    df["pre_close"] = df["pre_close"].fillna(df["close"])
    df["pct_chg"] = df["close"].pct_change().fillna(0.0).round(6)
    df["is_limit_up"] = False
    df["is_limit_down"] = False
    df["is_suspend"] = False
    return df


def _canonical(value, tol: float = TOLERANCE):
    """把输出转成可 JSON 序列化、且对浮点微小抖动稳健的结构。"""
    import numpy as np
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
        # 量化到 1e-9 网格：让 1e-12 级噪声不触发差异
        return round(value / tol) * tol
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return _canonical(float(value), tol)
    if isinstance(value, dict):
        return {str(k): _canonical(v, tol) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v, tol) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonical(v, tol) for v in value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item") and hasattr(value, "dtype"):  # numpy scalar
        return _canonical(value.item(), tol)
    return str(value)


def _fingerprint_metrics(metrics: dict) -> dict:
    """从 metrics 里挑出**口径敏感**的标量指标做指纹。

    只取标量（数值/字符串），不取整个 equity curve ——
    curve 长度随实现细节漂移，而指标口径才是策略决策依据。
    """
    if not isinstance(metrics, dict):
        return {"_raw": _canonical(metrics)}
    out = {}
    for k, v in sorted(metrics.items()):
        if isinstance(v, (dict, list, tuple)):
            out[k] = {"_type": type(v).__name__, "_len": len(v)}
        elif isinstance(v, (int, float, str, bool)) or v is None:
            out[k] = _canonical(v)
        else:
            out[k] = _canonical(v)
    return out


def _summarize_trades(trades) -> dict:
    """交易明细的指纹：笔数 + 逐笔关键字段。

    注意：engine.run_backtest 返回的 trades 是 **DataFrame**（不是 list）
    ⇒ 必须先判类型，否则 len() 触发 ValueError（truth value of a DataFrame is ambiguous）
    """
    if trades is None:
        return {"count": 0, "trades": []}
    if hasattr(trades, "to_dict"):
        try:
            raw = trades.to_dict("records")
        except Exception:
            raw = []
    elif isinstance(trades, (list, tuple)):
        raw = list(trades)
    else:
        raw = []

    KEYS = ("symbol", "code", "side", "direction", "entry_date", "exit_date",
            "entry_price", "exit_price", "quantity", "shares", "pnl", "profit",
            "return_pct", "pnl_pct", "exit_reason", "reason", "signal_type",
            "hold_days", "atr_pct")
    rows = []
    for t in raw:
        if isinstance(t, dict):
            d = t
        elif hasattr(t, "__dict__"):
            d = vars(t)
        else:
            d = {"_repr": str(t)}
        rows.append({k: _canonical(d.get(k)) for k in sorted(d) if k in KEYS})
    return {"count": len(rows), "trades": rows}

def _capture_entry_signals() -> dict:
    """直接捕获三份 check_entry 的**判定序列** —— 这才是「入场口径」本身。

    为什么需要这一层：
    `engine.run_backtest` 的**串行路径**（L821）写死调用 `check_entry`，
    `multi_signal_entry` 参数只在并行 worker 路径（L361 的 `entry_fn`）生效
    ⇒ 串行模式下两种口径产出完全相同，基线无法区分它们。

    因此把「口径」拆成两层：
      ① 判定序列层（本函数）—— 覆盖三份 check_entry，直接锁定口径差异
      ② 引擎指标层（_run_core_chain / _run_engine_chain）—— 锁定整体回测产出
    """
    import backtest.engine as engine
    from backtest.utils import add_indicators

    out = {}
    for code in SYMBOLS:
        df = add_indicators(_synthetic_frame(code, DAYS).copy())
        for name, fn in (("donchian", engine.check_entry),
                         ("multi_signal", engine.check_entry_multi_signal)):
            hits = []
            for i in range(30, len(df) - 1):
                try:
                    ok, sig = fn(df, i)
                except Exception as exc:
                    hits.append({"i": i, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                if ok:
                    hits.append({"i": i, "sig": sig})
            out.setdefault(name, {})[code] = hits
    # full.py 的多信号口径（独立实现）
    try:
        import backtest.full as full
        for code in SYMBOLS:
            df = add_indicators(_synthetic_frame(code, DAYS).copy())
            hits = []
            for i in range(30, len(df) - 1):
                try:
                    if full.check_entry(df, i):
                        hits.append({"i": i})
                except Exception as exc:
                    hits.append({"i": i, "error": f"{type(exc).__name__}: {exc}"})
            out.setdefault("full_multi_signal", {})[code] = hits
    except Exception as exc:
        out["full_multi_signal_error"] = f"{type(exc).__name__}: {exc}"
    return out

def _run_core_chain():
    """链路 1：core.run_factor_backtest（因子回测主链路）。"""
    import backtest.core as core

    frames = {s: _synthetic_frame(s) for s in SYMBOLS}

    orig = core.get_stock_data

    def fake_get_stock_data(symbol, days=250, use_cache=True):
        return frames.get(symbol, _synthetic_frame(symbol, days)).copy()

    core.get_stock_data = fake_get_stock_data
    try:
        res = core.run_factor_backtest(symbols=list(SYMBOLS), days=DAYS, preset="balanced")
    finally:
        core.get_stock_data = orig

    if not isinstance(res, dict):
        return {"_raw": _canonical(res)}
    out = {
        "metrics": _fingerprint_metrics(res.get("metrics")),
        "trade_count": res.get("trade_count"),
        "cost_summary": _canonical(res.get("cost_summary")),
    }
    trades = res.get("trades")
    # engine 返回 DataFrame，core 返回 list ⇒ 两种都要处理
    if trades is not None and (isinstance(trades, (list, tuple)) or hasattr(trades, "to_dict")):
        out["trades"] = _summarize_trades(trades)
    return out


def _fake_batch(frames, codes, days=60):
    # rebalance 模式走 TDX 本地文件批量读（batch_read_day_files），
    # 绕过 get_stock_data => 必须单独注入，否则本地无 TDX 数据时返回 None。
    out = {}
    for code in codes:
        df = frames.get(code)
        if df is None:
            continue
        recs = df.to_dict("records")
        for r in recs:
            r["date"] = r["date"].strftime("%Y-%m-%d")
            r.pop("trade_date", None)
        out[code] = recs
    return out

def _run_engine_chain(multi_signal: bool):
    """链路 2/3：engine.run_backtest（默认 Donchian 口径 / 多信号 OR 口径）。"""
    import backtest.engine as engine
    import backtest.core as core
    import lib.data.data_layer as data_layer
    import lib.data.data_source_registry as registry

    # ⚠️ 必须 patch **三处**，缺一不可（P-19 的延伸）：
    #   ① engine.py:22 模块级 import → 已绑定到 backtest.engine 的属性
    #   ② engine.py:305 函数体内延迟 import → 运行时从**定义方**重新取
    #      ⇒ 只 patch ① 时，并行 worker 路径仍走真实数据源（实测 trades 0 → 6）
    #   ③ engine.py:682 函数体内延迟 import get_index_hist → patch data_layer
    frames = {s: _synthetic_frame(s) for s in SYMBOLS}

    orig_multi_engine = engine.get_stock_hist_multi_source
    orig_multi_registry = registry.get_stock_hist_multi_source
    orig_get = core.get_stock_data

    def fake_get_stock_hist_multi_source(code, days=180, source="auto", **kw):
        return frames.get(code, _synthetic_frame(code, days)).copy()

    def fake_get_stock_data(symbol, days=250, use_cache=True):
        return frames.get(symbol, _synthetic_frame(symbol, days)).copy()

    engine.get_stock_hist_multi_source = fake_get_stock_hist_multi_source
    registry.get_stock_hist_multi_source = fake_get_stock_hist_multi_source
    core.get_stock_data = fake_get_stock_data

    hs300_frame = _synthetic_frame("000300", DAYS)
    orig_index = data_layer.get_index_hist
    data_layer.get_index_hist = lambda code, days=180, **kw: hs300_frame.copy()
    try:
        res = engine.run_backtest(
            stock_codes=list(SYMBOLS),
            days=DAYS,
            parallel=False,
            multi_signal_entry=multi_signal,
            # 显式 pin 窗口起点：不传则由 datetime.now() 推导 => 基线随日历漂移
            start_date=PIN_START_DATE,
        )
    finally:
        engine.get_stock_hist_multi_source = orig_multi_engine
        registry.get_stock_hist_multi_source = orig_multi_registry
        core.get_stock_data = orig_get
        data_layer.get_index_hist = orig_index

    if not isinstance(res, dict):
        return {"_raw": _canonical(res)}
    out = {
        "metrics": _fingerprint_metrics(res.get("metrics")),
    }
    trades = res.get("trades")
    # engine 返回 DataFrame，core 返回 list ⇒ 两种都要处理
    if trades is not None and (isinstance(trades, (list, tuple)) or hasattr(trades, "to_dict")):
        out["trades"] = _summarize_trades(trades)
    curve = res.get("equity_curve")
    if isinstance(curve, list):
        out["equity_curve_len"] = len(curve)
        if curve:
            out["equity_first"] = _canonical(curve[0])
            out["equity_last"] = _canonical(curve[-1])
    return out


def _run_unified_modes():
    """统一入口（backtest.unified）的 6 个 mode 全部跑一遍。

    这是 Phase 3 的核心安全网：改造后所有调用方都走这里，
    因此「统一入口的参数透传是否正确」必须被尺子覆盖 ——
    漏传一个参数（如 exit_days_map / confidence_threshold）会**静默改变回测口径**。

    ⚠️ 必须注入合成数据：不注入则走真实数据源 ⇒ 跨环境不可复现 ⇒ 基线失效。
    """
    import backtest.core as core
    import backtest.engine as engine
    import lib.data.data_layer as data_layer
    import lib.data.data_source_registry as registry
    from backtest.unified import BacktestRunner

    frames = {s: _synthetic_frame(s) for s in SYMBOLS}
    hs300_frame = _synthetic_frame("000300", DAYS)

    def fake_multi(code, days=180, source="auto", **kw):
        return frames.get(code, _synthetic_frame(code, days)).copy()

    def fake_get(symbol, days=250, use_cache=True):
        return frames.get(symbol, _synthetic_frame(symbol, days)).copy()

    import lib.data.tdx_batch as tdx_batch

    orig = (engine.get_stock_hist_multi_source, registry.get_stock_hist_multi_source,
            core.get_stock_data, data_layer.get_index_hist, tdx_batch.batch_read_day_files)
    engine.get_stock_hist_multi_source = fake_multi
    registry.get_stock_hist_multi_source = fake_multi
    core.get_stock_data = fake_get
    data_layer.get_index_hist = lambda code, days=180, **kw: hs300_frame.copy()
    tdx_batch.batch_read_day_files = lambda codes, days=60, max_workers=8: _fake_batch(frames, codes, days)

    out = {}
    try:
        for mode in ("factor", "simple", "rebalance", "full", "walk_forward", "stress"):
            try:
                # stress: 默认 8 个压力窗口 => 8 次因子回测 + 蒙特卡洛再跑 1 次
                #          = 9 次（实测 152s，是其他链路的 60 倍）
                # => 这里只跑**第一个窗口**: 窗口口径仍被锁定, 耗时降到 ~1/8
                extra_kwargs = {}
                if mode == "stress":
                    try:
                        from backtest.stress import STRESS_WINDOWS
                        extra_kwargs["windows"] = list(STRESS_WINDOWS[:1])
                    except Exception:
                        pass
                unified = BacktestRunner().run(
                    mode=mode,
                    symbols=list(SYMBOLS),
                    days=DAYS,
                    # G1：显式 pin 窗口起点。simple/full 此前漏传 start_date，
                    # 引擎用 datetime.now()-days 推导 ⇒ 每跨一天交易数就变，
                    # 基线假红（2026-11-15 实测 6→5 笔、26 个 metrics 字段漂移）。
                    # PIN_START_DATE = 2025-12-22，与 2026-11-07 基线采集窗口一致。
                    start_date=PIN_START_DATE,
                    **extra_kwargs,
                )
            except Exception as exc:
                out[mode] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                continue
            if unified is None:
                out[mode] = {"ok": True, "data": {"_result": None}}
                continue
            raw = unified.raw if unified.raw is not None else {}
            metrics = raw.get("metrics") if isinstance(raw, dict) else None
            out[mode] = {
                "ok": True,
                "data": {
                    "mode": unified.mode,
                    # ⚠️ unified.trade_count 不可靠：多数引擎返回的 dict 没有 trade_count 键
                    #    （run_backtest 返回 trades DataFrame + metrics.total_trades）
                    #    ⇒ 交易数取 metrics.total_trades，并同时记录 trades 明细指纹
                    "trade_count": (metrics.get("total_trades")
                                     if isinstance(metrics, dict) else unified.trade_count),
                    "unified_trade_count": unified.trade_count,
                    "metrics": _fingerprint_metrics(metrics if isinstance(metrics, dict) else {}),
                    "trades": (_summarize_trades(raw.get("trades"))
                              if isinstance(raw, dict) else {"count": 0}),
                    "raw_keys": sorted(raw.keys()) if isinstance(raw, dict) else [],
                    # walk_forward 的实质产出在 rounds/summary（无 metrics/trades）
                    # stress 的实质产出在 dict 顶层（无 metrics/trades）
                    "extra": (
                        {
                            "summary": _canonical(raw.get("summary")),
                            "rounds_count": len(raw.get("rounds") or []),
                            "rounds": _canonical(raw.get("rounds")),
                        }
                        if mode == "walk_forward" and isinstance(raw, dict)
                        else (
                            # ⚠️ stress 的 monte_carlo 段是**未播种的随机量**
                            #    （实测两次运行 ci_5pct/prob_positive 等逐次不同）
                            #    ⇒ 随机量不能进基线，否则门禁随机假红。
                            #    这里只取确定性字段（窗口/统计口径），排除蒙特卡洛随机段。
                            {
                                "stress_keys": sorted(raw.keys()),
                                "stress_deterministic": _canonical({
                                    k: v for k, v in raw.items()
                                    if k not in ("monte_carlo", "bootstrap",
                                                "cost_sensitivity")
                                }),
                            }
                            if mode == "stress" and isinstance(raw, dict)
                            else {}
                        )
                    ),
                    "raw_top_level_scalars": {
                        k: _canonical(v) for k, v in sorted(raw.items())
                        if isinstance(v, (int, float, str, bool))
                    } if isinstance(raw, dict) else {},
                },
            }
    finally:
        (engine.get_stock_hist_multi_source, registry.get_stock_hist_multi_source,
         core.get_stock_data, data_layer.get_index_hist,
         tdx_batch.batch_read_day_files) = orig
    return out


CHAINS = {
    "entry_signals": lambda: _capture_entry_signals(),
    "unified_modes": lambda: _run_unified_modes(),
    "core_factor_backtest": lambda: _run_core_chain(),
    "engine_donchian_entry": lambda: _run_engine_chain(False),
    "engine_multi_signal_entry": lambda: _run_engine_chain(True),
}


def capture() -> dict:
    """跑三条链路，固化基线。"""
    chains = {}
    for name, fn in CHAINS.items():
        try:
            chains[name] = {"ok": True, "data": fn()}
        except Exception as exc:  # noqa: BLE001
            chains[name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
    return {
        "schema_version": 1,
        "constraints": {
            "seed_salt": SEED_SALT,
            "days": DAYS,
            "symbols": list(SYMBOLS),
            "as_of_date": AS_OF_DATE,
            "tolerance": TOLERANCE,
            "data_source": "synthetic (deterministic, zlib.crc32 seed)",
        },
        "chains": chains,
    }


def _diff(a, b, path="", out=None, tol=TOLERANCE):
    """字段级差异定位。"""
    if out is None:
        out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            _diff(a.get(k), b.get(k), f"{path}.{k}", out, tol)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path, f"len {len(a)}", f"len {len(b)}"))
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, tol)
        return out
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if abs(float(a) - float(b)) > tol:
            out.append((path, a, b))
        return out
    if a != b:
        out.append((path, a, b))
    return out


def check(base: dict, cur: dict) -> int:
    """比对基线与当前，字段级定位差异。返回退出码。"""
    ok = True
    for name in sorted(set(base.get("chains", {})) | set(cur.get("chains", {}))):
        b = base.get("chains", {}).get(name)
        c = cur.get("chains", {}).get(name)
        if b is None or c is None:
            print(f"[FAIL] chain {name}: missing in baseline or current")
            ok = False
            continue
        if not b.get("ok"):
            print(f"[WARN] chain {name}: baseline itself failed -> skip")
            continue
        if not c.get("ok"):
            print(f"[FAIL] chain {name}: current run failed: {c.get('error')}")
            ok = False
            continue
        diffs = _diff(b.get("data"), c.get("data"), name)
        if diffs:
            print(f"[FAIL] chain {name}: {len(diffs)} field(s) differ")
            for p, x, y in diffs[:20]:
                print(f"       {p}: {x} -> {y}")
            ok = False
        else:
            print(f"[PASS] chain {name}: identical")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="回测对拍基线工具")
    ap.add_argument("--capture", action="store_true", help="固化基线")
    ap.add_argument("--check", action="store_true", help="与基线比对（判红即退出码 1）")
    ap.add_argument("--summary", action="store_true", help="只打印统计")
    ap.add_argument("--out", default=str(ROOT / "docs" / "refactor" / "backtest-baseline.json"))
    ap.add_argument("--baseline", default=None, help="指定基线文件（--check 时）")
    args = ap.parse_args(argv)

    out_path = pathlib.Path(args.out)

    if args.summary:
        cur = capture()
        for name, c in cur["chains"].items():
            status = "ok" if c.get("ok") else f"FAILED: {c.get('error')}"
            n = (c.get("data", {}).get("trades", {}) or {}).get("count", 0) if c.get("ok") else 0
            print(f"  {name}: {status}  trades={n}")
        return 0

    if args.capture:
        cur = capture()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
        n_ok = sum(1 for c in cur["chains"].values() if c.get("ok"))
        print(f"[OK] baseline written: {out_path}  ({n_ok}/{len(cur['chains'])} chains ok)")
        for name, c in cur["chains"].items():
            if not c.get("ok"):
                print(f"     [WARN] chain {name} failed: {c.get('error')}")
        return 0

    if args.check:
        base_path = pathlib.Path(args.baseline) if args.baseline else out_path
        if not base_path.exists():
            print(f"[FAIL] baseline not found: {base_path}")
            return 1
        base = json.loads(base_path.read_text(encoding="utf-8"))
        cur = capture()
        rc = check(base, cur)
        if rc == 0:
            print("[PASS] backtest baseline: all chains match")
        return rc

    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
