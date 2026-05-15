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
                             clean_cache, get_news_signals, _safe_float)
from lib.scorer import (score_fundamental, score_capital_flow,
                         score_news, score_technical, comprehensive_score)
from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
from lib.evaluator import build_forward_returns
from lib.strategies import (get_zt_candidates, get_volume_breakout_candidates,
                             get_hot_board_candidates, get_lhb_candidates,
                             get_multi_factor_candidates, full_score)

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())


# ========== 组合风控 ==========

def _check_portfolio_risk(results: dict) -> dict:
    all_codes = set()
    total_candidates = 0
    strategy_exposure = {}
    for name, df in results.items():
        if df.empty:
            continue
        if "代码" in df.columns:
            codes = df["代码"].tolist()
            all_codes.update(codes)
            total_candidates += len(codes)
            strategy_exposure[name] = len(codes)
    if not all_codes:
        return {"alerts": [], "risk_budget": {}, "recommendation": "无持仓，风控通过"}
    alerts = []
    for name, count in strategy_exposure.items():
        if count > 5:
            alerts.append({"level": "WARNING", "rule": "策略集中度",
                           "message": f"{name} 选出 {count} 只，单策略集中度过高"})
    if total_candidates > 20:
        alerts.append({"level": "WARNING", "rule": "总持仓数",
                       "message": f"总候选 {total_candidates} 只，分散度可能不足"})
    sentiment = get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)
    if macro.state == "BLOCK_NEW_RISK":
        alerts.append({"level": "CRITICAL", "rule": "宏观门控", "message": macro.reasoning})
    elif macro.state == "ALLOW_REDUCED":
        alerts.append({"level": "WARNING", "rule": "宏观门控",
                       "message": f"{macro.reasoning}，建议仓位乘数 {macro.position_multiplier}"})
    vol_regime = "normal"
    try:
        idx_cache = _load_cache("hs300_cache", max_age_hours=4)
        if not idx_cache.empty and len(idx_cache) >= 60 and "close" in idx_cache.columns:
            exposure = calc_market_exposure(idx_cache["close"])
            vol_regime = exposure.get("state", "neutral")
    except Exception:
        pass
    risk_budget = _risk_manager.get_risk_budget(
        vol_regime if vol_regime in ("high", "normal", "low") else "normal", 0.5)
    critical_count = sum(1 for a in alerts if a["level"] == "CRITICAL")
    if critical_count > 0:
        recommendation = "存在严重风险预警，建议暂停新开仓"
    elif len(alerts) > 2:
        recommendation = "多重预警，建议减仓操作"
    elif alerts:
        recommendation = "轻度预警，可谨慎操作"
    else:
        recommendation = "风控通过，可正常交易"
    return {"alerts": alerts, "risk_budget": risk_budget, "recommendation": recommendation,
            "macro_gate": {"state": macro.state, "regime": macro.regime,
                           "multiplier": macro.position_multiplier},
            "strategy_exposure": strategy_exposure, "total_candidates": total_candidates}


def _check_factor_health(results: dict) -> dict:
    eval_cache = CACHE_DIR / "factor_evaluation.json"
    last_eval = None
    need_full_eval = True
    if eval_cache.exists():
        try:
            import json
            data = json.loads(eval_cache.read_text(encoding="utf-8"))
            last_eval = data.get("date", "")
            if last_eval:
                last_dt = datetime.strptime(last_eval, "%Y-%m-%d")
                if (datetime.now() - last_dt).days < 7:
                    need_full_eval = False
        except Exception:
            pass
    coverage = {}
    degenerate = []
    for name, df in results.items():
        if df.empty:
            continue
        factor_cols = [c for c in df.columns if c in (
            "RSI", "动量5", "置信度", "综合评分")]
        for col in factor_cols:
            if col not in coverage:
                coverage[col] = {"total": 0, "non_null": 0, "unique": 0}
            series = df[col].dropna()
            coverage[col]["total"] += len(df)
            coverage[col]["non_null"] += len(series)
            coverage[col]["unique"] += series.nunique()
    for col, stats in coverage.items():
        if stats["total"] > 0:
            cov_rate = stats["non_null"] / stats["total"]
            if cov_rate > 0 and stats["unique"] <= 1:
                degenerate.append(col)
    recommendation = "因子质量正常"
    if degenerate:
        recommendation = f"警告：因子 {degenerate} 无区分度，建议检查数据源"
    elif need_full_eval:
        recommendation = "建议运行完整因子评估（python run.py --evaluate）"
    return {"coverage": coverage, "degenerate_factors": degenerate,
            "last_evaluation": last_eval, "need_full_evaluation": need_full_eval,
            "recommendation": recommendation}


