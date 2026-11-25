#!/usr/bin/env python
"""评分对拍基线 —— RF-01 Phase 4 的核心安全网。

证明「引入统一内核 + 9 个适配器」**没有改变任何评分数值**：
每条链路用同一组固定输入，分别跑「直接调旧实现」与「经 ScoringKernel 调适配器」，
逐字段比对（容差 1e-9），并把旧实现的输出固化为 JSON 指纹。

三条内置硬约束（与 backtest_baseline.py 同口径，缺一不可）：
  1. **固定合成数据** —— 用 zlib.crc32 派生确定性种子，不用内置 hash()
  2. **pin 住全部外部数据入口** —— 资金流/新闻/板块/大盘/行业/历史上下文；
     模块级 from-import patch 使用方，延迟 import patch 定义方（P-18/P-19、RULES §4.5 G2）
  3. **同一份输入、同一个进程** —— legacy 与 kernel 共用 inputs 对象

用法：
  python scripts/scoring_baseline.py --capture --out docs/refactor/scoring-baseline.json
  python scripts/scoring_baseline.py --check   --out docs/refactor/scoring-baseline.json
  python scripts/scoring_baseline.py --summary
输出为纯 ASCII（Windows CI stdout 默认 cp1252，中文 print 会抛 UnicodeEncodeError）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import math
import os
import pathlib
import sys
import traceback
import zlib

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass


def _resolve_repo_root(start):
    """自锚定仓库根：向上找同时含 AGENTS.md 与 lib/ 的目录，脚本可任意移动。"""
    for cand in (start, *start.parents):
        if (cand / "AGENTS.md").is_file() and (cand / "lib").is_dir():
            return cand
    raise SystemExit(f"[gate] 无法定位仓库根: 从 {start} 向上未找到 AGENTS.md + lib/")


ROOT = _resolve_repo_root(pathlib.Path(__file__).resolve().parent)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SEED_SALT = "stock-scanner-scoring-baseline-v1"
TOLERANCE = 1e-9
SAMPLE_CODES = [
    "600519", "000858", "600036", "000001", "601318", "600030", "000333", "002415",
    "600887", "601899", "600276", "300750", "002594", "601012", "600009", "000651",
    "601166", "002304", "600585", "601288",
]
SAMPLE_DATES = ["2026-05-28", "2026-05-29", "2026-05-30"]
HEAVY_SAMPLE_CODES = ["600519", "000858", "600036"]  # DataFrame 重链路少跑几组

FIXED_FUND_FLOW = {"main_net_ratio": 0.06, "net_inflow_5d": 1.2e8}
FIXED_NEWS = {
    "major_positive": False, "institutional_buy": False,
    "no_negative": True, "earnings_positive": False,
}


def _seed(text: str) -> int:
    return zlib.crc32((SEED_SALT + "|" + text).encode("utf-8"))


def _unit(code: str, date: str, salt: str = "") -> float:
    """确定性 0~1 浮点数（不用 random，跨进程/跨平台一致）。"""
    return (_seed(f"{code}|{date}|{salt}") % 10000) / 10000.0


def _inputs(code: str, date: str) -> dict:
    """构造一条链路的全部输入（纯数据，不含任何随机源）。"""
    import numpy as np
    import pandas as pd

    u = lambda s: _unit(code, date, s)  # noqa: E731
    n = 60
    rng = np.random.default_rng(_seed(f"{code}|{date}|hist"))
    close = 100.0 + np.cumsum(rng.normal(0.05, 0.8, n))
    dates = pd.bdate_range(end=date, periods=n)
    hist = pd.DataFrame({
        "date": dates,
        "open": np.round(close + rng.normal(0, 0.2, n), 4),
        "high": np.round(close + np.abs(rng.normal(0, 0.5, n)), 4),
        "low": np.round(close - np.abs(rng.normal(0, 0.5, n)), 4),
        "close": np.round(close, 4),
        "volume": np.abs(rng.normal(2e6, 3e5, n)).round().astype("int64"),
    })
    row = pd.Series({
        "代码": code, "名称": f"股票{code}",
        "最新价": round(100 + u("price") * 20, 4),
        "涨跌幅": round(u("chg") * 6 - 3, 4),
        "60日涨跌幅": round(u("ret60") * 20 - 10, 4),
        "市盈率-动态": round(10 + u("pe") * 25, 4),
        "市净率": round(0.8 + u("pb") * 3, 4),
        "总市值": float(1e10 + u("mcap") * 5e11),
        "量比": round(0.6 + u("vr") * 2, 4),
        "换手率": round(0.2 + u("to") * 4, 4),
        "成交额": float(1e8 + u("amt") * 8e8),
    })
    factors = {
        "trend_strength": u("trend") * 10 - 3,
        "momentum_5": u("m5") * 10 - 5,
        "momentum_20": u("m20") * 20 - 10,
        "momentum_10": u("m10") * 12 - 6,
        "volume_ratio": 0.5 + u("vol") * 2,
        "volatility_20": u("vola") * 5,
        "rsi_14": 20 + u("rsi") * 60,
        "turnover_avg": u("ta") * 8,
        "max_drawdown": -u("dd") * 20,
        "pe_ttm": 10 + u("pettm") * 30,
        "roe": u("roe") * 0.3,
    }
    return {
        "code": code, "as_of_date": date, "row": row, "hist": hist,
        "hist_df": hist, "factors": factors, "data_factors": factors,
        "fundamentals": {"pe": 20.0, "roe": 0.15},
        "market_data": {"regime": "TREND_UP"},
        "fund_flow": dict(FIXED_FUND_FLOW), "news_sig": dict(FIXED_NEWS),
        # 随样本确定性变化（否则 scorecard 只剩 2 种输出，基线无鉴别力）
        "market_brief": {"l1": round(u("l1"), 4),
                         "bench_ret_5d": round(u("bench") * 0.1 - 0.05, 6)},
        "industry_ret": round(u("indret") * 0.1 - 0.05, 6),
        "sector_data": {},
        # judge_panel 走 evaluate_with_ml 才有实质投票（evaluate_all 自 P0#13 起恒返回 HOLD/0）
        "ml_prediction": {
            "model_available": True,
            "consensus": "BUY" if u("cons") > 0.5 else "HOLD",
            "confidence": round(u("conf"), 4),
            "score": round(u("mlscore") * 100, 4),
            # _evaluate_ml 要求 [{"name":..., "value":...}]，给字符串会触发 TypeError
            "top3_factors": [
                {"name": "trend_strength", "value": round(u("t3a") * 10, 4)},
                {"name": "volume_ratio", "value": round(u("t3b") * 10, 4)},
                {"name": "momentum_20", "value": round(u("t3c") * 10, 4)},
            ],
        },
        "fundamental": round(u("f") * 10, 4),
        "capital_flow": round(u("c") * 10, 4),
        "news": round(u("nw") * 10, 4),
        "technical": round(u("t") * 10, 4),
        "sector_score": round(u("sec") * 100, 4),
        "regime": "TREND_UP", "regime_confidence": round(u("rc"), 4),
        "alpha_posterior": round(u("ap"), 4),
        "trend_strength": factors["trend_strength"],
        "volume_ratio": factors["volume_ratio"],
    }


# ---------------------------------------------------------------- 规范化

def _canonical(value, tol: float = TOLERANCE):
    import numpy as np
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return str(value)
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
        return sorted(str(v) for v in value)
    if hasattr(value, "to_dict"):  # DataFrame / Series / dataclass-ish
        try:
            return _canonical(value.to_dict(), tol)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return _canonical(vars(value), tol)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------- pin

@contextlib.contextmanager
def pinned_scoring_environment():
    """固定所有评分链路可能触达的外部输入（RULES §4.5 G2）。"""
    targets = []

    def pin(module: str, name: str, fake):
        mod = importlib.import_module(module)
        if hasattr(mod, name):
            targets.append((mod, name, getattr(mod, name)))
            setattr(mod, name, fake)

    ff = lambda *a, **k: dict(FIXED_FUND_FLOW)  # noqa: E731
    news = lambda *a, **k: dict(FIXED_NEWS)  # noqa: E731
    # 使用方（模块级 from-import 已绑定）
    pin("lib.strategy.strategies", "get_stock_fund_flow", ff)
    pin("lib.strategy.strategies", "get_news_signals", news)
    # 定义方（函数内延迟 import 会重新从定义方取）
    pin("lib.data.data_layer", "get_stock_fund_flow", ff)
    pin("lib.data.data_layer", "get_news_signals", news)
    pin("lib.strategy.sector_score", "get_sector_score_for_stock", lambda *a, **k: 50.0)
    # 打分卡的市场/行业/层级输入
    sc = importlib.import_module("lib.scoring.scorecard")
    for name in ("_get_market_return", "_get_industry_return"):
        pin("lib.scoring.scorecard", name, lambda *a, **k: 0.01)
    for name in ("_compute_l1", "layer1_market_trend", "layer2_sector_strength",
                 "layer3_money_flow", "layer4_pattern_score"):
        # ⚠️ 必须从 __dict__ 取原始 staticmethod 描述符；用 getattr 拿到的是解包后的
        # 函数，还原时 setattr 会把它变成实例方法（多一个 self）=> 污染同进程调用方。
        orig = sc.MarketFilter.__dict__.get(name)
        if orig is not None:
            targets.append((sc.MarketFilter, name, orig))
            setattr(sc.MarketFilter, name, staticmethod(lambda *a, **k: 0.5))
    pin("lib.scoring.scorecard", "_get_industry", lambda *a, **k: "PinnedIndustry")
    # AgentPanel 的 past_context 会读本地 signal_tracker 缓存 => 固定为不注入
    ia = importlib.import_module("lib.scoring.investor_agents")
    targets.append((ia.AgentPanel, "_inject_past_context", ia.AgentPanel._inject_past_context))
    ia.AgentPanel._inject_past_context = lambda self, code, result: result
    # 机会分权重状态：清掉任何历史 set() 覆盖，保证从 config 读默认值
    sw = importlib.import_module("lib.scoring.scoring_weights")
    sw.reset_opp_score_weights()
    # P7-3 收口后 flag 已删除，评分路径唯一（ScoringKernel），无需再 pin 环境变量。
    try:
        yield
    finally:
        for mod, name, orig in reversed(targets):
            if orig is not None:
                setattr(mod, name, orig)


# ---------------------------------------------------------------- 旧实现（不经过 adapters）

def _legacy(source: str, i: dict):
    """独立直接调用旧实现 —— 对拍的另一侧，绝不复用 _call_*（否则是同义反复）。"""
    if source == "scorer_4d":
        from lib.scoring.scorer import comprehensive_score
        return comprehensive_score(i["fundamental"], i["capital_flow"], i["news"], i["technical"])
    if source == "scorer_6f":
        from lib.scoring.scorer import six_factor_score
        return six_factor_score(i["factors"], i["sector_score"])
    if source == "scorecard":
        from lib.scoring.scorecard import ScoreCard
        return ScoreCard.score(i["code"], i["row"], i["hist"], i["market_brief"],
                               i["industry_ret"], i["sector_data"], i["as_of_date"])
    if source == "judge_panel":
        from lib.scoring.judge_panel import JudgePanel
        return JudgePanel(i["factors"], i["fundamentals"], i["market_data"]).evaluate_with_ml(
            i["ml_prediction"]
        )
    if source == "investor_agents":
        from lib.scoring.investor_agents import AgentPanel
        return AgentPanel().vote(i["hist_df"], code=i["code"], data_factors=i["data_factors"],
                                 as_of_date=i["as_of_date"])
    if source == "panel_decision":
        from lib.scoring.panel_decision import get_panel_decision
        return get_panel_decision(i["code"], i["hist_df"], i["data_factors"], i["as_of_date"])
    if source == "opportunity_scorer":
        from lib.scoring.opportunity_scorer import compute_opportunity_score
        return compute_opportunity_score(i["regime"], i["regime_confidence"],
                                         i["alpha_posterior"], i["sector_score"],
                                         i["trend_strength"], i["volume_ratio"])
    if source == "strategies_quick":
        from lib.strategy.strategies import quick_score
        return quick_score(i["row"], i["fund_flow"], i["news_sig"])
    if source == "strategies_full":
        from lib.strategy.strategies import full_score
        return full_score(i["code"], i["hist"], i["factors"], i["row"])
    raise KeyError(source)


HEAVY = {"investor_agents", "panel_decision", "strategies_full", "scorecard", "strategies_quick"}


def _samples_for(source: str):
    codes = HEAVY_SAMPLE_CODES if source in HEAVY else SAMPLE_CODES
    return [(c, d) for c in codes for d in SAMPLE_DATES]


def _run_chain(source: str, kernel) -> dict:
    out = {"sample_count": 0, "samples": {}, "mismatches": []}
    for code, date in _samples_for(source):
        i = _inputs(code, date)
        legacy_raw = _legacy(source, i)
        result = kernel.score(source, i)
        legacy = _canonical(legacy_raw)
        via_kernel = _canonical(result.raw)
        key = f"{code}|{date}"
        if legacy != via_kernel:
            out["mismatches"].append({
                "sample": key,
                "legacy": json.dumps(legacy, sort_keys=True, ensure_ascii=True)[:400],
                "kernel": json.dumps(via_kernel, sort_keys=True, ensure_ascii=True)[:400],
            })
        out["samples"][key] = {
            "legacy": legacy,
            "contract": _canonical(result.to_dict()),
            # legacy 与 kernel 各调一次旧实现，故 raw 不是同一个对象；
            # 对拍比的是**数值全等**。`kernel_raw_type` 记录返回类型，防止结构漂移。
            "kernel_raw_type": type(result.raw).__name__,
        }
        out["sample_count"] += 1
    return out


def _judge_groups_constraints() -> dict:
    """登记评委组配置源与实际生效权重（G5 机器判据，P-78）。

    背景：`judge_panel.JUDGE_GROUPS` 优先读 `config/judges.json`、缺失才回退代码默认表。
    这是一个**隐含外部输入**：2026-11-13 RF-02 D3 删除该文件后，权重 0.20→1.00 让
    `group_scores` ×5，L5 只报出 34 行数值 diff，排查成本极高且长期被当作「既有漂移」豁免。
    把配置源与权重写进 constraints 后，配置一变 `--check` 会直接报
    `constraints.judge_groups_source: ...` 这类可读信号，并强制显式重新 capture。
    """
    from lib.scoring import judge_panel as jp
    source = "config/judges.json" if jp._JUDGES_CONFIG_PATH.exists() else "builtin-default"
    weights = {str(k): float(cfg.get("weight", 0.0)) for k, cfg in jp.JUDGE_GROUPS.items()}
    return {"judge_groups_source": source, "judge_groups_weights": weights}


def capture() -> dict:
    from lib.scoring.scoring_adapters import build_kernel
    kernel = build_kernel()
    chains = {}
    with pinned_scoring_environment():
        for source in kernel.sources:
            try:
                chains[source] = {"ok": True, "data": _run_chain(source, kernel)}
            except Exception as exc:  # noqa: BLE001
                chains[source] = {
                    "ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
    return {
        "schema_version": 1,
        "constraints": {
            "seed_salt": SEED_SALT,
            "sample_codes": list(SAMPLE_CODES),
            "heavy_sample_codes": list(HEAVY_SAMPLE_CODES),
            "sample_dates": list(SAMPLE_DATES),
            "tolerance": TOLERANCE,
            "sources": list(kernel.sources),
            "feature_unified_scoring": "off (pinned; env var is an external input)",
            "data_source": "synthetic (deterministic, zlib.crc32 seed) + pinned external inputs",
            **_judge_groups_constraints(),
        },
        "chains": chains,
    }


# ---------------------------------------------------------------- CLI

def _diff(a, b, path="", out=None, tol=TOLERANCE):
    if out is None:
        out = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: MISSING")
            else:
                _diff(a[k], b[k], f"{path}.{k}", out, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}[len]: {len(a)} -> {len(b)}")
        for idx, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{idx}]", out, tol)
    elif isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > tol + tol * max(abs(a), abs(b)):
            out.append(f"{path}: {a} -> {b}")
    elif a != b:
        out.append(f"{path}: {a!r} -> {b!r}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capture", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--summary", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "docs" / "refactor" / "scoring-baseline.json"))
    args = ap.parse_args()
    out = pathlib.Path(args.out)

    if args.summary:
        if not out.exists():
            print(f"[FAIL] baseline not found: {out}")
            return 1
        doc = json.loads(out.read_text(encoding="utf-8"))
        for name, chain in sorted(doc["chains"].items()):
            d = chain.get("data", {})
            print(f"{name:22s} ok={chain['ok']} samples={d.get('sample_count', 0)} "
                  f"mismatches={len(d.get('mismatches', []))}")
        return 0

    snap = capture()
    if args.capture:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                       encoding="utf-8")
        bad = [n for n, c in snap["chains"].items()
               if not c["ok"] or c["data"]["mismatches"]]
        print(f"[OK] baseline written: {out} ({len(snap['chains']) - len(bad)}/{len(snap['chains'])} sources clean)")
        return 1 if bad else 0

    if not out.exists():
        print(f"[FAIL] baseline not found: {out}")
        return 1
    base = json.loads(out.read_text(encoding="utf-8"))
    diffs = []
    for name in sorted(set(base["chains"]) | set(snap["chains"])):
        old, new = base["chains"].get(name), snap["chains"].get(name)
        if old is None or new is None:
            diffs.append(f"{name}: chain MISSING")
            continue
        if new["ok"] is not True:
            diffs.append(f"{name}: capture error {new.get('error')}")
            continue
        if new["data"]["mismatches"]:
            diffs.append(f"{name}: legacy/kernel mismatch on "
                         f"{len(new['data']['mismatches'])} sample(s)")
        diffs += _diff(old.get("data", {}), new["data"], name)
    # 约束变更必须显式重新 capture，不能静默通过
    diffs += _diff(base.get("constraints", {}), snap.get("constraints", {}), "constraints")

    if diffs:
        print(f"[FAIL] scoring baseline: {len(diffs)} difference(s)")
        for d in diffs[:30]:
            print("      " + d)
        return 1
    print(f"[PASS] scoring baseline: {len(snap['chains'])} sources match "
          f"({sum(c['data']['sample_count'] for c in snap['chains'].values() if c['ok'])} samples)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
