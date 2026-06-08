"""四层打分卡：贝叶斯过滤系统

L1: 大盘方向  (market_trend)
L2: 行业强度  (sector_rps)
L3: 资金认可  (money_flow)
L4: 微观形态  (pattern_score)

每层输出 0/0.5/1，加权求和得总信心分（0~1）。
总分 > 0.5 才执行交易。
"""
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional
import logging
import threading

from config import LAYER_WEIGHTS
logger = logging.getLogger(__name__)


class MarketFilter:
    """大盘+行业环境过滤"""

    @staticmethod
    def layer1_market_trend(days: int = 60, as_of_date: Optional[str] = None) -> float:
        """L1: 大盘方向 — 沪深300 > 60日均线？

        含负面滤网：指数 > 均线但量能萎缩时降分。

        Args:
            days: 回溯窗口
            as_of_date: 时间截断 (YYYY-MM-DD)。回测必传, 实时可省略。
                P0-11 修复 (2026-06-08): 不传 = 走 T 日实时, 回测若不传会前视偏差

        Returns:
            1.0 = 上涨趋势（指数 > 均线 + 量能配合）
            0.5 = 震荡（接近均线 ±2%）或虚涨（量价背离）
            0.0 = 下跌趋势（指数 < 均线）
        """
        try:
            return MarketFilter._compute_l1(days, as_of_date=as_of_date)
        except Exception as e:
            logger.warning(f"L1 大盘判断失败: {e}")
            return 0.5

    @staticmethod
    def _compute_l1(days: int = 60, as_of_date: Optional[str] = None) -> float:
        """L1 核心计算逻辑（供 score 和 get_market_brief 调用）

        P0-11 修复 (2026-06-08, lib-incremental-audit §2.1):
        - 旧实现 ak.stock_zh_index_daily 拉**今日**实时数据
        - 回测环境若不传 as_of_date，会用 T 日行情回判历史日 → 前视偏差
        - 修复: as_of_date 截断 idx['date'] <= as_of_date
        - akshare 不支持历史快照接口, 只能在 DataFrame 层做截断
        """
        import akshare as ak
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if idx.empty:
            return 0.5
        # P0-11 修复: as_of_date 截断
        if as_of_date is not None and "date" in idx.columns:
            idx = idx[pd.to_datetime(idx["date"]) <= pd.to_datetime(as_of_date)]
            if len(idx) < days:
                # 截断后数据不足以回溯, 保守返回 0.5 (震荡)
                logger.debug(f"L1 as_of_date={as_of_date} 截断后仅 {len(idx)} 行, 不足 {days}")
                return 0.5
        elif len(idx) < days:
            return 0.5
        close = idx["close"].values
        volume = idx.get("volume", idx.get("amount"))
        current = close[-1]
        ma = np.mean(close[-days:])
        ratio = current / ma - 1
        if ratio > 0.02:
            if volume is not None and len(volume) >= 10:
                vol_5 = np.mean(volume[-5:])
                vol_10 = np.mean(volume[-10:])
                if vol_10 > 0 and vol_5 < vol_10 * 0.9:
                    return 0.5
            return 1.0
        elif ratio < -0.02:
            return 0.0
        return 0.5

    @classmethod
    def get_market_brief(cls, as_of_date: Optional[str] = None) -> dict:
        """预获取市场大盘信息(L1)，供批量评分使用

        一次获取市场数据和基准收益，避免 N+1 调用问题。
        P0-11 修复: 透传 as_of_date
        """
        l1 = cls._compute_l1(as_of_date=as_of_date)
        bench_ret = _get_market_return(5, as_of_date=as_of_date)  # 预获取 5 日市场收益
        return {"l1": l1, "bench_ret_5d": bench_ret}

    @staticmethod
    def layer2_sector_strength(code: str, days: int = 20) -> float:
        """L2: 行业强度 — 个股所属行业过去 N 日涨幅在全市场中的分位

        Returns:
            1.0 = 行业涨幅前 20%
            0.5 = 前 20%-50%
            0.0 = 后 50%
        """
        try:
            industry = _get_industry(code)
            if not industry:
                return 0.5

            # 获取该行业所有股票的收益率均值作为行业指数
            sector_stocks = _get_industry_stocks(industry)
            if not sector_stocks:
                return 0.5

            # 批量获取行业股票收益率（一次 API 调用替代 30 次）
            returns_dict = _get_stock_returns_batch(sector_stocks[:30], days)
            valid_returns = [r for r in returns_dict.values() if r is not None]
            if not valid_returns:
                return 0.5
            sector_ret = float(np.mean(valid_returns))

            # 计算全市场平均收益率作为基准
            market_ret = _get_market_return(days)
            if market_ret is None:
                return 0.5

            # 行业相对强度
            relative = sector_ret - market_ret
            if relative > 0.03:
                return 1.0
            elif relative > 0:
                return 0.5
            return 0.0
        except Exception as e:
            logger.debug(f"L2 行业判断失败: {e}")
            return 0.5

    @staticmethod
    def layer3_money_flow(row: pd.Series) -> float:
        """L3: 资金认可 — 量价配合度

        Args:
            row: 包含 volume_ratio, amount, close 等字段的行情行

        Returns:
            1.0 = 放量上涨（量比 > 1.2 且 涨跌幅 > 0）
            0.5 = 正常（量比 0.8~1.2 或 涨跌幅 0 附近）
            0.0 = 缩量/放量下跌
        """
        vol_ratio = float(row.get("volume_ratio", 1.0))
        change = float(row.get("涨跌幅", row.get("pct_chg", 0)))

        if vol_ratio > 1.2 and change > 0:
            return 1.0
        elif vol_ratio < 0.8 or change < -2:
            return 0.0
        return 0.5

    @staticmethod
    def layer4_pattern_score(hist: pd.DataFrame, row: pd.Series = None) -> float:
        """L4: 微观形态 — 凯尔特纳触轨 + 反转K线

        Returns:
            1.0 = 触下轨 + 看涨吞没/长下影
            0.5 = 仅触轨或仅反转
            0.0 = 不符合
        """
        try:
            if hist is None or len(hist) < 30:
                return 0.5
            close = hist["close"].values
            high = hist["high"].values if "high" in hist.columns else close
            low = hist["low"].values if "low" in hist.columns else close
            # 开盘价：优先用实时行情今开，回退 hist.open，最后回退 close
            if row is not None and "今开" in row:
                o = np.array([float(row["今开"])] * len(close))  # 仅当日有实时价
            elif "open" in hist.columns:
                o = hist["open"].values
            else:
                o = close

            # 简化凯尔特纳通道（用ATR代替）
            tr = np.maximum(high[1:] - low[1:],
                          np.maximum(np.abs(high[1:] - close[:-1]),
                                     np.abs(low[1:] - close[:-1])))
            atr = np.mean(tr[-20:]) if len(tr) >= 20 else np.mean(tr)
            atr_5 = np.mean(tr[-5:]) if len(tr) >= 5 else atr

            # 负面滤网：波动率突然扩张（ATR 短期/长期比 > 1.5）
            vol_spike = False
            if atr > 0 and atr_5 > atr * 1.5:
                vol_spike = True
                logger.debug("L4 波动率异常扩张，降低信号")
            ma20 = np.mean(close[-20:])
            kc_lower = ma20 - 2 * atr

            # 是否触下轨
            touch_lower = close[-1] <= kc_lower * 1.01

            # 是否出现看涨反转K线
            bullish_reversal = False
            if len(close) >= 3:
                # 长下影线（下影线 > 实体 2倍）
                body = abs(close[-1] - o[-1]) if "open" in hist.columns else 0
                lower_shadow = min(close[-1], o[-1]) - low[-1] if "open" in hist.columns else 0
                if lower_shadow > body * 2 and close[-1] > close[-2]:
                    bullish_reversal = True
                # 看涨吞没（需历史开盘价数据，回退时无法判断）
                elif "open" in hist.columns and close[-1] > o[-2] and \
                     close[-2] < o[-2] and close[-1] > o[-2] and o[-1] < close[-2]:
                    bullish_reversal = True

            if touch_lower and bullish_reversal:
                score = 1.0
            elif touch_lower or bullish_reversal:
                score = 0.5
            else:
                score = 0.0

            # 波动率扩张时降一级
            if vol_spike and score > 0:
                score = max(0, score - 0.5)
            return score
        except Exception as e:
            logger.debug(f"L4 形态判断失败: {e}")
            return 0.5


