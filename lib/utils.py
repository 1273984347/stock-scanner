"""通用工具函数"""
import pandas as pd
import numpy as np


def _safe_float(val, default=0.0):
    """安全提取数值（统一实现）"""
    if val is None:
        return default
    try:
        if pd.isna(val) or np.isnan(val):
            return default
        return float(val)
    except (ValueError, TypeError, OverflowError):
        return default
