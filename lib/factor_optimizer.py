"""因子权重自动优化器 —— GA 大范围搜索 + 贝叶斯精调

使用遗传算法搜索最优 IC_FACTOR_WEIGHTS，然后用贝叶斯优化在最优解周围精调。
内置 walk-forward 验证防止过拟合。

使用方式：
    python -m lib.factor_optimizer --symbols 600519 000858 002714 --generations 50
    python -m lib.factor_optimizer --eval-only  # 只评估当前权重
"""

import logging
import time
import json
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
import numpy as np

logger = logging.getLogger(__name__)

# 因子名称列表（与 factors.py IC_FACTOR_WEIGHTS 对应）
FACTOR_NAMES = [
    "volume_ratio", "adx", "williams_r", "cci", "rsi_14",
    "trend_strength", "kdj_j", "momentum_20", "volatility_20",
]

# 权重约束
WEIGHT_MIN = -0.30
WEIGHT_MAX = 0.30
WEIGHT_SUM_TARGET = 1.0


@dataclass
class OptimizerConfig:
    """优化器配置"""
    # GA 参数
    population_size: int = 50
    generations: int = 100
    crossover_rate: float = 0.8
    mutation_rate: float = 0.15
    mutation_strength: float = 0.05
    elite_ratio: float = 0.1
    tournament_size: int = 3

    # 贝叶斯精调参数
    bayesian_iterations: int = 30
    bayesian_n_initial: int = 10

    # Walk-forward 参数
    wf_train_days: int = 500
    wf_test_days: int = 125
    wf_n_splits: int = 4

    # 适应度权重
    sharpe_weight: float = 0.6
    maxdd_weight: float = 0.3
    calmar_weight: float = 0.1

    # 回测参数
    backtest_days: int = 750
    backtest_n_stocks: int = 20
    disable_loss_breaker: bool = True  # 优化时禁用，评估时启用

    # 输出
    output_dir: str = "cache/optimizer"


@dataclass
class Individual:
    """个体（一组因子权重）"""
    weights: Dict[str, float]
    fitness: float = 0.0
    sharpe: float = 0.0
    max_dd: float = 0.0
    calmar: float = 0.0
    win_rate: float = 0.0
    n_trades: int = 0
    eval_time: float = 0.0


def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """归一化权重到 WEIGHT_SUM_TARGET"""
    total = sum(weights.values())
    if abs(total) < 1e-8:
        return weights
    return {k: v / total * WEIGHT_SUM_TARGET for k, v in weights.items()}


def _clamp_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """裁剪权重到 [WEIGHT_MIN, WEIGHT_MAX]"""
    return {k: max(WEIGHT_MIN, min(WEIGHT_MAX, v)) for k, v in weights.items()}


def _random_weights(rng: np.random.Generator) -> Dict[str, float]:
    """生成随机权重"""
    raw = rng.uniform(WEIGHT_MIN, WEIGHT_MAX, len(FACTOR_NAMES))
    weights = dict(zip(FACTOR_NAMES, raw))
    return _normalize_weights(weights)


def _weights_hash(weights: Dict[str, float]) -> str:
    """权重的哈希值，用于缓存"""
    s = json.dumps(weights, sort_keys=True, default=float)
    return hashlib.md5(s.encode()).hexdigest()[:12]


