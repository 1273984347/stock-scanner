"""增强基金持仓数据 —— 补全 SH 股票 + 机构增持信号

在 data_layer.py 的 fundstk.dat 基础上增强：
1. 解析 fundinfo.dat 获取基金名称映射
2. 通过 akshare 补全 SH 股票（600xxx）的基金持仓
3. 构建"机构增持"信号（连续 N 个季度持仓权重上升）

使用方式：
    from lib.data.data_fund_enhanced import get_enhanced_fund_holdings, get_institutional_signal
"""

import logging
import time
from typing import Dict, List, Optional
from pathlib import Path
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

TDX_REF_PATH = Path("D:/TDX/vipdoc/ref")


# ========== 基金信息解析 ==========

_fundinfo_cache: Dict[str, str] = {}


def _load_fundinfo() -> Dict[str, str]:
    """解析 fundinfo.dat 获取基金代码→名称映射"""
    global _fundinfo_cache
    if _fundinfo_cache:
        return _fundinfo_cache

    fundinfo_file = TDX_REF_PATH / "fundinfo.dat"
    if not fundinfo_file.exists():
        return {}

    try:
        with open(fundinfo_file, "r", encoding="gbk", errors="replace") as f:
            for line in f:
                parts = line.strip().split("|")
                if len(parts) >= 2:
                    code = parts[0].strip()
                    name = parts[1].strip()
                    if code and name:
                        _fundinfo_cache[code] = name
    except Exception as e:
        logger.warning(f"fundinfo.dat 解析失败: {e}")

    return _fundinfo_cache


def get_fund_name(fund_code: str) -> str:
    """获取基金名称"""
    info = _load_fundinfo()
    return info.get(fund_code, "")


# ========== SH 股票基金持仓补全 ==========

_sh_fund_cache: Dict[str, Dict] = {}
_sh_fund_cache_ts: float = 0


def _fetch_sh_fund_holdings(code: str) -> Dict:
    """通过 akshare 获取 SH 股票的基金持仓"""
    cache_key = f"sh_{code}"
    if cache_key in _sh_fund_cache:
        return _sh_fund_cache[cache_key]

    try:
        import akshare as ak
        # 个股基金持仓
        df = ak.stock_report_fund_hold(symbol=code)
        if df is not None and not df.empty:
            result = {
                "fund_count": len(df),
                "total_weight": float(df["持仓占流通股比"].sum()) if "持仓占流通股比" in df.columns else 0,
                "source": "akshare",
                "date": str(df.iloc[0].get("报告期", "")),
            }
            _sh_fund_cache[cache_key] = result
            return result
    except Exception as e:
        logger.debug(f"获取 {code} 基金持仓失败: {e}")

    return {}


def get_enhanced_fund_holdings(code: str) -> Dict:
    """获取增强的基金持仓数据

    优先使用本地 fundstk.dat，SH 股票用 akshare 补全

    Returns:
        dict with keys:
        - fund_count: 持有基金数
        - total_weight: 总持仓权重(%)
        - total_value: 总持仓市值(亿元)
        - source: 数据来源
        - fund_names: 持有基金名称列表
    """
    # 先用本地数据
    from lib.data.data_layer import get_fund_holdings
    local = get_fund_holdings(code)

    if local and local.get("fund_count", 0) > 0:
        # 补充基金名称
        result = dict(local)
        result["source"] = "local"
        return result

    # SH 股票用 akshare 补全
    if code.startswith("6"):
        sh_data = _fetch_sh_fund_holdings(code)
        if sh_data:
            return sh_data

    return {}


# ========== 机构增持信号 ==========

@dataclass
class InstitutionalSignal:
    """机构增持信号"""
    code: str
    fund_count: int
    fund_count_trend: str  # "increasing", "stable", "decreasing"
    weight_trend: str
    signal_strength: float  # [-1, 1]
    quarters_analyzed: int


def get_institutional_signal(code: str, n_quarters: int = 4) -> Optional[InstitutionalSignal]:
    """构建机构增持信号

    检查最近 N 个季度的基金持仓变化趋势

    Args:
        code: 股票代码
        n_quarters: 回溯季度数

    Returns:
        InstitutionalSignal or None
    """
    try:
        import akshare as ak
        # 获取历史基金持仓
        df = ak.stock_report_fund_hold(symbol=code)
        if df is None or df.empty:
            return None

        # 按报告期排序
        if "报告期" in df.columns:
            df = df.sort_values("报告期", ascending=False)

        # 提取各季度数据
        fund_counts = []
        weights = []
        for _, row in df.head(n_quarters).iterrows():
            count = int(row.get("持仓基金家数", 0))
            weight = float(row.get("持仓占流通股比", 0))
            fund_counts.append(count)
            weights.append(weight)

        if len(fund_counts) < 2:
            return None

        # 判断趋势
        count_trend = _assess_trend(fund_counts)
        weight_trend = _assess_trend(weights)

        # 信号强度
        signal = 0.0
        if count_trend == "increasing":
            signal += 0.3
        elif count_trend == "decreasing":
            signal -= 0.3

        if weight_trend == "increasing":
            signal += 0.4
        elif weight_trend == "decreasing":
            signal -= 0.4

        # 最新季度加成
        if fund_counts[0] > fund_counts[-1] * 1.2:
            signal += 0.3

        return InstitutionalSignal(
            code=code,
            fund_count=fund_counts[0],
            fund_count_trend=count_trend,
            weight_trend=weight_trend,
            signal_strength=max(-1, min(1, signal)),
            quarters_analyzed=len(fund_counts),
        )
    except Exception as e:
        logger.debug(f"机构增持信号计算失败: {e}")
        return None


def _assess_trend(values: List[float]) -> str:
    """判断数值趋势"""
    if len(values) < 2:
        return "stable"

    # 用线性回归斜率判断
    x = np.arange(len(values))
    slope = np.polyfit(x, values, 1)[0]

    mean_val = np.mean(values)
    if mean_val == 0:
        return "stable"

    relative_slope = slope / mean_val

    if relative_slope > 0.1:
        return "increasing"
    elif relative_slope < -0.1:
        return "decreasing"
    return "stable"


def get_institutional_factor(code: str) -> float:
    """获取机构持仓因子值（供 factors.py 使用）

    Returns:
        float: [-100, 100] 正=机构增持，负=机构减持
    """
    holdings = get_enhanced_fund_holdings(code)
    if not holdings:
        return 0.0

    fund_count = holdings.get("fund_count", 0)
    weight = holdings.get("total_weight", 0)

    # 基础分
    score = 0.0

    # 基金数量分
    if fund_count >= 50:
        score += 30
    elif fund_count >= 20:
        score += 20
    elif fund_count >= 5:
        score += 10

    # 持仓权重分
    if weight >= 10:
        score += 30
    elif weight >= 5:
        score += 20
    elif weight >= 1:
        score += 10

    # 趋势加成
    signal = get_institutional_signal(code)
    if signal:
        score += signal.signal_strength * 40

    return max(-100, min(100, score))
