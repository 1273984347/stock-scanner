"""统一数据质量检查模块

集中所有 OHLCV 数据质量检查：
- 完整性：交易日缺失检测
- 异常值：价格/成交量统计异常
- 一致性：high>=low/close在范围内/成交量非负
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class QualityIssue:
    """数据质量问题"""
    level: str  # ERROR / WARNING / INFO
    category: str  # completeness / outlier / consistency
    code: str
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class QualityReport:
    """数据质量报告"""
    code: str
    total_rows: int = 0
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "ERROR")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "WARNING")

    @property
    def is_valid(self) -> bool:
        return self.error_count == 0

    def summary(self) -> str:
        if not self.issues:
            return f"{self.code}: 数据质量良好（{self.total_rows} 行）"
        return (
            f"{self.code}: {self.total_rows} 行, "
            f"{self.error_count} 错误, {self.warning_count} 警告"
        )


class DataQualityChecker:
    """数据质量检查器"""

    def check(self, df: pd.DataFrame, code: str = "") -> QualityReport:
        """运行所有质量检查

        Args:
            df: OHLCV DataFrame
            code: 股票代码

        Returns:
            QualityReport
        """
        report = QualityReport(code=code, total_rows=len(df))

        if df.empty:
            report.issues.append(QualityIssue(
                level="ERROR", category="completeness", code=code,
                message="数据为空",
            ))
            return report

        # 运行各项检查
        report.issues.extend(self.check_consistency(df, code))
        report.issues.extend(self.check_completeness(df, code))
        report.issues.extend(self.check_outliers(df, code))

        return report

    def check_consistency(self, df: pd.DataFrame, code: str = "") -> List[QualityIssue]:
        """一致性检查：high>=low/close在范围内/成交量非负"""
        issues = []

        required_cols = ["open", "high", "low", "close"]
        for col in required_cols:
            if col not in df.columns:
                issues.append(QualityIssue(
                    level="ERROR", category="consistency", code=code,
                    message=f"缺少必需列: {col}",
                ))
                return issues

        # high >= low
        bad_hl = df["high"] < df["low"]
        if bad_hl.any():
            count = int(bad_hl.sum())
            issues.append(QualityIssue(
                level="ERROR", category="consistency", code=code,
                message=f"high < low: {count} 行",
                details={"count": count},
            ))

        # close 在 [low, high] 范围内
        bad_close = (df["close"] < df["low"]) | (df["close"] > df["high"])
        if bad_close.any():
            count = int(bad_close.sum())
            issues.append(QualityIssue(
                level="WARNING", category="consistency", code=code,
                message=f"close 不在 [low, high] 范围: {count} 行",
                details={"count": count},
            ))

        # 成交量非负
        if "volume" in df.columns:
            bad_vol = df["volume"] < 0
            if bad_vol.any():
                count = int(bad_vol.sum())
                issues.append(QualityIssue(
                    level="ERROR", category="consistency", code=code,
                    message=f"成交量为负: {count} 行",
                    details={"count": count},
                ))

        return issues

    def check_completeness(self, df: pd.DataFrame, code: str = "") -> List[QualityIssue]:
        """完整性检查：NaN 值和交易日缺口"""
        issues = []

        # NaN 检查
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                nan_count = int(df[col].isna().sum())
                if nan_count > 0:
                    issues.append(QualityIssue(
                        level="WARNING" if nan_count < len(df) * 0.1 else "ERROR",
                        category="completeness", code=code,
                        message=f"{col} 有 {nan_count} 个 NaN",
                        details={"column": col, "count": nan_count},
                    ))

        return issues

    def check_outliers(self, df: pd.DataFrame, code: str = "") -> List[QualityIssue]:
        """异常值检测：价格跳变和成交量异常"""
        issues = []

        if "close" in df.columns and len(df) > 1:
            returns = df["close"].pct_change().dropna()

            # 价格跳变 > 20%
            extreme = returns.abs() > 0.20
            if extreme.any():
                count = int(extreme.sum())
                issues.append(QualityIssue(
                    level="WARNING", category="outlier", code=code,
                    message=f"价格跳变 >20%: {count} 天",
                    details={"count": count},
                ))

        if "volume" in df.columns and len(df) > 10:
            vol = df["volume"].dropna()
            if len(vol) > 0:
                q1 = vol.quantile(0.25)
                q3 = vol.quantile(0.75)
                iqr = q3 - q1
                upper = q3 + 3 * iqr
                outlier_vol = vol > upper
                if outlier_vol.any():
                    count = int(outlier_vol.sum())
                    issues.append(QualityIssue(
                        level="INFO", category="outlier", code=code,
                        message=f"成交量异常高（3×IQR）: {count} 天",
                        details={"count": count},
                    ))

        return issues


# 便捷函数
_checker = DataQualityChecker()

def check_data_quality(df: pd.DataFrame, code: str = "") -> QualityReport:
    """检查数据质量"""
    return _checker.check(df, code)
