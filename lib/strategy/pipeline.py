"""管道层：策略编排、组合优化、ML 集成"""
import pandas as pd
import numpy as np
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import logging
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import get_config, SHIBOR_TIGHT_THRESHOLD, SHIBOR_CAUTION_THRESHOLD
from factors import (compute_all_signals, evaluate_macro_gate, calc_market_exposure)
from lib.data.data_layer import (get_stock_hist_multi_source, get_realtime_quotes,
                             get_market_sentiment, _load_cache, _get_financial_data,
                             clean_cache, get_news_signals, _safe_float,
                             get_macro_hist_local, get_index_hist)
from lib.scoring.scorer import (score_fundamental, score_capital_flow,
                         score_news, score_technical)
from lib.risk.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
from lib.scoring.evaluator import build_forward_returns
from lib.strategy.strategies import (get_zt_candidates, get_volume_breakout_candidates,
                             get_hot_board_candidates, get_lhb_candidates,
                             get_multi_factor_candidates, full_score)
from lib.data.data_sector import get_sector_stocks
from lib.data.broker import LRUCache
from lib.monitoring.market_regime import (detect_market_regime_from_hs300,
                                 TREND_UP, MEAN_REVERSION, HIGH_VOLATILITY, LOW_VOLATILITY,
                                 TREND_DOWN, RISK_OFF, REGIME_POSITION_SCALAR)
from lib.risk.position_size import RegimeAwarePositionSizer

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "cache"
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())

# ML 因子缓存（容量限制的 LRU 缓存，避免无界增长）
_ml_factor_cache = LRUCache(maxsize=256)


# ========== 扫描全局 deadline（评审 P1-1，2026-11-20） ==========

class ScanDeadlineExceeded(Exception):
    """扫描预算耗尽（部分结果语义：候选股与风控保留，昂贵尾相位跳过）。"""


def _scan_deadline_s() -> float:
    """扫描全局预算（秒）。env ``SCAN_DEADLINE_S`` 秒调；0 = 关闭（回滚值）。

    默认 480s：冷扫正常 2~4 分钟、源降级最坏路径（ADR-0019 后单源有界）叠加
    ML 富集 < 8 分钟——超线说明有相位病态卡死，宁要部分结果不要无限等
    （ADR-0019 Context 实测「10+ 分钟无新日志」的复发封堵）。
    """
    raw = os.environ.get("SCAN_DEADLINE_S", "480").strip()
    try:
        return float(raw)
    except ValueError:
        return 480.0


def _deadline_left(t0: float, deadline_s: float, phase: str) -> bool:
    """相位检查点：预算耗尽 → warning 一次，调用方跳过该相位。

    只护**昂贵尾相位**（板块过滤 / 组合优化 / ML 富集），策略并行是扫描核心价值
    不设检查点——部分结果 = 候选股 + 风控 + 宏观上下文保留。
    """
    if deadline_s > 0 and (time.monotonic() - t0) > deadline_s:
        logger.warning(
            f"[scan-deadline] 预算 {deadline_s:.0f}s 在相位 [{phase}] 前耗尽——"
            f"跳过该相位，返回部分结果")
        return False
    return True


# ========== 策略降频机制（V6 设计要求） ==========

class StrategyFailureTracker:
    """策略连续失效跟踪器

    设计要求（V6 系统失效规则）：
    - 信号连续失效 → 先降频，不硬扛
    - 连续 3 次空结果或异常 → 跳过该策略（降低扫描频率）
    - 策略恢复产出 → 重置计数器

    持久化到 cache/strategy_failures.json，跨 session 保持。
    """

    def __init__(self, persist: bool = False):
        self._path = Path(__file__).parent.parent.parent / "cache" / "strategy_failures.json"
        self._failures: Dict[str, int] = {}  # strategy_name -> consecutive failures
        self._threshold = 3  # 连续失效次数阈值
        self._persist = persist
        if persist:
            self._load()

    def record_failure(self, strategy_name: str):
        """记录策略失效（空结果或异常）"""
        self._failures[strategy_name] = self._failures.get(strategy_name, 0) + 1
        if self._failures[strategy_name] >= self._threshold:
            logger.warning(
                f"【降频】策略 {strategy_name} 连续失效 {self._failures[strategy_name]} 次，本轮跳过"
            )

    def record_success(self, strategy_name: str):
        """记录策略成功（有候选股产出）"""
        if self._failures.get(strategy_name, 0) > 0:
            logger.info(f"【恢复】策略 {strategy_name} 恢复产出，重置失效计数")
        self._failures[strategy_name] = 0

    def should_skip(self, strategy_name: str) -> bool:
        """检查是否应跳过该策略"""
        return self._failures.get(strategy_name, 0) >= self._threshold

    def get_failure_count(self, strategy_name: str) -> int:
        return self._failures.get(strategy_name, 0)

    def save(self):
        # P0 修复（2026-06-05）：save 此前被 _persist 闸住，默认不写盘，导致
        # test_persistence 失败。_persist 仅控制 init 时是否自动 _load 即可。
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._failures, f)

    def _load(self):
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._failures = json.load(f)
        except Exception:
            self._failures = {}


