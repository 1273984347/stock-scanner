"""策略层：四维评分 + 5 大选股策略实现"""
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
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
                             _retry_api_call, _safe_float, _load_cache,
                             _batch_fund_flow, _batch_get_news_signals)

logger = logging.getLogger(__name__)

# 模块级复用
_constraints = DeterministicConstraints()
_position_sizer = RiskSizedPosition(PositionSizeConfig())
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())


def _batch_get_financial_data(codes: List[str]) -> Dict[str, Optional[pd.Series]]:
    """批量并行获取财务数据（解决 N+1 查询问题）"""
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_get_financial_data, code): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception:
                results[code] = None
    return results


def _batch_get_stock_hist(codes: List[str], days: int = 180) -> Dict[str, pd.DataFrame]:
    """批量并行获取股票历史数据（解决 N+1 查询问题）

    Args:
        codes: 股票代码列表
        days: 历史数据天数

    Returns:
        Dict[code -> DataFrame]，只包含成功获取且非空的股票
    """
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(get_stock_hist_multi_source, code, days, "auto"): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                df = future.result()
                if df is not None and not df.empty and len(df) >= 60:
                    results[code] = df
            except Exception:
                pass
    return results


# ========== 四维评分 ==========

def _compute_base_scores(row: pd.Series, code: str, tech_return_10d: float,
                         tech_return_20d: float,
                         fund_flow: Optional[dict] = None,
                         news_sig: Optional[dict] = None) -> dict:
    """提取 fund/cap/news 公共评分逻辑

    Args:
        row: 股票实时行情数据
        code: 股票代码
        tech_return_10d: 10日收益率（用于技术评分）
        tech_return_20d: 20日收益率（用于技术评分）
        fund_flow: 可选，预获取的资金流向数据（避免 N+1）
        news_sig: 可选，预获取的新闻信号数据（避免 N+1）
    """
    pe = _safe_float(row.get("市盈率-动态"))
    fund = score_fundamental(
        pe=pe if pe > 0 else None, roe=None,
        net_growth=None, gross_margin=None,
        debt_ratio=None,
        ocf_healthy=(10 < pe < 40) if pe > 0 else False,
    )
    vol_ratio = _safe_float(row.get("量比"))
    turnover = _safe_float(row.get("换手率")) / 100.0
    amount = _safe_float(row.get("成交额"))
    ff = fund_flow if fund_flow is not None else get_stock_fund_flow(code)
    cap = score_capital_flow(
        main_net_ratio=ff["main_net_ratio"],
        net_inflow_5d=ff["net_inflow_5d"],
        northbound_increase=(vol_ratio > 1.3),
        super_large_net_inflow=(amount > 5e8),
        turnover_rate=turnover,
    )
    ns = news_sig if news_sig is not None else get_news_signals(code)
    news = score_news(
        major_positive=ns["major_positive"],
        institutional_buy=ns["institutional_buy"],
        no_negative=ns["no_negative"],
        earnings_positive=ns["earnings_positive"],
    )
    tech = score_technical(
        return_10d=tech_return_10d,
        return_20d=tech_return_20d,
    )
    return comprehensive_score(fund, cap, news, tech)


def quick_score(row: pd.Series,
                fund_flow: Optional[dict] = None,
                news_sig: Optional[dict] = None) -> dict:
    """快速四维评分 —— 仅用实时行情数据（无需拉 K 线）

    Args:
        row: 股票实时行情
        fund_flow: 可选，预获取的资金流向数据
        news_sig: 可选，预获取的新闻信号数据
    """
    code = str(row.get("代码", ""))
    pct_chg = _safe_float(row.get("涨跌幅"))
    ret_60d = _safe_float(row.get("60日涨跌幅"))
    tech_return_10d = pct_chg / 100 if abs(pct_chg) < 50 else 0
    tech_return_20d = ret_60d / 100 if abs(ret_60d) < 200 else 0
    return _compute_base_scores(row, code, tech_return_10d, tech_return_20d,
                                fund_flow, news_sig)


