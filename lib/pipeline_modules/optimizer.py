"""组合优化模块

从 pipeline.py 提取的组合优化逻辑。
"""
import numpy as np
import pandas as pd
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)


def _get_sector_map(codes: list) -> dict:
    """获取股票行业分布"""
    from lib.data_layer import get_realtime_quotes

    sector_map = {}
    try:
        df = get_realtime_quotes()
        if not df.empty:
            codes_set = set(codes)
            df_filtered = df[df["代码"].astype(str).isin(codes_set)]
            df_filtered = df_filtered.copy()
            df_filtered["代码"] = df_filtered["代码"].astype(str)
            df_filtered["行业"] = df_filtered["行业"].fillna(df_filtered["所属行业"]).fillna("")
            sector_map = df_filtered.set_index("代码")["行业"].to_dict()
    except Exception:
        pass
    return sector_map


def _load_current_weights() -> dict:
    """获取当前持仓权重"""
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


def optimize_portfolio(results: dict) -> dict:
    """优化组合权重

    Args:
        results: 策略结果字典

    Returns:
        包含权重、预期收益、波动率、夏普比率的字典
    """
    from lib.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig
    from lib.data_layer import get_stock_hist_multi_source
    from lib import config

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
                candidate_map[code] = {
                    "code": code,
                    "name": str(row.get("名称", "")),
                    "score": float(score),
                    "volatility": float(row.get("波动率20", row.get("volatility_20", 25))) / 100
                }

    if not candidate_map:
        return {
            "weights": {}, "expected_return": 0, "expected_vol": 0,
            "sharpe_ratio": 0, "sector_exposure": {}, "n_positions": 0
        }

    candidates = list(candidate_map.values())
    sector_map = _get_sector_map([c["code"] for c in candidates])
    n = len(candidates)
    codes = [c["code"] for c in candidates]
    vols = np.array([c["volatility"] for c in candidates])

    # 动态协方差估计
    corr = np.full((n, n), config.DEFAULT_CORRELATION)
    for i in range(n):
        corr[i, i] = 1.0

    corr_estimated = False
    if n >= 2:
        try:
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
            logger.warning(f"[pipeline] 协方差矩阵计算失败，使用默认值: {e}")

    if not corr_estimated:
        logger.debug("协方差矩阵未校准，风险厌恶系数提高")

    if sector_map:
        for i in range(n):
            for j in range(i + 1, n):
                si = sector_map.get(codes[i], "")
                sj = sector_map.get(codes[j], "")
                if si and sj and si == sj:
                    if corr_estimated:
                        corr[i, j] = max(corr[i, j], 0.5)
                        corr[j, i] = max(corr[j, i], 0.5)
                    else:
                        corr[i, j] = 0.5
                        corr[j, i] = 0.5

    cov = np.outer(vols, vols) * corr * 252
    risk_aversion = config.RISK_AVERSION_CALIBRATED if corr_estimated else config.RISK_AVERSION_UNCALIBRATED
    current_weights = _load_current_weights()
    optimizer = PortfolioOptimizer(OptimizerConfig(
        max_positions=min(config.MAX_POSITIONS, n),
        risk_aversion=risk_aversion,
        turnover_penalty=config.TURNOVER_PENALTY
    ))
    cov_df = pd.DataFrame(cov, index=codes, columns=codes)
    result = optimizer.optimize(
        candidates=candidates,
        covariance_matrix=cov_df,
        current_weights=current_weights,
        sector_map=sector_map
    )

    return {
        "weights": result.weights,
        "expected_return": result.expected_return,
        "expected_vol": result.expected_vol,
        "sharpe_ratio": result.sharpe_ratio,
        "sector_exposure": result.sector_exposure,
        "n_positions": result.n_positions,
        "turnover": result.turnover,
        "details": result.details
    }