# 全局实例
_failure_tracker = StrategyFailureTracker()


# ========== V4 市场状态调整 ==========

def _apply_regime_adjustment(candidates: list, regime: str) -> list:
    """根据 V4 市场状态调整候选股评分

    V4 规则：
    - TREND_UP: 正常评分
    - MEAN_REVERSION: 短线为主，降低趋势类因子权重（波动率20低 → 加分）
    - HIGH_VOLATILITY: 降低置信度，等突破确认
    - LOW_VOLATILITY: 适度加分
    """
    if not candidates or regime == TREND_UP:
        return candidates

    adjusted = []
    for c in candidates:
        c = c.copy()
        if regime == HIGH_VOLATILITY:
            # 高波动市场：降低置信度，等突破确认
            orig_conf = c.get("ML置信度", 0.5)
            c["ML置信度"] = round(orig_conf * 0.5, 4)
            c["调整原因"] = "HIGH_VOLATILITY"

        elif regime == LOW_VOLATILITY:
            # 低波动市场：适度加分
            orig_conf = c.get("ML置信度", 0.5)
            c["ML置信度"] = round(orig_conf * 1.1, 4)
            c["调整原因"] = "LOW_VOLATILITY加分"

        elif regime == MEAN_REVERSION:
            # 均值回归市场：短线操作
            vol = c.get("波动率20", 25)
            # 低波动股票在均值回归市场表现更好，小幅加分
            if vol < 20:
                orig_conf = c.get("ML置信度", 0.5)
                c["ML置信度"] = round(orig_conf * 1.1, 4)
                c["调整原因"] = "MEAN_REVERSION低波动加分"

        adjusted.append(c)

    return adjusted


# ========== 组合风控（委托给 pipeline_modules）==========

def _check_portfolio_risk(results: dict, shibor_mult: float = 1.0) -> dict:
    """检查组合风险（委托实现）"""
    from lib.strategy.risk_checker import (
        check_portfolio_risk as _rc_check_portfolio_risk,
    )
    return _rc_check_portfolio_risk(results, shibor_mult=shibor_mult)


def _check_factor_health(results: dict) -> dict:
    """检查因子健康度（委托实现）"""
    from lib.strategy.risk_checker import (
        check_factor_health as _rc_check_factor_health,
    )
    return _rc_check_factor_health(results)


def _get_sector_map(codes: list) -> dict:
    sector_map = {}
    try:
        # 优先用缓存，不阻塞扫描
        df = _load_cache("realtime/realtime", max_age_hours=1)
        if df.empty:
            df = get_realtime_quotes()
        if not df.empty:
            code_col = None
            sector_col = None
            for col in df.columns:
                if "代码" in str(col):
                    code_col = col
                if "行业" in str(col):
                    sector_col = col
            if code_col and sector_col:
                mask = df[code_col].astype(str).isin(codes)
                sector_map = df.loc[mask].set_index(code_col)[sector_col].astype(str).to_dict()
    except Exception as e:
        logger.debug(f"[pipeline] 行业分布获取失败: {e}")
    return sector_map


def _load_current_weights() -> dict:
    try:
        from lib.risk.position_manager import PositionManager
        pm = PositionManager()
        positions = pm.get_all_positions()
        if not positions:
            return {}
        total_value = sum(p.get("market_value", 0) for p in positions.values())
        if total_value <= 0:
            return {}
        return {code: p.get("market_value", 0) / total_value
                for code, p in positions.items()}
    except Exception as e:
        logger.debug(f"[pipeline] 持仓权重获取失败: {e}")
        return {}


