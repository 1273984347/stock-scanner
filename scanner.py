"""选股策略模块 —— 五大策略统一因子框架 + 四维评分 + 组合风控

核心改动（相比旧版）：
1. 所有策略都走因子系统（涨停板/龙虎榜也补充因子信号）
2. 集成 scorer.py 四维评分（技术/基本面/资金/消息）
3. 集成 risk_manager.py 组合级风控
4. 新增 get_stock_score() 供 API 单股评分
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
from config import SystemConfig, get_config
from factors import (compute_all_signals, calc_volatility_adjusted_limit,
                     detect_market_regime, evaluate_macro_gate)
from lib.constraints import DeterministicConstraints
from lib.position_size import RiskSizedPosition, PositionSizeConfig
from lib.technical_signals import calc_composite_verdict
from lib.scorer import (score_fundamental, score_capital_flow,
                         score_news, score_technical, comprehensive_score)
from lib.risk_manager import VolCorrRiskManager, VolCorrRiskConfig
import logging
import threading
import time

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# 约束引擎 + 仓位计算器 + 风控管理器（模块级复用）
_constraints = DeterministicConstraints()
_position_sizer = RiskSizedPosition(PositionSizeConfig())
_risk_manager = VolCorrRiskManager(VolCorrRiskConfig())


# ========== 基本面数据获取（供 constraints 使用） ==========

_financial_cache: Dict[str, tuple] = {}  # code -> (data, timestamp)

def _get_financial_data(code: str) -> Optional[pd.Series]:
    """获取个股基本面数据（PE/PB/市值/质押率等），供 constraints 使用

    带缓存，5分钟内不重复请求
    """
    import time
    now = time.time()
    if code in _financial_cache:
        data, ts = _financial_cache[code]
        if now - ts < 300:  # 5分钟缓存
            return data

    try:
        import akshare as ak
        # 个股基本信息（PE/PB/市值/行业等）
        info_df = _retry_api_call(ak.stock_individual_info_em, symbol=code)
        if info_df is None or info_df.empty:
            _financial_cache[code] = (None, now)
            return None

        info = {}
        for _, row in info_df.iterrows():
            info[row["item"]] = row["value"]

        data = pd.Series({
            "pe": _to_float(info.get("市盈率-动态")),
            "pb": _to_float(info.get("市净率")),
            "market_cap": _to_float(info.get("总市值")),
            "pledge_ratio": None,  # akshare 个股信息无此字段，后续补充
            "net_assets": None,    # 同上
            "revenue_growth": None,
            "prev_revenue_growth": None,
        })

        # 总市值需要换算单位（akshare 返回的是元，约束里用的也是元）
        if data["market_cap"] is not None and data["market_cap"] < 1e6:
            # 可能是万元或亿元，akshare stock_individual_info_em 总市值单位是元
            pass

        _financial_cache[code] = (data, now)
        return data
    except Exception as e:
        logger.debug(f"获取 {code} 基本面数据失败: {e}")
        _financial_cache[code] = (None, now)
        return None

def _to_float(v) -> Optional[float]:
    """安全转换字符串为浮点"""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("亿", "").replace("万", ""))
    except (ValueError, TypeError):
        return None


# ========== API 限流 ==========

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


# ========== 数据缓存 ==========

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.parquet"


def _load_cache(key: str, max_age_hours: int = 2) -> pd.DataFrame:
    path = _cache_path(key)
    if not path.exists():
        return pd.DataFrame()
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    if datetime.now() - mtime > timedelta(hours=max_age_hours):
        path.unlink(missing_ok=True)  # 过期即删，不再留死文件
        return pd.DataFrame()
    try:
        return pd.read_parquet(path)
    except Exception:
        path.unlink(missing_ok=True)  # 损坏即删
        return pd.DataFrame()


def _save_cache(key: str, df: pd.DataFrame):
    if not df.empty:
        df.to_parquet(_cache_path(key), index=False)


def clean_cache(max_age_hours: int = 24, max_total_mb: int = 500):
    """清理过期缓存文件 + 总大小限制（LRU 淘汰）

    Args:
        max_age_hours: 超过此小时数的文件删除
        max_total_mb: 缓存总大小上限（MB），超出后按最旧先删
    """
    if not CACHE_DIR.exists():
        return 0
    now = datetime.now()
    deleted = 0
    total_size = 0
    alive_files = []

    for path in CACHE_DIR.iterdir():
        if not path.is_file():
            continue
        if path.suffix not in (".parquet", ".json"):
            continue
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            size = path.stat().st_size
            age_hours = (now - mtime).total_seconds() / 3600

            if age_hours > max_age_hours:
                path.unlink(missing_ok=True)
                deleted += 1
            else:
                total_size += size
                alive_files.append((path, mtime, size))
        except Exception:
            continue

    # 总大小超限：按最旧先删
    max_bytes = max_total_mb * 1024 * 1024
    if total_size > max_bytes:
        alive_files.sort(key=lambda x: x[1])  # 最旧排前
        for path, _, size in alive_files:
            path.unlink(missing_ok=True)
            total_size -= size
            deleted += 1
            if total_size <= max_bytes:
                break

    if deleted > 0:
        logger.info(f"缓存清理: 删除 {deleted} 个文件")
    return deleted


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


# 数据源优先级
DATA_SOURCE_PRIORITY = ["adata", "westock", "tushare", "akshare"]


def _fetch_from_source(code: str, days: int, source: str) -> pd.DataFrame:
    if source == "adata":
        from lib.data_adata import get_kline as adata_kline
        return adata_kline(code, days=days)

    if source == "westock":
        from lib.data_westock import get_kline as westock_kline
        df = westock_kline(code, period="day", limit=min(days, 2000))
        if df.empty:
            return pd.DataFrame()
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

    if source == "akshare":
        return get_stock_hist(code, days)

    logger.warning(f"未知数据源: {source}")
    return pd.DataFrame()


def get_stock_hist_multi_source(code: str, days: int = 180,
                                source: str = "auto") -> pd.DataFrame:
    if source != "auto":
        return _fetch_from_source(code, days, source)

    errors = []
    for src in DATA_SOURCE_PRIORITY:
        df = _fetch_from_source(code, days, src)
        if not df.empty:
            if src != "akshare":
                logger.debug(f"使用数据源 [{src}] 获取 {code}")
            return df
        errors.append(f"{src}: empty")

    logger.warning(f"所有数据源均无法获取 {code}: {', '.join(errors)}")
    return pd.DataFrame()


def get_stock_fund_flow(code: str) -> dict:
    """获取个股资金流向数据（主力净流入、5日累计净流入）

    使用 akshare stock_individual_fund_flow 接口
    返回: {"main_net_ratio": float, "net_inflow_5d": float}
    """
    try:
        market = "sh" if code.startswith(("6", "5")) else "sz"
        df = _retry_api_call(ak.stock_individual_fund_flow, stock=code, market=market)
        if df.empty:
            return {"main_net_ratio": 0, "net_inflow_5d": 0}

        # 取最新一天的主力净流入占比
        latest = df.iloc[-1]
        main_net_ratio = 0.0
        for col in df.columns:
            if "主力净流入-净占比" in str(col):
                main_net_ratio = _safe_float(latest[col]) / 100.0  # 百分比转小数
                break

        # 取近5天主力净流入累计额
        net_inflow_5d = 0.0
        recent = df.tail(5)
        for col in df.columns:
            if "主力净流入-净额" in str(col):
                net_inflow_5d = recent[col].sum()
                break

        return {"main_net_ratio": main_net_ratio, "net_inflow_5d": net_inflow_5d}
    except Exception as e:
        logger.debug(f"获取 {code} 资金流向失败: {e}")
        return {"main_net_ratio": 0, "net_inflow_5d": 0}


def _batch_fund_flow(codes: list) -> dict:
    """批量获取资金流向（用即时资金流向接口一次拉全市场）

    Args:
        codes: 股票代码列表

    Returns:
        {code: {"main_net_ratio": float, "net_inflow_5d": float}}
    """
    result = {c: {"main_net_ratio": 0, "net_inflow_5d": 0} for c in codes}
    try:
        df = _retry_api_call(ak.stock_fund_flow_individual, symbol="即时")
        if df.empty:
            return result

        # 构建代码映射
        code_col = None
        for col in df.columns:
            if "代码" in str(col):
                code_col = col
                break
        if code_col is None:
            return result

        net_col = None
        amount_col = None
        for col in df.columns:
            if "净额" in str(col):
                net_col = col
            if "成交额" in str(col):
                amount_col = col

        flow_map = {}
        for _, row in df.iterrows():
            c = str(row.get(code_col, ""))
            net_val = _safe_float(row.get(net_col, 0))
            total_amount = _safe_float(row.get(amount_col, 1))
            flow_map[c] = {
                "main_net_ratio": net_val / total_amount if total_amount > 0 else 0,
                "net_inflow_5d": net_val,  # 即时数据没有5日累计，用当日净额近似
            }

        for code in codes:
            if code in flow_map:
                result[code] = flow_map[code]

    except Exception as e:
        logger.debug(f"批量资金流向获取失败: {e}")

    return result


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
    try:
        df = get_realtime_quotes()
        if df.empty:
            return 0
        up_count = (df["涨跌幅"] > 0).sum()
        down_count = (df["涨跌幅"] < 0).sum()
        total = up_count + down_count
        if total == 0:
            return 0
        ratio = (up_count - down_count) / total
        return max(-1, min(1, ratio))
    except Exception:
        return 0


# ========== 新闻/业绩信号 ==========

# 模块级缓存：同一次扫描内复用
_earnings_positive_codes: set = set()
_major_positive_codes: set = set()
_lhb_buy_codes: set = set()
_news_cache_loaded: bool = False


def _load_news_cache():
    """加载新闻相关信号缓存（业绩预告 + 龙虎榜）

    每次扫描只拉一次，避免重复 API 调用。
    """
    global _earnings_positive_codes, _major_positive_codes, _lhb_buy_codes, _news_cache_loaded
    if _news_cache_loaded:
        return

    try:
        from lib.data_earnings import get_earnings_forecast, get_earnings_flash
        # 业绩预告：预增/扭亏 → major_positive
        forecast = get_earnings_forecast()
        if not forecast.empty:
            code_col = None
            type_col = None
            for col in forecast.columns:
                if "代码" in str(col):
                    code_col = col
                if "预告类型" in str(col) or "类型" in str(col):
                    type_col = col
            if code_col and type_col:
                strong = forecast[forecast[type_col].str.contains("预增|略增|扭亏", na=False)]
                _major_positive_codes = set(strong[code_col].astype(str))

        # 业绩快报：净利增长 >30% → earnings_positive
        flash = get_earnings_flash()
        if not flash.empty:
            code_col = None
            growth_col = None
            for col in flash.columns:
                if "代码" in str(col):
                    code_col = col
                if "净利" in str(col) and "增长" in str(col):
                    growth_col = col
            if code_col and growth_col:
                flash[growth_col] = pd.to_numeric(flash[growth_col], errors="coerce")
                beat = flash[flash[growth_col] > 0.30]
                _earnings_positive_codes = set(beat[code_col].astype(str))
    except Exception as e:
        logger.debug(f"加载业绩数据失败: {e}")

    try:
        # 龙虎榜：机构买入 → institutional_buy
        df = _load_cache("lhb_codes", max_age_hours=4)
        if df.empty:
            df = _retry_api_call(ak.stock_lhb_detail_em)
            if not df.empty:
                _save_cache("lhb_codes", df)
        if not df.empty:
            code_col = None
            dir_col = None
            for col in df.columns:
                if "代码" in str(col):
                    code_col = col
                if "方向" in str(col):
                    dir_col = col
            if code_col:
                if dir_col:
                    buy = df[df[dir_col] == "买入"]
                else:
                    buy = df
                _lhb_buy_codes = set(buy[code_col].astype(str).unique())
    except Exception as e:
        logger.debug(f"加载龙虎榜数据失败: {e}")

    _news_cache_loaded = True


def get_news_signals(code: str) -> dict:
    """获取单股新闻信号

    Returns:
        {"major_positive": bool, "earnings_positive": bool,
         "institutional_buy": bool, "no_negative": bool}
    """
    _load_news_cache()

    return {
        "major_positive": code in _major_positive_codes,
        "earnings_positive": code in _earnings_positive_codes,
        "institutional_buy": code in _lhb_buy_codes,
        "no_negative": True,  # 无负面新闻源，默认True
    }


# ========== 四维评分（核心新增） ==========

def _safe_float(val, default=0.0):
    """安全提取数值"""
    if val is None:
        return default
    try:
        if pd.isna(val) or np.isnan(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def quick_score(row: pd.Series) -> dict:
    """快速四维评分 —— 仅用实时行情数据（无需拉 K 线）

    适用场景：涨停板/龙虎榜等不需要拉历史数据的策略
    数据来源：akshare stock_zh_a_spot 返回的列

    Returns:
        {"score": float, "rating": str, "breakdown": dict}
    """
    # 基本面：PE + PB
    pe = _safe_float(row.get("市盈率-动态"))
    pb = _safe_float(row.get("市净率"))
    fund = score_fundamental(pe=pe if pe > 0 else None, roe=None,
                             net_growth=None, gross_margin=None,
                             debt_ratio=None,
                             ocf_healthy=(10 < pe < 40) if pe > 0 else False)

    # 资金面：量比 + 换手率 + 真实主力净流入
    vol_ratio = _safe_float(row.get("量比"))
    turnover = _safe_float(row.get("换手率")) / 100.0  # 换手率是百分比
    amount = _safe_float(row.get("成交额"))
    code = str(row.get("代码", ""))

    # 获取真实资金流向
    fund_flow = get_stock_fund_flow(code)
    cap = score_capital_flow(
        main_net_ratio=fund_flow["main_net_ratio"],
        net_inflow_5d=fund_flow["net_inflow_5d"],
        northbound_increase=(vol_ratio > 1.5),  # 量比>1.5 视为类北向信号
        super_large_net_inflow=(amount > 5e8),  # 成交额>5亿
        turnover_rate=turnover,
    )

    # 消息面：用业绩预告 + 龙虎榜数据
    code = str(row.get("代码", ""))
    news_sig = get_news_signals(code)
    news = score_news(
        major_positive=news_sig["major_positive"],
        institutional_buy=news_sig["institutional_buy"],
        no_negative=news_sig["no_negative"],
        earnings_positive=news_sig["earnings_positive"],
    )

    # 技术面：涨跌幅 + 60日涨幅
    pct_chg = _safe_float(row.get("涨跌幅"))
    ret_60d = _safe_float(row.get("60日涨跌幅"))
    tech = score_technical(
        return_10d=pct_chg / 100 if abs(pct_chg) < 50 else 0,
        return_20d=ret_60d / 100 if abs(ret_60d) < 200 else 0,
    )

    return comprehensive_score(fund, cap, news, tech)


def full_score(code: str, hist: pd.DataFrame, factors: dict,
               row: pd.Series) -> dict:
    """完整四维评分 —— 用 K 线 + 因子 + 实时数据

    适用场景：量价突破/多因子等已拉历史数据的策略

    Returns:
        {"score": float, "rating": str, "breakdown": dict}
    """
    # 基本面：PE + PB（来自实时行情）
    pe = _safe_float(row.get("市盈率-动态"))
    pb = _safe_float(row.get("市净率"))

    # 从因子提取 ROE 等信息（如果有的话）
    vol_20 = factors.get("volatility_20", 25)
    fund = score_fundamental(
        pe=pe if pe > 0 else None,
        roe=None,
        net_growth=None,
        gross_margin=None,
        debt_ratio=None,
        ocf_healthy=(10 < pe < 40) if pe > 0 else False,
    )

    # 资金面：量比 + 换手率 + 真实主力净流入
    vol_ratio = _safe_float(row.get("量比"))
    turnover = _safe_float(row.get("换手率")) / 100.0
    amount = _safe_float(row.get("成交额"))
    factor_vol_ratio = factors.get("volume_ratio", 1.0)

    # 获取真实资金流向
    fund_flow = get_stock_fund_flow(code)
    cap = score_capital_flow(
        main_net_ratio=fund_flow["main_net_ratio"],
        net_inflow_5d=fund_flow["net_inflow_5d"],
        northbound_increase=(factor_vol_ratio > 1.3),
        super_large_net_inflow=(amount > 5e8),
        turnover_rate=turnover,
    )

    # 消息面：用业绩预告 + 龙虎榜数据
    news_sig = get_news_signals(code)
    news = score_news(
        major_positive=news_sig["major_positive"],
        institutional_buy=news_sig["institutional_buy"],
        no_negative=news_sig["no_negative"],
        earnings_positive=news_sig["earnings_positive"],
    )

    # 技术面：用因子数据
    mom_5 = factors.get("momentum_5", 0) / 100
    mom_20 = factors.get("momentum_20", 0) / 100
    tech = score_technical(return_10d=mom_5, return_20d=mom_20)

    score_result = comprehensive_score(fund, cap, news, tech)

    # 裁判团投票评分（做第二重验证）
    judge_result = {}
    try:
        from lib.judge_panel import JudgePanel
        panel = JudgePanel(
            factors=factors,
            fundamentals={"profitability": {"ROE": None}, "solvency": {"debt_to_asset": None}},
            market_data=hist,
        )
        judge_result = panel.evaluate_all()
    except Exception:
        pass

    # 融合裁判团评分：如果裁判团偏向强烈，加权调整
    if judge_result and judge_result.get("consensus") in ("BUY", "SELL"):
        judge_bias = 1.0 if judge_result["consensus"] == "BUY" else -1.0
        judge_strength = abs(judge_result.get("score", 0))
        # 裁判团权重 20%，原有评分权重 80%
        adjusted = score_result["score"] * 0.8 + (5 + judge_bias * judge_strength * 5) * 0.2
        score_result["judge_adjusted"] = True
        score_result["judge_consensus"] = judge_result["consensus"]
        score_result["judge_score"] = round(judge_result["score"], 3)
        # 重新计算评级
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
    """涨停板复盘策略（增强版：补因子信号 + 四维评分）"""
    try:
        df = _retry_api_call(ak.stock_zt_pool_em, date=datetime.now().strftime("%Y%m%d"))
        if df.empty:
            return pd.DataFrame()
        df = df[df["连板数"] >= config.scan.min_zt_days]
        df = df.sort_values(["连板数", "封板资金"], ascending=[False, False])
        result = df[["代码", "名称", "最新价", "涨跌幅", "连板数",
                      "封板资金", "首次封板时间"]].head(config.scan.max_candidates)
        result = result.copy()

        # 因子信号：连板数越多越强
        result["信号"] = "bullish"
        result["置信度"] = result.apply(
            lambda r: min(0.3 + r["连板数"] * 0.15, 0.9), axis=1).round(2)
        result["策略"] = "涨停板复盘"

        # 补充四维评分（用实时行情数据，不额外拉 K 线）
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
    """量价突破策略（增强版：6大信号 + 四维评分 + 约束 + 风控）"""
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

        # 构建实时行情行映射（用于评分）
        row_map = {}
        for _, r in df.iterrows():
            row_map[str(r["代码"])] = r

        candidates = []
        for _, row in df.iterrows():
            code = row["代码"]
            hist = get_stock_hist_multi_source(code, days=180, source="auto")
            if hist.empty or len(hist) < 60:
                continue

            # 约束过滤（含基本面检查）
            fin_data = _get_financial_data(code)
            cr = _constraints.check_ticker(code, market_data=hist, financial_data=fin_data, stock_name=row["名称"])
            if cr.blocked:
                continue

            result = compute_all_signals(hist, macro_score=sentiment, factor_config=config.factor)
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

            # 四维评分
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
    """热门板块策略（增强版：补板块因子信号）"""
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

        # 板块热度评分（涨停数 + 连板高度 + 封板资金）
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
    """龙虎榜策略（增强版：补因子信号 + 四维评分）"""
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

        # 基础置信度（买入金额越大越高）
        if "买入金额" in result.columns:
            max_buy = result["买入金额"].max()
            if max_buy and max_buy > 0:
                result["置信度"] = (0.5 + 0.4 * result["买入金额"] / max_buy).round(2)
            else:
                result["置信度"] = 0.6
        else:
            result["置信度"] = 0.6

        result["信号"] = "bullish"

        # 补充四维评分
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
    """多因子选股（增强版：6大信号 + 四维评分 + 约束 + 风控）"""
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
            regime = result["regime"]

            confidence = max(0, combined["confidence"] + cr.score_adjustment)

            if combined["signal"] != "bullish" or confidence < 0.3 or combined.get("macro_blocked"):
                continue

            bullish_count = sum(1 for s in signals.values() if s.get("signal") == "bullish")
            vol = factors.get("volatility_20", 25) / 100
            position_limit = calc_volatility_adjusted_limit(vol)

            tech = calc_composite_verdict(hist)
            tech_summary = tech.get("summary", {})

            # 四维评分
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
                "市场体制": regime["regime"],
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


# ========== 组合风控 ==========

def _check_portfolio_risk(results: dict) -> dict:
    """组合级风控检查

    Returns:
        {"alerts": [...], "risk_budget": {...}, "recommendation": str}
    """
    # 统计入选股票
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

    # 检查 1：单策略过度集中
    for name, count in strategy_exposure.items():
        if count > 5:
            alerts.append({
                "level": "WARNING",
                "rule": "策略集中度",
                "message": f"{name} 选出 {count} 只，单策略集中度过高",
            })

    # 检查 2：总候选数过多
    if total_candidates > 20:
        alerts.append({
            "level": "WARNING",
            "rule": "总持仓数",
            "message": f"总候选 {total_candidates} 只，分散度可能不足",
        })

    # 检查 3：市场情绪 + 宏观门控
    sentiment = get_market_sentiment()
    macro = evaluate_macro_gate(sentiment)
    if macro.state == "BLOCK_NEW_RISK":
        alerts.append({
            "level": "CRITICAL",
            "rule": "宏观门控",
            "message": macro.reasoning,
        })
    elif macro.state == "ALLOW_REDUCED":
        alerts.append({
            "level": "WARNING",
            "rule": "宏观门控",
            "message": f"{macro.reasoning}，建议仓位乘数 {macro.position_multiplier}",
        })

    # 推荐风险预算
    vol_regime = "normal"
    try:
        from factors import calc_market_exposure
        idx_cache = _load_cache("hs300_cache", max_age_hours=4)
        if not idx_cache.empty and len(idx_cache) >= 60 and "close" in idx_cache.columns:
            exposure = calc_market_exposure(idx_cache["close"])
            vol_regime = exposure.get("state", "neutral")
    except Exception:
        pass

    risk_budget = _risk_manager.get_risk_budget(
        vol_regime if vol_regime in ("high", "normal", "low") else "normal",
        0.5,  # 默认分散化分数
    )

    # 生成建议
    critical_count = sum(1 for a in alerts if a["level"] == "CRITICAL")
    if critical_count > 0:
        recommendation = "存在严重风险预警，建议暂停新开仓"
    elif len(alerts) > 2:
        recommendation = "多重预警，建议减仓操作"
    elif alerts:
        recommendation = "轻度预警，可谨慎操作"
    else:
        recommendation = "风控通过，可正常交易"

    return {
        "alerts": alerts,
        "risk_budget": risk_budget,
        "recommendation": recommendation,
        "macro_gate": {
            "state": macro.state,
            "regime": macro.regime,
            "multiplier": macro.position_multiplier,
        },
        "strategy_exposure": strategy_exposure,
        "total_candidates": total_candidates,
    }


# ========== 因子质量检查 ==========

def _check_factor_health(results: dict) -> dict:
    """轻量级因子质量检查

    不跑完整 evaluator（太慢），而是对本次扫描结果中的因子数据做快速统计：
    - 因子覆盖率：非空值占比
    - 因子分布：检查是否有因子全部为同一值（无区分度）
    - 持久化上次完整评估结果，判断是否需要重新评估

    Returns:
        {"coverage": {...}, "degenerate_factors": [...], "last_evaluation": str,
         "recommendation": str}
    """
    # 检查是否到了该做完整评估的时候（每周一次）
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

    # 对本次扫描结果的因子做快速覆盖率检查
    coverage = {}
    degenerate = []

    for name, df in results.items():
        if df.empty:
            continue
        # 检查有因子数据的列
        factor_cols = [c for c in df.columns if c in (
            "RSI", "动量5", "置信度", "综合评分")]
        for col in factor_cols:
            if col not in coverage:
                coverage[col] = {"total": 0, "non_null": 0, "unique": 0}
            series = df[col].dropna()
            coverage[col]["total"] += len(df)
            coverage[col]["non_null"] += len(series)
            coverage[col]["unique"] += series.nunique()

    # 标记退化因子（区分度不足）
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

    return {
        "coverage": coverage,
        "degenerate_factors": degenerate,
        "last_evaluation": last_eval,
        "need_full_evaluation": need_full_eval,
        "recommendation": recommendation,
    }


# ========== 组合优化 ==========

def _optimize_portfolio(results: dict) -> dict:
    """从扫描结果构建优化组合

    不再是简单排名选股，而是：
    1. 汇总所有策略候选股
    2. 用评分作为预期收益代理
    3. 用协方差矩阵建模风险
    4. 均值方差优化 + 行业约束 + 换手惩罚
    """
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig

    # 汇总候选股（去重，取最高分）
    candidate_map = {}  # code -> {code, name, score, volatility, sector}
    for name, df in results.items():
        if df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            score = row.get("综合评分")
            if score is None:
                # 用置信度作为备选
                conf = row.get("置信度")
                score = conf * 10 if conf is not None else None
            if score is None:
                continue
            # 取最高分
            if code not in candidate_map or score > candidate_map[code].get("score", 0):
                candidate_map[code] = {
                    "code": code,
                    "name": str(row.get("名称", "")),
                    "score": float(score),
                    "volatility": float(row.get("波动率20", row.get("volatility_20", 25))) / 100,
                }

    if not candidate_map:
        return {"weights": {}, "expected_return": 0, "expected_vol": 0,
                "sharpe_ratio": 0, "sector_exposure": {}, "n_positions": 0}

    candidates = list(candidate_map.values())

    # 获取行业映射
    sector_map = _get_sector_map([c["code"] for c in candidates])

    # 估算协方差矩阵（用简化的相关系数矩阵）
    n = len(candidates)
    # 基础相关系数：同行业0.5，不同行业0.2
    codes = [c["code"] for c in candidates]
    vols = np.array([c["volatility"] for c in candidates])

    corr = np.full((n, n), 0.2)
    for i in range(n):
        corr[i, i] = 1.0
    if sector_map:
        for i in range(n):
            for j in range(i + 1, n):
                si = sector_map.get(codes[i], "")
                sj = sector_map.get(codes[j], "")
                if si and sj and si == sj:
                    corr[i, j] = 0.5
                    corr[j, i] = 0.5

    cov = np.outer(vols, vols) * corr * 252  # 年化协方差

    # 读取当前持仓
    current_weights = _load_current_weights()

    # 优化
    optimizer = PortfolioOptimizer(OptimizerConfig(
        max_positions=min(10, n),
        risk_aversion=2.0,
        turnover_penalty=0.005,
    ))

    cov_df = pd.DataFrame(cov, index=codes, columns=codes)
    result = optimizer.optimize(
        candidates=candidates,
        covariance_matrix=cov_df,
        current_weights=current_weights,
        sector_map=sector_map,
    )

    return {
        "weights": result.weights,
        "expected_return": result.expected_return,
        "expected_vol": result.expected_vol,
        "sharpe_ratio": result.sharpe_ratio,
        "sector_exposure": result.sector_exposure,
        "n_positions": result.n_positions,
        "turnover": result.turnover,
        "details": result.details,
    }


def _get_sector_map(codes: list) -> dict:
    """获取股票→行业映射"""
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


def _run_alpha_analysis(results: dict) -> dict:
    """用 AlphaSynthesizer 做因子截面分析

    Returns:
        {"factor_ic": {factor: {ic, rank_ic, n_samples}},
         "cross_section_zscore": {factor: {code: zscore}},
         "optimized_weights": {factor: weight}}
    """
    try:
        from lib.alpha import AlphaSynthesizer
    except ImportError:
        return {}

    # 收集候选股的因子数据
    factor_rows = []
    for name, df in results.items():
        if df.empty:
            continue
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if not code:
                continue
            factor_rows.append({
                "code": code,
                "RSI": row.get("RSI"),
                "动量5": row.get("动量5"),
                "置信度": row.get("置信度"),
                "综合评分": row.get("综合评分"),
                "涨跌幅": row.get("涨跌幅"),
            })

    if len(factor_rows) < 5:
        return {}

    factor_df = pd.DataFrame(factor_rows).set_index("code")
    factor_df = factor_df.apply(pd.to_numeric, errors="coerce").dropna(how="all")

    if factor_df.empty or len(factor_df) < 3:
        return {}

    synth = AlphaSynthesizer()

    # 截面标准化
    try:
        zscored = synth.normalize_cross_section(factor_df)
        zscore_summary = {}
        for col in zscored.columns:
            vals = zscored[col].dropna()
            if len(vals) > 0:
                zscore_summary[col] = {
                    "mean": round(float(vals.mean()), 4),
                    "std": round(float(vals.std()), 4),
                    "min": round(float(vals.min()), 4),
                    "max": round(float(vals.max()), 4),
                }
    except Exception:
        zscore_summary = {}

    # 因子 IC 分析（用次日收益率近似 — 这里用涨跌幅做简化代理）
    try:
        forward_returns = factor_df["涨跌幅"].fillna(0) / 100 if "涨跌幅" in factor_df.columns else None
        factor_ic = {}
        if forward_returns is not None and len(forward_returns) >= 5:
            for col in factor_df.columns:
                if col == "涨跌幅":
                    continue
                ic_result = synth.compute_ic(factor_df[col], forward_returns)
                if not np.isnan(ic_result.get("rank_ic", np.nan)):
                    factor_ic[col] = {
                        "ic": round(ic_result["ic"], 4) if not np.isnan(ic_result.get("ic", np.nan)) else None,
                        "rank_ic": round(ic_result["rank_ic"], 4),
                        "n_samples": int(ic_result.get("n_samples", 0)),
                    }

            # IC-based 权重优化
            try:
                opt_weights = synth.optimize_weights_ic_ir(
                    factor_df.drop(columns=["涨跌幅"], errors="ignore"),
                    forward_returns,
                )
                opt_weights = {k: round(v, 4) for k, v in opt_weights.items()}
            except Exception:
                opt_weights = {}
        else:
            opt_weights = {}
    except Exception:
        factor_ic = {}
        opt_weights = {}

    return {
        "factor_ic": factor_ic,
        "cross_section_zscore": zscore_summary,
        "optimized_weights": opt_weights,
    }


def _load_current_weights() -> dict:
    """从 PositionManager 读取当前持仓权重"""
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


# ========== 单股评分（供 API 调用） ==========

def get_stock_score(code: str) -> dict:
    """单股完整评分：因子 + 技术 + 四维评分 + 约束 + 风控

    Returns:
        {"code": str, "factors": dict, "combined": dict, "regime": dict,
         "macro_gate": dict, "technical": dict, "score": dict, "constraints": dict}
    """
    hist = get_stock_hist_multi_source(code, days=180, source="auto")
    if hist.empty:
        return {"error": f"未找到 {code} 数据"}

    result = compute_all_signals(hist, factor_config=get_config().factor)
    if not result:
        return {"error": f"{code} 因子计算失败"}

    tech = calc_composite_verdict(hist)

    # 四维评分
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
        # 无实时行情，用因子数据做简化评分
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

    # 约束检查（含基本面）
    fin_data = _get_financial_data(code)
    cr = _constraints.check_ticker(code, market_data=hist, financial_data=fin_data)

    # ML 预测
    ml_prediction = {}
    try:
        from lib.ml_predictor import MLPredictor
        predictor = MLPredictor()
        if not predictor.is_model_stale():
            ml_prediction = predictor.predict_single(result.get("factors", {}))
    except Exception as e:
        logger.debug(f"ML 预测失败: {e}")

    # 裁判团投票评分（含 ML）
    judge_result = {}
    try:
        from lib.judge_panel import JudgePanel
        panel = JudgePanel(
            factors=result.get("factors", {}),
            fundamentals=score_result.get("breakdown", {}),
            market_data=hist,
        )
        judge_result = panel.evaluate_with_ml(ml_prediction)
    except Exception as e:
        logger.debug(f"裁判团评分失败: {e}")

    return {
        "code": code,
        "factors": result.get("factors", {}),
        "combined": result.get("combined", {}),
        "regime": result.get("regime", {}),
        "macro_gate": result.get("macro_gate", {}),
        "technical": {
            "verdict": tech.get("verdict"),
            "signals": tech.get("signals"),
            "summary": tech.get("summary"),
        },
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

def run_all_strategies(preset: str = "balanced") -> dict:
    config = get_config(preset)
    results = {}

    # 启动时清理过期缓存
    clean_cache(max_age_hours=24)

    # 市场概况
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

    # 组合风控检查
    risk_check = _check_portfolio_risk(results)

    # 因子质量快速检查（轻量级，不拉全量数据）
    factor_health = _check_factor_health(results)

    # 组合优化：从候选股中构建最优组合
    portfolio = _optimize_portfolio(results)

    # Alpha 因子合成分析（IC / 截面标准化）
    alpha_analysis = _run_alpha_analysis(results)

    # 审计日志
    scan_result = {
        "results": results,
        "risk_check": risk_check,
        "factor_health": factor_health,
        "portfolio": portfolio,
        "alpha_analysis": alpha_analysis,
        "preset": preset,
    }
    try:
        from lib.audit_logger import log_scan
        log_scan(scan_result)
    except Exception as e:
        logger.debug(f"审计日志写入失败: {e}")

    return scan_result
