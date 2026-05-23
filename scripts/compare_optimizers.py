#!/usr/bin/env python
"""新旧优化器增量对比测试

旧优化器: lib.portfolio.PortfolioBuilder (等权)
新优化器: lib.portfolio_manager.PortfolioManager (PositionSizer + RiskBudget + Turnover)

用法:
    python scripts/compare_optimizers.py
    python scripts/compare_optimizers.py --candidates 20 --seed 42
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import numpy as np
import random


def generate_candidates(n: int = 20, seed: int = 42) -> list:
    """生成模拟候选股"""
    random.seed(seed)
    np.random.seed(seed)
    codes = [f"{random.randint(1, 9999):06d}" for _ in range(n)]
    names = [f"股票{i+1}" for i in range(n)]
    raw_scores = np.random.randn(n) * 10 + 70
    scores = np.clip(raw_scores, 0, 100)
    return [
        {"code": code, "name": name, "score": float(score)}
        for code, name, score in zip(codes, names, scores)
    ]


def test_old_optimizer(candidates: list) -> dict:
    """旧优化器: PortfolioBuilder (等权)"""
    from lib.portfolio import PortfolioBuilder
    from config import PortfolioConfig
    import pandas as pd

    ml_scores = pd.Series(
        data=[c["score"] for c in candidates],
        index=[c["code"] for c in candidates],
        name="score",
    )

    cfg = PortfolioConfig(
        optimize_method="equal_weight",
        top_n=min(len(candidates), 10),
        max_single_weight=0.15,
        max_sector_exposure=0.30,
    )
    builder = PortfolioBuilder(cfg)
    result = builder.build(ml_scores)

    weights = {k: v["weight"] for k, v in result.items() if k != "_meta"}
    n_pos = result.get("_meta", {}).get("n_positions", len(weights))

    # 旧优化器不计算这些指标，显示 —
    return {
        "weights": weights,
        "expected_return": None,
        "expected_vol": None,
        "sharpe_ratio": None,
        "n_positions": n_pos,
        "turnover": None,
        "method": result.get("_meta", {}).get("method", "unknown"),
    }


def test_new_optimizer(candidates: list) -> dict:
    """新优化器: PortfolioManager (PositionSizer + RiskBudget + Turnover)"""
    from lib.portfolio_manager import PortfolioManager, StrategyConfig

    strategies = [StrategyConfig(name="signal", weight=1.0)]
    manager = PortfolioManager(strategies=strategies)
    result = manager.optimize({"signal": candidates})

    return {
        "weights": result.weights,
        "expected_return": result.expected_return,
        "expected_vol": result.expected_vol,
        "sharpe_ratio": result.sharpe_ratio,
        "n_positions": result.n_positions,
        "turnover": result.turnover,
        "method": "layered",
    }


def weight_concentration(weights: dict) -> float:
    """权重集中度：top3 占比"""
    if not weights:
        return 0.0
    sorted_w = sorted(weights.values(), reverse=True)
    top3 = sum(sorted_w[:3])
    return top3


def print_comparison(old_result: dict, new_result: dict):
    print("\n" + "=" * 60)
    print("新旧优化器对比结果")
    print("=" * 60)

    old_method = old_result["method"]
    new_method = new_result["method"]

    print(f"\n{'指标':<18} {'旧优化器':>18} {'新优化器':>18}")
    print("-" * 60)

    rows = [
        ("优化方法", old_method, new_method),
        ("持仓数", str(old_result["n_positions"]), str(new_result["n_positions"])),
        ("Top3 集中度", f"{weight_concentration(old_result['weights']):.1%}",
         f"{weight_concentration(new_result['weights']):.1%}"),
        ("预期年化收益", "—", f"{new_result['expected_return']:.2%}" if new_result['expected_return'] else "—"),
        ("预期年化波动", "—", f"{new_result['expected_vol']:.2%}" if new_result['expected_vol'] else "—"),
        ("夏普比率", "—", f"{new_result['sharpe_ratio']:.2f}" if new_result['sharpe_ratio'] else "—"),
        ("换手率", "—" if old_result['turnover'] is None else f"{old_result['turnover']:.2%}",
         f"{new_result['turnover']:.2%}" if new_result['turnover'] else "—"),
    ]

    for name, old_val, new_val in rows:
        print(f"{name:<18} {str(old_val):>18} {str(new_val):>18}")

    # 权重分布对比
    print("\n--- 权重分布 ---")
    old_w = old_result["weights"]
    new_w = new_result["weights"]

    all_codes = sorted(set(old_w.keys()) | set(new_w.keys()))

    print(f"{'代码':<10} {'旧权重':>10} {'新权重':>10} {'差异':>10}")
    for code in all_codes[:10]:
        ow = old_w.get(code, 0)
        nw = new_w.get(code, 0)
        diff = nw - ow
        print(f"{code:<10} {ow:>9.1%} {nw:>9.1%} {diff:>+9.1%}")

    # 新优化器独有的股票
    new_only = set(new_w.keys()) - set(old_w.keys())
    if new_only:
        print(f"\n新优化器新增持仓: {', '.join(sorted(new_only))}")
    old_only = set(old_w.keys()) - set(new_w.keys())
    if old_only:
        print(f"旧优化器独有持仓: {', '.join(sorted(old_only))}")

    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="新旧优化器对比")
    parser.add_argument("--candidates", type=int, default=20, help="候选股数量")
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    args = parser.parse_args()

    print(f"生成 {args.candidates} 只模拟候选股 (seed={args.seed})...")
    candidates = generate_candidates(n=args.candidates, seed=args.seed)
    scores = [c["score"] for c in candidates]
    print(f"评分分布: min={min(scores):.1f}  max={max(scores):.1f}  mean={np.mean(scores):.1f}")

    print("\n[1/2] 运行旧优化器 (PortfolioBuilder 等权)...")
    old_result = test_old_optimizer(candidates)
    print(f"      持仓 {old_result['n_positions']} 只，Top3集中度 {weight_concentration(old_result['weights']):.1%}")

    print("[2/2] 运行新优化器 (PortfolioManager 分层架构)...")
    new_result = test_new_optimizer(candidates)
    print(f"      持仓 {new_result['n_positions']} 只，Top3集中度 {weight_concentration(new_result['weights']):.1%}")
    print(f"      预期收益 {new_result['expected_return']:.2%}，波动 {new_result['expected_vol']:.2%}，夏普 {new_result['sharpe_ratio']:.2f}")

    print_comparison(old_result, new_result)