def evaluate_fitness(
    weights: Dict[str, float],
    symbols: List[str],
    days: int = 750,
    n_stocks: int = 20,
    disable_loss_breaker: bool = True,
    sharpe_weight: float = 0.6,
    maxdd_weight: float = 0.3,
    calmar_weight: float = 0.1,
) -> Tuple[float, Dict]:
    """评估一组因子权重的适应度

    Returns:
        (fitness_score, metrics_dict)
    """
    import factors as F

    # 保存原始权重
    orig_weights = dict(F.IC_FACTOR_WEIGHTS)

    try:
        # 设置新权重
        F.IC_FACTOR_WEIGHTS.clear()
        F.IC_FACTOR_WEIGHTS.update(weights)

        # 选择股票子集
        if len(symbols) > n_stocks:
            rng = np.random.default_rng(hash(_weights_hash(weights)) % 2**31)
            selected = rng.choice(symbols, size=n_stocks, replace=False).tolist()
        else:
            selected = symbols

        # 运行回测
        from backtest.rebalance import run_rebalance_backtest
        result = run_rebalance_backtest(
            symbols=selected,
            days=days,
            disable_loss_breaker=disable_loss_breaker,
        )

        if result is None:
            return -999.0, {"error": "backtest returned None"}

        # 兼容两种返回格式（rebalance_backtest vs 其他）
        sharpe = result.get("sharpe", result.get("final_sharpe", 0)) or 0
        max_dd = abs(result.get("max_drawdown", result.get("final_drawdown", 1.0)) or 1.0)
        win_rate = result.get("win_rate", 0) or 0
        n_trades = result.get("n_trades", result.get("trade_count", 0)) or 0
        total_return = result.get("total_return", 0) or 0

        # Calmar = total_return / max_drawdown
        calmar = total_return / max_dd if max_dd > 0.001 else 0

        # 组合适应度
        # 惩罚过少交易（<10 笔说明权重配置有问题）
        trade_penalty = min(1.0, n_trades / 10) if n_trades < 10 else 1.0

        fitness = (
            sharpe_weight * sharpe
            + maxdd_weight * (1.0 - min(max_dd, 1.0))  # 回撤越小越好
            + calmar_weight * min(calmar, 5.0)  # cap Calmar to avoid extreme
        ) * trade_penalty

        metrics = {
            "sharpe": round(sharpe, 4),
            "max_dd": round(max_dd, 4),
            "calmar": round(calmar, 4),
            "win_rate": round(win_rate, 4),
            "n_trades": n_trades,
            "total_return": round(total_return, 4),
            "trade_penalty": round(trade_penalty, 4),
        }

        return fitness, metrics

    finally:
        # 恢复原始权重
        F.IC_FACTOR_WEIGHTS.clear()
        F.IC_FACTOR_WEIGHTS.update(orig_weights)


# ============================================================
# 遗传算法
# ============================================================

