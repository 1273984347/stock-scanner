"""策略层：四维评分 + 5 大选股策略实现"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional
import logging

from config import SystemConfig, get_config
from factors import (compute_all_signals, calc_volatility_adjusted_limit,
                     evaluate_macro_gate)
from lib.constraints import DeterministicConstraints
from lib.position_size import RiskSizedPosition, PositionSizeConfig
from lib.technical_signals import calc_composite_verdict
from lib.scorer import (score_fundamental, score_capital_flow,
                         score_news, score_technical, comprehensive_score)
from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
from lib.data_layer import (get_stock_hist_multi_source, get_realtime_quotes,
                             get_stock_fund_flow, get_news_signals,
                             get_market_sentiment, _get_financial_data,
                             _retry_api_call, _safe_float, _load_cache)

logger = logging.getLogger(__name__)

# 模块级复用
_constraints = DeterministicConstraints()
_position_sizer = RiskSizedPosition(PositionSizeConfig())
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())


# ========== 四维评分 ==========

def quick_score(row: pd.Series) -> dict:
    """快速四维评分 —— 仅用实时行情数据（无需拉 K 线）"""
    pe = _safe_float(row.get("市盈率-动态"))
    pb = _safe_float(row.get("市净率"))
    fund = score_fundamental(pe=pe if pe > 0 else None, roe=None,
                             net_growth=None, gross_margin=None,
                             debt_ratio=None,
                             ocf_healthy=(10 < pe < 40) if pe > 0 else False)
    vol_ratio = _safe_float(row.get("量比"))
    turnover = _safe_float(row.get("换手率")) / 100.0
    amount = _safe_float(row.get("成交额"))
    code = str(row.get("代码", ""))
    fund_flow = get_stock_fund_flow(code)
    cap = score_capital_flow(
        main_net_ratio=fund_flow["main_net_ratio"],
        net_inflow_5d=fund_flow["net_inflow_5d"],
        northbound_increase=(vol_ratio > 1.5),
        super_large_net_inflow=(amount > 5e8),
        turnover_rate=turnover,
    )
    news_sig = get_news_signals(code)
    news = score_news(
        major_positive=news_sig["major_positive"],
        institutional_buy=news_sig["institutional_buy"],
        no_negative=news_sig["no_negative"],
        earnings_positive=news_sig["earnings_positive"],
    )
    pct_chg = _safe_float(row.get("涨跌幅"))
    ret_60d = _safe_float(row.get("60日涨跌幅"))
    tech = score_technical(
        return_10d=pct_chg / 100 if abs(pct_chg) < 50 else 0,
        return_20d=ret_60d / 100 if abs(ret_60d) < 200 else 0,
    )
    return comprehensive_score(fund, cap, news, tech)


def full_score(code: str, hist: pd.DataFrame, factors: dict,
               row: pd.Series) -> dict:
    """完整四维评分 —— 用 K 线 + 因子 + 实时数据"""
    pe = _safe_float(row.get("市盈率-动态"))
    fund = score_fundamental(
        pe=pe if pe > 0 else None, roe=None, net_growth=None,
        gross_margin=None, debt_ratio=None,
        ocf_healthy=(10 < pe < 40) if pe > 0 else False,
    )
    vol_ratio = _safe_float(row.get("量比"))
    turnover = _safe_float(row.get("换手率")) / 100.0
    amount = _safe_float(row.get("成交额"))
    factor_vol_ratio = factors.get("volume_ratio", 1.0)
    fund_flow = get_stock_fund_flow(code)
    cap = score_capital_flow(
        main_net_ratio=fund_flow["main_net_ratio"],
        net_inflow_5d=fund_flow["net_inflow_5d"],
        northbound_increase=(factor_vol_ratio > 1.3),
        super_large_net_inflow=(amount > 5e8),
        turnover_rate=turnover,
    )
    news_sig = get_news_signals(code)
    news = score_news(
        major_positive=news_sig["major_positive"],
        institutional_buy=news_sig["institutional_buy"],
        no_negative=news_sig["no_negative"],
        earnings_positive=news_sig["earnings_positive"],
    )
    mom_5 = factors.get("momentum_5", 0) / 100
    mom_20 = factors.get("momentum_20", 0) / 100
    tech = score_technical(return_10d=mom_5, return_20d=mom_20)
    score_result = comprehensive_score(fund, cap, news, tech)
    judge_result = {}
    try:
        from lib.judge_panel import JudgePanel
        panel = JudgePanel(
            factors={"pe_ttm": pe, **factors},
            fundamentals={
                "profitability": {"ROE": None, "gross_margin": None},
                "solvency": {"debt_to_asset": None},
                "per_share": {"BVPS": None},
                "cash_flow": {"free_cash_flow": None},
            },
            market_data=hist,
        )
        judge_result = panel.evaluate_all()
    except Exception:
        pass
    if judge_result and judge_result.get("consensus") in ("BUY", "SELL"):
        judge_bias = 1.0 if judge_result["consensus"] == "BUY" else -1.0
        judge_strength = abs(judge_result.get("score", 0))
        adjusted = score_result["score"] * 0.8 + (5 + judge_bias * judge_strength * 5) * 0.2
        score_result["judge_adjusted"] = True
        score_result["judge_consensus"] = judge_result["consensus"]
        score_result["judge_score"] = round(judge_result["score"], 3)
        adjusted_score = round(min(10, max(0, adjusted)), 1)
        if adjusted_score >= 8:
            score_result["rating"] = "强烈买入"
        elif adjusted_score >= 6:
            score_result["rating"] = "买入"
        elif adjusted_score >= 4:
            score_result["rating"] = "持有"
        elif adjusted_score >= 2:
            score_result["rating"] = "谨慎"
        else:
            score_result["rating"] = "回避"
        score_result["score"] = adjusted_score
    return score_result


# ========== 策略实现 ==========

def get_zt_candidates(config: SystemConfig) -> pd.DataFrame:
    """涨停板复盘策略"""
    try:
        df = _retry_api_call(ak.stock_zt_pool_em, date=datetime.now().strftime("%Y%m%d"))
        if df.empty:
            return pd.DataFrame()
        df = df[df["连板数"] >= config.scan.min_zt_days]
        df = df.sort_values(["连板数", "封板资金"], ascending=[False, False])
        result = df[["代码", "名称", "最新价", "涨跌幅", "连板数",
                      "封板资金", "首次封板时间"]].head(config.scan.max_candidates)
        result = result.copy()
        result["信号"] = "bullish"
        result["置信度"] = result.apply(
            lambda r: min(0.3 + r["连板数"] * 0.15, 0.9), axis=1).round(2)
        result["策略"] = "涨停板复盘"
        realtime = get_realtime_quotes()
        if not realtime.empty:
            row_map = {}
            for _, r in realtime.iterrows():
                row_map[str(r.get("代码", ""))] = r
            scores = []
            for _, cand in result.iterrows():
                rt_row = row_map.get(str(cand["代码"]))
                if rt_row is not None:
                    sc = quick_score(rt_row)
                    scores.append(sc["score"])
                else:
                    scores.append(None)
            result["综合评分"] = scores
        return result
    except Exception as e:
        logger.error(f"涨停板策略出错: {e}")
        return pd.DataFrame()


def get_volume_breakout_candidates(config: SystemConfig) -> pd.DataFrame:
    """量价突破策略"""
    try:
        df = get_realtime_quotes()
        if df.empty:
            return pd.DataFrame()
        if config.scan.exclude_st:
            df = df[~df["名称"].str.contains("ST", na=False)]
        df = df[df["成交量"] > 0]
        df = df[(df["涨跌幅"] >= config.scan.price_change_min) &
                (df["涨跌幅"] <= config.scan.price_change_max)]
        df = df.sort_values("成交额", ascending=False).head(30)
        sentiment = get_market_sentiment()
        row_map = {}
        for _, r in df.iterrows():
            row_map[str(r["代码"])] = r
        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = get_stock_hist_multi_source(code, days=180, source="auto")
            if hist.empty or len(hist) < 60:
                continue
            fin_data = _get_financial_data(code)
            cr = _constraints.check_ticker(code, market_data=hist, financial_data=fin_data, stock_name=row["名称"])
            if cr.blocked:
                continue
            from lib.scorecard import ScoreCard
            card = ScoreCard.score(code, row, hist)
            if card['verdict'] == '回避':
                continue
            row['打分卡'] = card['score']
            row['打分卡_结论'] = card['verdict']
            result = compute_all_signals(hist, macro_score=sentiment, factor_config=config.factor)
            if not result:
                continue
            combined = result["combined"]
            factors = result["factors"]
            regime = result["regime"]
            if combined["signal"] != "bullish" or combined.get("macro_blocked"):
                continue
            confidence = max(0, combined["confidence"] + cr.score_adjustment)
            vol = factors.get("volatility_20", 25) / 100
            position_limit = calc_volatility_adjusted_limit(vol)
            tech = calc_composite_verdict(hist)
            tech_summary = tech.get("summary", {})
            rt_row = row_map.get(code)
            if rt_row is not None:
                score_result = full_score(code, hist, factors, rt_row)
                comp_score = score_result["score"]
                comp_rating = score_result["rating"]
            else:
                comp_score = None
                comp_rating = "N/A"
            candidates.append({
                "代码": code, "名称": row["名称"],
                "最新价": row["最新价"], "涨跌幅": row["涨跌幅"],
                "成交额(亿)": round(row["成交额"] / 1e8, 2),
                "信号": combined["signal"],
                "置信度": round(confidence, 3),
                "市场体制": regime["regime"],
                "技术研判": tech_summary.get("verdict", "N/A"),
                "看多信号": f"{tech_summary.get('bullish_count', 0)}/{tech_summary.get('total_indicators', 0)}",
                "RSI": round(factors.get("rsi_14", 50), 1),
                "动量5": round(factors.get("momentum_5", 0), 1),
                "仓位上限": f"{position_limit:.0%}",
                "综合评分": comp_score,
                "评级": comp_rating,
                "波动率20": round(factors.get("volatility_20", 25), 1),
            })
            if len(candidates) >= config.scan.max_candidates:
                break
        if not candidates:
            return pd.DataFrame()
        result = pd.DataFrame(candidates)
        result = result.sort_values("置信度", ascending=False)
        result["策略"] = "量价突破"
        return result
    except Exception as e:
        logger.error(f"量价突破策略出错: {e}")
        return pd.DataFrame()


def get_hot_board_candidates(config: SystemConfig) -> pd.DataFrame:
    """热门板块策略"""
    try:
        df = _retry_api_call(ak.stock_zt_pool_em, date=datetime.now().strftime("%Y%m%d"))
        if df.empty:
            return pd.DataFrame()
        board_stats = df.groupby("所属行业").agg(
            涨停数=("代码", "count"),
            最高连板=("连板数", "max"),
            代表股=("名称", lambda x: "、".join(x.head(3))),
            平均封板资金=("封板资金", "mean"),
        ).reset_index()
        board_stats = board_stats.sort_values("涨停数", ascending=False)
        board_stats["板块热度"] = (
            board_stats["涨停数"] * 0.4 +
            board_stats["最高连板"] * 0.3 +
            (board_stats["平均封板资金"] / board_stats["平均封板资金"].max()).fillna(0) * 0.3 * 10
        ).round(1)
        result = board_stats.head(8).copy()
        result["策略"] = "热门板块"
        result = result.rename(columns={"所属行业": "板块"})
        return result
    except Exception as e:
        logger.error(f"热门板块策略出错: {e}")
        return pd.DataFrame()


def get_lhb_candidates(config: SystemConfig) -> pd.DataFrame:
    """龙虎榜策略"""
    try:
        df = _retry_api_call(ak.stock_lhb_detail_em)
        if df.empty:
            return pd.DataFrame()
        if "方向" in df.columns:
            df = df[df["方向"] == "买入"]
        if "买入金额" in df.columns:
            df = df.sort_values("买入金额", ascending=False)
        if "代码" in df.columns:
            df = df.drop_duplicates(subset=["代码"], keep="first")
        cols = [c for c in ["代码", "名称", "买入金额", "净额", "上榜原因"] if c in df.columns]
        result = df[cols].head(config.scan.max_candidates).copy()
        if "买入金额" in result.columns:
            max_buy = result["买入金额"].max()
            if max_buy and max_buy > 0:
                result["置信度"] = (0.5 + 0.4 * result["买入金额"] / max_buy).round(2)
            else:
                result["置信度"] = 0.6
        else:
            result["置信度"] = 0.6
        result["信号"] = "bullish"
        realtime = get_realtime_quotes()
        if not realtime.empty:
            row_map = {}
            for _, r in realtime.iterrows():
                row_map[str(r.get("代码", ""))] = r
            scores = []
            for _, cand in result.iterrows():
                rt_row = row_map.get(str(cand.get("代码", "")))
                if rt_row is not None:
                    sc = quick_score(rt_row)
                    scores.append(sc["score"])
                else:
                    scores.append(None)
            result["综合评分"] = scores
        result["策略"] = "龙虎榜"
        return result
    except Exception as e:
        logger.error(f"龙虎榜策略出错: {e}")
        return pd.DataFrame()


def get_multi_factor_candidates(config: SystemConfig) -> pd.DataFrame:
    """多因子选股"""
    try:
        df = get_realtime_quotes()
        if df.empty:
            return pd.DataFrame()
        if config.scan.exclude_st:
            df = df[~df["名称"].str.contains("ST", na=False)]
        df = df[df["成交量"] > 0]
        df = df[df["涨跌幅"] > 0]
        df = df.sort_values("成交额", ascending=False).head(50)
        sentiment = get_market_sentiment()
        row_map = {}
        for _, r in df.iterrows():
            row_map[str(r["代码"])] = r
        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = get_stock_hist_multi_source(code, days=180, source="auto")
            if hist.empty or len(hist) < 60:
                continue
            fin_data = _get_financial_data(code)
            cr = _constraints.check_ticker(code, market_data=hist, financial_data=fin_data, stock_name=row["名称"])
            if cr.blocked:
                continue
            result = compute_all_signals(hist, macro_score=sentiment, factor_config=config.factor)
            if not result:
                continue
            combined = result["combined"]
            signals = result["signals"]
            factors = result["factors"]
            confidence = max(0, combined["confidence"] + cr.score_adjustment)
            if combined["signal"] != "bullish" or confidence < 0.3 or combined.get("macro_blocked"):
                continue
            bullish_count = sum(1 for s in signals.values() if s.get("signal") == "bullish")
            vol = factors.get("volatility_20", 25) / 100
            position_limit = calc_volatility_adjusted_limit(vol)
            tech = calc_composite_verdict(hist)
            tech_summary = tech.get("summary", {})
            rt_row = row_map.get(code)
            if rt_row is not None:
                score_result = full_score(code, hist, factors, rt_row)
                comp_score = score_result["score"]
                comp_rating = score_result["rating"]
                score_breakdown = score_result["breakdown"]
            else:
                comp_score = None
                comp_rating = "N/A"
                score_breakdown = {}
            candidates.append({
                "代码": code, "名称": row["名称"],
                "最新价": row["最新价"], "涨跌幅": row["涨跌幅"],
                "信号": combined["signal"],
                "置信度": round(confidence, 3),
                "市场体制": result["regime"]["regime"],
                "看多策略": f"{bullish_count}/6",
                "技术研判": tech_summary.get("verdict", "N/A"),
                "看多信号": f"{tech_summary.get('bullish_count', 0)}/{tech_summary.get('total_indicators', 0)}",
                "RSI": round(factors.get("rsi_14", 50), 1),
                "动量5": round(factors.get("momentum_5", 0), 1),
                "仓位上限": f"{position_limit:.0%}",
                "综合评分": comp_score,
                "评级": comp_rating,
                "评分明细": score_breakdown,
                "波动率20": round(factors.get("volatility_20", 25), 1),
            })
        if not candidates:
            return pd.DataFrame()
        result = pd.DataFrame(candidates)
        result = result.sort_values(["看多策略", "置信度"], ascending=[False, False])
        result = result.head(config.scan.max_candidates)
        result["策略"] = "多因子选股"
        return result
    except Exception as e:
        logger.error(f"多因子选股策略出错: {e}")
        return pd.DataFrame()