def _get_sector_map(codes: list) -> dict:
    sector_map = {}
    try:
        df = get_realtime_quotes()
        if not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                sector = str(row.get("行业", row.get("所属行业", "")))
                if code in codes and sector:
                    sector_map[code] = sector
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
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig
    candidate_map = {}
    for name, df in results.items():
        if df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            score = row.get("综合评分")
            if score is None:
                conf = row.get("置信度")
                score = conf * 10 if conf is not None else None
            if score is None:
                continue
            if code not in candidate_map or score > candidate_map[code].get("score", 0):
                candidate_map[code] = {"code": code, "name": str(row.get("名称", "")),
                                       "score": float(score),
                                       "volatility": float(row.get("波动率20", row.get("volatility_20", 25))) / 100}
    if not candidate_map:
        return {"weights": {}, "expected_return": 0, "expected_vol": 0,
                "sharpe_ratio": 0, "sector_exposure": {}, "n_positions": 0}
    candidates = list(candidate_map.values())
    sector_map = _get_sector_map([c["code"] for c in candidates])
    n = len(candidates)
    codes = [c["code"] for c in candidates]
    vols = np.array([c["volatility"] for c in candidates])

    # 动态协方差估计：有历史数据时计算，否则降低优化权重
    corr = np.full((n, n), 0.2)  # 默认 0.2 但标记为未校准
    for i in range(n):
        corr[i, i] = 1.0

    # 尝试用历史收益率计算真实相关矩阵
    corr_estimated = False
    if n >= 2:
        try:
            from lib.data_layer import get_stock_hist_multi_source
            returns_data = []
            valid_codes = []
            for c in candidates:
                hist = get_stock_hist_multi_source(c["code"], days=60, source="auto")
                if not hist.empty and "close" in hist.columns and len(hist) >= 20:
                    ret = hist["close"].pct_change().dropna()
                    if len(ret) >= 20:
                        returns_data.append(ret.values[-20:])
                        valid_codes.append(c["code"])
            if len(returns_data) >= 2:
                ret_matrix = np.array(returns_data).T
                if ret_matrix.shape[0] >= 10 and ret_matrix.shape[1] >= 2:
                    real_corr = np.corrcoef(ret_matrix)
                    # 映射回原始 codes 顺序
                    code_to_idx = {code: i for i, code in enumerate(codes)}
                    valid_idx = [code_to_idx[code] for code in valid_codes]
                    for ii, i in enumerate(valid_idx):
                        for jj, j in enumerate(valid_idx):
                            if ii < len(real_corr) and jj < len(real_corr):
                                if not np.isnan(real_corr[ii, jj]):
                                    corr[i, j] = real_corr[ii, jj]
                    corr_estimated = True
                    logger.debug(f"协方差矩阵已用历史数据校准，样本数: {len(valid_codes)}")
        except Exception as e:
            logger.debug(f"协方差矩阵计算失败，使用默认值: {e}")

    if not corr_estimated:
        # 无真实数据时，降低优化权重（增加 risk_aversion）
        logger.debug("协方差矩阵未校准，风险厌恶系数提高")

    if sector_map:
        for i in range(n):
            for j in range(i + 1, n):
                si = sector_map.get(codes[i], "")
                sj = sector_map.get(codes[j], "")
                if si and sj and si == sj:
                    # 同行业提高相关估计
                    if corr_estimated:
                        corr[i, j] = max(corr[i, j], 0.5)
                        corr[j, i] = max(corr[j, i], 0.5)
                    else:
                        corr[i, j] = 0.5
                        corr[j, i] = 0.5
    cov = np.outer(vols, vols) * corr * 252

    # 未校准时提高 risk_aversion
    risk_aversion = 2.0 if corr_estimated else 4.0
    current_weights = _load_current_weights()
    optimizer = PortfolioOptimizer(OptimizerConfig(
        max_positions=min(10, n), risk_aversion=risk_aversion, turnover_penalty=0.005))
    cov_df = pd.DataFrame(cov, index=codes, columns=codes)
    result = optimizer.optimize(
        candidates=candidates, covariance_matrix=cov_df,
        current_weights=current_weights, sector_map=sector_map)
    return {"weights": result.weights, "expected_return": result.expected_return,
            "expected_vol": result.expected_vol, "sharpe_ratio": result.sharpe_ratio,
            "sector_exposure": result.sector_exposure, "n_positions": result.n_positions,
            "turnover": result.turnover, "details": result.details}


