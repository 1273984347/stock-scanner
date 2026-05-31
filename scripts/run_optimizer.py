#!/usr/bin/env python3
"""GA optimizer runner with practical parameters.

Bypasses the auto-bump in GeneticOptimizer.__init__ so we can control
population/generations directly.  Runs GA + walk-forward, saves results.
"""

import sys
import json
import time
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

from lib.factor_optimizer import (
    OptimizerConfig,
    GeneticOptimizer,
    walk_forward_validate,
    apply_optimal_weights,
    FACTOR_NAMES,
    REGIME_NAMES,
    STRATEGY_NAMES,
)

# ── Symbols ──────────────────────────────────────────────
SYMBOLS = [
    "600519", "000858", "601318", "600036", "000333",
    "002714", "600276", "300750", "601012", "600900",
    "000001", "600000", "601166", "002475", "600309",
    "000651", "002415", "601888", "300059", "600585",
]


def run_ga_optimization():
    """Run GA optimization with extended search space."""
    config = OptimizerConfig(
        # GA params — minimal feasible for extended search space
        population_size=8,
        generations=4,
        crossover_rate=0.8,
        mutation_rate=0.20,
        mutation_strength=0.05,
        elite_ratio=0.15,
        tournament_size=3,
        # Backtest params — smaller to fit time budget
        backtest_days=200,
        backtest_n_stocks=3,
        disable_loss_breaker=True,
        # Fitness weights
        sharpe_weight=0.6,
        maxdd_weight=0.3,
        calmar_weight=0.1,
        # Output
        output_dir="cache/optimizer",
        # Extended search space
        optimize_exit_days=True,
        optimize_regime_weights=True,
    )

    # Prevent auto-bump
    # The GeneticOptimizer.__init__ bumps pop to 100/gen to 50 when
    # optimize_exit_days or optimize_regime_weights is True.
    # We override those AFTER construction by patching the config.
    # Actually, let's just patch the config before constructing.
    # The auto-bump happens in __init__, so we need to trick it.
    # Set flags to False for the check, then set them back.
    exit_days_flag = config.optimize_exit_days
    regime_flag = config.optimize_regime_weights
    config.optimize_exit_days = False
    config.optimize_regime_weights = False

    ga = GeneticOptimizer(config, SYMBOLS)

    # Restore flags for chromosome encoding/decoding
    ga.config.optimize_exit_days = exit_days_flag
    ga.config.optimize_regime_weights = regime_flag
    ga._dim = len(FACTOR_NAMES)
    if exit_days_flag:
        ga._dim += len(REGIME_NAMES)
    if regime_flag:
        ga._dim += len(REGIME_NAMES) * len(STRATEGY_NAMES)

    logger.info(
        f"GA config: pop={config.population_size} gen={config.generations} "
        f"dim={ga._dim} stocks={config.backtest_n_stocks} days={config.backtest_days}"
    )
    logger.info(
        f"Extended: exit_days={exit_days_flag} regime_weights={regime_flag}"
    )

    # Run GA
    t0 = time.time()
    best = ga.run()
    ga_elapsed = time.time() - t0
    logger.info(f"GA completed in {ga_elapsed:.1f}s")

    # Walk-forward validation (1 split to keep it fast)
    logger.info("=" * 60)
    logger.info("Walk-Forward validation (1 split)")
    logger.info("=" * 60)
    wf_result = walk_forward_validate(
        best.weights, SYMBOLS[:5],
        train_days=200,
        test_days=100,
        n_splits=1,
    )

    # Build result dict
    result = {
        "factor_weights": best.weights,
        "exit_days_map": best.exit_days_map,
        "regime_strategy_presets": best.regime_strategy_presets,
        "fitness": round(best.fitness, 6),
        "metrics": {
            "sharpe": best.sharpe,
            "max_dd": best.max_dd,
            "calmar": best.calmar,
            "win_rate": best.win_rate,
            "n_trades": best.n_trades,
        },
        "walk_forward": wf_result,
        "ga_history": ga.history[-5:],
        "config": {
            "population_size": config.population_size,
            "generations": config.generations,
            "backtest_days": config.backtest_days,
            "backtest_n_stocks": config.backtest_n_stocks,
            "optimize_exit_days": exit_days_flag,
            "optimize_regime_weights": regime_flag,
        },
    }

    # ── Print summary ────────────────────────────────────
    print("\n" + "=" * 60)
    print("OPTIMIZATION RESULTS")
    print("=" * 60)
    print(f"Fitness: {result['fitness']}")
    print(f"Sharpe:  {result['metrics']['sharpe']}")
    print(f"Max DD:  {result['metrics']['max_dd']}")
    print(f"Win rate: {result['metrics']['win_rate']}")
    print(f"Trades:  {result['metrics']['n_trades']}")

    print("\nFactor Weights:")
    for k, v in result["factor_weights"].items():
        print(f"  {k}: {v:.4f}")

    if result.get("exit_days_map"):
        print("\nExit Days:")
        for k, v in result["exit_days_map"].items():
            print(f"  {k}: {v}")

    if result.get("regime_strategy_presets"):
        print("\nRegime Strategy Presets:")
        for regime, weights in result["regime_strategy_presets"].items():
            print(f"  {regime}:")
            for strategy, w in weights.items():
                print(f"    {strategy}: {w:.4f}")

    wf = result["walk_forward"]
    print(f"\nWalk-Forward: mean_sharpe={wf['mean_sharpe']} std={wf['std_sharpe']} stable={wf['is_stable']}")

    print(f"\nGenerated code for factors.py:")
    print(apply_optimal_weights(result["factor_weights"]))

    # ── Save results ─────────────────────────────────────
    output_dir = Path("cache/optimizer")
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"optimal_weights_{ts}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Saved: {output_file}")

    # Also save as latest
    latest_file = output_dir / "optimal_weights_latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Saved: {latest_file}")

    # Save compact version for cache/optimal_params.json
    optimal_params = {
        "factor_weights": result["factor_weights"],
        "exit_days_map": result["exit_days_map"],
        "regime_strategy_presets": result["regime_strategy_presets"],
        "fitness": result["fitness"],
        "walk_forward": {
            "mean_sharpe": wf["mean_sharpe"],
            "std_sharpe": wf["std_sharpe"],
            "is_stable": wf["is_stable"],
            "min_sharpe": wf["min_sharpe"],
        },
    }
    params_file = Path("cache/optimal_params.json")
    with open(params_file, "w", encoding="utf-8") as f:
        json.dump(optimal_params, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"Saved: {params_file}")

    return result


if __name__ == "__main__":
    result = run_ga_optimization()
