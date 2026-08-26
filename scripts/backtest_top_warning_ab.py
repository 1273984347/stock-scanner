"""顶部预警 A/B 回测 —— G2 闸门量化验收

用法:
    python scripts/backtest_top_warning_ab.py --inspect-keys   # 打印 metrics 键名（首次校准）
    python scripts/backtest_top_warning_ab.py                  # 完整 A/B

G2 通过条件（两条件同时满足）:
    md_on  <= md_off * 0.90    （最大回撤改善 ≥ 10%）
    wr_on  >= wr_off - 0.05    （胜率回撤 ≤ 5 个百分点）
否则退出码 1。

符号集与天数固定，禁止改动以保证可比性。
"""
import argparse
import json
import sys
from datetime import datetime

SYMBOLS = ['600519', '000858', '601318', '600036', '000333',
           '601012', '600887', '000651', '600276', '300750']
DAYS = 2500


def _run(enabled: bool):
    from backtest.engine import run_rebalance_backtest
    return run_rebalance_backtest(symbols=SYMBOLS, days=DAYS,
                                  top_warning_enabled=enabled)


def _pick(metrics, keys):
    """依次尝试候选键（dict 或 dataclass 属性），返回第一个存在且非 None 的值"""
    for k in keys:
        if isinstance(metrics, dict):
            v = metrics.get(k)
        else:
            v = getattr(metrics, k, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inspect-keys", action="store_true")
    args = parser.parse_args()

    if args.inspect_keys:
        r = _run(False)
        print("report keys:", sorted(r.keys()))
        m = r.get("metrics")
        if isinstance(m, dict):
            print("metrics keys:", sorted(m.keys()))
            print(json.dumps(m, default=str)[:2000])
        else:
            print("metrics attrs:", sorted(vars(m).keys()))
            print(json.dumps(vars(m), default=str)[:2000])
        return 0

    r_off = _run(False)
    r_on = _run(True)
    m_off, m_on = r_off["metrics"], r_on["metrics"]

    # 同一口径比较；若 analyzer 键名不同，先 --inspect-keys 再修 _pick 候选键
    md_off = _pick(m_off, ["max_drawdown", "max_drawdown_pct"])
    md_on = _pick(m_on, ["max_drawdown", "max_drawdown_pct"])
    wr_off = _pick(m_off, ["win_rate"])
    wr_on = _pick(m_on, ["win_rate"])

    if None in (md_off, md_on, wr_off, wr_on):
        print("ERROR: 指标键名缺失，先运行 --inspect-keys 校准，再修 _pick 候选键")
        return 2

    # analyzer 口径：max_drawdown = drawdown.min() 为负数；取绝对值统一为回撤幅度再比较
    md_off, md_on = abs(md_off), abs(md_on)

    pass_md = md_on <= md_off * 0.90
    pass_wr = wr_on >= wr_off - 0.05
    result = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "symbols": SYMBOLS, "days": DAYS,
        "max_drawdown_off": md_off, "max_drawdown_on": md_on,
        "win_rate_off": wr_off, "win_rate_on": wr_on,
        "pass_drawdown_improved_10pct": bool(pass_md),
        "pass_winrate_within_5pp": bool(pass_wr),
        "G2_pass": bool(pass_md and pass_wr),
    }
    out = f"cache/top_warning_ab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("result ->", out)
    return 0 if result["G2_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