class GeneticOptimizer:
    """遗传算法因子权重优化器"""

    def __init__(self, config: OptimizerConfig, symbols: List[str]):
        self.config = config
        self.symbols = symbols
        self.rng = np.random.default_rng(42)
        self.population: List[Individual] = []
        self.best_ever: Optional[Individual] = None
        self.generation = 0
        self.history: List[Dict] = []
        self._fitness_cache: Dict[str, Tuple[float, Dict]] = {}

    def _evaluate(self, ind: Individual) -> float:
        """评估个体，带缓存"""
        h = _weights_hash(ind.weights)
        if h in self._fitness_cache:
            fitness, metrics = self._fitness_cache[h]
        else:
            t0 = time.time()
            fitness, metrics = evaluate_fitness(
                ind.weights,
                self.symbols,
                self.config.backtest_days,
                self.config.backtest_n_stocks,
                self.config.disable_loss_breaker,
                self.config.sharpe_weight,
                self.config.maxdd_weight,
                self.config.calmar_weight,
            )
            ind.eval_time = time.time() - t0
            self._fitness_cache[h] = (fitness, metrics)

        ind.fitness = fitness
        ind.sharpe = metrics.get("sharpe", 0)
        ind.max_dd = metrics.get("max_dd", 0)
        ind.calmar = metrics.get("calmar", 0)
        ind.win_rate = metrics.get("win_rate", 0)
        ind.n_trades = metrics.get("n_trades", 0)
        return fitness

    def _init_population(self):
        """初始化种群"""
        self.population = []
        # 注入当前手工权重作为种子
        import factors as F
        seed = Individual(weights=dict(F.IC_FACTOR_WEIGHTS))
        self._evaluate(seed)
        self.population.append(seed)
        logger.info(f"种子个体: fitness={seed.fitness:.4f} sharpe={seed.sharpe:.4f}")

        # 注入均匀权重
        uniform = Individual(weights={k: 1.0 / len(FACTOR_NAMES) for k in FACTOR_NAMES})
        self._evaluate(uniform)
        self.population.append(uniform)

        # 随机初始化剩余
        while len(self.population) < self.config.population_size:
            ind = Individual(weights=_random_weights(self.rng))
            self._evaluate(ind)
            self.population.append(ind)

        self.population.sort(key=lambda x: x.fitness, reverse=True)
        self.best_ever = self.population[0]

    def _tournament_select(self) -> Individual:
        """锦标赛选择"""
        candidates = self.rng.choice(
            len(self.population),
            size=min(self.config.tournament_size, len(self.population)),
            replace=False,
        )
        best_idx = max(candidates, key=lambda i: self.population[i].fitness)
        return self.population[best_idx]

    def _crossover(self, p1: Individual, p2: Individual) -> Individual:
        """算术交叉"""
        alpha = self.rng.uniform(0.3, 0.7)
        child_weights = {}
        for k in FACTOR_NAMES:
            child_weights[k] = alpha * p1.weights[k] + (1 - alpha) * p2.weights[k]
        return Individual(weights=_normalize_weights(_clamp_weights(child_weights)))

    def _mutate(self, ind: Individual) -> Individual:
        """高斯变异"""
        new_weights = dict(ind.weights)
        for k in FACTOR_NAMES:
            if self.rng.random() < self.config.mutation_rate:
                noise = self.rng.normal(0, self.config.mutation_strength)
                new_weights[k] = new_weights[k] + noise
        new_weights = _normalize_weights(_clamp_weights(new_weights))
        return Individual(weights=new_weights)

    def _step(self):
        """进化一代"""
        self.generation += 1
        n_elite = max(1, int(len(self.population) * self.config.elite_ratio))

        # 精英保留
        new_pop = self.population[:n_elite]

        # 生成后代
        while len(new_pop) < self.config.population_size:
            p1 = self._tournament_select()
            p2 = self._tournament_select()

            if self.rng.random() < self.config.crossover_rate:
                child = self._crossover(p1, p2)
            else:
                child = Individual(weights=dict(p1.weights))

            child = self._mutate(child)
            self._evaluate(child)
            new_pop.append(child)

        self.population = sorted(new_pop, key=lambda x: x.fitness, reverse=True)

        if self.population[0].fitness > self.best_ever.fitness:
            self.best_ever = self.population[0]

        # 记录历史
        gen_stats = {
            "generation": self.generation,
            "best_fitness": round(self.population[0].fitness, 6),
            "avg_fitness": round(np.mean([i.fitness for i in self.population]), 6),
            "best_sharpe": self.population[0].sharpe,
            "best_maxdd": self.population[0].max_dd,
            "best_trades": self.population[0].n_trades,
            "cache_size": len(self._fitness_cache),
        }
        self.history.append(gen_stats)

        if self.generation % 5 == 0:
            logger.info(
                f"Gen {self.generation}: best={self.population[0].fitness:.4f} "
                f"sharpe={self.population[0].sharpe:.3f} "
                f"dd={self.population[0].max_dd:.3f} "
                f"trades={self.population[0].n_trades}"
            )

    def run(self) -> Individual:
        """运行遗传算法"""
        logger.info(f"GA 开始: pop={self.config.population_size} gen={self.config.generations}")
        t0 = time.time()

        self._init_population()
        logger.info(f"初始种群: best_fitness={self.best_ever.fitness:.4f}")

        for _ in range(self.config.generations):
            self._step()

        elapsed = time.time() - t0
        logger.info(f"GA 完成: {elapsed:.1f}s, best_fitness={self.best_ever.fitness:.4f}")
        return self.best_ever


# ============================================================
# 贝叶斯精调
# ============================================================

class BayesianRefiner:
    """在 GA 最优解周围用贝叶斯优化精调"""

    def __init__(self, config: OptimizerConfig, symbols: List[str]):
        self.config = config
        self.symbols = symbols

    def refine(self, center: Dict[str, float]) -> Individual:
        """围绕 center 进行贝叶斯精调"""
        try:
            from skopt import gp_minimize
            from skopt.space import Real
        except ImportError:
            logger.warning("scikit-optimize 未安装，跳过贝叶斯精调。pip install scikit-optimize")
            return Individual(weights=center)

        # 定义搜索空间（围绕 center ± 0.10）
        dimensions = []
        for k in FACTOR_NAMES:
            c = center[k]
            lo = max(WEIGHT_MIN, c - 0.10)
            hi = min(WEIGHT_MAX, c + 0.10)
            dimensions.append(Real(lo, hi, name=k))

        def objective(params):
            weights = dict(zip(FACTOR_NAMES, params))
            weights = _normalize_weights(_clamp_weights(weights))
            fitness, _ = evaluate_fitness(
                weights, self.symbols,
                self.config.backtest_days,
                self.config.backtest_n_stocks,
                self.config.disable_loss_breaker,
                self.config.sharpe_weight,
                self.config.maxdd_weight,
                self.config.calmar_weight,
            )
            return -fitness  # gp_minimize 求最小值

        logger.info(f"贝叶斯精调: {self.config.bayesian_iterations} 次迭代")
        result = gp_minimize(
            objective,
            dimensions,
            n_calls=self.config.bayesian_iterations,
            n_initial_points=self.config.bayesian_n_initial,
            random_state=42,
            verbose=False,
        )

        best_weights = dict(zip(FACTOR_NAMES, result.x))
        best_weights = _normalize_weights(_clamp_weights(best_weights))
        best = Individual(weights=best_weights)
        best.fitness = -result.fun
        logger.info(f"贝叶斯精调完成: fitness={best.fitness:.4f}")
        return best