class ScoreCard:
    """四层打分卡"""

    WEIGHTS = [LAYER_WEIGHTS["L1"], LAYER_WEIGHTS["L2"],
               LAYER_WEIGHTS["L3"], LAYER_WEIGHTS["L4"]]  # L1/L2/L3/L4

    @classmethod
    def score(cls, code: str, row: pd.Series, hist: pd.DataFrame = None,
              market_brief: dict = None, industry_ret: float = None,
              sector_data: dict = None, as_of_date: Optional[str] = None) -> dict:
        """对一只股票执行四层打分

        Args:
            code: 股票代码
            row: 当日行情 Series
            hist: 历史行情 DataFrame
            market_brief: 预获取的市场信息（包含 l1 和 bench_ret_5d）
            industry_ret: 预获取的行业收益率（5日）
            sector_data: 预获取的行业数据（避免 N+1 调用）
            as_of_date: P0-11 修复 (2026-06-08) — 回测必传, 实时可省略
        """
        # L1 使用传入的预计算值或重新计算
        if market_brief:
            l1 = market_brief.get("l1", MarketFilter._compute_l1(as_of_date=as_of_date))
            bench_ret_5d = market_brief.get("bench_ret_5d")
        else:
            l1 = MarketFilter.layer1_market_trend(as_of_date=as_of_date)
            bench_ret_5d = _get_market_return(5, as_of_date=as_of_date)

        # L2 使用预获取的行业数据或独立计算
        if sector_data and code in sector_data:
            l2 = sector_data[code].get("l2", 0.5)
        else:
            l2 = MarketFilter.layer2_sector_strength(code)

        # L3 使用预获取的 bench_ret_5d
        l3 = MarketFilter.layer3_money_flow(row)

        # L4 不变
        l4 = MarketFilter.layer4_pattern_score(hist, row)

        # L2 负面滤网：行业内部质量——个股 vs 行业平均
        # 个股涨幅低于行业均值说明是跟风补涨而非领涨，L2 降分
        if hist is not None and len(hist) >= 6:
            stock_ret_5d = (hist["close"].iloc[-1] / hist["close"].iloc[-6] - 1)
            _ind_ret = industry_ret if industry_ret is not None else _get_industry_return(code, 5)
            if _ind_ret is not None and _ind_ret != 0 and l2 > 0:
                # 个股跑输行业均值 → 补涨跟风，降级
                if stock_ret_5d < _ind_ret * 0.5:
                    l2 = max(0, l2 - 0.5)

        # L3 负面滤网：相对强度（RS = 个股收益 - 基准收益）
        # 个股跑输大盘说明是跟风而非领涨，降低资金认可分
        if hist is not None and len(hist) >= 6:
            stock_ret_5d = (hist["close"].iloc[-1] / hist["close"].iloc[-6] - 1)
            if bench_ret_5d is not None:
                rs_5d = stock_ret_5d - bench_ret_5d
                if rs_5d < 0:
                    # 跑输大盘：L3 降 0.5 级
                    l3 = max(0, l3 - 0.5)

        layers = {"L1_大盘": l1, "L2_行业": l2, "L3_资金": l3, "L4_形态": l4}
        total = sum(l * w for l, w in zip([l1, l2, l3, l4], cls.WEIGHTS))

        if total >= 0.6:
            verdict = "强烈"
        elif total >= 0.4:
            verdict = "一般"
        else:
            verdict = "回避"

        return {"score": round(total, 2), "layers": layers, "verdict": verdict}