def _optimize_portfolio(results: dict) -> dict:
    """组合优化 — 使用 PortfolioManager（分层架构）

    多策略结构：每策略独立 PositionSizer → RiskBudget → Turnover，
    再由 PortfolioManager 汇总权重 + 总风控检查。
    """
    from lib.portfolio.portfolio_manager import PortfolioManager, StrategyConfig

    # 构建每策略候选股 dict {策略名: [候选股列表]}
    strategy_candidates = {}
    candidate_info = {}  # code → {name, score, volatility}

    for name, df in results.items():
        if df.empty or "代码" not in df.columns:
            continue
        df_work = df.copy()
        df_work["代码"] = df_work["代码"].astype(str)
        if "名称" in df_work.columns:
            df_work["名称"] = df_work["名称"].fillna("").astype(str)

        if "综合评分" in df_work.columns:
            df_work["_score"] = pd.to_numeric(df_work["综合评分"], errors="coerce")
        else:
            df_work["_score"] = pd.to_numeric(df_work.get("置信度", 0), errors="coerce") * 10

        vol_col = "波动率20" if "波动率20" in df_work.columns else "volatility_20"
        if vol_col in df_work.columns:
            df_work["_volatility"] = pd.to_numeric(df_work[vol_col], errors="coerce").fillna(25) / 100
        else:
            df_work["_volatility"] = 0.25  # 默认25%波动率

        df_valid = df_work[df_work["_score"].notna()].copy()
        strat_candidates = []
        for _, row in df_valid.iterrows():
            code = row["代码"]
            score = float(row["_score"])
            vol = float(row["_volatility"])
            strat_candidates.append({
                "code": code,
                "name": row.get("名称", ""),
                "score": score,
                "volatility": vol,
            })
            # candidate_info: 以最高评分为准
            if code not in candidate_info or score > candidate_info[code].get("score", 0):
                candidate_info[code] = {
                    "name": row.get("名称", ""),
                    "score": score,
                    "volatility": vol,
                }

        if strat_candidates:
            strategy_candidates[name] = strat_candidates

    if not strategy_candidates:
        return {"weights": {}, "expected_return": 0, "expected_vol": 0,
                "sharpe_ratio": 0, "sector_exposure": {}, "n_positions": 0,
                "turnover": 0, "details": []}

    # 多策略等权配置
    strategies = [
        StrategyConfig(name=name, weight=1.0 / len(strategy_candidates))
        for name in strategy_candidates
    ]
    current_weights = _load_current_weights()
    pm = PortfolioManager(strategies=strategies)
    portfolio = pm.optimize(strategy_candidates, current_weights)

    # 构建 sector_map（用于 details 和 sector_exposure）
    codes_with_weights = list(portfolio.weights.keys())
    sector_map = _get_sector_map(codes_with_weights) if codes_with_weights else {}

    # 构建 details（与旧 optimizer 格式兼容）
    details = []
    sector_exposure = {}
    for code, weight in portfolio.weights.items():
        info = candidate_info.get(code, {})
        sector = sector_map.get(code, "")
        details.append({
            "code": code,
            "name": info.get("name", ""),
            "weight": round(float(weight), 4),
            "score": round(float(info.get("score", 0)), 2),
            "sector": sector,
        })
        if sector:
            sector_exposure[sector] = sector_exposure.get(sector, 0) + weight

    # sector_exposure 归一化为权重
    total_sec = sum(sector_exposure.values())
    if total_sec > 0:
        sector_exposure = {k: round(v / total_sec, 4) for k, v in sector_exposure.items()}

    return {
        "weights": portfolio.weights,
        "expected_return": portfolio.expected_return,
        "expected_vol": portfolio.expected_vol,
        "sharpe_ratio": portfolio.sharpe_ratio,
        "sector_exposure": sector_exposure,
        "n_positions": portfolio.n_positions,
        "turnover": portfolio.turnover,
        "details": details,
    }


def _run_alpha_analysis(results: dict) -> dict:
    try:
        from lib.scoring.alpha import AlphaSynthesizer
    except ImportError:
        return {}
    factor_rows = []
    for name, df in results.items():
        if df.empty:
            continue
        if "代码" not in df.columns:
            continue
        # 向量化处理，避免 iterrows
        df_work = df[[c for c in ["代码", "RSI", "动量5", "置信度", "综合评分", "涨跌幅"] if c in df.columns]].copy()
        df_work["代码"] = df_work["代码"].astype(str)
        df_work = df_work.rename(columns={"代码": "code"})
        factor_rows.append(df_work)
    if not factor_rows:
        return {}
    factor_df = pd.concat(factor_rows, ignore_index=False).set_index("code")
    factor_df = factor_df.apply(pd.to_numeric, errors="coerce").dropna(how="all")
    if factor_df.empty or len(factor_df) < 3:
        return {}
    synth = AlphaSynthesizer()
    zscore_summary = {}
    try:
        zscored = synth.normalize_cross_section(factor_df)
        for col in zscored.columns:
            vals = zscored[col].dropna()
            if len(vals) > 0:
                zscore_summary[col] = {"mean": round(float(vals.mean()), 4),
                                       "std": round(float(vals.std()), 4),
                                       "min": round(float(vals.min()), 4),
                                       "max": round(float(vals.max()), 4)}
    except Exception as e:
        logger.warning(f"[pipeline] Alpha zscore 计算失败: {e}")
    factor_ic = {}
    opt_weights = {}
    try:
        # 使用 evaluator.py 的 build_forward_returns 获取真正的未来收益
        # 默认使用 5 日前向收益（5d）进行 IC 分析
        forward_returns = None
        if "close" in factor_df.columns:
            forward_df = build_forward_returns(factor_df["close"], periods=[5])
            forward_returns = forward_df["5d"].fillna(0) if "5d" in forward_df.columns else None
        elif "涨跌幅" in factor_df.columns:
            # 降级方案：使用当日涨跌幅（仅当无 close 列时）
            forward_returns = factor_df["涨跌幅"].fillna(0) / 100
        if forward_returns is not None and len(forward_returns) >= 5:
            for col in factor_df.columns:
                if col == "涨跌幅":
                    continue
                ic_result = synth.compute_ic(factor_df[col], forward_returns)
                if not np.isnan(ic_result.get("rank_ic", np.nan)):
                    factor_ic[col] = {"ic": round(ic_result["ic"], 4) if not np.isnan(ic_result.get("ic", np.nan)) else None,
                                      "rank_ic": round(ic_result["rank_ic"], 4),
                                      "n_samples": int(ic_result.get("n_samples", 0))}
            try:
                opt_weights = synth.optimize_weights_ic_ir(
                    factor_df.drop(columns=["涨跌幅"], errors="ignore"), forward_returns)
                opt_weights = {k: round(v, 4) for k, v in opt_weights.items()}
            except Exception as e:
                logger.warning(f"组合权重优化失败: {e}")
    except Exception as e:
        logger.debug(f"因子 IC 分析异常: {e}")
    result = {"factor_ic": factor_ic, "cross_section_zscore": zscore_summary,
              "optimized_weights": opt_weights}

    # V8: Alpha 工厂 — 自动发现和评估 Alpha
    try:
        from lib.scoring.alpha_factory import AlphaFactory
        factory = AlphaFactory()
        # 用 IC 分析结果发现新 Alpha
        for factor_name, ic_info in factor_ic.items():
            ic_val = ic_info.get("rank_ic", 0) or 0
            if abs(ic_val) >= 0.03:
                factory.discover(factor_name, name=f"{factor_name}(IC={ic_val:.3f})")
        factory.update_lifecycle()
        result["alpha_factory"] = factory.get_report()
        factory.save()
    except Exception as e:
        logger.debug(f"[pipeline] Alpha 工厂分析失败: {e}")

    return result