# ============================================================
# Walk-Forward 验证
# ============================================================

def walk_forward_validate(
    weights: Dict[str, float],
    symbols: List[str],
    train_days: int = 500,
    test_days: int = 125,
    n_splits: int = 4,
) -> Dict:
    """Walk-forward 验证权重稳定性

    将历史数据分为 n_splits 个 train/test 窗口，
    在每个窗口评估权重表现，检查是否稳定。
    """
    results = []
    total_days = train_days + test_days * n_splits

    for i in range(n_splits):
        # 每个窗口偏移 test_days
        offset = test_days * (n_splits - 1 - i)
        days = train_days + test_days + offset

        fitness, metrics = evaluate_fitness(
            weights, symbols,
            days=days,
            n_stocks=len(symbols),
            disable_loss_breaker=False,
        )
        results.append({
            "split": i,
            "fitness": round(fitness, 4),
            **metrics,
        })
        logger.info(f"WF split {i}: fitness={fitness:.4f} sharpe={metrics.get('sharpe', 0):.3f}")

    # 稳定性检查
    sharpes = [r["sharpe"] for r in results]
    fitnesses = [r["fitness"] for r in results]

    return {
        "splits": results,
        "mean_sharpe": round(np.mean(sharpes), 4),
        "std_sharpe": round(np.std(sharpes), 4),
        "mean_fitness": round(np.mean(fitnesses), 4),
        "std_fitness": round(np.std(fitnesses), 4),
        "is_stable": np.std(sharpes) < 0.5,  # Sharpe 标准差 < 0.5 算稳定
        "min_sharpe": round(min(sharpes), 4),
    }


# ============================================================
# 主优化流程
# ============================================================

def run_optimization(
    symbols: List[str],
    config: Optional[OptimizerConfig] = None,
    skip_bayesian: bool = False,
    skip_walkforward: bool = False,
) -> Dict:
    """完整的因子权重优化流程

    1. GA 搜索
    2. 贝叶斯精调
    3. Walk-forward 验证
    4. 保存结果
    """
    if config is None:
        config = OptimizerConfig()

    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: GA
    logger.info("=" * 60)
    logger.info("Step 1/3: 遗传算法搜索")
    logger.info("=" * 60)
    ga = GeneticOptimizer(config, symbols)
    ga_best = ga.run()

    # Step 2: 贝叶斯精调
    best = ga_best
    if not skip_bayesian:
        logger.info("=" * 60)
        logger.info("Step 2/3: 贝叶斯精调")
        logger.info("=" * 60)
        refiner = BayesianRefiner(config, symbols)
        bayesian_best = refiner.refine(ga_best.weights)
        if bayesian_best.fitness > ga_best.fitness:
            best = bayesian_best
            logger.info("贝叶斯精调改善了适应度")
        else:
            logger.info("贝叶斯精调未改善，使用 GA 最优解")

    # Step 3: Walk-forward 验证
    wf_result = None
    if not skip_walkforward:
        logger.info("=" * 60)
        logger.info("Step 3/3: Walk-Forward 验证")
        logger.info("=" * 60)
        wf_result = walk_forward_validate(
            best.weights, symbols,
            config.wf_train_days,
            config.wf_test_days,
            config.wf_n_splits,
        )

    # 汇总结果
    result = {
        "optimal_weights": best.weights,
        "fitness": round(best.fitness, 6),
        "metrics": {
            "sharpe": best.sharpe,
            "max_dd": best.max_dd,
            "calmar": best.calmar,
            "win_rate": best.win_rate,
            "n_trades": best.n_trades,
        },
        "baseline_weights": dict(GeneticOptimizer(config, symbols).population[0].weights) if False else {},
        "ga_history": ga.history[-5:],  # 最后 5 代
        "walk_forward": wf_result,
        "config": {
            "population_size": config.population_size,
            "generations": config.generations,
            "backtest_days": config.backtest_days,
            "backtest_n_stocks": config.backtest_n_stocks,
        },
    }

    # 保存
    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"optimal_weights_{ts}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    logger.info(f"结果已保存: {output_file}")

    # 同时保存一个 latest 链接
    latest_file = output_dir / "optimal_weights_latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)

    return result