def full_score(code: str, hist: pd.DataFrame, factors: dict,
               row: pd.Series) -> dict:
    """完整四维评分 —— 用 K 线 + 因子 + 实时数据"""
    mom_5 = factors.get("momentum_5", 0) / 100
    mom_20 = factors.get("momentum_20", 0) / 100
    score_result = _compute_base_scores(row, code, mom_5, mom_20)

    # Judge Panel 调整逻辑（仅 full_score 有）
    pe = _safe_float(row.get("市盈率-动态"))
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
    except Exception as e:
        logger.warning(f"JudgePanel 评估失败 [{code}]: {e}")
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
            row_map = realtime.set_index(realtime["代码"].astype(str)).to_dict("index")
            # 批量预获取，避免 N+1
            codes = [str(c) for c in result["代码"]]
            batch_flows = _batch_fund_flow(codes)
            batch_news = _batch_get_news_signals(codes)
            scores = []
            for _, cand in result.iterrows():
                code = str(cand["代码"])
                rt_row = row_map.get(code)
                if rt_row is not None:
                    sc = quick_score(rt_row,
                                   fund_flow=batch_flows.get(code),
                                   news_sig=batch_news.get(code))
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
        row_map = df.set_index(df["代码"].astype(str)).to_dict("index")
        # 批量并行获取历史数据（解决 N+1 查询问题）
        all_codes = df["代码"].astype(str).tolist()
        stock_hist_data = _batch_get_stock_hist(all_codes, days=180)
        batch_financial = _batch_get_financial_data(all_codes)
        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = stock_hist_data.get(code)
            if hist is None or len(hist) < 60:
                continue
            fin_data = batch_financial.get(code)
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
            row_map = realtime.set_index(realtime["代码"].astype(str)).to_dict("index")
            # 批量预获取，避免 N+1
            codes = [str(c) for c in result["代码"]]
            batch_flows = _batch_fund_flow(codes)
            batch_news = _batch_get_news_signals(codes)
            scores = []
            for _, cand in result.iterrows():
                code = str(cand.get("代码", ""))
                rt_row = row_map.get(code)
                if rt_row is not None:
                    sc = quick_score(rt_row,
                                   fund_flow=batch_flows.get(code),
                                   news_sig=batch_news.get(code))
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
        row_map = df.set_index(df["代码"].astype(str)).to_dict("index")
        # 批量并行获取历史数据（解决 N+1 查询问题）
        all_codes = df["代码"].astype(str).tolist()
        stock_hist_data = _batch_get_stock_hist(all_codes, days=180)
        batch_financial = _batch_get_financial_data(all_codes)
        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = stock_hist_data.get(code)
            if hist is None or len(hist) < 60:
                continue
            fin_data = batch_financial.get(code)
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


# ========== 第6策略：超短线通道策略（凯尔特纳+薛斯） ==========

