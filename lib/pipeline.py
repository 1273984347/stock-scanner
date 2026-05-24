"""管道层：策略编排、组合优化、ML 集成"""
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import logging

from config import get_config
from factors import (compute_all_signals, evaluate_macro_gate, calc_market_exposure)
from lib.data_layer import (get_stock_hist_multi_source, get_realtime_quotes,
                             get_market_sentiment, _load_cache, _get_financial_data,
                             clean_cache, get_news_signals, _safe_float,
                             get_macro_hist_local, get_index_hist)
from lib.scorer import (score_fundamental, score_capital_flow,
                         score_news, score_technical, comprehensive_score)
from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
from lib.evaluator import build_forward_returns
from lib.strategies import (get_zt_candidates, get_volume_breakout_candidates,
                             get_hot_board_candidates, get_lhb_candidates,
                             get_multi_factor_candidates, full_score)
from lib.broker import LRUCache

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())

# ML 因子缓存（容量限制的 LRU 缓存，避免无界增长）
_ml_factor_cache = LRUCache(maxsize=256)


# ========== 组合风控（委托给 pipeline_modules）==========

def _check_portfolio_risk(results: dict, shibor_mult: float = 1.0) -> dict:
    """检查组合风险（委托实现）"""
    from lib.pipeline_modules.risk_checker import (
        check_portfolio_risk as _rc_check_portfolio_risk,
    )
    return _rc_check_portfolio_risk(results, shibor_mult=shibor_mult)


def _check_factor_health(results: dict) -> dict:
    """检查因子健康度（委托实现）"""
    from lib.pipeline_modules.risk_checker import (
        check_factor_health as _rc_check_factor_health,
    )
    return _rc_check_factor_health(results)


def _get_sector_map(codes: list) -> dict:
    sector_map = {}
    try:
        df = get_realtime_quotes()
        if not df.empty:
            # 向量化处理：直接用 pandas 的 groupby + to_dict
            code_col = None
            sector_col = None
            for col in df.columns:
                if "代码" in str(col):
                    code_col = col
                if "行业" in str(col):
                    sector_col = col
            if code_col and sector_col:
                # 只筛选 codes 中存在的股票，然后转 dict
                mask = df[code_col].astype(str).isin(codes)
                sector_map = df.loc[mask].set_index(code_col)[sector_col].astype(str).to_dict()
    except Exception:
        pass
    return sector_map


def _load_current_weights() -> dict:
    try:
        from lib.position_manager import PositionManager
        pm = PositionManager()
        positions = pm.get_all_positions()
        if not positions:
            return {}
        total_value = sum(p.get("market_value", 0) for p in positions.values())
        if total_value <= 0:
            return {}
        return {code: p.get("market_value", 0) / total_value
                for code, p in positions.items()}
    except Exception:
        return {}


def _optimize_portfolio(results: dict) -> dict:
    """组合优化 — 使用 PortfolioManager（分层架构）

    多策略结构：每策略独立 PositionSizer → RiskBudget → Turnover，
    再由 PortfolioManager 汇总权重 + 总风控检查。
    """
    from lib.portfolio_manager import PortfolioManager, StrategyConfig

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
        df_work["_volatility"] = pd.to_numeric(df_work.get(vol_col, 25), errors="coerce").fillna(25) / 100

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
        from lib.alpha import AlphaSynthesizer
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
    return {"factor_ic": factor_ic, "cross_section_zscore": zscore_summary,
            "optimized_weights": opt_weights}


# ========== 20d ML 评分 ==========

