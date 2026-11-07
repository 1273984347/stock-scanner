"""选股策略入口 —— 组合协调器

原有功能已拆分到 lib/data_layer.py / lib/strategies.py / lib/pipeline.py。
本模块提供 ScanOrchestrator 统一编排跨模块调用。
"""
from lib.data.data_layer import (
    get_stock_hist, get_stock_hist_multi_source, get_realtime_quotes,
    get_market_sentiment, get_stock_fund_flow, get_news_signals,
    _load_cache, _save_cache, clean_cache, _retry_api_call, _safe_float, _get_financial_data,
)
from lib.strategy.strategies import (
    quick_score, full_score,
    get_zt_candidates, get_volume_breakout_candidates,
    get_hot_board_candidates, get_lhb_candidates, get_multi_factor_candidates,
)
from lib.strategy.pipeline import (
    run_all_strategies, get_stock_score,
    _enrich_with_ml_scores, _optimize_portfolio, _check_portfolio_risk,
    _run_alpha_analysis,
)
from typing import Dict, List, Optional, Any


class ScanOrchestrator:
    """选股策略组合协调器

    负责跨模块的策略编排、数据获取协调、结果聚合。
    本类是 scanner.py 的核心抽象，封装了以下跨模块调用逻辑：
    - 数据层获取（lib.data.data_layer）
    - 策略执行（lib.strategy.strategies）
    - 流水线处理（lib.strategy.pipeline）
    """

    def __init__(self, preset: str = "balanced"):
        self.preset = preset
        self._cache = {}

    def run_all_strategies(
        self,
        symbols: List[str] = None,
        include_ml: bool = True,
        include_alpha: bool = False,
    ) -> Dict[str, Any]:
        """运行所有策略并聚合结果

        Args:
            symbols: 股票代码列表，用于结果过滤（可选）
            include_ml: 是否启用 ML 增强
            include_alpha: 是否执行 Alpha 分析

        Returns:
            聚合后的策略结果字典
        """
        return run_all_strategies(
            symbols=symbols,
            preset=self.preset,
            include_ml=include_ml,
            include_alpha=include_alpha,
        )

    def get_stock_score(self, symbol: str, days: int = 250) -> Optional[Dict]:
        """获取单只股票的综合评分

        Args:
            symbol: 股票代码
            days: 历史数据天数（保留参数，仅用于API兼容性）
        """
        return get_stock_score(symbol)

    def get_realtime_signals(self, symbols: List[str]) -> Dict[str, Any]:
        """获取实时信号（行情 + 情绪 + 资金流）"""
        result = {}
        for sym in symbols:
            try:
                quote = get_realtime_quotes(sym)
                sentiment = get_market_sentiment()
                fund_flow = get_stock_fund_flow(sym)
                result[sym] = {
                    "quote": quote,
                    "sentiment": sentiment,
                    "fund_flow": fund_flow,
                }
            except Exception:
                result[sym] = {"error": "failed to fetch"}
        return result

    # ========== 数据获取接口（统一入口）==========

    def get_stock_hist(self, code: str, days: int = 180):
        """获取个股日线历史数据"""
        return get_stock_hist(code, days=days)

    def get_stock_hist_multi_source(self, code: str, days: int = 180, source: str = "auto"):
        """多数据源获取个股日线"""
        return get_stock_hist_multi_source(code, days=days, source=source)

    def get_realtime_quotes(self):
        """获取实时行情"""
        return get_realtime_quotes()

    def get_market_sentiment(self) -> float:
        """获取市场情绪"""
        return get_market_sentiment()

    def get_news_signals(self, code: str) -> dict:
        """获取新闻/业绩/龙虎榜信号"""
        return get_news_signals(code)