def apply_optimal_weights(weights: Dict[str, float]) -> str:
    """将最优权重写入 factors.py

    Returns:
        生成的代码字符串
    """
    lines = ["IC_FACTOR_WEIGHTS = {"]
    for k in FACTOR_NAMES:
        v = weights.get(k, 0)
        lines.append(f'    "{k}": {v:.4f},')
    lines.append("}")
    return "\n".join(lines)


def eval_current_weights(symbols: List[str], days: int = 750, n_stocks: int = 20) -> Dict:
    """评估当前权重的表现"""
    import factors as F
    current = dict(F.IC_FACTOR_WEIGHTS)
    fitness, metrics = evaluate_fitness(current, symbols, days, n_stocks, disable_loss_breaker=False)
    return {
        "weights": current,
        "fitness": round(fitness, 6),
        "metrics": metrics,
    }


# ============================================================
# CLI 入口
# ============================================================

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="因子权重自动优化器")
    parser.add_argument("--symbols", nargs="+", default=None, help="股票代码列表")
    parser.add_argument("--generations", type=int, default=50, help="GA 代数")
    parser.add_argument("--population", type=int, default=30, help="种群大小")
    parser.add_argument("--backtest-days", type=int, default=750, help="回测天数")
    parser.add_argument("--n-stocks", type=int, default=20, help="评估股票数")
    parser.add_argument("--eval-only", action="store_true", help="只评估当前权重")
    parser.add_argument("--skip-bayesian", action="store_true", help="跳过贝叶斯精调")
    parser.add_argument("--skip-walkforward", action="store_true", help="跳过 walk-forward")
    args = parser.parse_args()

    # 默认股票池
    if args.symbols is None:
        # 使用沪深300成分股子集
        default_symbols = [
            "600519", "000858", "601318", "600036", "000333",
            "002714", "600276", "300750", "601012", "600900",
            "000001", "600000", "601166", "002475", "600309",
            "000651", "002415", "601888", "300059", "600585",
        ]
        args.symbols = default_symbols

    if args.eval_only:
        result = eval_current_weights(args.symbols, args.backtest_days, args.n_stocks)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        sys.exit(0)

    config = OptimizerConfig(
        population_size=args.population,
        generations=args.generations,
        backtest_days=args.backtest_days,
        backtest_n_stocks=args.n_stocks,
    )

    result = run_optimization(
        symbols=args.symbols,
        config=config,
        skip_bayesian=args.skip_bayesian,
        skip_walkforward=args.skip_walkforward,
    )

    print("\n" + "=" * 60)
    print("优化结果")
    print("=" * 60)
    print(f"适应度: {result['fitness']}")
    print(f"Sharpe: {result['metrics']['sharpe']}")
    print(f"最大回撤: {result['metrics']['max_dd']}")
    print(f"胜率: {result['metrics']['win_rate']}")
    print(f"交易笔数: {result['metrics']['n_trades']}")
    print("\n最优权重:")
    for k, v in result["optimal_weights"].items():
        print(f"  {k}: {v:.4f}")

    if result.get("walk_forward"):
        wf = result["walk_forward"]
        print(f"\nWalk-Forward: mean_sharpe={wf['mean_sharpe']} std={wf['std_sharpe']} stable={wf['is_stable']}")

    print(f"\n应用权重: 将以下代码替换 factors.py 中的 IC_FACTOR_WEIGHTS:")
    print(apply_optimal_weights(result["optimal_weights"]))
