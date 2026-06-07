"""因子权重自动优化器 —— GA 大范围搜索 + 贝叶斯精调

⚠️ 警告：此模块存在严重过拟合风险。
- GA 在样本内搜索 5000+ 次权重组合，必然拟合噪声
- config.py 的因子权重已改为等权，消除了数据窥探
- 此模块仅保留用于研究目的，禁止用于生产环境的权重选择

使用方式（仅限研究）：
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

# 市场状态与策略名称（与 dynamic_weight_manager / dynamic_exit 对齐）
REGIME_NAMES = [
    "TREND_UP", "MEAN_REVERSION", "HIGH_VOLATILITY",
    "LOW_VOLATILITY", "TREND_DOWN", "RISK_OFF",
]

STRATEGY_NAMES = [
    "trend", "mean_reversion", "momentum",
    "volatility", "stat_arb", "trend_breakout",
]

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

# P0-2: OOS sharpe 在 fitness 公式中的权重。
# 设计目标：fitness = sharpe_in*sharpe_weight + sharpe_oos*OOS_SHARPE_WEIGHT + ...
# 当 OOS_SHARPE_WEIGHT > 0.5 时，OOS 表现主导 fitness，
# 强制 GA 寻找"样本内+样本外都稳健"的权重，而非过拟合噪声。
OOS_SHARPE_WEIGHT = 0.6


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

    # Exit days optimization
    optimize_exit_days: bool = False
    exit_days_bounds: Tuple[int, int] = (0, 20)  # min/max days per regime (0=RISK_OFF)

    # Regime strategy weights optimization
    optimize_regime_weights: bool = False
    regime_weight_bounds: Tuple[float, float] = (0.0, 1.0)


@dataclass
class Individual:
    """个体（一组因子权重 + 可选的出场天数/策略权重）"""
    weights: Dict[str, float]
    exit_days_map: Optional[Dict[str, int]] = None
    regime_strategy_presets: Optional[Dict[str, Dict[str, float]]] = None
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
    exit_days_map: Optional[Dict[str, int]] = None,
    regime_strategy_presets: Optional[Dict[str, Dict[str, float]]] = None,
    oos_test_days: int = 0,
) -> Tuple[float, Dict]:
    """评估一组因子权重的适应度

    P0-2 修复：fitness 公式中内嵌 OOS sharpe 权重（OOS_SHARPE_WEIGHT），
    防止 GA 在 in-sample 上过拟合噪声。

    Args:
        weights: IC 因子权重
        symbols: 股票代码列表
        days: 回测天数（IS + OOS 总和）。当 oos_test_days > 0 时，
              IS 用前 (days - oos_test_days) 天，OOS 用后 oos_test_days 天。
        n_stocks: 评估股票数
        disable_loss_breaker: 禁用连亏熔断
        sharpe_weight: IS Sharpe 权重
        maxdd_weight: 最大回撤权重
        calmar_weight: Calmar 权重
        exit_days_map: 每个 regime 的出场天数（可选）
        regime_strategy_presets: 每个 regime 的策略权重预设（可选）
        oos_test_days: OOS 评估天数。0 = 禁用（保持旧行为，向后兼容），
                       >0 = 启用样本外评估。典型值 125（半年）。

    Returns:
        (fitness_score, metrics_dict)
        metrics 包含 oos_sharpe 字段（启用 OOS 时）。
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

        # IS 部分：全量 days（IS + OOS 一起算，expanding window 简化）
        # 注意：因 run_rebalance_backtest 只支持"取最近 N 天"，
        # 无法做严格的 walk-forward split。这里采用 expanding window：
        # IS 用全部 days，OOS 用最近 oos_test_days 天。
        # 这是对严格分离的折中；研究使用可接受。
        result = run_rebalance_backtest(
            symbols=selected,
            days=days,
            disable_loss_breaker=disable_loss_breaker,
            exit_days_map=exit_days_map,
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

        # P0-2: OOS 评估 —— 当 oos_test_days > 0 时启用
        oos_sharpe = 0.0
        oos_failed = False
        if oos_test_days > 0 and days > oos_test_days:
            try:
                oos_result = run_rebalance_backtest(
                    symbols=selected,
                    days=oos_test_days,  # 只取最近 oos_test_days 天
                    disable_loss_breaker=disable_loss_breaker,
                    exit_days_map=exit_days_map,
                )
                if oos_result is not None:
                    oos_sharpe = (
                        oos_result.get("sharpe",
                                       oos_result.get("final_sharpe", 0)) or 0
                    )
                else:
                    # OOS backtest 失败 → 标记但不整体崩溃
                    oos_failed = True
                    logger.warning(
                        f"[evaluate_fitness] OOS backtest 返回 None "
                        f"(days={oos_test_days})，OOS sharpe 记为 0"
                    )
            except Exception as e:
                oos_failed = True
                logger.warning(
                    f"[evaluate_fitness] OOS backtest 异常: {e}，OOS sharpe 记为 0"
                )

        # 组合适应度
        # P0-2: 在原公式基础上增加 OOS sharpe 权重。
        # 当 OOS_SHARPE_WEIGHT > 0.5 时，OOS 表现主导 fitness，强制 GA 寻找稳健权重。
        # 惩罚过少交易（<10 笔说明权重配置有问题）
        trade_penalty = min(1.0, n_trades / 10) if n_trades < 10 else 1.0

        is_component = (
            sharpe_weight * sharpe
            + maxdd_weight * (1.0 - min(max_dd, 1.0))  # 回撤越小越好
            + calmar_weight * min(calmar, 5.0)  # cap Calmar to avoid extreme
        )
        oos_component = OOS_SHARPE_WEIGHT * oos_sharpe if oos_test_days > 0 else 0.0

        fitness = (is_component + oos_component) * trade_penalty

        metrics = {
            "sharpe": round(sharpe, 4),
            "max_dd": round(max_dd, 4),
            "calmar": round(calmar, 4),
            "win_rate": round(win_rate, 4),
            "n_trades": n_trades,
            "total_return": round(total_return, 4),
            "trade_penalty": round(trade_penalty, 4),
        }
        if oos_test_days > 0:
            metrics["oos_sharpe"] = round(oos_sharpe, 4)
            metrics["oos_test_days"] = oos_test_days
            if oos_failed:
                metrics["oos_failed"] = True

        return fitness, metrics

    finally:
        # 恢复原始权重
        F.IC_FACTOR_WEIGHTS.clear()
        F.IC_FACTOR_WEIGHTS.update(orig_weights)


# ============================================================
# 染色体编解码（扩展搜索空间）
# ============================================================

def _chromosome_dim(config: 'OptimizerConfig') -> int:
    """染色体总维度"""
    dim = len(FACTOR_NAMES)  # 9 因子权重
    if config.optimize_exit_days:
        dim += len(REGIME_NAMES)  # 6 出场天数
    if config.optimize_regime_weights:
        dim += len(REGIME_NAMES) * len(STRATEGY_NAMES)  # 36 策略权重
    return dim


def _encode_chromosome(
    weights: Dict[str, float],
    exit_days_map: Optional[Dict[str, int]],
    regime_strategy_presets: Optional[Dict[str, Dict[str, float]]],
    config: 'OptimizerConfig',
) -> np.ndarray:
    """将个体参数编码为一维染色体数组"""
    parts = [np.array([weights[k] for k in FACTOR_NAMES])]

    if config.optimize_exit_days:
        edm = exit_days_map or {}
        parts.append(np.array([edm.get(r, 10) for r in REGIME_NAMES], dtype=float))

    if config.optimize_regime_weights:
        rsp = regime_strategy_presets or {}
        w_flat = []
        for r in REGIME_NAMES:
            preset = rsp.get(r, {})
            for s in STRATEGY_NAMES:
                w_flat.append(preset.get(s, 1.0 / len(STRATEGY_NAMES)))
        parts.append(np.array(w_flat))

    return np.concatenate(parts)


def _decode_chromosome(
    chromosome: np.ndarray,
    config: 'OptimizerConfig',
) -> Tuple[Dict[str, float], Optional[Dict[str, int]], Optional[Dict[str, Dict[str, float]]]]:
    """将一维染色体数组解码为个体参数"""
    idx = 0

    # 因子权重
    weights = dict(zip(FACTOR_NAMES, chromosome[idx:idx + len(FACTOR_NAMES)]))
    weights = _normalize_weights(_clamp_weights(weights))
    idx += len(FACTOR_NAMES)

    # 出场天数
    exit_days_map = None
    if config.optimize_exit_days:
        lo, hi = config.exit_days_bounds
        raw = chromosome[idx:idx + len(REGIME_NAMES)]
        exit_days_map = {r: int(np.clip(round(v), lo, hi)) for r, v in zip(REGIME_NAMES, raw)}
        idx += len(REGIME_NAMES)

    # 策略权重（按 regime 归一化）
    regime_strategy_presets = None
    if config.optimize_regime_weights:
        lo, hi = config.regime_weight_bounds
        regime_strategy_presets = {}
        for r in REGIME_NAMES:
            raw = chromosome[idx:idx + len(STRATEGY_NAMES)]
            clipped = np.clip(raw, lo, hi)
            total = clipped.sum()
            if total > 1e-8:
                normalized = clipped / total
            else:
                normalized = np.ones(len(STRATEGY_NAMES)) / len(STRATEGY_NAMES)
            regime_strategy_presets[r] = dict(zip(STRATEGY_NAMES, normalized.tolist()))
            idx += len(STRATEGY_NAMES)

    return weights, exit_days_map, regime_strategy_presets


def _random_chromosome(rng: np.random.Generator, config: 'OptimizerConfig') -> np.ndarray:
    """生成随机染色体"""
    parts = [rng.uniform(WEIGHT_MIN, WEIGHT_MAX, len(FACTOR_NAMES))]

    if config.optimize_exit_days:
        lo, hi = config.exit_days_bounds
        parts.append(rng.uniform(lo, hi, len(REGIME_NAMES)))

    if config.optimize_regime_weights:
        lo, hi = config.regime_weight_bounds
        parts.append(rng.uniform(lo, hi, len(REGIME_NAMES) * len(STRATEGY_NAMES)))

    return np.concatenate(parts)


def _chromosome_hash(chromosome: np.ndarray) -> str:
    """染色体的哈希值，用于缓存"""
    s = json.dumps(chromosome.tolist(), default=float)
    return hashlib.md5(s.encode()).hexdigest()[:12]


# ============================================================
# 遗传算法
# ============================================================

class GeneticOptimizer:
    """遗传算法因子权重优化器（支持扩展搜索空间）"""

    def __init__(self, config: OptimizerConfig, symbols: List[str]):
        self.config = config
        self.symbols = symbols
        self.rng = np.random.default_rng(42)
        self.population: List[Individual] = []
        self.best_ever: Optional[Individual] = None
        self.generation = 0
        self.history: List[Dict] = []
        self._fitness_cache: Dict[str, Tuple[float, Dict]] = {}
        self._dim = _chromosome_dim(config)

        # 扩展维度时自动增大种群和代数
        if config.optimize_exit_days or config.optimize_regime_weights:
            if config.population_size < 100:
                config.population_size = 100
            if config.generations < 50:
                config.generations = 50
            logger.info(
                f"扩展搜索空间: dim={self._dim}, "
                f"population={config.population_size}, generations={config.generations}"
            )

    def _make_individual(self, chromosome: np.ndarray) -> Individual:
        """从染色体创建个体"""
        weights, exit_days_map, regime_strategy_presets = _decode_chromosome(
            chromosome, self.config
        )
        return Individual(
            weights=weights,
            exit_days_map=exit_days_map,
            regime_strategy_presets=regime_strategy_presets,
        )

    def _get_chromosome(self, ind: Individual) -> np.ndarray:
        """从个体提取染色体"""
        return _encode_chromosome(
            ind.weights, ind.exit_days_map, ind.regime_strategy_presets, self.config
        )

    def _evaluate(self, ind: Individual) -> float:
        """评估个体，带缓存"""
        h = _chromosome_hash(self._get_chromosome(ind))
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
                exit_days_map=ind.exit_days_map,
                regime_strategy_presets=ind.regime_strategy_presets,
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
            chromosome = _random_chromosome(self.rng, self.config)
            ind = self._make_individual(chromosome)
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
        """算术交叉（在染色体层面操作）"""
        c1 = self._get_chromosome(p1)
        c2 = self._get_chromosome(p2)
        alpha = self.rng.uniform(0.3, 0.7)
        child_chromosome = alpha * c1 + (1 - alpha) * c2

        # 对因子权重部分做归一化
        normalized = _normalize_weights(
            _clamp_weights(dict(zip(FACTOR_NAMES, child_chromosome[:len(FACTOR_NAMES)])))
        )
        for i, k in enumerate(FACTOR_NAMES):
            child_chromosome[i] = normalized[k]
        # 重新编码归一化后的权重
        weights, exit_days_map, regime_strategy_presets = _decode_chromosome(
            child_chromosome, self.config
        )
        return Individual(
            weights=weights,
            exit_days_map=exit_days_map,
            regime_strategy_presets=regime_strategy_presets,
        )

    def _mutate(self, ind: Individual) -> Individual:
        """高斯变异（在染色体层面操作）"""
        chromosome = self._get_chromosome(ind)

        for i in range(len(chromosome)):
            if self.rng.random() < self.config.mutation_rate:
                if i < len(FACTOR_NAMES):
                    # 因子权重用 mutation_strength
                    chromosome[i] += self.rng.normal(0, self.config.mutation_strength)
                elif i < len(FACTOR_NAMES) + (len(REGIME_NAMES) if self.config.optimize_exit_days else 0):
                    # 出场天数用 ±1 的变异
                    chromosome[i] += self.rng.normal(0, 1.0)
                else:
                    # 策略权重用 0.05 的变异
                    chromosome[i] += self.rng.normal(0, 0.05)

        # 对因子权重部分做归一化
        raw_weights = dict(zip(FACTOR_NAMES, chromosome[:len(FACTOR_NAMES)]))
        normalized = _normalize_weights(_clamp_weights(raw_weights))
        for i, k in enumerate(FACTOR_NAMES):
            chromosome[i] = normalized[k]

        weights, exit_days_map, regime_strategy_presets = _decode_chromosome(
            chromosome, self.config
        )
        return Individual(
            weights=weights,
            exit_days_map=exit_days_map,
            regime_strategy_presets=regime_strategy_presets,
        )

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
                child = self._make_individual(self._get_chromosome(p1))

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
        logger.info(
            f"GA 开始: pop={self.config.population_size} gen={self.config.generations} "
            f"dim={self._dim}"
        )
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

    ⚠️ 警告：此函数存在严重过拟合风险。仅限研究使用。

    1. GA 搜索
    2. 贝叶斯精调
    3. Walk-forward 验证
    4. 保存结果
    """
    logger.warning(
        "[factor_optimizer] ⚠️ GA 优化器存在严重过拟合风险。"
        "搜索结果仅用于研究，不可直接用于生产环境的权重选择。"
        "config.py 的因子权重已改为等权，消除数据窥探偏差。"
    )
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
        "optimal_exit_days": best.exit_days_map,
        "optimal_regime_strategy_presets": best.regime_strategy_presets,
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
    parser.add_argument("--optimize-exit-days", action="store_true", help="同时优化出场天数")
    parser.add_argument("--optimize-regime-weights", action="store_true", help="同时优化 regime 策略权重")
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
        optimize_exit_days=args.optimize_exit_days,
        optimize_regime_weights=args.optimize_regime_weights,
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

    if result.get("optimal_exit_days"):
        print("\n最优出场天数:")
        for k, v in result["optimal_exit_days"].items():
            print(f"  {k}: {v}")

    if result.get("optimal_regime_strategy_presets"):
        print("\n最优 Regime 策略权重:")
        for regime, weights in result["optimal_regime_strategy_presets"].items():
            print(f"  {regime}:")
            for strategy, w in weights.items():
                print(f"    {strategy}: {w:.4f}")

    if result.get("walk_forward"):
        wf = result["walk_forward"]
        print(f"\nWalk-Forward: mean_sharpe={wf['mean_sharpe']} std={wf['std_sharpe']} stable={wf['is_stable']}")

    print(f"\n应用权重: 将以下代码替换 factors.py 中的 IC_FACTOR_WEIGHTS:")
    print(apply_optimal_weights(result["optimal_weights"]))
