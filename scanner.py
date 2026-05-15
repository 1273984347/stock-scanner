"""选股策略入口 —— 向后兼容的薄转发层

原有功能已拆分到 lib/data_layer.py / lib/strategies.py / lib/pipeline.py。
本模块仅做 re-export 以保证现有 import 语句继续可用。
"""
from lib.data_layer import (
    get_stock_hist, get_stock_hist_multi_source, get_realtime_quotes,
    get_market_sentiment, get_stock_fund_flow, get_news_signals,
    _load_cache, _save_cache, clean_cache, _retry_api_call, _safe_float, _get_financial_data,
)
from lib.strategies import (
    quick_score, full_score,
    get_zt_candidates, get_volume_breakout_candidates,
    get_hot_board_candidates, get_lhb_candidates, get_multi_factor_candidates,
)
from lib.pipeline import (
    run_all_strategies, get_stock_score,
    _enrich_with_ml_scores, _optimize_portfolio, _check_portfolio_risk,
    _run_alpha_analysis,
)
