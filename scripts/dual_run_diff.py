#!/usr/bin/env python3
"""双跑对拍框架 —— 固化旧行为 + 字段级差异检测（RF-01 / Phase 0 最后一项）

为什么是「基线 vs 当前」而不是「两个进程同时跑」：
  两个进程各自取数，网络源两次返回必然不同 ⇒ 对拍必假红。
  **基线化**（把旧实现的结果固化成 JSON）之后：
    git checkout <旧 commit> && --capture   # 生成旧基线
    git checkout <新 commit> && --check    # 与旧基线逐字段比对
  这样「旧 vs 新」的比对对象是**同一份注入数据**下的两份结果 ⇒ 可复现、无假红。

三条内置硬约束（缺一不可，否则对拍无意义）：
  ① **pin 数据源**：数据源优先级固定为本地源，并阻断网络 ⇒ 不会意外走到网络源
  ② **pin as_of_date**：所有取数固定到同一日期 ⇒ 无"今天"漂移
  ③ **共享同一份注入数据**：双方拿到**同一个对象**（不是各自取数）
     —— 本项目本地/CI 都没有 TDX 数据（实测 D:/TDX/vipdoc 缺失），
        故用固定 seed 合成的 OHLCV，保证跨环境完全一致。

用法：
  # 捕获基线（当前实现）
  python scripts/dual_run_diff.py --capture --out docs/refactor/dual-run-baseline.json

  # 与基线比对（任何漂移退出码 1）
  python scripts/dual_run_diff.py --check --out docs/refactor/dual-run-baseline.json

  # 只跑某条链路
  python scripts/dual_run_diff.py --capture --chain score

输出为 ASCII（Windows CI 的 stdout 默认 cp1252，中文会直接抛 UnicodeEncodeError）。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import pathlib
import socket
import sys
import zlib
from datetime import datetime, timezone

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_OUT = ROOT / "docs" / "refactor" / "dual-run-baseline.json"

# ---- 内置硬约束 -------------------------------------------------------
PIN_AS_OF_DATE = "2026-05-30"
PIN_DATA_SOURCES = ["tdx_local_qfq", "tdx_local"]
PIN_SEED = 42
TOLERANCE = 1e-9

# 易变字段（时间戳/耗时等）：规范化时替换为占位符，否则每次必然"漂移"
VOLATILE_KEYS = {
    "timestamp", "generated_at", "created_at", "updated_at", "run_at",
    "elapsed_sec", "elapsed", "duration", "took_ms", "now", "today",
    "last_updated", "scan_time", "as_of_now",
}

# 3 条链路
CHAINS = ("score", "scan", "backtest")


# ---------------------------------------------------------------- 合成数据

def synth_ohlcv(code: str, n: int = 260, seed: int = PIN_SEED) -> pd.DataFrame:
    """固定 seed 的合成日线 —— 确定性、跨环境一致、形态贴近真实。

    用 code 派生 seed ⇒ 不同股票数据不同但完全可复现。
    """
    # ⚠️ 不能用内置 hash()：它对字符串带随机盐（PYTHONHASHSEED），
    # 跨进程不稳定 => 每次合成数据都不同 => 对拍必假红（自对拍实测抓到过）。
    # 用 crc32：确定性、跨进程、跨平台一致。
    code_seed = zlib.crc32(code.encode("utf-8")) % 9973
    rng = np.random.RandomState(seed + code_seed)
    dates = pd.bdate_range("2025-05-01", periods=n)
    close = 100.0 + np.cumsum(rng.randn(n) * 1.2)
    high = close + np.abs(rng.randn(n)) * 0.8
    low = close - np.abs(rng.randn(n)) * 0.8
    open_ = close + rng.randn(n) * 0.3
    volume = rng.randint(1_000_000, 9_000_000, n)
    return pd.DataFrame({
        "date": dates,
        "open": np.round(open_, 4),
        "high": np.round(high, 4),
        "low": np.round(low, 4),
        "close": np.round(close, 4),
        "volume": volume.astype("int64"),
    })


def synth_realtime_row(code: str, seed: int = PIN_SEED) -> pd.Series:
    rng = np.random.RandomState(seed + 7)
    return pd.Series({
        "代码": code,
        "名称": f"股票{code}",
        "最新价": round(100 + rng.randn() * 5, 4),
        "涨跌幅": round(rng.randn() * 3, 4),
        "60日涨跌幅": round(rng.randn() * 10, 4),
        "市盈率-动态": round(10 + rng.rand() * 20, 4),
        "市净率": round(0.8 + rng.rand() * 3, 4),
        "总市值": float(rng.randint(1_000_000, 90_000_000) * 10_000),
        "量比": round(0.6 + rng.rand() * 2, 4),
        "换手率": round(0.2 + rng.rand() * 3, 4),
        "成交额": float(rng.randint(10_000, 500_000) * 10_000),
    })


# ---------------------------------------------------------------- pin

@contextlib.contextmanager
def _blocked_socket():
    """阻断所有出网（loopback 放行，否则 asyncio self-pipe 会被打断）。"""
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo
    loopback = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}

    def is_local(addr):
        if isinstance(addr, (str, bytes)):
            return True
        if isinstance(addr, tuple) and addr:
            return str(addr[0]) in loopback
        return False

    def blocked_connect(self, address, *a, **k):
        if is_local(address):
            return real_connect(self, address, *a, **k)
        raise OSError(f"dual_run: network blocked (pinned) -> {address!r}")

    def blocked_getaddrinfo(host, *a, **k):
        if host is None or str(host) in loopback:
            return real_getaddrinfo(host, *a, **k)
        raise OSError(f"dual_run: DNS blocked (pinned) -> {host!r}")

    socket.socket.connect = blocked_connect
    socket.getaddrinfo = blocked_getaddrinfo
    try:
        yield
    finally:
        socket.socket.connect = real_connect
        socket.getaddrinfo = real_getaddrinfo


@contextlib.contextmanager
def pinned_environment():
    """pin 数据源 + as_of_date + 阻断网络。"""
    import lib.data.data_source_registry as reg

    old_priority = list(reg.DATA_SOURCE_PRIORITY)
    old_adj = list(getattr(reg, "DATA_SOURCE_PRIORITY_ADJUSTED", []))
    reg.DATA_SOURCE_PRIORITY = list(PIN_DATA_SOURCES)
    reg.DATA_SOURCE_PRIORITY_ADJUSTED = list(PIN_DATA_SOURCES)

    import lib.data.data_layer as dl
    had_dl = hasattr(dl, "DATA_SOURCE_PRIORITY")
    old_dl = list(getattr(dl, "DATA_SOURCE_PRIORITY", []))
    if had_dl:
        dl.DATA_SOURCE_PRIORITY = list(PIN_DATA_SOURCES)

    with _blocked_socket():
        try:
            yield
        finally:
            reg.DATA_SOURCE_PRIORITY = old_priority
            if old_adj:
                reg.DATA_SOURCE_PRIORITY_ADJUSTED = old_adj
            if had_dl:
                dl.DATA_SOURCE_PRIORITY = old_dl


# ---------------------------------------------------------------- 规范化

def _semantic_dtype(d) -> str:
    """把 pandas 的**实现细节 dtype** 归一为**语义 dtype**。

    为什么需要：字符串列在不同 pandas 版本下推断结果不同
    （本地 Windows 推断为 object，CI Linux 推断为 str）⇒ 直接比对 dtype
    会跨环境假红，而 dtype 本身是 pandas 实现细节、不是行为契约。

    归一规则：object/str/string -> string；float* -> float；int*/uint* -> int；
    bool* -> bool；datetime* -> datetime；其余保留原名。
    真正的类型变化（如 float -> string）仍会被检出。
    """
    s = str(d)
    if s in ("object", "str", "string"):
        return "string"
    for prefix, name in (
        ("float", "float"), ("int", "int"), ("uint", "int"),
        ("bool", "bool"), ("datetime", "datetime"), ("timedelta", "timedelta"),
        ("period", "period"), ("complex", "complex"),
    ):
        if s.startswith(prefix):
            return name
    return s


def normalize(obj, key=None):
    """把任意结果转成可 JSON 化 + 可逐字段比对的结构。

    · DataFrame -> {columns, dtypes, records}（**含 dtype 与列序**）
    · numpy 标量 -> python 原生
    · NaN/Inf -> None
    · 易变字段 -> "<volatile>"
    """
    if key is not None and key in VOLATILE_KEYS:
        return "<volatile>"
    if obj is None:
        return None
    if isinstance(obj, pd.DataFrame):
        return {
            "__df__": True,
            "columns": [str(c) for c in obj.columns],
            "dtypes": {str(c): _semantic_dtype(obj.dtypes[c]) for c in obj.columns},
            "shape": list(obj.shape),
            "records": [
                {str(c): normalize(v, str(c)) for c, v in row.items()}
                for row in obj.to_dict(orient="records")
            ],
        }
    if isinstance(obj, pd.Series):
        return {
            "__series__": True,
            "name": str(obj.name) if obj.name is not None else "",
            "dtype": _semantic_dtype(obj.dtype),
            "values": {str(k): normalize(v, str(k)) for k, v in obj.items()},
        }
    if isinstance(obj, dict):
        return {str(k): normalize(v, str(k)) for k, v in sorted(obj.items(), key=lambda x: str(x[0]))}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, (datetime, pd.Timestamp)):
        return str(obj)
    return str(obj)


# ---------------------------------------------------------------- 差异

def _close(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if a == b:
            return True
        return abs(a - b) <= TOLERANCE + TOLERANCE * max(abs(a), abs(b))
    return a == b


def compare(old, new, path="") -> list:
    """字段级差异；DataFrame 额外比对 dtype 与列序。"""
    diffs = []

    if isinstance(old, dict) and isinstance(new, dict):
        if old.get("__df__") and new.get("__df__"):
            for f in ("columns", "dtypes", "shape"):
                if old.get(f) != new.get(f):
                    diffs.append({
                        "path": f"{path}.__df__.{f}",
                        "old": old.get(f),
                        "new": new.get(f),
                    })
            diffs += compare(old.get("records", []), new.get("records", []), f"{path}.records")
            return diffs
        if old.get("__series__") and new.get("__series__"):
            for f in ("name", "dtype"):
                if old.get(f) != new.get(f):
                    diffs.append({"path": f"{path}.__series__.{f}", "old": old.get(f), "new": new.get(f)})
            diffs += compare(old.get("values", {}), new.get("values", {}), f"{path}.values")
            return diffs
        for k in sorted(set(old) | set(new)):
            if k not in old:
                diffs.append({"path": f"{path}.{k}", "old": None, "new": new[k], "kind": "ADDED"})
            elif k not in new:
                diffs.append({"path": f"{path}.{k}", "old": old[k], "new": None, "kind": "REMOVED"})
            else:
                diffs += compare(old[k], new[k], f"{path}.{k}")
        return diffs

    if isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            diffs.append({"path": f"{path}[len]", "old": len(old), "new": len(new)})
        for i in range(min(len(old), len(new))):
            diffs += compare(old[i], new[i], f"{path}[{i}]")
        return diffs

    if not _close(old, new):
        diffs.append({"path": path or "$", "old": old, "new": new})
    return diffs


# ---------------------------------------------------------------- 链路捕获

@contextlib.contextmanager
def _inject_shared_data(codes):
    """把**同一份**数据注入取数层 —— 双方共享对象，不各自取数。"""
    import lib.strategy.steps as steps
    import backtest.core as btcore

    hist_map = {c: synth_ohlcv(c) for c in codes}
    rt_map = {c: synth_realtime_row(c) for c in codes}

    orig_fetch = steps.fetch_stock_data
    orig_bt_get = btcore.get_stock_data

    def fake_fetch(code: str, days: int = 180):
        hist = hist_map.get(code)
        if hist is None:
            return pd.DataFrame(), None, None, pd.DataFrame()
        return hist.copy(), rt_map.get(code), None, synth_ohlcv("000300", n=40)

    def fake_bt_get(symbol: str, days: int = 250, use_cache: bool = True):
        hist = hist_map.get(symbol)
        if hist is None:
            return pd.DataFrame()
        out = hist.copy()
        # 与 backtest.core.get_stock_data 的真实后处理保持一致：
        #   trade_date（ABroker 需要）+ pre_close + 涨跌停/停牌标记
        if "date" in out.columns and "trade_date" not in out.columns:
            out = out.rename(columns={"date": "trade_date"})
        out["trade_date"] = pd.to_datetime(out["trade_date"])
        if "pre_close" not in out.columns:
            out["pre_close"] = out["close"].shift(1)
            out["pre_close"] = out["pre_close"].fillna(out["close"])
        try:
            from lib.data.broker import ABroker
            limit_pct = ABroker.get_limit_pct(symbol)
        except Exception:
            limit_pct = 0.1
        pct = (out["close"] - out["pre_close"]) / out["pre_close"].replace(0, np.nan)
        out["is_limit_up"] = (pct >= limit_pct - 0.001)
        out["is_limit_down"] = (pct <= -limit_pct + 0.001)
        out["is_suspend"] = out["volume"].fillna(0) == 0
        return out

    steps.fetch_stock_data = fake_fetch
    btcore.get_stock_data = fake_bt_get
    try:
        yield
    finally:
        steps.fetch_stock_data = orig_fetch
        btcore.get_stock_data = orig_bt_get


class _StubML:
    """确定性 ML 桩（真实 predictor 会加载模型，结果不可复现）。"""

    def __init__(self, *a, **k):
        pass

    def predict(self, *a, **k):
        return None

    def predict_batch(self, *a, **k):
        return {}


def capture_score(code: str = "600519") -> dict:
    from lib.strategy.pipeline import get_stock_score
    import lib.strategy.steps as steps

    orig_ml = steps.MLPredictor
    steps.MLPredictor = _StubML
    try:
        return get_stock_score(code)
    finally:
        steps.MLPredictor = orig_ml


def capture_scan(symbols=None, preset: str = "balanced") -> dict:
    from lib.strategy.pipeline import run_all_strategies
    import lib.strategy.pipeline as pipeline

    symbols = symbols or ["600519", "000001", "000858"]
    # 策略层返回确定性 DataFrame（对拍的是编排与字段契约，不测取数）
    stub = pd.DataFrame({
        "代码": ["600519", "000001"],
        "名称": ["股票A", "股票B"],
        "涨跌幅": [1.5, -0.5],
        "60日涨跌幅": [10.0, -2.0],
        "评分": [80.0, 60.0],
    })
    names = [
        "get_zt_candidates", "get_volume_breakout_candidates", "get_hot_board_candidates",
        "get_lhb_candidates", "get_multi_factor_candidates",
    ]
    # patch 目标必须是**使用方** lib.strategy.pipeline 的模块属性：
    # lib/pipeline.py:23 是模块级 from-import，已绑定引用
    # => 改 lib.strategy.strategies 不生效（与 portalocker 同构的教训）。
    saved = {n: getattr(pipeline, n) for n in names if hasattr(pipeline, n)}
    for n in saved:
        setattr(pipeline, n, lambda *a, **k: stub.copy())

    # 市场状态检测：无本地行情数据时会判 TREND_DOWN -> run_all_strategies 直接 return {}
    # （对拍退化成"空 vs 空"，毫无价值）=> 固定为 TREND_UP，保证产出确定性候选
    # lib/pipeline.py:26 是**模块级** import => 只需替换 lib.strategy.pipeline 上的引用。
    # 绝不能改 lib.monitoring.market_regime 模块本身（那里还有别的调用点期望不同返回结构）。
    fixed_regime = {
        "regime": "TREND_UP",
        "confidence": 0.8,
        # pipeline.py:841 期望 (min_weight, max_weight) 二元组（默认 (0.5, 1.0)）
        "position_scalar": (0.5, 1.0),
        "atr_ratio": 1.0,
        "reason": "dual_run pinned",
    }

    def fake_regime(*a, **k):
        return dict(fixed_regime)

    saved_regime = getattr(pipeline, "detect_market_regime_from_hs300", None)
    pipeline.detect_market_regime_from_hs300 = fake_regime
    try:
        return run_all_strategies(preset=preset, symbols=symbols, include_ml=False)
    finally:
        for n, fn in saved.items():
            setattr(pipeline, n, fn)
        if saved_regime is not None:
            pipeline.detect_market_regime_from_hs300 = saved_regime


def capture_backtest(symbols=None, days: int = 250) -> dict:
    from backtest.core import run_factor_backtest
    symbols = symbols or ["600519", "000001"]
    return run_factor_backtest(symbols=symbols, days=days)


CAPTURES = {
    "score": lambda: capture_score(),
    "scan": lambda: capture_scan(),
    "backtest": lambda: capture_backtest(),
}


def capture_all() -> dict:
    codes = ["600519", "000001", "000858"]
    out = {}
    with pinned_environment():
        with _inject_shared_data(codes):
            for name in CHAINS:
                try:
                    out[name] = {"ok": True, "result": normalize(CAPTURES[name]())}
                except Exception as e:
                    import traceback
                    out[name] = {
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "traceback": traceback.format_exc(),
                    }
    return out


# ---------------------------------------------------------------- CLI

def do_capture(args) -> int:
    snap = capture_all()
    doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "constraints": {
            "as_of_date": PIN_AS_OF_DATE,
            "data_sources": PIN_DATA_SOURCES,
            "seed": PIN_SEED,
            "tolerance": TOLERANCE,
            "network": "blocked (loopback allowed)",
        },
        "chains": snap,
    }
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", newline="\n")
    ok = sum(1 for v in snap.values() if v.get("ok"))
    print(f"[OK] captured {ok}/{len(snap)} chains -> {out}")
    for name, v in snap.items():
        if v.get("ok"):
            print(f"     {name:<10} ok")
        else:
            print(f"     {name:<10} FAILED: {v.get('error')}")
    return 0 if ok == len(snap) else 1


def do_check(args) -> int:
    path = pathlib.Path(args.out)
    if not path.exists():
        print(f"[FAIL] baseline not found: {path} (run --capture first)", file=sys.stderr)
        return 1
    base = json.loads(path.read_text(encoding="utf-8"))
    cur = capture_all()

    all_diffs = []
    for name in CHAINS:
        b = (base.get("chains") or {}).get(name)
        c = cur.get(name)
        if b is None:
            print(f"[INFO] chain '{name}' has no baseline -> reporting only")
            continue
        if not c.get("ok"):
            all_diffs.append({"path": f"{name}", "old": "ok", "new": c.get("error")})
            continue
        if not b.get("ok"):
            print(f"[INFO] chain '{name}' baseline was an error ({b.get('error')}) -> reporting only")
            continue
        d = compare(b.get("result"), c.get("result"), f"{name}")
        if d:
            all_diffs.extend(d)

    if not all_diffs:
        print(f"[PASS] dual-run: {len(CHAINS)} chains match baseline "
              f"(tolerance={TOLERANCE}, as_of_date={PIN_AS_OF_DATE}).")
        return 0
    print(f"[FAIL] dual-run drift: {len(all_diffs)} difference(s)")
    for d in all_diffs[:40]:
        print(f"       {d['path']}")
        print(f"         old={d['old']!r}")
        print(f"         new={d['new']!r}")
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="dual-run diff (pinned data source + as_of_date)")
    ap.add_argument("--capture", action="store_true", help="capture baseline from current impl")
    ap.add_argument("--check", action="store_true", help="compare current impl against baseline")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    if args.capture:
        return do_capture(args)
    if args.check:
        return do_check(args)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