# ========== 20d ML 评分 ==========

def _check_monitor_alerts(ml_ranking: dict = None):
    """因子健康度检查 + IC 衰减告警"""
    from config import MonitorConfig
    monitor_config = MonitorConfig()

    panel_path = Path(__file__).resolve().parent.parent.parent / "cache" / "panel_2015.parquet"
    if panel_path.exists():
        try:
            from lib.scoring.factor_monitor import FactorMonitor
            panel = pd.read_parquet(panel_path)
            panel["date"] = pd.to_datetime(panel["date"])
            recent = panel[panel["date"] >= panel["date"].max() - pd.Timedelta(days=90)]
            if len(recent) > 0:
                fm = FactorMonitor()
                ic_result = fm.compute_ic(recent)
                negative_ic = [k for k, v in ic_result.items()
                              if isinstance(v, dict) and v.get("mean_ic", 0) < 0]
                if negative_ic:
                    logger.warning(f"因子监控: {len(negative_ic)} 个因子近 90 天 IC 为负: {negative_ic[:5]}")

                # IC 衰减告警：连续 N 天 IC < 0
                decay_alerts = fm.detect_ic_decay_alerts(
                    ic_result, consecutive_threshold=monitor_config.ic_alert_consecutive
                )
                if decay_alerts:
                    for alert in decay_alerts:
                        logger.warning(f"IC衰减告警 [{alert['severity']}]: {alert['message']}")
                    # 尝试推送飞书告警（P-79 遗留项修复：此前把 alert dict 直接传
                    # 旧策略统计入口，形状不匹配 AttributeError 被吞，从未真正推送）
                    try:
                        from reporter.scan_notify import send_alert_message
                        send_alert_message("ic_decay_alert", decay_alerts)
                    except Exception as e:
                        logger.debug(f"[pipeline] 飞书告警发送失败: {e}")
                else:
                    logger.info("因子监控: 全部因子 IC 正常")
        except Exception as e:
            logger.warning(f"[pipeline] 因子监控检查失败: {e}")
    else:
        logger.debug("因子监控: 面板数据不存在，跳过")


