"""C16 断网演练：验证「本地仓自给自足」的端到端判据。

前置：QUANT_OFFLINE=1 + socket 全阻断（loopback 放行）。
判定：回测与扫描两条主链路在零网络下**完成且产出结构合法**（允许候选为空
与显式降级日志，不允许裸异常/挂死）。
"""
import os
import socket
import sys
import warnings

os.environ["QUANT_OFFLINE"] = "1"
os.environ.setdefault("QUANT_LOCAL_STORE", "on")

# ---- 真·断网：阻断 socket 建连与 DNS（loopback 放行，asyncio self-pipe 需要）----
_REAL_CONNECT = socket.socket.connect
_REAL_GETADDRINFO = socket.getaddrinfo
_LOOP = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_local(addr):
    if isinstance(addr, (str, bytes)):
        return True
    if isinstance(addr, tuple) and addr:
        return str(addr[0]) in _LOOP
    return False


def _blocked_connect(self, address, *a, **k):
    if _is_local(address):
        return _REAL_CONNECT(self, address, *a, **k)
    raise OSError(f"DRILL: network blocked -> {address!r}")


def _blocked_getaddrinfo(host, *a, **k):
    if host is None or str(host) in _LOOP:
        return _REAL_GETADDRINFO(host, *a, **k)
    raise OSError(f"DRILL: DNS blocked -> {host!r}")


socket.socket.connect = _blocked_connect
socket.getaddrinfo = _blocked_getaddrinfo

sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

failures = []

# ---------------------------------------------------------------- 1) 断网回测
print("=" * 60)
print("[1] 断网回测（factor 模式，本地 kline 前复权 + 本地指数 + 本地日历）")
try:
    from backtest.unified import BacktestRequest, BacktestRunner

    result = BacktestRunner().run(BacktestRequest(
        mode="factor", symbols=["600519", "000001"], days=250))
    metrics = getattr(result, "metrics", None) or {}
    print(f"  metrics keys: {sorted(list(metrics))[:8]}")
    total = metrics.get("total_return", metrics.get("total_ret"))
    print(f"  total_return = {total}")
    print("  [PASS] 断网回测完成")
except Exception as e:
    import traceback
    failures.append("backtest")
    print(f"  [FAIL] 断网回测: {type(e).__name__}: {e}")
    traceback.print_exc(limit=4)

# ---------------------------------------------------------------- 2) 断网扫描
print("=" * 60)
print("[2] 断网扫描（本地 kline_raw + 本地资金流 + 本地指数 + 本地日历）")
try:
    from lib.strategy.pipeline import run_all_strategies

    res = run_all_strategies(preset="balanced",
                             symbols=["600519", "000001"],
                             include_ml=False)
    if res is None:
        res = {}
    results = res.get("results", {}) if isinstance(res, dict) else {}
    print(f"  results 策略数: {len(results)} -> {sorted(results)}")
    for name, df in sorted(results.items()):
        n = len(df) if hasattr(df, "__len__") else "?"
        print(f"    {name}: {n} 条候选")
    macro = res.get("macro_filter", {}) if isinstance(res, dict) else {}
    print(f"  macro regime: {macro.get('v4_regime', macro.get('regime', '?'))}")
    print("  [PASS] 断网扫描完成")
except Exception as e:
    import traceback
    failures.append("scan")
    print(f"  [FAIL] 断网扫描: {type(e).__name__}: {e}")
    traceback.print_exc(limit=4)

print("=" * 60)
if failures:
    print(f"[DRILL FAIL] 断网未自洽: {failures}")
    sys.exit(1)
print("[DRILL PASS] 断网演练通过：两条主链路零网络完成")