# ─── 辅助函数 ───

_industry_cache = None
_industry_cache_time = None
_industry_cache_lock = threading.Lock()
INDUSTRY_CACHE_TTL = 3600 * 24  # 24小时 TTL

def refresh_industry_cache():
    """强制刷新行业缓存（供外部调用）"""
    global _industry_cache, _industry_cache_time
    with _industry_cache_lock:
        _industry_cache = None
        _industry_cache_time = None
    logger.info("行业缓存已清除，下次查询将重新加载")


def _get_industry(code: str) -> str:
    """获取股票所属行业（带 TTL 缓存）"""
    global _industry_cache, _industry_cache_time
    import time
    now = time.time()
    with _industry_cache_lock:
        if _industry_cache is None or (_industry_cache_time is not None and now - _industry_cache_time > INDUSTRY_CACHE_TTL):
            import baostock as bs
            lg = bs.login()
            rs = bs.query_stock_industry()
            data = []
            while (rs.error_code == "0") and rs.next():
                data.append(rs.get_row_data())
            bs.logout()
            _industry_cache = {row[1]: row[3] for row in data}  # code -> industry
            _industry_cache_time = now
            logger.debug(f"行业缓存已刷新，条目数: {len(_industry_cache)}")
    # 转换代码格式
    if code.startswith(("sh", "sz", "bj")):
        pass
    else:
        # 用 helper 替代手写市场判断（修复 bj 静默丢失）
        from lib.market_utils import detect_market
        market = detect_market(code)
        if market in ("sh", "sz", "bj"):
            code = f"{market}.{code}"
        else:
            code = f"sz.{code}"  # 默认深圳
    with _industry_cache_lock:
        return _industry_cache.get(code, "")