def _enrich_with_ml_scores(results: dict) -> dict:
    """对候选股执行 20d ML 评分 + momentum_20 > -5% 过滤

    包含信号频率监控：月均信号 < 3 次时日志告警。
    """
    all_codes = set()
    code_names = {}
    for name, df in results.items():
        if df.empty:
            continue
        if "代码" not in df.columns:
            continue
        # 向量化处理，避免 iterrows
        codes = df["代码"].astype(str).tolist()
        names = df["名称"].astype(str).tolist() if "名称" in df.columns else ["" for _ in codes]
        for code, name in zip(codes, names):
            if code:
                all_codes.add(code)
                code_names[code] = name
    if not all_codes:
        return {"data": None, "n_candidates": 0, "n_passed": 0, "method": "20d_ml"}
    try:
        from lib.ml.ml_predictor import get_predictor
        _predictor = get_predictor()
    except Exception as e:
        logger.warning(f"ML 模型加载失败: {e}")
        return {"data": None, "n_candidates": len(all_codes), "n_passed": 0,
                "method": "20d_ml", "error": str(e)}
    # ML 因子缓存：模块级 LRUCache（容量256，自动淘汰）
    # P1-1（2026-11-20）：历史**串行**逐股 0.5~1.5s × 80-130 只 = 50~150s（ADR-0019
    # Context 实测 10+ min 的主要贡献段）→ 拆两阶段：取数+因子计算 8 线程并行（≈6-20s），
    # predict 保持串行（predictor 单例，单样本毫秒级，避免并发触模型内部状态）。
    # None 也入缓存（负缓存：本轮不重取空历史）。
    pending = [c for c in all_codes if c not in _ml_factor_cache]

    def _compute_one(code):
        try:
            hist = get_stock_hist_multi_source(code, days=180, source="auto",
                                                prefer_adjusted=True)
            if hist.empty or len(hist) < 60:
                return code, None
            return code, compute_all_signals(hist, code=code)
        except Exception as e:
            logger.warning(f"[pipeline] {code} ML 因子计算失败: {e}")
            return code, None

    if pending:
        with ThreadPoolExecutor(max_workers=min(8, len(pending)),
                                thread_name_prefix="ml-enrich") as _pool:
            for code, sig in _pool.map(_compute_one, pending):
                _ml_factor_cache[code] = sig

    candidates = []
    for code in all_codes:
        try:
            result = _ml_factor_cache[code] if code in _ml_factor_cache else None
            if not result:
                continue
            factors = result["factors"]
            mom_20 = factors.get("momentum_20", 0)
            # P0: momentum_20 > -5% 过滤（性价比最高的过滤，+17% 夏普提升）
            if mom_20 <= -5:
                continue
            ml_pred = _predictor.predict_single(factors)
            ml_score = ml_pred.get("score", 0)
            ml_consensus = ml_pred.get("consensus", "HOLD")
            if ml_consensus not in ("BUY",):
                continue
            candidates.append({
                "代码": code, "名称": code_names.get(code, ""),
                "ML评分": round(ml_score, 4), "ML置信度": round(ml_pred.get("confidence", 0), 4),
                "动量20": round(mom_20, 2), "动量5": round(factors.get("momentum_5", 0), 2),
                "RSI": round(factors.get("rsi_14", 50), 1),
                "波动率20": round(factors.get("volatility_20", 25), 1),
            })
        except Exception as e:
            logger.warning(f"[pipeline] {code} ML 评分失败: {e}")
            continue
    if not candidates:
        return {"data": None, "n_candidates": len(all_codes), "n_passed": 0, "method": "20d_ml"}
    result_df = pd.DataFrame(candidates)
    result_df = result_df.sort_values("ML评分", ascending=False)
    result_df["策略"] = "20d_ML"

    # 信号频率监控：月均信号 < 3 次时告警
    if len(result_df) < 3:
        logger.warning(f"ML 信号频率过低: 仅 {len(result_df)} 只候选股通过，月均可能不足 3 次")

    logger.info(f"20d ML 评分: {len(all_codes)} 候选 -> {len(result_df)} 通过 -> Top 10 输出")
    # 契约（P-101）："data" 必须是 list[dict]，与 "top10" 同形。
    # 三个消费方都按这个形状写：
    #   - 本模块 _apply_regime_adjustment(candidates: list) 逐条 c.copy()/c.get()
    #   - 本模块 run_all_strategies 的 if ml_ranking.get("data")（DataFrame 布尔判断会抛 ValueError）
    #   - reporter/report_generator 的 `for item in data[:10]`（DataFrame 迭代出列名）
    # 旧实现塞 DataFrame，只有 ML 分支被数据恢复激活后才暴露（2026-11-19 扫描崩溃）。
    top10 = result_df.head(10).to_dict(orient="records")
    return {"data": top10, "n_candidates": len(all_codes),
            "n_passed": len(result_df),
            "top10": top10,
            "method": "20d_ml", "recommended_config": "纯20dML·月频·tc=0.01"}


# ========== 单股评分（供 API 调用） ==========

def get_stock_score(code: str) -> dict:
    """单股完整评分：因子 + 技术 + 四维评分 + 约束 + 风控"""
    from lib.strategy.steps import (
        fetch_stock_data, calculate_factors,
        score_with_realtime, score_without_realtime,
        evaluate_with_judge_ml, check_offset_anomaly,
    )

    # 1. 数据获取
    hist, rt_row, fin_data, idx_df = fetch_stock_data(code, days=180)
    if hist.empty:
        return {"error": f"未找到 {code} 数据"}

    # 2. 因子计算
    factors, tech = calculate_factors(hist)
    if not factors:
        return {"error": f"{code} 因子计算失败"}

    # 3. 评分（根据是否有实时行情选择路径）
    if rt_row is not None:
        score_result = score_with_realtime(code, hist, factors, rt_row, fin_data)
    else:
        score_result = score_without_realtime(code, factors, fin_data)

    # 4. 约束检查
    from lib.risk.selection_constraints import DeterministicConstraints
    cr = DeterministicConstraints().check_ticker(code, hist, fin_data)

    # 5. ML 预测
    ml_prediction = {}
    try:
        from lib.ml.ml_predictor import get_predictor
        predictor = get_predictor()
        if not predictor.is_model_stale():
            ml_prediction = predictor.predict_single(factors)
    except Exception as e:
        logger.debug(f"ML 预测失败: {e}")

    # 6. 裁判团 + ML 评审
    judge_result = evaluate_with_judge_ml(code, hist, factors, fin_data, ml_prediction)

    # 7. 偏移值监控
    offset_alert, score_adjustment = check_offset_anomaly(code, hist, factors, idx_df)
    if offset_alert and score_adjustment != 0:
        score_result["score"] = max(0, score_result.get("score", 0) + score_adjustment)
        score_result["offset_adjusted"] = True
        score_result["offset_alert"] = offset_alert

    return {
        "code": code,
        "factors": factors,
        "combined": {},
        "regime": {},
        "macro_gate": {},
        "technical": tech,
        "score": score_result,
        "judge_panel": judge_result,
        "ml_prediction": ml_prediction,
        "constraints": {
            "passed": cr.passed,
            "blocked": cr.blocked,
            "violations": cr.violations,
            "score_adjustment": cr.score_adjustment,
        },
    }

    # V7: 机会评分
    try:
        from lib.scoring.opportunity_scorer import score_from_signals
        # 构造简化的 signal_result 用于评分
        signal_result = {
            "regime": {"regime": "MEAN_REVERSION", "confidence": 0.5},
            "factors": factors,
            "data_factors": {},
        }
        result["opportunity_score"] = score_from_signals(signal_result)
    except Exception as e:
        logger.debug(f"[pipeline] 机会评分计算失败: {e}")