def _check_monitor_alerts(ml_ranking: dict = None):
    """因子健康度检查 + IC 衰减告警"""
    from config import MonitorConfig
    monitor_config = MonitorConfig()

    panel_path = Path(__file__).resolve().parent.parent / "cache" / "panel_2015.parquet"
    if panel_path.exists():
        try:
            from lib.factor_monitor import FactorMonitor
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
                        # 尝试推送飞书通知
                        try:
                            from notifier import send_feishu_message
                            send_feishu_message(
                                {"type": "ic_decay_alert", "alerts": decay_alerts},
                                preset="alert"
                            )
                        except Exception:
                            pass
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
        from lib.ml_predictor import MLPredictor
        _predictor = MLPredictor()
    except Exception as e:
        logger.warning(f"ML 模型加载失败: {e}")
        return {"data": None, "n_candidates": len(all_codes), "n_passed": 0,
                "method": "20d_ml", "error": str(e)}
    # ML 因子缓存：模块级 LRUCache（容量256，自动淘汰）
    candidates = []
    for code in all_codes:
        try:
            if code in _ml_factor_cache:
                result = _ml_factor_cache[code]
            else:
                hist = get_stock_hist_multi_source(code, days=180, source="auto", prefer_adjusted=True)
                if hist.empty or len(hist) < 60:
                    _ml_factor_cache[code] = None
                    continue
                result = compute_all_signals(hist)
                _ml_factor_cache[code] = result
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
    return {"data": result_df.head(10), "n_candidates": len(all_codes),
            "n_passed": len(result_df),
            "top10": result_df.head(10).to_dict(orient="records"),
            "method": "20d_ml", "recommended_config": "纯20dML·月频·tc=0.01"}


# ========== 单股评分（供 API 调用） ==========

def get_stock_score(code: str) -> dict:
    """单股完整评分：因子 + 技术 + 四维评分 + 约束 + 风控"""
    from lib.pipeline_core.steps import (
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
        score_result = score_without_realtime(factors)

    # 4. 约束检查
    from lib.constraints import DeterministicConstraints
    cr = DeterministicConstraints().check_ticker(code, hist, fin_data)

    # 5. ML 预测
    ml_prediction = {}
    try:
        from lib.ml_predictor import MLPredictor
        predictor = MLPredictor()
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
    sentiment = get_market_sentiment()
    logger.info(f"市场情绪分数: {sentiment:.2f}")

    # ========== 宏观 + 指数过滤 ==========
    macro_filter = {"shibor": None, "shibor_regime": "UNKNOWN", "index_ma20": None, "index_trend": "UNKNOWN", "position_mult": 1.0}
    index_signal = {"bull_market": True}
    try:
        shibor_df = get_macro_hist_local("SHIBOR", days=10)
        if not shibor_df.empty:
            shibor_val = float(shibor_df["close"].iloc[-1])
            macro_filter["shibor"] = shibor_val
            if shibor_val > 3.5:
                macro_filter["shibor_regime"] = "TIGHT"
                macro_filter["position_mult"] = 0.0
            elif shibor_val > 2.5:
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

    strategies = {
        "涨停板复盘": get_zt_candidates,
        "量价突破": get_volume_breakout_candidates,
        "热门板块": get_hot_board_candidates,
        "龙虎榜": get_lhb_candidates,
        "多因子选股": get_multi_factor_candidates,
    }
    for name, func in strategies.items():
        logger.info(f"运行策略: {name}")
        try:
            df = func(config)
            if not df.empty:
                # 如果指定了symbols，过滤结果
                if symbols:
                    df = df[df["代码"].isin(symbols)] if "代码" in df.columns else df
                if not df.empty:
                    results[name] = df
                    logger.info(f"  -> {len(df)} 只候选股")
                else:
                    logger.info(f"  -> 无候选股（过滤后）")
            else:
                logger.info(f"  -> 无候选股")
        except Exception as e:
            logger.error(f"  -> 策略执行失败: {e}")
    risk_check = _check_portfolio_risk(results, shibor_mult=macro_filter["position_mult"])
    factor_health = _check_factor_health(results)
    portfolio = _optimize_portfolio(results)

    alpha_analysis = {}
    if include_alpha:
        alpha_analysis = _run_alpha_analysis(results)
    else:
        alpha_analysis = {}

    ml_ranking = {}
    if include_ml:
        ml_ranking = _enrich_with_ml_scores(results)
        # 因子监控预警
        _check_monitor_alerts(ml_ranking)
    else:
        ml_ranking = {}

    scan_result = {
        "results": results, "risk_check": risk_check,
        "factor_health": factor_health, "portfolio": portfolio,
        "alpha_analysis": alpha_analysis, "ml_ranking": ml_ranking,
        "preset": preset,
        "macro_filter": macro_filter,
        "index_signal": index_signal,
    }
    try:
        from lib.audit_logger import log_scan
        log_scan(scan_result)
    except Exception as e:
        logger.debug(f"审计日志写入失败: {e}")
    return scan_result