def get_keltner_xosi_mt_candidates(config: SystemConfig) -> pd.DataFrame:
    """超短线通道策略 —— 基于凯尔特纳+薛斯通道

    参考：因子.txt + v4.txt

    入场规则（回踩型优先）：
    - 条件：已发生突破 → 回踩21EMA → 缩量 → 不破结构 → 再次转强
    - 突破型：Donchian突破 + 放量 + 市场TREND_UP + 主线板块

    止盈：
    - 第一目标：KC中轨（EMA21）
    - 第二目标：薛斯短期通道另一侧边界

    止损：
    - KC下轨下方 1×ATR

    证伪：
    - T+0 收盘：跌破入场关键价位 → 次日评估
    - T+1 动量：未量价齐升 → 减半
    - T+3 逻辑：未触及第一止盈 → 强制离场
    """
    try:
        from lib.indicators import compute_all_channels, get_regime_from_kc
        from lib.market_regime import detect_market_regime

        df = get_realtime_quotes()
        if df.empty:
            return pd.DataFrame()
        if config.scan.exclude_st:
            df = df[~df["名称"].str.contains("ST", na=False)]
        df = df[df["成交量"] > 0]
        df = df[df["涨跌幅"] > 0]
        df = df.sort_values("成交额", ascending=False).head(50)

        # 获取大盘指数数据（用于市场状态判断）
        index_df = None
        try:
            import akshare as ak
            idx_df = ak.stock_zh_index_spot_em()
            if not idx_df.empty:
                sh_df = idx_df[idx_df["名称"] == "上证指数"]
                if not sh_df.empty:
                    code = str(sh_df.iloc[0]["代码"])
                    index_hist = get_stock_hist_multi_source(code, 60)
                    if index_hist is not None and not index_hist.empty:
                        index_df = index_hist
        except Exception:
            pass

        # 大盘状态
        market_regime_info = {"regime": "MEAN_REVERSION", "confidence": 0.3}
        if index_df is not None and len(index_df) > 30:
            market_regime_info = detect_market_regime(index_df)

        regime = market_regime_info.get("regime", "MEAN_REVERSION")
        regime_confidence = market_regime_info.get("confidence", 0.3)

        # 只在 TREND_UP 或 MEAN_REVERSION 下做多
        if regime not in ("TREND_UP", "MEAN_REVERSION"):
            return pd.DataFrame()

        candidates = []
        row_map = df.set_index(df["代码"].astype(str)).to_dict("index")
        all_codes = df["代码"].astype(str).tolist()
        stock_hist_data = _batch_get_stock_hist(all_codes, days=60)

        for _, row in df.iterrows():
            code = row["代码"]
            hist = stock_hist_data.get(code)
            if hist is None or len(hist) < 30:
                continue
            if {"high", "low", "close", "open"}.difference(hist.columns):
                continue

            try:
                channels = compute_all_channels(hist)
            except Exception:
                continue

            signals = channels.get("signals")
            if signals is None or signals.empty:
                continue

            latest_signal = signals.iloc[-1]
            signal_val = latest_signal.get("signal", 0)
            if signal_val != 1:  # 只做多
                continue

            confidence = latest_signal.get("confidence", 0)
            reason = latest_signal.get("reason", "")

            if confidence < 0.6:
                continue

            # 计算入场价位和止损
            kc = channels.get("kc")
            if kc is None or kc.empty:
                continue

            entry_price = float(hist["close"].iloc[-1])
            stop_price = float(kc["kc_lower"].iloc[-1]) - float(kc["atr"].iloc[-1])
            first_target = float(kc["kc_mid"].iloc[-1])  # 第一止盈：KC中轨

            # 检查是否满足回踩型条件（相比前一交易日缩量）
            vol_today = float(hist["volume"].iloc[-1])
            vol_5d_avg = float(hist["volume"].iloc[-5:].mean())
            volume_cond = vol_today < vol_5d_avg  # 回踩型要求缩量

            # 计算回踩深度（价格从近期高点回落了多少）
            high_5d = float(hist["high"].iloc[-5:].max())
            pullback_pct = (high_5d - entry_price) / (high_5d + 1e-9)

            candidates.append({
                "代码": code,
                "名称": row["名称"],
                "最新价": entry_price,
                "涨跌幅": row["涨跌幅"],
                "信号": "bullish",
                "置信度": round(confidence, 3),
                "市场状态": regime,
                "入场价": round(entry_price, 2),
                "止损价": round(stop_price, 2),
                "第一止盈": round(first_target, 2),
                "回踩深度": f"{pullback_pct:.1%}",
                "缩量确认": "是" if volume_cond else "否",
                "通道信号": reason,
                "策略": "超短线通道",
            })

        if not candidates:
            return pd.DataFrame()

        result = pd.DataFrame(candidates)
        result = result.sort_values("置信度", ascending=False)
        result = result.head(config.scan.max_candidates)
        return result

    except Exception as e:
        logger.error(f"超短线通道策略出错: {e}")
        return pd.DataFrame()