# ========== 主入口 ==========

def run_all_strategies(
    preset: str = "balanced",
    symbols: list = None,
    include_ml: bool = True,
    include_alpha: bool = False,
) -> dict:
    """运行所有策略

    Args:
        preset: 策略预设 (balanced/aggressive/conservative)
        symbols: 股票代码列表，用于结果过滤（可选）
        include_ml: 是否启用ML增强评分
        include_alpha: 是否执行Alpha分析
    """
    config = get_config(preset)
    results = {}
    clean_cache(max_age_hours=24)
    # P1-1：全局预算（env SCAN_DEADLINE_S 可秒调，0=关闭）
    t0 = time.monotonic()
    budget = _scan_deadline_s()
    deadline_skipped: list = []

    # 市场情绪：用缓存，不阻塞扫描（实时行情获取耗时 30s+）
    sentiment = 0
    try:
        cached = _load_cache("realtime/realtime", max_age_hours=1)
        if not cached.empty and "涨跌幅" in cached.columns:
            up = (cached["涨跌幅"] > 0).sum()
            down = (cached["涨跌幅"] < 0).sum()
            total = up + down
            if total > 0:
                sentiment = max(-1, min(1, (up - down) / total))
                logger.info(f"市场情绪(缓存): {sentiment:.2f}")
    except Exception:
        pass

    # ========== 宏观 + 指数过滤 ==========
    macro_filter = {"shibor": None, "shibor_regime": "UNKNOWN", "index_ma20": None, "index_trend": "UNKNOWN", "position_mult": 1.0}
    index_signal = {"bull_market": True}
    try:
        shibor_df = get_macro_hist_local("SHIBOR", days=10)
        if not shibor_df.empty:
            shibor_val = float(shibor_df["close"].iloc[-1])
            macro_filter["shibor"] = shibor_val
            if shibor_val > SHIBOR_TIGHT_THRESHOLD:
                macro_filter["shibor_regime"] = "TIGHT"
                macro_filter["position_mult"] = 0.0
            elif shibor_val > SHIBOR_CAUTION_THRESHOLD:
                macro_filter["shibor_regime"] = "CAUTION"
                macro_filter["position_mult"] = 0.5
            else:
                macro_filter["shibor_regime"] = "NORMAL"
            logger.info(f"SHIBOR: {shibor_val:.3f}% ({macro_filter['shibor_regime']})")
    except Exception as e:
        logger.debug(f"SHIBOR 获取失败: {e}")

    try:
        idx_df = get_index_hist("000001", days=30)
        if not idx_df.empty and "close" in idx_df.columns:
            close_arr = idx_df["close"].values
            if len(close_arr) >= 20:
                ma20 = float(np.mean(close_arr[-20:]))
                latest_close = float(close_arr[-1])
                macro_filter["index_ma20"] = ma20
                if latest_close > ma20:
                    macro_filter["index_trend"] = "BULL"
                    index_signal["bull_market"] = True
                else:
                    macro_filter["index_trend"] = "BEAR"
                    index_signal["bull_market"] = False
                logger.info(f"上证指数: {latest_close:.2f} / MA20 {ma20:.2f} ({macro_filter['index_trend']})")
    except Exception as e:
        logger.debug(f"指数数据获取失败: {e}")

    # 指数熊市时强制降低仓位
    if not index_signal["bull_market"] and macro_filter["position_mult"] > 0:
        macro_filter["position_mult"] *= 0.5
        logger.info("指数熊市，仓位乘数减半")
    logger.info(f"宏观仓位乘数: {macro_filter['position_mult']}")

    # ========== V4 市场状态检测（HS300 驱动）==========
    try:
        hs300_regime = detect_market_regime_from_hs300(days=60, use_hysteresis=True, hysteresis_days=3)
        regime = hs300_regime["regime"]
        regime_conf = hs300_regime["confidence"]
        position_scalar = hs300_regime["position_scalar"]
        logger.info(f"V4市场状态: {regime} (置信度 {regime_conf:.0%}), 仓位范围 {position_scalar}")

        # V4 规则：TREND_DOWN 或 RISK_OFF 时禁止开仓
        if regime in (TREND_DOWN, RISK_OFF):
            logger.warning(f"【V4】市场状态 {regime}，禁止开仓，返回空结果")
            return {}

        # V4 规则：HIGH_VOLATILITY 时降低仓位
        if regime == HIGH_VOLATILITY:
            macro_filter["position_mult"] *= 0.5
            logger.info("【V4】市场高波动，仓位乘数减半")

        # 将 V4 仓位范围注入 macro_filter 供后续使用
        macro_filter["v4_regime"] = regime
        macro_filter["v4_position_scalar"] = position_scalar
    except Exception as e:
        logger.debug(f"V4市场状态检测失败: {e}")

    strategies = {
        "涨停板复盘": get_zt_candidates,
        "量价突破": get_volume_breakout_candidates,
        "热门板块": get_hot_board_candidates,
        "龙虎榜": get_lhb_candidates,
        "多因子选股": get_multi_factor_candidates,
    }

    # ========== 市场状态→策略映射（V6 连续权重分配）==========
    regime = macro_filter.get("v4_regime", "MEAN_REVERSION")

    # V6: 动态权重管理
    try:
        from lib.strategy.dynamic_weight_manager import DynamicWeightManager
        from lib.strategy.strategy_regime_mapper import get_strategy_allocation, apply_strategy_weights_to_candidates
        # P0-2 修复（2026-06-05）：维护 BayesianUpdater 单例并传给 DynamicWeightManager，
        # 让"根据交易结果微调"的自适应承诺真正生效
        from lib.scoring.bayesian_updater import BayesianUpdater
        from pathlib import Path
        _bayes_updater = BayesianUpdater(
            state_path=Path("cache/bayesian_state.json"),
            mode="live",
        )
        _weight_mgr = DynamicWeightManager(bayesian_updater=_bayes_updater)
        dyn_weights = _weight_mgr.compute_weights(regime=regime)
        allocation = get_strategy_allocation(regime)
        logger.info(
            f"【V6策略分配】{regime}: "
            f"active={allocation.active}, skipped={allocation.skipped}, "
            f"source={dyn_weights.source}"
        )
        # 跳过低于阈值的策略
        strategies = {k: v for k, v in strategies.items() if k in allocation.active}
    except Exception as e:
        # 回退到 V4 二元过滤
        logger.warning(f"V6 动态权重加载失败，回退 V4: {e}")
        allocation = None
        dyn_weights = None
        if regime == "MEAN_REVERSION":
            allowed = {"热门板块", "龙虎榜"}
            strategies = {k: v for k, v in strategies.items() if k in allowed}
        elif regime == "HIGH_VOLATILITY":
            allowed = {"龙虎榜"}
            strategies = {k: v for k, v in strategies.items() if k in allowed}
    # 并行执行策略（ThreadPoolExecutor）
    active_strategies = {
        name: func for name, func in strategies.items()
        if not _failure_tracker.should_skip(name)
    }
    skipped = set(strategies.keys()) - set(active_strategies.keys())
    for name in skipped:
        logger.warning(f"跳过策略 {name}（连续失效 {_failure_tracker.get_failure_count(name)} 次）")

    def _run_strategy(name, func):
        """单个策略执行（线程安全）"""
        logger.info(f"运行策略: {name}")
        try:
            df = func(config)
            return name, df, None
        except Exception as e:
            return name, pd.DataFrame(), e

    # 批次 B4 / P2-3：留存「热门板块」策略的**原始**输出，供下方主线板块过滤复用。
    # 必须在 symbols 过滤与策略权重调整之前取，否则复用会改变过滤口径（数值回归）。
    raw_hot_boards = None
    with ThreadPoolExecutor(max_workers=min(len(active_strategies), 4)) as executor:
        futures = {
            executor.submit(_run_strategy, name, func): name
            for name, func in active_strategies.items()
        }
        for future in as_completed(futures):
            name, df, error = future.result()
            if error:
                _failure_tracker.record_failure(name)
                logger.error(f"  -> 策略 {name} 执行失败: {error}")
                continue
            if name == "热门板块" and not df.empty:
                raw_hot_boards = df.copy()
            if not df.empty:
                # 如果指定了symbols，过滤结果
                if symbols:
                    df = df[df["代码"].isin(symbols)] if "代码" in df.columns else df
                if not df.empty:
                    # V6: 应用策略权重到候选股评分
                    try:
                        if allocation is not None:
                            weight = allocation.weights.get(name, 0.5)
                            if weight < 1.0:
                                records = df.to_dict(orient="records")
                                adjusted = apply_strategy_weights_to_candidates(records, name, weight)
                                if adjusted:
                                    df = pd.DataFrame(adjusted)
                    except Exception:
                        pass  # 权重应用失败不影响主流程
                    results[name] = df
                    _failure_tracker.record_success(name)
                    logger.info(f"  -> {name}: {len(df)} 只候选股")
                else:
                    _failure_tracker.record_failure(name)
                    logger.info(f"  -> {name}: 无候选股（过滤后）")
            else:
                _failure_tracker.record_failure(name)
                logger.info(f"  -> {name}: 无候选股")
    # 保存降频状态
    _failure_tracker.save()
    # ========== 主线板块过滤 ==========
    if _deadline_left(t0, budget, "主线板块过滤"):
        try:
            # 批次 B4 / P2-3：复用本轮并行策略已拿到的原始热门板块结果，
            # 少一次完整重跑（含涨停池网络拉取）。策略被降频跳过 / 执行失败 /
            # 返回空时回退到原路径，保证行为不变。
            hot_boards = raw_hot_boards
            if hot_boards is None:
                hot_boards = get_hot_board_candidates(config)
            if not hot_boards.empty:
                hot_sector_codes = set()
                # 同一板块名去重：结果并入 set，重复查询不改变任何数值，
                # 但能少建几次 pytdx 连接（P2-3 的低风险部分）。
                sectors = {str(row.get('板块', '')).strip()
                           for _, row in hot_boards.iterrows()}
                for sector in sorted(sectors):
                    if sector:
                        hot_sector_codes.update(get_sector_stocks(sector))
                if hot_sector_codes:
                    logger.info(f"主线板块过滤: {len(hot_sector_codes)} 只股票在主线板块中")
                    filtered_results = {}
                    for name, df in results.items():
                        if df.empty or "代码" not in df.columns:
                            filtered_results[name] = df
                            continue
                        mask = df["代码"].astype(str).isin(hot_sector_codes)
                        filtered = df[mask]
                        if not filtered.empty:
                            filtered_results[name] = filtered
                        logger.info(f"  {name}: {len(df)} -> {len(filtered)} (板块过滤后)")
                    results = filtered_results
        except Exception as e:
            logger.debug(f"主线板块过滤失败（跳过）: {e}")
    else:
        deadline_skipped.append("主线板块过滤")

    risk_check = _check_portfolio_risk(results, shibor_mult=macro_filter["position_mult"])
    factor_health = _check_factor_health(results)
    if _deadline_left(t0, budget, "组合优化"):
        portfolio = _optimize_portfolio(results)
    else:
        deadline_skipped.append("组合优化")
        # 最小合法形状：RegimeAwarePositionSizer 读 weights、审计读 n_positions 均有默认
        portfolio = {"weights": {}, "details": []}

    # ========== RegimeAwarePositionSizer: 市场状态仓位限制 ==========
    regime = macro_filter.get("v4_regime")
    regime_scalar = macro_filter.get("v4_position_scalar", (0.5, 1.0))
    sizer = RegimeAwarePositionSizer()

    if regime and regime not in (TREND_DOWN, RISK_OFF):
        # 用 regime 的 max scalar 作为组合权重上限
        max_weight = regime_scalar[1] if regime_scalar else 1.0
        min_weight = regime_scalar[0] if regime_scalar else 0.0

        weights = portfolio.get("weights", {})
        capped_weights = {}
        capped_count = 0
        for code, w in weights.items():
            capped = min(w, max_weight)
            capped_weights[code] = capped
            if capped < w:
                capped_count += 1

        # 如果有裁剪，重新归一化权重
        if capped_count > 0:
            total = sum(capped_weights.values())
            if total > 0:
                capped_weights = {k: v / total for k, v in capped_weights.items()}
            portfolio["weights"] = capped_weights
            # 同步更新 details 中的 weight
            for d in portfolio.get("details", []):
                if d["code"] in capped_weights:
                    d["weight"] = round(capped_weights[d["code"]], 4)

        logger.info(
            f"RegimeAwarePositionSizer: regime={regime}, "
            f"scalar={regime_scalar}, max_weight={max_weight:.0%}, "
            f"capped={capped_count} positions"
        )

    alpha_analysis = {}
    if include_alpha:
        alpha_analysis = _run_alpha_analysis(results)
    else:
        alpha_analysis = {}

    # ========== V7: 机会评分 ==========
    try:
        from lib.scoring.opportunity_scorer import score_from_signals
        from lib.scoring.alpha_feedback import AlphaFeedback
        alpha_feedback = AlphaFeedback()
        for name, df in results.items():
            if df.empty or "代码" not in df.columns:
                continue
            # 为候选股计算机会评分（使用已有的信号数据）
            # 注：这里不做完整 compute_all_signals（太慢），用已有的置信度和评分近似
            pass  # 机会评分在 API 单股评分中使用（get_stock_score）
    except Exception as e:
        logger.debug(f"[pipeline] V7 机会评分初始化失败: {e}")

    ml_ranking = {}
    if include_ml:
        if _deadline_left(t0, budget, "ML 富集"):
            ml_ranking = _enrich_with_ml_scores(results)
            # V4 市场状态调整
            regime = macro_filter.get("v4_regime", MEAN_REVERSION)
            if ml_ranking.get("data"):
                ml_ranking = ml_ranking.copy()
                ml_ranking["data"] = _apply_regime_adjustment(
                    ml_ranking["data"], regime
                )
            # 因子监控预警
            _check_monitor_alerts(ml_ranking)
        else:
            deadline_skipped.append("ML 富集")

    scan_result = {
        "results": results, "risk_check": risk_check,
        "factor_health": factor_health, "portfolio": portfolio,
        "alpha_analysis": alpha_analysis, "ml_ranking": ml_ranking,
        "preset": preset,
        "macro_filter": macro_filter,
        "index_signal": index_signal,
    }
    # P1-1：仅当发生过相位跳过才打标（正常路径零新增键，L2/L9 基线无漂移）
    if deadline_skipped:
        scan_result["scan_deadline"] = {
            "budget_s": budget, "skipped": deadline_skipped}
    try:
        from lib.monitoring.audit_logger import log_scan
        log_scan(scan_result)
    except Exception as e:
        logger.debug(f"审计日志写入失败: {e}")
    return scan_result