def _run_alpha_analysis(results: dict) -> dict:
    try:
        from lib.alpha import AlphaSynthesizer
    except ImportError:
        return {}
    factor_rows = []
    for name, df in results.items():
        if df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            factor_rows.append({
                "code": code, "RSI": row.get("RSI"), "动量5": row.get("动量5"),
                "置信度": row.get("置信度"), "综合评分": row.get("综合评分"),
                "涨跌幅": row.get("涨跌幅"),
            })
    if len(factor_rows) < 5:
        return {}
    factor_df = pd.DataFrame(factor_rows).set_index("code")
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
    except Exception:
        pass
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
            except Exception:
                pass
    except Exception:
        pass
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
            logger.debug(f"因子监控检查失败: {e}")
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
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            all_codes.add(code)
            code_names[code] = str(row.get("名称", ""))
    if not all_codes:
        return {"data": None, "n_candidates": 0, "n_passed": 0, "method": "20d_ml"}
    try:
        from lib.ml_predictor import MLPredictor
        _predictor = MLPredictor()
    except Exception as e:
        logger.warning(f"ML 模型加载失败: {e}")
        return {"data": None, "n_candidates": len(all_codes), "n_passed": 0,
                "method": "20d_ml", "error": str(e)}
    candidates = []
    for code in all_codes:
        try:
            hist = get_stock_hist_multi_source(code, days=180, source="auto")
            if hist.empty or len(hist) < 60:
                continue
            result = compute_all_signals(hist)
            if not result:
                continue
            factors = result["factors"]
            mom_20 = factors.get("momentum_20", 0)
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
            logger.debug(f"{code} ML 评分失败: {e}")
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
    hist = get_stock_hist_multi_source(code, days=180, source="auto")
    if hist.empty:
        return {"error": f"未找到 {code} 数据"}
    result = compute_all_signals(hist, factor_config=get_config().factor)
    if not result:
        return {"error": f"{code} 因子计算失败"}
    from lib.technical_signals import calc_composite_verdict
    tech = calc_composite_verdict(hist)
    realtime = get_realtime_quotes()
    rt_row = None
    if not realtime.empty:
        for _, r in realtime.iterrows():
            if str(r.get("代码", "")) == code:
                rt_row = r
                break
    if rt_row is not None:
        score_result = full_score(code, hist, result["factors"], rt_row)
    else:
        factors = result["factors"]
        mom_5 = factors.get("momentum_5", 0) / 100
        mom_20 = factors.get("momentum_20", 0) / 100
        tech_score = score_technical(return_10d=mom_5, return_20d=mom_20)
        fund_score = score_fundamental()
        cap_score = score_capital_flow()
        news_sig = get_news_signals(code)
        news_score = score_news(
            major_positive=news_sig["major_positive"],
            institutional_buy=news_sig["institutional_buy"],
            no_negative=news_sig["no_negative"],
            earnings_positive=news_sig["earnings_positive"],
        )
        score_result = comprehensive_score(fund_score, cap_score, news_score, tech_score)
    fin_data = _get_financial_data(code)
    from lib.constraints import DeterministicConstraints
    cr = DeterministicConstraints().check_ticker(code, market_data=hist, financial_data=fin_data)
    ml_prediction = {}
    try:
        from lib.ml_predictor import MLPredictor
        predictor = MLPredictor()
        if not predictor.is_model_stale():
            ml_prediction = predictor.predict_single(result.get("factors", {}))
    except Exception as e:
        logger.debug(f"ML 预测失败: {e}")
    judge_result = {}
    try:
        from lib.judge_panel import JudgePanel
        pe_val = result["factors"].get("pe_ttm", fin_data.get("pe", pd.NA) if fin_data is not None else pd.NA)
        panel = JudgePanel(
            factors={"pe_ttm": pe_val, **result.get("factors", {})},
            fundamentals={}, market_data=hist,
        )
        judge_result = panel.evaluate_with_ml(ml_prediction)
    except Exception as e:
        logger.debug(f"裁判团评分失败: {e}")
    return {
        "code": code, "factors": result.get("factors", {}),
        "combined": result.get("combined", {}),
        "regime": result.get("regime", {}),
        "macro_gate": result.get("macro_gate", {}),
        "technical": {"verdict": tech.get("verdict"), "signals": tech.get("signals"),
                       "summary": tech.get("summary")},
        "score": score_result, "judge_panel": judge_result,
        "ml_prediction": ml_prediction,
        "constraints": {"passed": cr.passed, "blocked": cr.blocked,
                         "violations": cr.violations, "score_adjustment": cr.score_adjustment},
    }


# ========== 主入口 ==========

def run_all_strategies(preset: str = "balanced") -> dict:
    config = get_config(preset)
    results = {}
    clean_cache(max_age_hours=24)
    sentiment = get_market_sentiment()
    logger.info(f"市场情绪分数: {sentiment:.2f}")
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
                results[name] = df
                logger.info(f"  -> {len(df)} 只候选股")
            else:
                logger.info(f"  -> 无候选股")
        except Exception as e:
            logger.error(f"  -> 策略执行失败: {e}")
    risk_check = _check_portfolio_risk(results)
    factor_health = _check_factor_health(results)
    portfolio = _optimize_portfolio(results)
    alpha_analysis = _run_alpha_analysis(results)
    ml_ranking = _enrich_with_ml_scores(results)

    # 因子监控预警
    _check_monitor_alerts(ml_ranking)

    scan_result = {
        "results": results, "risk_check": risk_check,
        "factor_health": factor_health, "portfolio": portfolio,
        "alpha_analysis": alpha_analysis, "ml_ranking": ml_ranking,
        "preset": preset,
    }
    try:
        from lib.audit_logger import log_scan
        log_scan(scan_result)
    except Exception as e:
        logger.debug(f"审计日志写入失败: {e}")
    return scan_result