def _get_industry_stocks(industry: str) -> list:
    """获取某行业下所有股票代码"""
    global _industry_cache
    if _industry_cache is None:
        _get_industry("")
    return [k for k, v in _industry_cache.items() if v == industry]


def _get_stock_returns_batch(codes: list, days: int = 20) -> dict:
    """批量获取多只股票的收益率（并行获取，减少延迟）

    Returns:
        {code: return_float or None}
    """
    from lib.data_layer import get_stock_hist_multi_source
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}

    def _fetch_one(code: str):
        try:
            clean_code = code.split('.')[-1] if '.' in code else code
            hist = get_stock_hist_multi_source(clean_code, days=days + 10, source="auto")
            if hist.empty or len(hist) < days:
                return code, None
            close = hist["close"].values
            return code, close[-1] / close[-days] - 1
        except Exception as e:
            logger.debug(f"[scorecard] 股票收益率获取失败 [{code}]: {e}")
            return code, None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_fetch_one, code) for code in codes]
        for future in as_completed(futures):
            code, ret = future.result()
            results[code] = ret

    return results


def _get_stock_return(code: str, days: int = 20) -> float:
    """获取股票过去 N 日收益率（单只，保留兼容）"""
    batch_result = _get_stock_returns_batch([code], days)
    return batch_result.get(code)


def _get_market_return(days: int = 20, as_of_date: Optional[str] = None) -> float:
    """获取沪深300过去 N 日收益率

    P0-11 修复 (2026-06-08): as_of_date 截断防止回测前视偏差
    """
    try:
        import akshare as ak
        idx = ak.stock_zh_index_daily(symbol="sh000300")
        if idx.empty:
            return None
        # P0-11 修复: as_of_date 截断
        if as_of_date is not None and "date" in idx.columns:
            idx = idx[pd.to_datetime(idx["date"]) <= pd.to_datetime(as_of_date)]
        if len(idx) < days:
            return None
        close = idx["close"].values
        return close[-1] / close[-days] - 1
    except Exception as e:
        logger.debug(f"[scorecard] 市场收益率获取失败: {e}")
        return None


def _get_industry_return(code: str, days: int = 5) -> float:
    """获取个股所属行业的平均收益率（用于判断个股是否领涨）

    采样行业前 10 只股票的收益率均值作为行业代理。
    比拉取行业指数 ETF 更轻量，复用已有数据源。
    """
    try:
        industry = _get_industry(code)
        if not industry:
            return None
        stocks = _get_industry_stocks(industry)
        if not stocks:
            return None
        # 批量获取，一次 API 调用替代 10 次
        returns_dict = _get_stock_returns_batch(stocks[:10], days)
        returns = [r for r in returns_dict.values() if r is not None]
        if not returns:
            return None
        return float(np.mean(returns))
    except Exception as e:
        logger.debug(f"[scorecard] 行业收益率获取失败 [{code}]: {e}")
        return None
