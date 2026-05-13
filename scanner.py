"""选股策略模块 —— 整合 quant-system + ai-hedge-fund + cryptoquant-ai（A股适用版）"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from config import SystemConfig, get_config
from factors import (compute_all_signals, calc_volatility_adjusted_limit,
                     detect_market_regime, evaluate_macro_gate)
from lib.constraints import DeterministicConstraints
from lib.position_size import RiskSizedPosition, PositionSizeConfig
from lib.technical_signals import calc_composite_verdict
import logging
import threading
import time

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"

# 约束引擎实例（模块级复用）
_constraints = DeterministicConstraints()
_position_sizer = RiskSizedPosition(PositionSizeConfig())


# ========== API 限流（来自 quant-system） ==========

class _RateLimiter:
    """线程安全的 API 调用频率限制器"""
    def __init__(self, max_calls_per_second: float = 3.0):
        self._min_interval = 1.0 / max_calls_per_second
        self._last_call_time = 0.0
        self._lock = threading.Lock()

    def acquire(self):
        with self._lock:
            now = time.time()
            wait = self._last_call_time + self._min_interval - now
            self._last_call_time = max(now, self._last_call_time + self._min_interval)
        if wait > 0:
            time.sleep(wait)


_rate_limiter = _RateLimiter(max_calls_per_second=3.0)


def _retry_api_call(func, *args, max_retries: int = 3, **kwargs):
    """带重试和限流的 API 调用"""
    last_error = None
    for attempt in range(max_retries):
        try:
            _rate_limiter.acquire()
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 2.0 * (attempt + 1)
                logger.debug(f"API 调用失败 (尝试 {attempt + 1}/{max_retries})，{wait}s 后重试: {e}")
                time.sleep(wait)
    raise last_error
CACHE_DIR.mkdir(exist_ok=True)


# ========== 数据缓存 ==========

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.parquet"


def _load_cache(key: str, max_age_hours: int = 2) -> pd.DataFrame:
    path = _cache_path(key)
    if not path.exists():
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        return pd.DataFrame()


def _save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


# ========== 数据获取 ==========

def get_stock_hist(code: str, days: int = 180) -> pd.DataFrame:
    cache_key = f"hist_{code}_{days}"
    cached = _load_cache(cache_key, max_age_hours=4)
    if not cached.empty:
        return cached
    try:
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = _retry_api_call(ak.stock_zh_a_hist, symbol=code, period="daily",
                             start_date=start, end_date=end, adjust="qfq")
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover", "涨跌幅": "pct_chg"
        })
        _save_cache(cache_key, df)
        return df
    except Exception as e:
        logger.error(f"获取 {code} 数据失败: {e}")
        return pd.DataFrame()


def get_stock_hist_multi_source(code: str, days: int = 180,
                                source: str = "akshare") -> pd.DataFrame:
    """多数据源获取 K 线数据

    Args:
        code: 股票代码
        source: "akshare" / "westock" / "tushare"
    """
    if source == "akshare":
        return get_stock_hist(code, days)

    if source == "adata":
        from lib.data_adata import get_kline as adata_kline
        df = adata_kline(code, days=days)
        if df.empty:
            return pd.DataFrame()
        return df

    if source == "westock":
        from lib.data_westock import get_kline as westock_kline
        df = westock_kline(code, period="day", limit=min(days, 2000))
        if df.empty:
            return pd.DataFrame()
        # 统一列名
        col_map = {"日期": "date", "开盘": "open", "收盘": "close",
                   "最高": "high", "最低": "low", "成交量": "volume"}
        df = df.rename(columns=col_map)
        for c in ["open", "close", "high", "low", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df

    if source == "tushare":
        from lib.data_tushare import get_daily, _to_ts_code
        ts_code = _to_ts_code(code)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        df = get_daily(ts_code, start, end)
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns={
            "trade_date": "date", "open": "open", "close": "close",
            "high": "high", "low": "low", "vol": "volume"
        })
        return df

    return get_stock_hist(code, days)


def get_realtime_quotes() -> pd.DataFrame:
    cached = _load_cache("realtime", max_age_hours=0.5)
    if not cached.empty:
        return cached
    try:
        df = _retry_api_call(ak.stock_zh_a_spot)
        if not df.empty:
            _save_cache("realtime", df)
        return df
    except Exception as e:
        logger.error(f"获取实时行情失败: {e}")
        return pd.DataFrame()


def get_market_sentiment() -> float:
    """获取市场情绪分数（-1 到 1）

    基于涨跌比计算：
    - 涨多跌少 → 正分
    - 跌多涨少 → 负分
    """
    try:
        df = get_realtime_quotes()
        if df.empty:
            return 0

        up_count = (df["涨跌幅"] > 0).sum()
        down_count = (df["涨跌幅"] < 0).sum()
        total = up_count + down_count

        if total == 0:
            return 0

        # 涨跌比转换为 -1 到 1 的分数
        ratio = (up_count - down_count) / total
        return max(-1, min(1, ratio))
    except Exception:
        return 0


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
        result["置信度"] = result.apply(lambda r: min(0.3 + r["连板数"] * 0.15, 0.9), axis=1).round(2)
        result["策略"] = "涨停板复盘"
        return result
    except Exception as e:
        logger.error(f"涨停板策略出错: {e}")
        return pd.DataFrame()


def get_volume_breakout_candidates(config: SystemConfig) -> pd.DataFrame:
    """量价突破策略（6大信号 + 市场体制 + 宏观门控）"""
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

        # 获取市场情绪
        sentiment = get_market_sentiment()

        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = get_stock_hist(code, days=180)
            if hist.empty or len(hist) < 60:
                continue

            # 约束过滤（HARD 规则直接跳过，SOFT 规则调整置信度）
            cr = _constraints.check_ticker(code, market_data=hist, stock_name=row["名称"])
            if cr.blocked:
                continue

            result = compute_all_signals(hist, macro_score=sentiment)
            if not result:
                continue

            combined = result["combined"]
            factors = result["factors"]
            regime = result["regime"]

            if combined["signal"] != "bullish" or combined.get("macro_blocked"):
                continue

            # 置信度叠加约束评分调整
            confidence = max(0, combined["confidence"] + cr.score_adjustment)

            vol = factors.get("volatility_20", 25) / 100
            position_limit = calc_volatility_adjusted_limit(vol)

            # 技术综合研判
            tech = calc_composite_verdict(hist)
            tech_summary = tech.get("summary", {})

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
            代表股=("名称", lambda x: "、".join(x.head(3)))
        ).reset_index()
        board_stats = board_stats.sort_values("涨停数", ascending=False)
        result = board_stats.head(5).copy()
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
        result["信号"] = "bullish"
        result["置信度"] = 0.6
        result["策略"] = "龙虎榜"
        return result
    except Exception as e:
        logger.error(f"龙虎榜策略出错: {e}")
        return pd.DataFrame()


def get_multi_factor_candidates(config: SystemConfig) -> pd.DataFrame:
    """多因子选股（6大信号 + 市场体制 + 宏观门控）"""
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

        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = get_stock_hist(code, days=180)
            if hist.empty or len(hist) < 60:
                continue

            # 约束过滤（HARD 规则直接跳过，SOFT 规则调整置信度）
            cr = _constraints.check_ticker(code, market_data=hist, stock_name=row["名称"])
            if cr.blocked:
                continue

            result = compute_all_signals(hist, macro_score=sentiment)
            if not result:
                continue

            combined = result["combined"]
            signals = result["signals"]
            factors = result["factors"]
            regime = result["regime"]

            # 置信度叠加约束评分调整
            confidence = max(0, combined["confidence"] + cr.score_adjustment)

            if combined["signal"] != "bullish" or confidence < 0.3 or combined.get("macro_blocked"):
                continue

            bullish_count = sum(1 for s in signals.values() if s.get("signal") == "bullish")
            vol = factors.get("volatility_20", 25) / 100
            position_limit = calc_volatility_adjusted_limit(vol)

            # 技术综合研判
            tech = calc_composite_verdict(hist)
            tech_summary = tech.get("summary", {})

            candidates.append({
                "代码": code, "名称": row["名称"],
                "最新价": row["最新价"], "涨跌幅": row["涨跌幅"],
                "信号": combined["signal"],
                "置信度": round(confidence, 3),
                "市场体制": regime["regime"],
                "看多策略": f"{bullish_count}/6",
                "技术研判": tech_summary.get("verdict", "N/A"),
                "看多信号": f"{tech_summary.get('bullish_count', 0)}/{tech_summary.get('total_indicators', 0)}",
                "RSI": round(factors.get("rsi_14", 50), 1),
                "动量5": round(factors.get("momentum_5", 0), 1),
                "仓位上限": f"{position_limit:.0%}",
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


def run_all_strategies(preset: str = "balanced") -> dict:
    config = get_config(preset)
    results = {}

    # 先输出市场概况
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

    return results
