#!/usr/bin/env python3
"""新股补充取数：把 HS300 样本外的新股纳入 ESG/风险验证

背景：HS300 成分不含上市不足期的新股，但新股恰是风险因子最需要验证的群体
（历史短、波动大、指标口径可能失真）。

取数口径（与主样本一致，避免口径漂移）：
- 风险指标：**逐只单查**（多主体查询实测会混淆回撤/波动率）
- ESG：批量 5 只（已验证稳定）
- 落盘含上市日期，便于区分"上市不足 52 周"样本

用法：
    py scripts/ifind_newstock_supplement.py                      # 默认科创板 2025-12 以来
    py scripts/ifind_newstock_supplement.py --codes 688795,688802
    py scripts/ifind_newstock_supplement.py --since 2025-12-01 --board kcb

⚠️ 定位说明：本脚本随 iFinD 风险因子线于 2026-11-27 关闭（ADR-0037 §决策4），
   作为**重启资产**保留——产出 27 只新股的风险指标快照，已固化于
   docs/adr/0037-evidence/20261127_044905_newstock_supplement.json。
   与 blocks 行业映射补全（scripts/fill_missing_blocks.py）无关，勿混用。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "ifind"


def _call(tool: str, params: dict, interval: float) -> dict:
    skill = Path(os.environ.get("IFIND_SKILL_DIR",
                                Path.home() / ".workbuddy" / "skills" / "ifind-finance-data"))
    spec = importlib.util.spec_from_file_location("_ifind_call", skill / "call.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    time.sleep(interval)
    res = mod.call("stock", tool, params)
    if not isinstance(res, dict) or res.get("ok") is False:
        raise RuntimeError(f"{tool} failed: {res.get('error') if isinstance(res, dict) else res}")
    return res


def _inner(res: dict) -> dict:
    payload = res.get("data", {}).get("result", {})
    for item in payload.get("content", []):
        try:
            return json.loads(item["text"])["data"]
        except (KeyError, ValueError, TypeError):
            continue
    return {}


def _rows(answer: str) -> list[dict[str, str]]:
    lines = [ln for ln in answer.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 3:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    out = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(header):
            out.append(dict(zip(header, cells)))
    return out


def _pick(row: dict[str, str], *tokens: str) -> str | None:
    for token in tokens:
        for k, v in row.items():
            if token in k:
                return v
    return None


def _code(row: dict[str, str]) -> str:
    return (_pick(row, "证券代码", "代码") or "").split(".")[0]


def _num(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _universe(since: str, board: str) -> list[dict[str, str]]:
    """按板块取上市日期 >= since 的新股"""
    import akshare as ak

    calls = {
        "kcb": [("stock_info_sh_name_code", {"symbol": "科创板"})],
        "all": [("stock_info_sh_name_code", {"symbol": "科创板"}),
                ("stock_info_sh_name_code", {"symbol": "主板A股"}),
                ("stock_info_sz_name_code", {"symbol": "A股列表"}),
                ("stock_info_bj_name_code", {})],
    }[board]

    out = []
    for fn, kw in calls:
        try:
            df = getattr(ak, fn)(**kw)
            dc = next(c for c in df.columns if "代码" in c)
            dd = next(c for c in df.columns if "日期" in c or "时间" in c)
            for _, row in df.iterrows():
                date = str(row[dd])[:10]
                if date >= since:
                    out.append({"code": str(row[dc]).zfill(6), "list_date": date})
        except Exception as exc:  # noqa: BLE001
            print(f"  [universe] {fn} 失败: {exc}", file=sys.stderr)
    # 去重
    seen, uniq = set(), []
    for item in out:
        if item["code"] not in seen:
            seen.add(item["code"])
            uniq.append(item)
    return sorted(uniq, key=lambda x: x["list_date"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--codes", default="", help="逗号分隔的 6 位代码；给了就忽略 --since/--board")
    ap.add_argument("--since", default="2025-12-01", help="上市日期下限 YYYY-MM-DD")
    ap.add_argument("--board", default="kcb", choices=["kcb", "all"], help="kcb=科创板 / all=沪深北")
    ap.add_argument("--interval", type=float, default=0.6)
    args = ap.parse_args()

    if args.codes:
        universe = [{"code": c.strip(), "list_date": "unknown"}
                    for c in args.codes.split(",") if c.strip()]
    else:
        universe = _universe(args.since, args.board)
    if not universe:
        raise RuntimeError("新股清单为空，拒绝产出空结果")
    print(f"新股 {len(universe)} 只（{args.board} / since {args.since}）", flush=True)

    # 风险指标：逐只单查
    for i, item in enumerate(universe):
        code = item["code"]
        try:
            data = _inner(_call("get_risk_indicators",
                                {"query": f"{code}的夏普比率、最大回撤、波动率"}, args.interval))
            row = next((r for r in _rows(data.get("answer", "")) if _code(r) == code), {})
            item.update({
                "sharpe": _num(_pick(row, "Sharpe", "夏普")),
                "max_drawdown_pct": _num(_pick(row, "最大回撤")),
                "volatility_pct": _num(_pick(row, "波动率")),
                "risk_raw": data.get("answer", ""),
                "risk_params": data.get("indicators_params", {}),
            })
        except Exception as exc:  # noqa: BLE001
            item.update({"sharpe": None, "max_drawdown_pct": None,
                         "volatility_pct": None, "risk_error": str(exc)})
        if (i + 1) % 10 == 0:
            print(f"  风险进度 {i + 1}/{len(universe)}", flush=True)

    # ESG：批量 5 只
    for i in range(0, len(universe), 5):
        batch = universe[i:i + 5]
        q = "、".join(b["code"] for b in batch) + "的ESG评级"
        try:
            data = _inner(_call("get_esg_data", {"query": q}, args.interval))
            rows = _rows(data.get("answer", ""))
            for item in batch:
                row = next((r for r in rows if _code(r) == item["code"]), {})
                item["esg_rating"] = _pick(row, "ESG评级")
        except Exception as exc:  # noqa: BLE001
            for item in batch:
                item["esg_error"] = str(exc)

    ok = sum(1 for x in universe if x.get("sharpe") is not None)
    print(f"风险覆盖 {ok}/{len(universe)}")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_newstock_supplement.json"
    out.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "board": args.board if not args.codes else "manual",
        "since": args.since if not args.codes else None,
        "n": len(universe),
        "risk_covered": ok,
        "items": universe,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for item in universe[:40]:
        print(f"  {item['code']} 上市{item['list_date']} sharpe={item.get('sharpe')} "
              f"mdd={item.get('max_drawdown_pct')} vol={item.get('volatility_pct')} "
              f"esg={item.get('esg_rating')}")
    print(f"落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
