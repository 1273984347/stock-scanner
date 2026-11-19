"""
确定性约束系统 —— 硬性规则过滤 + 软性评分调整
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging

# 按需加载（broker 内含 akshare 重依赖，不在 risk 包 import 期拉起）；
# 原因注释「避免循环依赖」已失效——V1 环由 C8 数据类型下沉解除（P1-6）
def _get_limit_pct(code: str, name: str = "") -> float:
    """获取涨跌停幅度（委托给 broker.ABroker.get_limit_pct）"""
    from lib.data.broker import ABroker
    return ABroker.get_limit_pct(code, name)

logger = logging.getLogger(__name__)


class ConstraintLevel(Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class Constraint:
    """约束规则"""
    name: str
    description: str
    level: ConstraintLevel
    check_fn: Callable
    penalty: float = 0.0


@dataclass
class ConstraintResult:
    """约束检查结果"""
    ticker: str
    passed: bool = True
    violations: List[Dict] = field(default_factory=list)
    score_adjustment: float = 0.0
    blocked: bool = False


class DeterministicConstraints:
    """确定性约束引擎"""

    def __init__(self):
        self.constraints: List[Constraint] = []
        self._register_defaults()

    def _register_defaults(self):
        """注册默认约束规则"""
        self.constraints = [
            Constraint(
                name="st_delist",
                description="排除 ST/*ST/退市风险股票",
                level=ConstraintLevel.HARD,
                check_fn=self._check_st_delist,
            ),
            Constraint(
                name="suspension",
                description="排除停牌股票",
                level=ConstraintLevel.HARD,
                check_fn=self._check_suspension,
            ),
            Constraint(
                name="extreme_pe",
                description="PE > 200 或 PE < 0 预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_extreme_pe,
                penalty=0.3,
            ),
            Constraint(
                name="negative_equity",
                description="净资产为负预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_negative_equity,
                penalty=0.5,
            ),
            Constraint(
                name="low_liquidity",
                description="日均成交额 < 1000万 预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_low_liquidity,
                penalty=0.2,
            ),
            Constraint(
                name="recent_limit_up",
                description="近5日涨停过的股票谨慎追高",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_recent_limit_up,
                penalty=0.15,
            ),
            Constraint(
                name="high_pledge_ratio",
                description="股权质押比例 > 50% 预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_high_pledge,
                penalty=0.25,
            ),
            Constraint(
                name="negative_growth",
                description="连续两期营收负增长预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_negative_growth,
                penalty=0.2,
            ),
            Constraint(
                name="new_stock",
                description="上市不满60个交易日谨慎操作",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_new_stock,
                penalty=0.1,
            ),
            Constraint(
                name="small_market_cap",
                description="市值 < 20亿预警",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_small_cap,
                penalty=0.15,
            ),
            Constraint(
                name="consecutive_decline",
                description="连续阴跌（近5日中4日下跌且单日跌幅<3%）",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_consecutive_decline,
                penalty=0.05,
            ),
            Constraint(
                name="high_volume_stall",
                description="高位爆量滞涨（价格高位+成交量>2倍均量+涨幅<0.5%）",
                level=ConstraintLevel.SOFT,
                check_fn=self._check_high_volume_stall,
                penalty=0.08,
            ),
            Constraint(
                name="chase_high_filter",
                description="禁止追高潮段（连续一字板或巨量长阳后）",
                level=ConstraintLevel.HARD,
                check_fn=self._check_chase_high,
            ),
        ]

    def add_constraint(self, constraint: Constraint):
        """添加自定义约束"""
        self.constraints.append(constraint)
        logger.info(f"添加约束: {constraint.name} ({constraint.level.value})")

    def check_ticker(
        self,
        ticker: str,
        market_data: pd.DataFrame = None,
        financial_data: pd.Series = None,
        industry: str = "",
        stock_name: str = "",
    ) -> ConstraintResult:
        """对单个股票执行所有约束检查"""
        if not ticker or not isinstance(ticker, str):
            return ConstraintResult(ticker=str(ticker), passed=False, blocked=True)

        result = ConstraintResult(ticker=ticker)
        context = {
            "ticker": ticker,
            "stock_name": stock_name,
            "market_data": market_data,
            "financial_data": financial_data,
            "industry": industry,
        }

        for constraint in self.constraints:
            try:
                violation = constraint.check_fn(context)
                if violation:
                    result.violations.append({
                        "constraint": constraint.name,
                        "level": constraint.level.value,
                        "description": constraint.description,
                        "detail": violation,
                    })
                    if constraint.level == ConstraintLevel.HARD:
                        result.blocked = True
                        result.passed = False
                    else:
                        result.score_adjustment -= constraint.penalty
            except Exception as e:
                logger.warning(f"约束 {constraint.name} 检查失败: {e}")

        return result

    def check_batch(
        self,
        tickers: List[str],
        market_data_map: Dict[str, pd.DataFrame] = None,
        financial_data_map: Dict[str, pd.Series] = None,
        industry_map: Dict[str, str] = None,
        stock_name_map: Dict[str, str] = None,
    ) -> Dict[str, ConstraintResult]:
        """批量约束检查"""
        results = {}
        for ticker in tickers:
            mkt = (market_data_map or {}).get(ticker)
            fin = (financial_data_map or {}).get(ticker)
            ind = (industry_map or {}).get(ticker, "")
            name = (stock_name_map or {}).get(ticker, "")
            results[ticker] = self.check_ticker(ticker, mkt, fin, ind, name)
        return results

    def filter_blocked(
        self, results: Dict[str, ConstraintResult]
    ) -> Tuple[List[str], List[str]]:
        """分离被阻止和通过的股票"""
        blocked = [t for t, r in results.items() if r.blocked]
        passed = [t for t, r in results.items() if not r.blocked]
        return blocked, passed

    def get_score_adjustments(
        self, results: Dict[str, ConstraintResult]
    ) -> Dict[str, float]:
        """获取评分调整"""
        return {t: r.score_adjustment for t, r in results.items()}

    def _check_st_delist(self, ctx: dict) -> Optional[str]:
        ticker = ctx["ticker"]
        stock_name = str(ctx.get("stock_name", "")).upper()

        if stock_name:
            if stock_name.startswith("*ST") or stock_name.startswith("ST"):
                return f"{ticker} ({ctx.get('stock_name', '')}) 为 ST/*ST 股票"
            if "ST" in stock_name and ("退市" in stock_name or "风险" in stock_name):
                return f"{ticker} ({ctx.get('stock_name', '')}) 存在退市风险警示"

        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            if fin.get("is_st") or fin.get("delist_risk"):
                return f"{ticker} 存在退市风险"

        if stock_name and ("退市" in stock_name):
            return f"{ticker} ({ctx.get('stock_name', '')}) 已进入退市程序"

        return None

    def _check_suspension(self, ctx: dict) -> Optional[str]:
        df = ctx.get("market_data")
        if df is None or df.empty:
            return None
        if "volume" in df.columns:
            recent_vol = df["volume"].tail(5)
            if (recent_vol == 0).all():
                return "近5日成交量为0，可能停牌"
        if "amount" in df.columns:
            recent_amt = df["amount"].tail(5)
            if (recent_amt == 0).all():
                return "近5日成交额为0，可能停牌"
        return None

    def _check_extreme_pe(self, ctx: dict) -> Optional[str]:
        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            pe = fin.get("pe")
            if pe is not None:
                if pe > 200:
                    return f"PE={pe:.1f}，极度高估"
                if pe < 0:
                    return f"PE={pe:.1f}，处于亏损状态"
        return None

    def _check_negative_equity(self, ctx: dict) -> Optional[str]:
        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            equity = fin.get("net_assets") or fin.get("book_value")
            if equity is not None and equity < 0:
                return f"净资产为负 ({equity:.0f})"
        return None

    def _check_low_liquidity(self, ctx: dict) -> Optional[str]:
        df = ctx.get("market_data")
        if df is not None and not df.empty and "amount" in df.columns:
            avg_amount = df["amount"].tail(20).mean()
            if avg_amount < 10_000_000:
                return f"日均成交额 {avg_amount/10000:.0f}万，流动性不足"
        return None

    def _check_recent_limit_up(self, ctx: dict) -> Optional[str]:
        df = ctx.get("market_data")
        ticker = ctx.get("ticker", "")
        if df is not None and not df.empty and "close" in df.columns:
            close = df["close"].values
            if len(close) < 6:
                return None
            recent = close[-5:]
            prev = close[-6:-1]
            limit_pct = _get_limit_pct(ticker, ctx.get("stock_name", ""))
            changes = np.where(prev != 0, (recent - prev) / prev, 0)
            limit_up_days = int(np.sum(changes > limit_pct - 0.005))
            if limit_up_days >= 2:
                return f"近5日涨停 {limit_up_days} 次，追高风险"
        return None

    def _check_high_pledge(self, ctx: dict) -> Optional[str]:
        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            pledge = fin.get("pledge_ratio")
            if pledge is not None and pledge > 0.50:
                return f"股权质押比例 {pledge:.1%}，风险较高"
        return None

    def _check_negative_growth(self, ctx: dict) -> Optional[str]:
        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            growth = fin.get("revenue_growth")
            prev_growth = fin.get("prev_revenue_growth")
            if growth is not None and prev_growth is not None:
                if growth < 0 and prev_growth < 0:
                    return f"连续两期营收负增长 ({prev_growth:.1%}, {growth:.1%})"
        return None

    def _check_new_stock(self, ctx: dict) -> Optional[str]:
        df = ctx.get("market_data")
        if df is not None and not df.empty:
            if len(df) < 60:
                return f"上市仅 {len(df)} 个交易日，数据不足"
        return None

    def _check_small_cap(self, ctx: dict) -> Optional[str]:
        fin = ctx.get("financial_data")
        if fin is not None and hasattr(fin, "get"):
            market_cap = fin.get("market_cap")
            if market_cap is not None and market_cap < 2_000_000_000:
                return f"市值 {market_cap/1e8:.1f}亿，小盘股风险"
        return None

    def _check_consecutive_decline(self, ctx: dict) -> Optional[str]:
        """连续阴跌：近5日中4日下跌且单日跌幅<3%"""
        df = ctx.get("market_data")
        if df is None or len(df) < 5:
            return None
        recent = df.tail(5)
        if "涨跌幅" in recent.columns:
            down_days = int((recent["涨跌幅"] < -0.3).sum())
            max_drop = float(recent["涨跌幅"].min())
            if down_days >= 4 and max_drop > -3.0:
                return f"连续阴跌：近5日{down_days}天下跌"
        return None

    def _check_high_volume_stall(self, ctx: dict) -> Optional[str]:
        """高位爆量滞涨：价格在20日高位+成交量>2倍均量+涨幅<0.5%"""
        df = ctx.get("market_data")
        if df is None or len(df) < 20:
            return None
        last = df.iloc[-1]
        high_20 = df["high"].rolling(20).max().iloc[-1] if "high" in df.columns else 0
        vol_ma20 = df["volume"].rolling(20).mean().iloc[-1] if "volume" in df.columns else 0
        close = last.get("close", 0) if hasattr(last, "get") else 0
        vol = last.get("volume", 0) if hasattr(last, "get") else 0
        pct_chg = last.get("涨跌幅", 0) if hasattr(last, "get") else 0

        if close > 0 and high_20 > 0:
            near_high = close >= high_20 * 0.90
            vol_surge = vol_ma20 > 0 and vol > vol_ma20 * 2.0
            stall = abs(pct_chg) < 0.5
            if near_high and vol_surge and stall:
                ratio = vol / vol_ma20 if vol_ma20 > 0 else 0
                return f"高位爆量滞涨：价格在20日高位90%+，成交量>{ratio:.1f}倍均量，涨幅{pct_chg:.1f}%"
        return None

    def _check_chase_high(self, ctx: dict) -> Optional[str]:
        """禁止追高潮段：连续一字板或巨量长阳后"""
        df = ctx.get("market_data")
        if df is None or len(df) < 3:
            return None
        recent = df.tail(3)
        # 连续一字板：近3日全部涨停（涨跌幅>9.5%）且振幅极小（<1%）
        if "涨跌幅" in recent.columns:
            all_limit_up = bool((recent["涨跌幅"] > 9.5).all())
            if all_limit_up and "high" in recent.columns and "low" in recent.columns:
                avg_amplitude = float(((recent["high"] - recent["low"]) / recent["low"] * 100).mean())
                if avg_amplitude < 1.0:
                    return "连续一字板，禁止追高"
        # 巨量长阳：单日成交量>5倍均量 + 涨幅>7%
        if len(df) >= 20 and "volume" in df.columns:
            vol_ma20 = df["volume"].rolling(20).mean().iloc[-1]
            last = df.iloc[-1]
            last_vol = last.get("volume", 0) if hasattr(last, "get") else 0
            last_chg = last.get("涨跌幅", 0) if hasattr(last, "get") else 0
            if vol_ma20 > 0 and last_vol > vol_ma20 * 5 and last_chg > 7:
                return "巨量长阳后禁止追高"
        return None